"""Each scenario, run on the sync executor and on the async one (`ekbatan.aio`).

The async API shares its logic with the sync one - statements, routing, the optimistic lock,
soft delete, the retry policy - so the claim worth testing is that the two *behave* the same.
Each scenario below is written once, as a coroutine, against a `Service` that awaits a call
only when it returned something awaitable. The sync run calls straight through; the async run
awaits. What was committed is then read by a plain sync connection that belongs to neither, so
the observer is the same for both runs.

The two exceptions are written out for each mode, because what they test is the concurrency
model itself: two transactions open at once on threads, and on tasks. Cancellation has no sync
counterpart and is async only.

The async runs use each database's default async driver: asyncpg, aiomysql, aiosqlite.

Most of the suite runs both ways through `tests/modes.py`; what is here is what that harness
cannot say - the scenarios written as coroutines, and tasks running at once on one loop.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import traceback
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, insert, select

from conftest import GLOBAL, MEXICO, NOT_UNDER_GEVENT, _registry, under_gevent
from ekbatan import Action
from ekbatan.action import ActionExecutor, ExecutionConfiguration
from ekbatan.aio import (
    AsyncAction,
    AsyncActionExecutor,
    AsyncDatabaseRegistry,
)
from ekbatan.errors import EntityNotFoundError, StaleRecordError, ValidationError
from ekbatan.ids import ShardedId
from ekbatan.sharding import EmbeddedBitsShardingStrategy, NoShardingStrategy
from fixtures.actions import (
    AsyncCreateWalletAction,
    AsyncDepositMoneyAction,
    AsyncDepositThenFailAction,
    CreateWalletAction,
    CreateWalletParams,
    DepositMoneyAction,
    DepositParams,
    DepositThenFailAction,
    WalletIdParams,
)
from fixtures.domain import NOW, Wallet
from fixtures.logins import data_source
from fixtures.repositories import AsyncWalletRepository, WalletRepository
from fixtures.schema import EVENTS, WALLETS

PARITY_MODES = ("sync", "async")
MAIN = "ekbatan_parity"
SHARDS = {GLOBAL: "ekbatan_parity_global", MEXICO: "ekbatan_parity_mexico"}


@pytest.fixture(params=PARITY_MODES)
def mode(request: Any) -> str:
    if request.param == "async" and under_gevent():
        pytest.skip(NOT_UNDER_GEVENT)
    return str(request.param)


async def aw(value: Any) -> Any:
    """Await it if it is awaitable - which is what lets one scenario drive both modes."""
    return await value if inspect.isawaitable(value) else value


# ------------------------------------------------------------------ the extra actions


class BumpThenDepositAction(Action[DepositParams, Wallet]):
    """Loses a version race on its first attempt: between its read and its commit, somebody
    else's update lands on the same wallet."""

    def __init__(self, wallets: Any, clock: Any) -> None:
        super().__init__(clock)
        self.wallets = wallets
        self.attempts = 0

    def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        self.attempts += 1
        wallet = self.wallets.get(params.wallet_id)
        if self.attempts == 1:
            # Outside the unit of work - there is no transaction open during `perform` - so
            # this commits at once and moves the version on under the read above.
            self.wallets.update(wallet.deposit(Decimal("1")))
        return self.plan.update(wallet.deposit(params.amount))


class AsyncBumpThenDepositAction(AsyncAction[DepositParams, Wallet]):
    def __init__(self, wallets: Any, clock: Any) -> None:
        super().__init__(clock)
        self.wallets = wallets
        self.attempts = 0

    @property
    def name(self) -> str:
        return "BumpThenDepositAction"

    async def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        self.attempts += 1
        wallet = await self.wallets.get(params.wallet_id)
        if self.attempts == 1:
            await self.wallets.update(wallet.deposit(Decimal("1")))
        return self.plan.update(wallet.deposit(params.amount))


