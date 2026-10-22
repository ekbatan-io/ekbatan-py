"""A sync transaction belongs to the thread that opened it.

The open transaction lives in a `ContextVar`, and `asyncio.to_thread` - with every helper built on
it - copies the caller's context into the new thread. Without a check, that thread found the
transaction and used its connection while the opening thread was using it too: one connection,
two threads, statements from both mixed into one transaction. The other thread is now refused,
loudly, rather than handed a connection of its own: its work was meant to be inside the
transaction, and outside it a rollback would leave that work behind.

`ThreadPoolExecutor.submit` copies no context, so a pool worker never finds the transaction at
all - `test_concurrency` pins that. Neither does `loop.run_in_executor`, nor a greenlet
`gevent.spawn` starts. anyio's `to_thread.run_sync` and Starlette's `run_in_threadpool` copy it,
as `asyncio.to_thread` does, and are refused the same way.

A FastAPI sync `yield` dependency is the other side of the same coin: FastAPI runs the
dependency and the endpoint in separate copies of the context, so a transaction opened in the
dependency is not the endpoint's. Its block ends with `TransactionContextError`, which says so.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from ekbatan import DEFAULT_SHARD, TransactionContextError, TransactionThreadError
from ekbatan.ids import ShardedId
from fixtures.domain import Wallet
from fixtures.schema import WALLETS


def test_a_thread_started_with_to_thread_is_refused_the_transaction(registry):
    manager = registry.default

    with manager.transaction(), pytest.raises(TransactionThreadError) as refused:
        asyncio.run(asyncio.to_thread(manager.current))

    message = str(refused.value)
    assert f"shard {manager.label} was opened by another thread" in message
    assert "asyncio.to_thread" in message
    assert "anyio/Starlette's run_in_threadpool, or anything else that copies the context" in (
        message
    )


def test_a_repository_s_connection_in_such_a_thread_is_refused_too(registry):
    """`connection()` is what a repository's reads and writes go through."""
    manager = registry.default

    def read() -> object:
        with manager.connection() as conn:
            return conn

    with manager.transaction(), pytest.raises(TransactionThreadError):
        asyncio.run(asyncio.to_thread(read))


def test_updates_gathered_onto_threads_inside_a_transaction_are_all_refused(registry):
    """The shape that would have mixed three threads' statements on one connection."""
    manager = registry.default
    reached: list[int] = []

    def update() -> None:
        reached.append(threading.get_ident())
        with manager.connection():
            pass

    async def all_three() -> list[BaseException | None]:
        return await asyncio.gather(
            *(asyncio.to_thread(update) for _ in range(3)), return_exceptions=True
        )

    with manager.transaction():
        outcomes = asyncio.run(all_three())

    assert len(reached) == 3
    assert all(isinstance(o, TransactionThreadError) for o in outcomes), outcomes


def test_the_opening_thread_still_finds_its_own_transaction(registry):
    manager = registry.default

    with manager.transaction() as tx:
        assert manager.current() is tx.connection
        with manager.connection() as conn:
            assert conn is tx.connection


def test_once_the_transaction_ends_another_thread_is_not_refused(registry):
    manager = registry.default

    with manager.transaction():
        pass

    assert asyncio.run(asyncio.to_thread(manager.current)) is None


# -- review fixes (EKB-0436-) -----------------------------------------------------------------


def _wallet(clock: Any) -> Wallet:
    return Wallet.open(
        id=ShardedId[Wallet].generate(DEFAULT_SHARD),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
        created_date=clock.instant(),
    )


def _count(registry: Any) -> int:
    with registry.default.connection(readonly=True) as conn:
        return int(conn.execute(select(func.count()).select_from(WALLETS)).scalar_one())


def test_a_repository_write_in_a_to_thread_thread_is_refused(registry, wallets, clock):
    manager = registry.default

    with manager.transaction(), pytest.raises(TransactionThreadError):
        asyncio.run(asyncio.to_thread(wallets.add, _wallet(clock)))
    assert _count(registry) == 0, "nothing was written on the borrowed connection"


