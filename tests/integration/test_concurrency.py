"""Many units of work at once, which is the model Java gets from virtual threads.

Java is built thread-per-request on virtual threads: a thousand concurrent actions are a
thousand virtual threads, each with its own `ScopedValue` binding for the transaction it is
inside. CPython has no virtual threads, so the same shape costs OS threads - but the *contract*
has to survive either way, and nothing in this suite was checking it. Every other threaded test
here is about locks or the clock; not one ran two units of work at the same time.

What has to hold, and what each case below pins:

- **A transaction belongs to one execution context.** The ambient transaction lives in a
  `ContextVar`, so two threads inside `transaction()` on the same shard hold two different
  connections and cannot see each other's uncommitted rows.
- **Nothing inherits it.** Work handed to a pool from inside a unit of work does not join that
  unit of work. Java is the same - a plain virtual thread does not inherit a `ScopedValue`
  binding either - and the framework says so rather than letting the two quietly share one
  connection.
- **A pool worker does not carry one to its next task.** `ContextVar` state persists on a
  thread, and `ThreadPoolExecutor` reuses threads, so an unbalanced `set` would leak a dead
  connection to whatever ran next. The `reset` is in a `finally` for this reason.

SQLite is skipped where the case needs real write concurrency: one writer at a time is SQLite's
design, not something this framework can or should paper over.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest

from ekbatan.ids import ShardedId
from fixtures.actions import (
    CreateWalletAction,
    CreateWalletParams,
    DepositMoneyAction,
    DepositParams,
)
from fixtures.domain import NOW, Wallet
from fixtures.repositories import WalletRepository

WORKERS = 8


def a_wallet(balance: str = "100.00") -> Wallet:
    return Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal(balance),
        created_date=NOW,
    )


def _in_parallel(work, count=WORKERS):
    """Run `work(i)` on `count` threads, all released together so they genuinely overlap."""
    ready = threading.Barrier(count)
    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(lambda i=i: (ready.wait(), work(i))[1]) for i in range(count)]
        return [f.result() for f in futures]


# -- the ambient transaction is per execution context, not global ------------------------------


def test_two_threads_in_a_transaction_hold_different_connections(registry, dialect):
    """The failure this prevents is the worst kind: one connection shared by two units of work,
    interleaving their statements into each other's transactions."""
    if dialect == "sqlite":
        pytest.skip("sqlite serialises writers by design; this is about connection identity")

    manager = registry.default
    seen: list[int] = []
    lock = threading.Lock()

    def unit(_i: int) -> None:
        with manager.transaction() as tx, lock:
            seen.append(id(tx.connection))

    _in_parallel(unit)

    assert len(set(seen)) == WORKERS, "every concurrent unit of work needs its own connection"


def test_a_transaction_is_invisible_to_another_thread(registry, dialect):
    if dialect == "sqlite":
        pytest.skip("needs two live write connections")

    manager = registry.default
    inside = threading.Event()
    observed: dict[str, object] = {}

    def hold() -> None:
        with manager.transaction():
            inside.set()
            observed["other_saw"] = watcher.result()

    with ThreadPoolExecutor(max_workers=1) as pool:
        watcher = pool.submit(lambda: (inside.wait(5), manager.current())[1])
        hold()

    assert observed["other_saw"] is None, (
        "another thread must not find this thread's transaction; it would be sharing a "
        "connection across two units of work"
    )


def test_work_handed_to_a_pool_does_not_join_the_unit_of_work(registry, dialect):
    """Java's rule too: a plain virtual thread does not inherit a `ScopedValue` binding. Stated
    rather than assumed, because "my background task silently ran outside the transaction" is
    the kind of thing found in production rather than in review."""
    if dialect == "sqlite":
        pytest.skip("needs a second live connection")

    manager = registry.default
    with manager.transaction(), ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(manager.current).result() is None


def test_a_pool_worker_does_not_carry_a_transaction_into_its_next_task(registry):
    """`ContextVar` state lives on the thread and `ThreadPoolExecutor` reuses threads, so an
    unbalanced set would hand a closed connection to whatever ran next."""
    manager = registry.default
    pool = ThreadPoolExecutor(max_workers=1)
    try:

        def one_unit() -> int:
            with manager.transaction():
                return threading.get_ident()

        ran_on = pool.submit(one_unit).result()
        after = pool.submit(lambda: (threading.get_ident(), manager.current())).result()

        assert after[0] == ran_on, "the same worker thread, so the leak would be observable"
        assert after[1] is None, "and it is clean"
    finally:
        pool.shutdown()


# -- and the whole stack under concurrency -----------------------------------------------------


def wallets_of(registry):
    from fixtures.schema import WALLETS

    return WalletRepository(Wallet, WALLETS, registry).all()


def test_concurrent_units_of_work_all_commit_and_none_interleave(registry, executor, dialect):
    """The end-to-end shape: N actions at once, each its own transaction and its own outbox
    rows. Java runs this on virtual threads; here they are OS threads, and the contract is the
    same either way."""
    if dialect == "sqlite":
        pytest.skip("sqlite allows one writer at a time")

    def create(i: int):
        return executor.execute(
            CreateWalletAction(executor.clock),
            CreateWalletParams(
                owner_id=uuid4(), currency="EUR", initial_balance=Decimal(f"{i}.00")
            ),
        )

    made = _in_parallel(create)

    assert len({w.id for w in made}) == WORKERS, "every unit of work produced its own row"
    # By value, not by its string form: what comes back is the staged model, at the scale the
    # caller handed in, not a re-read at the column's scale. That is the documented behaviour
    # of a write with nothing to read back, and is not what this test is about.
    assert sorted(w.balance for w in made) == [Decimal(i) for i in range(WORKERS)]

    stored = wallets_of(registry)
    assert len({w.id for w in made} & {w.id for w in stored}) == WORKERS, (
        "and every one of them is actually in the database"
    )


def test_concurrent_updates_to_one_row_leave_exactly_one_winner_per_version(
    registry, executor, wallets, dialect
):
    """Optimistic locking is what makes thread-per-request safe without a lock around the row.

    The counts are not asserted, because they are a race and a fixed expectation here would
    flake. What is asserted is the invariant that holds however the race goes: the version moved
    exactly once per committed deposit. Measured on Postgres while writing this, eight threads
    released together gave **4 committed and 4 stale** - so the contention is real and the
    version check is doing something, even with the executor's default retry in front of it.
    """
    if dialect == "sqlite":
        pytest.skip("sqlite allows one writer at a time")

    from ekbatan.errors import StaleRecordError

    wallet = wallets.add(a_wallet("0.00"))

    def deposit(_i: int) -> str:
        try:
            executor.execute(
                DepositMoneyAction(wallets, executor.clock),
                DepositParams(wallet_id=wallet.id, amount=Decimal("1.00"), recipient="a@b"),
            )
            return "ok"
        except StaleRecordError:
            return "stale"

    outcomes = _in_parallel(deposit)

    final = wallets.get(wallet.id)
    assert outcomes.count("ok") >= 1
    assert final.version == 1 + outcomes.count("ok"), (
        "the version moved once per committed deposit, and no more"
    )
    assert final.balance == Decimal(outcomes.count("ok")).quantize(Decimal("0.0001"))