class DeleteWalletAction(Action[WalletIdParams, Wallet]):
    def __init__(self, wallets: Any, clock: Any) -> None:
        super().__init__(clock)
        self.wallets = wallets

    def perform(self, principal: str | None, params: WalletIdParams) -> Wallet:
        return self.plan.update(self.wallets.get(params.wallet_id).delete())


class AsyncDeleteWalletAction(AsyncAction[WalletIdParams, Wallet]):
    def __init__(self, wallets: Any, clock: Any) -> None:
        super().__init__(clock)
        self.wallets = wallets

    @property
    def name(self) -> str:
        return "DeleteWalletAction"

    async def perform(self, principal: str | None, params: WalletIdParams) -> Wallet:
        return self.plan.update((await self.wallets.get(params.wallet_id)).delete())


class FailingOutbox:
    """Fails the outbox write - after the rows are already written in the transaction.

    Before failing it looks, on the open transaction, for the row the unit of work wrote, so a
    test can show the row was really there and was then rolled back - not merely never sent.
    """

    def __init__(self, registry: Any) -> None:
        self.registry = registry
        self.seen_inside: list[Decimal] = []

    def _query(self) -> Any:
        return select(WALLETS.c.balance)

    def persist_action_events(self, *args: Any) -> None:
        conn = self.registry.manager(args[6]).current()
        self.seen_inside += [r[0] for r in conn.execute(self._query())]
        raise RuntimeError("outbox down")


class AsyncFailingOutbox(FailingOutbox):
    async def persist_action_events(self, *args: Any) -> None:  # type: ignore[override]
        conn = self.registry.manager(args[6]).current()
        self.seen_inside += [r[0] for r in await conn.execute(self._query())]
        raise RuntimeError("outbox down")


# ------------------------------------------------------------------ the service, both ways


@dataclass
class Service:
    mode: str
    registry: Any
    executor: Any
    wallets: Any
    create: type
    deposit: type
    deposit_then_fail: type
    bump_then_deposit: type
    delete: type

    async def execute(self, action: type, params: Any, **options: Any) -> Any:
        return await aw(self.executor.execute(action, params, **options))

    async def open_wallet(self, balance: str = "10", wallet_id: Any = None) -> Wallet:
        params = CreateWalletParams(
            owner_id=uuid4(),
            currency="EUR",
            initial_balance=Decimal(balance),
            wallet_id=wallet_id,
        )
        wallet: Wallet = await self.execute(self.create, params)
        return wallet


@asynccontextmanager
async def service(
    mode: str,
    backend: Any,
    clock: Any,
    *,
    sharded: bool = False,
    outbox: Callable[[Any], Any] | None = None,
) -> AsyncIterator[Service]:
    """A wallet service over fresh databases: sync, or on `ekbatan.aio` and asyncpg."""
    names = SHARDS if sharded else {GLOBAL: MAIN}
    sharding = EmbeddedBitsShardingStrategy() if sharded else NoShardingStrategy()
    if mode == "sync":
        registry = _registry(backend, names)
        wallets: Any = WalletRepository(Wallet, WALLETS, registry, sharding=sharding, clock=clock)
        kinds: tuple[type, ...] = (
            CreateWalletAction,
            DepositMoneyAction,
            DepositThenFailAction,
            BumpThenDepositAction,
            DeleteWalletAction,
        )
        executor_type: Any = ActionExecutor
    else:
        registry = _registry(backend, names, kind=AsyncDatabaseRegistry)
        wallets = AsyncWalletRepository(Wallet, WALLETS, registry, sharding=sharding, clock=clock)
        kinds = (
            AsyncCreateWalletAction,
            AsyncDepositMoneyAction,
            AsyncDepositThenFailAction,
            AsyncBumpThenDepositAction,
            AsyncDeleteWalletAction,
        )
        executor_type = AsyncActionExecutor
    create, *rest = kinds
    actions = [create(clock), *(kind(wallets, clock) for kind in rest)]
    executor = executor_type(
        namespace="example.wallet",
        registry=registry,
        repositories=[wallets],
        actions=actions,
        events=EVENTS,
        outbox=outbox(registry) if outbox is not None else None,
        clock=clock,
    )
    try:
        yield Service(mode, registry, executor, wallets, *kinds)
    finally:
        await aw(registry.close() if mode == "sync" else registry.aclose())