def test_a_to_thread_thread_may_open_a_transaction_of_its_own(registry, wallets, clock):
    """Its own block makes it that thread's transaction; the outer one is not touched."""
    manager = registry.default

    def own() -> bool:
        with manager.transaction() as tx:
            wallets.add(_wallet(clock))
            return manager.current() is tx.connection

    with manager.transaction():
        assert asyncio.run(asyncio.to_thread(own))
    assert _count(registry) == 1


def test_anyio_s_to_thread_is_refused(registry):
    import anyio
    import anyio.to_thread

    manager = registry.default
    with manager.transaction(), pytest.raises(TransactionThreadError):
        anyio.run(anyio.to_thread.run_sync, manager.current)


def test_starlette_s_run_in_threadpool_is_refused(registry):
    from starlette.concurrency import run_in_threadpool

    manager = registry.default
    with manager.transaction(), pytest.raises(TransactionThreadError):
        asyncio.run(run_in_threadpool(manager.current))


def test_run_in_executor_copies_no_context_so_its_thread_finds_none(registry):
    manager = registry.default

    async def ask() -> object:
        return await asyncio.get_running_loop().run_in_executor(None, manager.current)

    with manager.transaction():
        assert asyncio.run(ask()) is None


def test_a_greenlet_spawned_inside_a_transaction_finds_none(registry):
    """A greenlet starts in a context of its own. Under `make test-gevent` - every thread a
    greenlet, `threading.get_ident` a greenlet's id - this is the run that matters."""
    gevent = pytest.importorskip("gevent")
    manager = registry.default

    with manager.transaction() as tx:
        found = gevent.spawn(manager.current)
        found.join()
        assert found.successful(), found.exception
        assert found.value is None
        assert manager.current() is tx.connection, "and the opener still has its own"


# FastAPI: a sync endpoint, run on Starlette's thread pool.


@pytest.fixture
def client_for() -> Iterator[Any]:
    from fastapi.testclient import TestClient

    opened: list[Any] = []

    def make(app: Any) -> Any:
        client = TestClient(app)
        opened.append(client.__enter__())
        return opened[-1]

    yield make
    for client in opened:
        client.__exit__(None, None, None)


def test_a_sync_endpoint_with_no_transaction_in_the_caller_works(
    registry, wallets, clock, client_for
):
    from fastapi import FastAPI

    app = FastAPI()

    @app.post("/wallets")
    def open_wallet() -> dict[str, bool]:
        wallets.add(_wallet(clock))
        return {"ok": True}

    assert client_for(app).post("/wallets").status_code == 200
    assert _count(registry) == 1


def test_a_transaction_opened_in_the_endpoint_covers_its_work(registry, wallets, clock, client_for):
    """The documented way: open it where the work runs. A failure rolls the write back."""
    from fastapi import FastAPI, HTTPException

    app = FastAPI()

    @app.post("/wallets/{fail}")
    def open_wallet(fail: bool) -> dict[str, bool]:
        manager = registry.default
        with manager.transaction() as tx:
            wallets.add(_wallet(clock))
            assert manager.current() is tx.connection
            if fail:
                raise HTTPException(status_code=409)
        return {"ok": True}

    client = client_for(app)
    assert client.post("/wallets/true").status_code == 409
    assert _count(registry) == 0, "rolled back with the endpoint"
    assert client.post("/wallets/false").status_code == 200
    assert _count(registry) == 1


def test_a_transaction_opened_in_a_sync_yield_dependency_is_said_to_miss_the_endpoint(
    registry, wallets, clock, client_for
):
    """What the docs warn against: the endpoint runs in another context, sees no transaction,
    and its write commits on its own. The block's end says why, instead of Python's bare
    "Token ... was created in a different Context"."""
    from fastapi import Depends, FastAPI

    manager = registry.default
    seen: list[object] = []

    def in_transaction() -> Iterator[None]:
        with manager.transaction():
            yield

    app = FastAPI()

    @app.post("/wallets", dependencies=[Depends(in_transaction)])
    def open_wallet() -> dict[str, bool]:
        seen.append(manager.current())
        wallets.add(_wallet(clock))
        return {"ok": True}

    with pytest.raises(TransactionContextError) as raised:
        client_for(app).post("/wallets")
    assert seen == [None], "the endpoint ran outside the dependency's transaction"
    assert _count(registry) == 1, "and its write committed on its own"
    message = str(raised.value)
    assert "closed in another context than the one that opened it" in message
    assert "never in a dependency" in message