def committed(backend: Any, name: str = MAIN) -> tuple[dict[Any, Decimal], list[dict[str, Any]]]:
    """What is committed, read by a sync connection that is part of neither run."""
    engine = create_engine(backend.url(name))
    try:
        with engine.connect() as conn:
            balances = {r.id: r.balance for r in conn.execute(select(WALLETS))}
            events = [dict(r._mapping) for r in conn.execute(select(EVENTS))]
        return balances, events
    finally:
        engine.dispose()


def deposit(wallet: Wallet, amount: str) -> DepositParams:
    return DepositParams(wallet_id=wallet.id, amount=Decimal(amount), recipient="ana")


def by_action(events: list[dict[str, Any]], action: str) -> list[dict[str, Any]]:
    return [e for e in events if e["action_name"] == action]


# ------------------------------------------------------------------ scenarios, written once


def test_a_deposit_commits_the_row_and_its_event_together(mode, backend, clock):
    async def scenario() -> None:
        async with service(mode, backend, clock) as svc:
            wallet = await svc.open_wallet("10")
            after = await svc.execute(svc.deposit, deposit(wallet, "5"))
            assert after.balance == Decimal("15")
            assert (await aw(svc.wallets.get(wallet.id))).balance == Decimal("15")
            assert (await aw(svc.wallets.get(wallet.id))).version == 2

        balances, events = committed(backend)
        assert balances == {wallet.id.value: Decimal("15")}
        deposited = by_action(events, "DepositMoneyAction")
        assert [e["event_type"] for e in deposited] == ["WalletMoneyDepositedEvent"]
        assert deposited[0]["payload"]["newBalance"] == Decimal("15")
        assert deposited[0]["action_params"]["amount"] == Decimal("5")
        assert [e["event_type"] for e in by_action(events, "CreateWalletAction")] == [
            "WalletCreatedEvent"
        ]

    asyncio.run(scenario())


def test_a_failing_action_commits_neither_row_nor_event(mode, backend, clock):
    async def scenario() -> None:
        async with service(mode, backend, clock) as svc:
            wallet = await svc.open_wallet("10")
            with pytest.raises(ValidationError, match="changed my mind"):
                await svc.execute(svc.deposit_then_fail, deposit(wallet, "5"))

        balances, events = committed(backend)
        assert balances == {wallet.id.value: Decimal("10")}
        assert by_action(events, "DepositThenFailAction") == []

    asyncio.run(scenario())


def test_a_failed_outbox_write_rolls_back_the_row_already_written(mode, backend, clock):
    """Both or neither, with the row really written first: the outbox fails after the wallet
    row is on the transaction - it sees the new balance there - and the commit never happens."""
    failing: list[FailingOutbox] = []

    def outbox(registry: Any) -> FailingOutbox:
        made = FailingOutbox(registry) if mode == "sync" else AsyncFailingOutbox(registry)
        failing.append(made)
        return made

    async def scenario() -> None:
        async with service(mode, backend, clock, outbox=outbox) as svc:
            wallet = Wallet.open(
                id=ShardedId[Wallet].random(),
                owner_id=uuid4(),
                currency="EUR",
                balance=Decimal("10"),
                created_date=NOW,
            )
            await aw(svc.wallets.add(wallet))  # outside any unit of work: commits itself
            with pytest.raises(RuntimeError, match="outbox down"):
                await svc.execute(svc.deposit, deposit(wallet, "5"))

        assert failing[0].seen_inside == [Decimal("15")], "the row was written before the failure"
        balances, events = committed(backend)
        assert balances == {wallet.id.value: Decimal("10")}, "and rolled back with it"
        assert events == []

    asyncio.run(scenario())


def test_a_lost_version_race_is_retried_and_commits_once(mode, backend, clock):
    async def scenario() -> None:
        async with service(mode, backend, clock) as svc:
            wallet = await svc.open_wallet("10")
            after = await svc.execute(svc.bump_then_deposit, deposit(wallet, "5"))
            action = svc.executor.actions.get(svc.bump_then_deposit)
            assert action.attempts == 2
            assert after.balance == Decimal("16")

        balances, events = committed(backend)
        assert balances == {wallet.id.value: Decimal("16")}
        # The losing attempt wrote nothing to the outbox; the winning one wrote one event.
        assert len(by_action(events, "BumpThenDepositAction")) == 1

    asyncio.run(scenario())


def test_a_lost_version_race_without_retry_raises_and_writes_nothing(mode, backend, clock):
    async def scenario() -> None:
        async with service(mode, backend, clock) as svc:
            wallet = await svc.open_wallet("10")
            with pytest.raises(StaleRecordError):
                await svc.execute(
                    svc.bump_then_deposit,
                    deposit(wallet, "5"),
                    config=ExecutionConfiguration.no_retry(),
                )

        balances, events = committed(backend)
        assert balances == {wallet.id.value: Decimal("11")}, "only the other writer's update"
        assert by_action(events, "BumpThenDepositAction") == []

    asyncio.run(scenario())


def test_a_soft_deleted_row_is_hidden_from_every_read(mode, backend, clock):
    async def scenario() -> None:
        async with service(mode, backend, clock) as svc:
            kept = await svc.open_wallet("10")
            gone = await svc.open_wallet("20")
            await svc.execute(svc.delete, WalletIdParams(wallet_id=gone.id))

            assert await aw(svc.wallets.find(gone.id)) is None
            with pytest.raises(EntityNotFoundError):
                await aw(svc.wallets.get(gone.id))
            assert [w.id for w in await aw(svc.wallets.find_where())] == [kept.id]
            assert [w.id for w in await aw(svc.wallets.all())] == [kept.id]
            assert await aw(svc.wallets.find_where(WALLETS.c.owner_id == gone.owner_id)) == []

        balances, events = committed(backend)
        assert set(balances) == {kept.id.value, gone.id.value}, "the row is still there"
        assert [e["event_type"] for e in by_action(events, "DeleteWalletAction")] == [
            "WalletDeletedEvent"
        ]

    asyncio.run(scenario())


def test_a_sharded_unit_of_work_lands_on_its_own_shard(mode, backend, clock):
    async def scenario() -> None:
        async with service(mode, backend, clock, sharded=True) as svc:
            wallet = await svc.open_wallet("10", wallet_id=ShardedId[Wallet].generate(MEXICO))
            await svc.execute(svc.deposit, deposit(wallet, "5"))
            assert [w.id for w in await aw(svc.wallets.find_where(shard=MEXICO))] == [wallet.id]
            assert await aw(svc.wallets.find_where(shard=GLOBAL)) == []
            assert [w.id for w in await aw(svc.wallets.find_where())] == [wallet.id]

        mexico, mexico_events = committed(backend, SHARDS[MEXICO])
        assert mexico == {wallet.id.value: Decimal("15")}
        assert {e["action_name"] for e in mexico_events} == {
            "CreateWalletAction",
            "DepositMoneyAction",
        }
        assert committed(backend, SHARDS[GLOBAL]) == ({}, [])

    asyncio.run(scenario())


def test_each_mode_refuses_the_other_kind_of_action(mode, backend, clock):
    async def scenario() -> None:
        async with service(mode, backend, clock) as svc:
            other = AsyncCreateWalletAction(clock) if mode == "sync" else CreateWalletAction(clock)
            with pytest.raises(ValidationError, match="all-sync or all-async"):
                type(svc.executor)(
                    namespace="example.wallet",
                    registry=svc.registry,
                    repositories=[svc.wallets],
                    actions=[other],
                    events=EVENTS,
                    clock=clock,
                )

    asyncio.run(scenario())


# ------------------------------------------------- two transactions at once: threads, tasks


def _isolation_row(wallets: Any) -> dict[str, Any]:
    return wallets.to_row(
        Wallet.open(
            id=ShardedId[Wallet].random(),
            owner_id=uuid4(),
            currency="EUR",
            balance=Decimal("1"),
            created_date=NOW,
        )
    )


def _assert_isolated(seen: dict[str, Any], backend: Any, ids: list[Any]) -> None:
    assert seen["a_conn"] is not seen["b_conn"], "each has a connection of its own"
    assert seen["a_sees_b"] == 0, "A never saw B's uncommitted row"
    assert seen["b_sees_a"] == 0, "B never saw A's uncommitted row"
    assert seen["b_joined"] is True, "a repository call inside B joined B's transaction"
    assert seen["a_still_own"] is True, "while B was open, A's ambient was still A's"
    assert seen["after"] is None, "and nothing is ambient once both are done"
    balances, _ = committed(backend)
    assert set(balances) == set(ids), "both committed"


def test_two_concurrent_transactions_never_see_each_other(mode, backend, clock, dialect):
    """The ambient transaction is a ContextVar: per thread under sync, per task under async."""
    if dialect == "sqlite":
        pytest.skip("SQLite takes one writer at a time, so two open write transactions block")

    def count(rows: Any) -> int:
        return int(next(iter(rows))[0])

    def where_id(row: dict[str, Any]) -> Any:
        return select(func.count()).select_from(WALLETS).where(WALLETS.c.id == row["id"])

    if mode == "sync":

        async def scenario() -> None:
            async with service(mode, backend, clock) as svc:
                manager = svc.registry.default
                row_a, row_b = _isolation_row(svc.wallets), _isolation_row(svc.wallets)
                a_in, b_checked, a_checked = (threading.Event() for _ in range(3))
                seen: dict[str, Any] = {}

                def a() -> None:
                    with manager.transaction() as tx:
                        tx.execute(insert(WALLETS).values(row_a))
                        seen["a_conn"] = manager.current()
                        a_in.set()
                        assert b_checked.wait(10)
                        seen["a_sees_b"] = count(tx.execute(where_id(row_b)))
                        seen["a_still_own"] = manager.current() is tx.connection
                        a_checked.set()

                def b() -> None:
                    assert a_in.wait(10)
                    with manager.transaction() as tx:
                        tx.execute(insert(WALLETS).values(row_b))
                        seen["b_conn"] = manager.current()
                        seen["b_sees_a"] = count(tx.execute(where_id(row_a)))
                        with svc.wallets.tx_db_else_db() as conn:
                            seen["b_joined"] = conn is tx.connection
                        b_checked.set()
                        # Open until A has looked, so what A saw was B uncommitted.
                        assert a_checked.wait(10)

                threads = [threading.Thread(target=a), threading.Thread(target=b)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(20)
                seen["after"] = manager.current()
            _assert_isolated(seen, backend, [row_a["id"], row_b["id"]])

    else:

        async def scenario() -> None:
            async with service(mode, backend, clock) as svc:
                manager = svc.registry.default
                row_a, row_b = _isolation_row(svc.wallets), _isolation_row(svc.wallets)
                a_in, b_checked, a_checked = (asyncio.Event() for _ in range(3))
                seen: dict[str, Any] = {}

                async def a() -> None:
                    async with manager.transaction() as tx:
                        await tx.execute(insert(WALLETS).values(row_a))
                        seen["a_conn"] = manager.current()
                        a_in.set()
                        await asyncio.wait_for(b_checked.wait(), 10)
                        seen["a_sees_b"] = count(await tx.execute(where_id(row_b)))
                        seen["a_still_own"] = manager.current() is tx.connection
                        a_checked.set()

                async def b() -> None:
                    await asyncio.wait_for(a_in.wait(), 10)
                    async with manager.transaction() as tx:
                        await tx.execute(insert(WALLETS).values(row_b))
                        seen["b_conn"] = manager.current()
                        seen["b_sees_a"] = count(await tx.execute(where_id(row_a)))
                        async with svc.wallets.tx_db_else_db() as conn:
                            seen["b_joined"] = conn is tx.connection
                        b_checked.set()
                        await asyncio.wait_for(a_checked.wait(), 10)

                await asyncio.gather(a(), b())
                seen["after"] = manager.current()
            _assert_isolated(seen, backend, [row_a["id"], row_b["id"]])

    asyncio.run(scenario())


def test_many_concurrent_async_deposits_each_commit_exactly_once(mode, backend, clock):
    """Fifty units of work at once on one event loop, each with its own plan and transaction."""
    if mode == "sync":
        pytest.skip("the async executor's own concurrency; the sync one is covered by threads")

    async def scenario() -> None:
        async with service(mode, backend, clock) as svc:
            wallets = [await svc.open_wallet("0") for _ in range(50)]
            done = await asyncio.gather(
                *(svc.execute(svc.deposit, deposit(w, str(i + 1))) for i, w in enumerate(wallets))
            )
            assert [d.balance for d in done] == [Decimal(i + 1) for i in range(50)]

        balances, events = committed(backend)
        assert balances == {w.id.value: Decimal(i + 1) for i, w in enumerate(wallets)}
        deposited = by_action(events, "DepositMoneyAction")
        assert len(deposited) == 50
        assert {e["model_id"] for e in deposited} == {str(w.id) for w in wallets}

    asyncio.run(scenario())


# ------------------------------------------------------------------ async only


def test_a_cancelled_task_rolls_its_transaction_back(mode, backend, clock):
    if mode == "sync":
        pytest.skip("cancellation is an asyncio thing")

    async def scenario() -> None:
        async with service(mode, backend, clock) as svc:
            manager = svc.registry.default
            row = _isolation_row(svc.wallets)
            inside = asyncio.Event()

            async def work() -> None:
                async with manager.transaction() as tx:
                    await tx.execute(insert(WALLETS).values(row))
                    inside.set()
                    await asyncio.sleep(3600)

            task = asyncio.create_task(work())
            await inside.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            assert manager.current() is None
            assert manager.primary.sync_engine.pool.checkedout() == 0, "the connection went back"
            # And it went back clean: the next transaction on the pool sees nothing.
            async with manager.transaction() as tx:
                rows = await tx.execute(select(func.count()).select_from(WALLETS))
                assert rows.scalar_one() == 0

        assert committed(backend) == ({}, [])

    asyncio.run(scenario())


def test_an_async_registry_never_shows_its_password(mode, backend, dialect):
    if mode == "sync":
        pytest.skip("the sync side has its own: test_secrets_stay_secret.py")
    if dialect == "sqlite":
        pytest.skip("SQLite has no passwords")
    marker = "Mk-7f3a-SECRET-pw"
    plain = data_source(backend.url("ekbatan_parity_secret"))
    # The real login, with the marker as its password: a refused connection, whose error must
    # not repeat it either.
    config = type(plain)(url=plain.url, username=plain.username, password=marker, pool_size=1)

    async def scenario() -> list[str]:
        from ekbatan.aio import async_engine_for
        from ekbatan.aio.persistence import AsyncTransactionManager

        engine = async_engine_for(config)
        manager = AsyncTransactionManager(GLOBAL, engine)
        registry = AsyncDatabaseRegistry({GLOBAL: manager})
        shown = [repr(config), repr(engine), str(engine.url), repr(manager), repr(registry)]
        try:
            async with manager.transaction() as tx:
                await tx.execute(select(1))
        except Exception as error:
            shown += [str(error), repr(error), "".join(traceback.format_exception(error))]
        else:
            pytest.fail("a wrong password connected")
        finally:
            await registry.aclose()
        return shown

    shown = asyncio.run(scenario())
    assert len(shown) == 8
    leaked = [text for text in shown if marker in text]
    assert leaked == []
