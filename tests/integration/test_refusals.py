"""The two refusals, on real databases: an object used in a process `fork()` copied it into, and
the sync API called on an event loop's thread.

**After fork.** gunicorn `--preload`, uwsgi, Celery prefork, RQ and `multiprocessing` build a
worker by copying a process that may already hold ekbatan's pools - their connections are the
parent's sockets, and the threads behind them did not come along. Each object that owns
connections or threads remembers the process that built it and refuses to work in any other,
with `ForkedProcessError`. Nothing is reset or reconnected. Each test forks for real: what was
built before the fork is refused in the child, what the child builds works, and the parent is
untouched.

**On an event loop.** The sync API waits on the database; on a loop's thread it would stop every
other task. `BlockingCallError` at its doors - a transaction, a connection, a repository's
reads, the executor, a migration run - and silence everywhere the sync API is meant to run: a
worker thread from `asyncio.to_thread` or `run_in_executor`, a FastAPI `def` endpoint, a sync
handler on dispatch's threads, the test harness's calling thread, a gevent greenlet.
"""

from __future__ import annotations

import asyncio
import os
import select
import signal
import threading
import time
import warnings
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, text
from sqlalchemy import select as sql_select

from conftest import NOT_UNDER_GEVENT, _registry, under_gevent
from ekbatan import (
    DEFAULT_SHARD,
    BlockingCallError,
    DatabaseRegistry,
    ForkedProcessError,
)
from ekbatan.aio import AsyncDatabaseRegistry
from ekbatan.distributedjobs import DistributedJob, job_registry
from ekbatan.events import HandlerLoop
from ekbatan.ids import ShardedId
from ekbatan.migrations import migrate
from fixtures.actions import CreateWalletAction, CreateWalletParams
from fixtures.domain import NOW, Wallet
from fixtures.handlers import RecordingHandler
from fixtures.logins import single
from fixtures.repositories import AsyncWalletRepository, WalletRepository
from fixtures.schema import WALLETS

#: What every ForkedProcessError says, whatever it was raised for.
FORKED = (
    "was created in another process and copied into this one by fork() - gunicorn --preload, "
    "uwsgi, Celery prefork, RQ and multiprocessing all fork. Its connections and threads belong "
    "to the process that created it. Create ekbatan's objects - the database registry, the job "
    "registry, lock providers - in your app's startup code (the FastAPI lifespan, or the top of "
    "the worker's main()), not at import time."
)

#: What every BlockingCallError says, whatever it was raised for.
BLOCKING = (
    "was called on an asyncio event loop's thread (inside `async def`). The sync API waits on "
    "the database, and here it would stop the event loop and every request on it. In `async "
    "def` code use ekbatan.aio; or make the endpoint a plain `def`; or call it with `await "
    "asyncio.to_thread(...)`."
)


# ------------------------------------------------------------------ helpers


def in_a_child(work: Callable[[], object], timeout: float = 30.0) -> list[str]:
    """Run ``work`` in a child made by ``os.fork()``; what it returned, one line per item, or the
    error it raised as ``"<type>: <message>"``. A child that hangs is killed and says so."""
    read, write = os.pipe()
    with warnings.catch_warnings():
        # Python warns that forking a process with threads may deadlock; this test forks on
        # purpose, as a server does, and the child touches no lock a parent thread may hold.
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = os.fork()
    if pid == 0:  # the child: never returns into pytest
        code = 0
        try:
            os.close(read)
            try:
                outcome = work()
                lines = outcome if isinstance(outcome, list) else [str(outcome)]
            except BaseException as error:
                lines = [f"{type(error).__name__}: {error}"]
            os.write(write, "\n".join(str(line) for line in lines).encode())
        except BaseException:
            code = 1
        finally:
            os._exit(code)
    os.close(write)
    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    try:
        while True:
            left = deadline - time.monotonic()
            ready, _, _ = select.select([read], [], [], max(left, 0))
            if not ready:
                os.kill(pid, signal.SIGKILL)
                return ["the child hung"]
            chunk = os.read(read, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(read)
        os.waitpid(pid, 0)
    return b"".join(chunks).decode().split("\n")


def refused(lines: list[str]) -> None:
    """Every line a ForkedProcessError with the whole message."""
    assert lines, "the child said nothing"
    for line in lines:
        assert line.startswith(f"{ForkedProcessError.__name__}: "), line
        assert line.endswith(FORKED), line


def a_wallet() -> Wallet:
    return Wallet.open(
        id=ShardedId[Wallet].generate(DEFAULT_SHARD),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
        created_date=NOW,
    )


def count(registry: DatabaseRegistry) -> int:
    with registry.default.connection(readonly=True) as conn:
        return int(conn.execute(sql_select(func.count()).select_from(WALLETS)).scalar_one())


def each_door(registry: DatabaseRegistry, wallets: Any, known: Wallet) -> list[str]:
    """Every way the sync API reaches a database, each tried once; what each raised."""
    manager = registry.default
    tries: dict[str, Callable[[], object]] = {
        "transaction": lambda: manager.transaction().__enter__(),
        "connection": lambda: manager.connection().__enter__(),
        "readonly connection": lambda: manager.connection(readonly=True).__enter__(),
        "repository read (db)": lambda: wallets.find(known.id),
        "repository readonly_db": lambda: wallets.readonly_db().__enter__(),
        "repository write (tx_db_else_db)": lambda: wallets.add(a_wallet()),
    }
    said: list[str] = []
    for name, attempt in tries.items():
        try:
            attempt()
        except BaseException as error:
            said.append(f"{type(error).__name__}: {error}")
        else:
            said.append(f"{name} was let through")
    return said


@pytest.fixture
def fork_db(backend: Any) -> dict[Any, str]:
    if not hasattr(os, "fork"):
        pytest.skip("no fork() on this platform")
    return {DEFAULT_SHARD: "ekbatan_fork"}


# ============================================================ refusal 1: after fork


def test_a_sync_registry_built_before_fork_is_refused_in_the_child(backend, fork_db, clock):
    registry = _registry(backend, fork_db)
    try:
        wallets = WalletRepository(Wallet, WALLETS, registry, clock=clock)
        known = wallets.add(a_wallet())  # the pool now holds a live connection to copy

        said = in_a_child(lambda: each_door(registry, wallets, known))

        refused(said)
        assert len(said) == 6
        assert said[0].startswith(f"ForkedProcessError: {registry.default!r} was created")
        # The parent is untouched: its pool still works, reads and writes.
        assert wallets.find(known.id) == known
        with registry.default.transaction():
            wallets.add(a_wallet())
        assert count(registry) == 2
    finally:
        registry.close()


def test_a_sync_registry_built_in_the_child_works(backend, fork_db, clock):
    parent = _registry(backend, fork_db)  # migrated, emptied, and holding the parent's pool
    try:
        wallets = WalletRepository(Wallet, WALLETS, parent, clock=clock)
        wallets.add(a_wallet())

        def child() -> list[str]:
            own = DatabaseRegistry.from_config(single(backend.url(fork_db[DEFAULT_SHARD])))
            try:
                mine = WalletRepository(Wallet, WALLETS, own, clock=clock)
                mine.add(a_wallet())
                return [str(count(own))]
            finally:
                own.close()

        assert in_a_child(child) == ["2"]
        assert count(parent) == 2, "and the parent sees the child's row through its own pool"
    finally:
        parent.close()


def test_an_async_registry_built_before_fork_is_refused_in_the_child(backend, fork_db, clock):
    if under_gevent():
        pytest.skip(NOT_UNDER_GEVENT)

    async def scenario() -> list[str]:
        registry = await asyncio.to_thread(_registry, backend, fork_db, kind=AsyncDatabaseRegistry)
        try:
            wallets = AsyncWalletRepository(Wallet, WALLETS, registry, clock=clock)
            known = await wallets.add(a_wallet())
            manager = registry.default

            async def doors() -> list[str]:
                tries = {
                    "transaction": lambda: manager.transaction().__aenter__(),
                    "connection": lambda: manager.connection().__aenter__(),
                    "repository read": lambda: wallets.find(known.id),
                    "repository write": lambda: wallets.add(a_wallet()),
                }
                said: list[str] = []
                for name, attempt in tries.items():
                    try:
                        await attempt()
                    except BaseException as error:
                        said.append(f"{type(error).__name__}: {error}")
                    else:
                        said.append(f"{name} was let through")
                return said

            said = in_a_child(lambda: asyncio.run(doors()))
            # The parent's pool, on the parent's loop, still works.
            assert await wallets.find(known.id) == known
            async with manager.transaction():
                await wallets.add(a_wallet())
            async with manager.connection(readonly=True) as conn:
                rows = await conn.execute(sql_select(func.count()).select_from(WALLETS))
                assert rows.scalar_one() == 2
            return said
        finally:
            await registry.aclose()

    said = asyncio.run(scenario())
    refused(said)
    assert len(said) == 4


def test_an_async_registry_built_in_the_child_works(backend, fork_db, clock):
    if under_gevent():
        pytest.skip(NOT_UNDER_GEVENT)
    parent = _registry(backend, fork_db)
    try:

        async def own() -> str:
            registry = AsyncDatabaseRegistry.from_config(
                single(backend.url(fork_db[DEFAULT_SHARD]))
            )
            try:
                await AsyncWalletRepository(Wallet, WALLETS, registry, clock=clock).add(a_wallet())
                async with registry.default.connection() as conn:
                    rows = await conn.execute(sql_select(func.count()).select_from(WALLETS))
                    return str(rows.scalar_one())
            finally:
                await registry.aclose()

        assert in_a_child(lambda: asyncio.run(own())) == ["1"]
        assert count(parent) == 1
    finally:
        parent.close()


# ------------------------------------------------------------ closing in the copy is refused

#: The server's own name for a session, per dialect. SQLite has no server.
SESSION_ID = {
    "postgres": "select pg_backend_pid()",
    "mysql": "select connection_id()",
    "mariadb": "select connection_id()",
}


def session(conn: Any, dialect: str) -> object:
    """Which server session ``conn`` is talking to - on SQLite, which connection object it is.
    The same one before and after the child's close() means the child left it alone."""
    sql = SESSION_ID.get(dialect)
    if sql is None:
        return id(conn.connection.dbapi_connection)
    return conn.execute(text(sql)).scalar_one()


async def async_session(conn: Any, dialect: str) -> object:
    sql = SESSION_ID.get(dialect)
    if sql is None:
        raw = await conn.get_raw_connection()
        return id(raw.dbapi_connection)
    return (await conn.execute(text(sql))).scalar_one()


def test_closing_a_sync_registry_in_the_child_is_refused_and_leaves_the_parent_alone(
    backend, fork_db, clock
):
    """The copied pools hold the parent's sockets, and closing them in the child ended the
    parent's sessions - measured on PostgreSQL, MySQL and MariaDB before this was refused. Now
    close() in the copy is refused like every other call there, and touches nothing."""
    registry = _registry(backend, fork_db)
    try:
        wallets = WalletRepository(Wallet, WALLETS, registry, clock=clock)
        known = wallets.add(a_wallet())  # the pool now holds a live connection to copy
        dialect = registry.default.dialect
        with registry.default.connection() as conn:
            before = session(conn, dialect)

        def close_both() -> list[str]:
            said: list[str] = []
            for closing in (registry.close, registry.default.close):
                try:
                    closing()
                except BaseException as error:
                    said.append(f"{type(error).__name__}: {error}")
                else:
                    said.append("closed")
            return said

        said = in_a_child(close_both)
        refused(said)
        assert len(said) == 2
        assert said[0].startswith(f"ForkedProcessError: {registry!r} was created")
        assert said[1].startswith(f"ForkedProcessError: {registry.default!r} was created")
        with registry.default.connection() as conn:
            assert session(conn, dialect) == before, "the child closed the parent's connection"
        assert wallets.find(known.id) == known
    finally:
        registry.close()


def test_closing_an_async_registry_in_the_child_is_refused_and_leaves_the_parent_alone(
    backend, fork_db, clock
):
    """As the sync one. Before it was refused, aiosqlite's close() in the copy never returned:
    it waits on a thread that did not come along."""
    if under_gevent():
        pytest.skip(NOT_UNDER_GEVENT)

    async def scenario() -> list[str]:
        registry = await asyncio.to_thread(_registry, backend, fork_db, kind=AsyncDatabaseRegistry)
        try:
            wallets = AsyncWalletRepository(Wallet, WALLETS, registry, clock=clock)
            known = await wallets.add(a_wallet())
            dialect = registry.default.dialect
            async with registry.default.connection() as conn:
                before = await async_session(conn, dialect)

            async def close_both() -> list[str]:
                said: list[str] = []
                for closing in (registry.aclose, registry.default.aclose):
                    try:
                        await closing()
                    except BaseException as error:
                        said.append(f"{type(error).__name__}: {error}")
                    else:
                        said.append("closed")
                return said

            said = in_a_child(lambda: asyncio.run(close_both()))
            async with registry.default.connection() as conn:
                after = await async_session(conn, dialect)
            assert after == before, "the child closed the parent's connection"
            assert await wallets.find(known.id) == known
            assert said[0].startswith(f"ForkedProcessError: {registry!r} was created")
            assert said[1].startswith(f"ForkedProcessError: {registry.default!r} was created")
            return said
        finally:
            await registry.aclose()

    said = asyncio.run(scenario())
    refused(said)
    assert len(said) == 2


#: The SQL lock providers, per dialect: sync, then async.
def _providers(dialect: str) -> tuple[Any, Any]:
    from ekbatan.aio.locks import (
        AsyncMariaDBKeyedLockProvider,
        AsyncMySQLKeyedLockProvider,
        AsyncPostgresKeyedLockProvider,
    )
    from ekbatan.locks.sql import (
        MariaDBKeyedLockProvider,
        MySQLKeyedLockProvider,
        PostgresKeyedLockProvider,
    )

    return {
        "postgres": (PostgresKeyedLockProvider, AsyncPostgresKeyedLockProvider),
        "mysql": (MySQLKeyedLockProvider, AsyncMySQLKeyedLockProvider),
        "mariadb": (MariaDBKeyedLockProvider, AsyncMariaDBKeyedLockProvider),
    }[dialect]


HOLD = timedelta(seconds=30)


def test_a_sync_lock_provider_built_before_fork_is_refused_in_the_child(backend, fork_db, dialect):
    if dialect == "sqlite":
        pytest.skip("SQLite has no keyed lock provider")
    from ekbatan.persistence import engine_for

    sync_provider, _ = _providers(dialect)
    config = single(backend.url(fork_db[DEFAULT_SHARD])).groups[0].members[0].primary_config()
    engine = engine_for(config)
    try:
        locks = sync_provider(engine)
        with locks.acquire("fork:parent", HOLD):  # a live connection in the pool to copy
            pass

        def child() -> list[str]:
            said: list[str] = []
            for attempt in (
                lambda: locks.acquire("fork:child", HOLD),
                lambda: locks.try_acquire("fork:child", timedelta(0), HOLD),
            ):
                try:
                    attempt()
                except BaseException as error:
                    said.append(f"{type(error).__name__}: {error}")
                else:
                    said.append("let through")
            # One built here works.
            own = engine_for(config)
            try:
                lease = sync_provider(own).try_acquire("fork:child", timedelta(0), HOLD)
                held = lease is not None and lease.held
                if lease is not None:
                    lease.release()
            finally:
                own.dispose()
            return [*said, f"own provider held: {held}"]

        said = in_a_child(child)
        refused(said[:2])
        assert said[0].startswith(f"ForkedProcessError: {sync_provider.__name__} was created")
        assert said[2] == "own provider held: True"
        # The parent's provider still takes and gives back.
        lease = locks.try_acquire("fork:parent", timedelta(0), HOLD)
        assert lease is not None and lease.held
        lease.release()
    finally:
        engine.dispose()


def test_an_async_lock_provider_built_before_fork_is_refused_in_the_child(
    backend, fork_db, dialect
):
    if dialect == "sqlite":
        pytest.skip("SQLite has no keyed lock provider")
    if under_gevent():
        pytest.skip(NOT_UNDER_GEVENT)
    from ekbatan.aio import async_engine_for

    _, async_provider = _providers(dialect)
    config = single(backend.url(fork_db[DEFAULT_SHARD])).groups[0].members[0].primary_config()

    async def scenario() -> list[str]:
        engine = async_engine_for(config)
        try:
            locks = async_provider(engine)
            async with locks.acquire("fork:parent", HOLD):
                pass

            async def child() -> list[str]:
                said: list[str] = []
                for attempt in (
                    lambda: locks.acquire("fork:child", HOLD),
                    lambda: locks.try_acquire("fork:child", timedelta(0), HOLD),
                ):
                    try:
                        await attempt()
                    except BaseException as error:
                        said.append(f"{type(error).__name__}: {error}")
                    else:
                        said.append("let through")
                own = async_engine_for(config)
                try:
                    lease = await async_provider(own).try_acquire("fork:child", timedelta(0), HOLD)
                    held = lease is not None and lease.held
                    if lease is not None:
                        await lease.release()
                finally:
                    await own.dispose()
                return [*said, f"own provider held: {held}"]

            said = in_a_child(lambda: asyncio.run(child()))
            lease = await locks.try_acquire("fork:parent", timedelta(0), HOLD)
            assert lease is not None and lease.held, "the parent's provider still works"
            await lease.release()
            return said
        finally:
            await engine.dispose()

    said = asyncio.run(scenario())
    refused(said[:2])
    assert said[2] == "own provider held: True"


class Dormant(DistributedJob):
    def name(self) -> str:
        return "fork-dormant"

    def schedule(self) -> Any:
        from db_scheduler.task.schedule import FixedDelay

        return FixedDelay.of(timedelta(hours=1))

    def execute(self, ctx: Any) -> None:
        pass


def test_a_job_registry_built_before_fork_is_refused_in_the_child(registry, backend):
    """db-scheduler's own worker pool refuses work in a forked child too (unchanged); this is
    the framework's word for it, at `start()`."""
    from ekbatan.persistence import engine_for

    built = job_registry().engine(registry.default.primary).with_job(Dormant()).build()
    manual = job_registry().engine(registry.default.primary).with_job(Dormant()).build_manual()
    config = single(backend.url("ekbatan_main")).groups[0].members[0].primary_config()

    def child() -> list[str]:
        said: list[str] = []
        for jobs in (built, manual):
            try:
                jobs.start()
            except BaseException as error:
                said.append(f"{type(error).__name__}: {error}")
            else:
                said.append("started")
        # One built here, on an engine built here, starts.
        engine = engine_for(config)
        try:
            own = job_registry().engine(engine).with_job(Dormant()).build_manual()
            own.start()
            own.stop()
            said.append("own: started")
        finally:
            engine.dispose()
        return said

    said = in_a_child(child)
    refused(said[:2])
    assert said[0].startswith(f"ForkedProcessError: {built!r} was created")
    assert said[2] == "own: started"
    manual.start()  # the parent's still starts
    manual.stop()


# ============================================================ refusal 2: on an event loop


def test_every_sync_door_is_refused_inside_a_coroutine(registry, wallets, clock, backend):
    known = wallets.add(a_wallet())

    async def on_the_loop() -> list[str]:
        return each_door(registry, wallets, known)

    said = asyncio.run(on_the_loop())
    assert len(said) == 6
    for line in said:
        assert line.startswith("BlockingCallError: the sync API (TransactionManager("), line
        assert line.endswith(BLOCKING), line
    assert count(registry) == 1, "nothing was written"


def test_the_sync_executor_is_refused_before_the_action_runs(executor, registry, clock):
    performed: list[bool] = []

    class Watched(CreateWalletAction):
        def perform(self, principal: str | None, params: CreateWalletParams) -> Wallet:
            performed.append(True)
            return super().perform(principal, params)

    from fixtures.executing import registered

    ready = registered(executor, Watched(clock))
    params = CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1"))

    async def on_the_loop() -> None:
        ready.execute(Watched, params)

    with pytest.raises(BlockingCallError) as error:
        asyncio.run(on_the_loop())
    assert str(error.value) == f"the sync ActionExecutor.execute {BLOCKING}"
    assert performed == [], "refused before perform()"
    assert count(registry) == 0
    # Off the loop the very same call runs.
    assert ready.execute(Watched, params).balance == Decimal("1")
    assert performed == [True]


def test_a_migration_run_is_refused_inside_a_coroutine(backend):
    layout = single(backend.url("ekbatan_main"))
    scripts = backend.MIGRATIONS / backend.MIGRATION_DIRECTORIES[backend.dialect]

    async def on_the_loop() -> None:
        migrate(layout, scripts)

    with pytest.raises(BlockingCallError) as error:
        asyncio.run(on_the_loop())
    assert str(error.value) == (f"ekbatan.migrations (migrate, info, repair, baseline) {BLOCKING}")


def test_an_async_handler_that_calls_the_sync_api_on_the_handler_loop_is_refused(registry, wallets):
    if under_gevent():
        pytest.skip(NOT_UNDER_GEVENT)
    known = wallets.add(a_wallet())
    loop = HandlerLoop()
    try:

        async def handler_body() -> Any:
            return wallets.find(known.id)  # sync, on the handler loop's own thread

        with pytest.raises(BlockingCallError):
            loop.run(handler_body())

        async def handler_body_done_right() -> Any:
            return await asyncio.to_thread(wallets.find, known.id)

        assert loop.run(handler_body_done_right()) == known
    finally:
        loop.close()


# -- where the sync API is meant to run: silent


def test_silent_on_a_to_thread_thread(registry, wallets):
    async def on_the_loop() -> Wallet:
        return await asyncio.to_thread(wallets.add, a_wallet())

    asyncio.run(on_the_loop())
    assert count(registry) == 1


def test_silent_on_a_run_in_executor_thread(registry, wallets):
    async def on_the_loop() -> Wallet:
        return await asyncio.get_running_loop().run_in_executor(None, wallets.add, a_wallet())

    asyncio.run(on_the_loop())
    assert count(registry) == 1


def test_silent_on_a_plain_thread_while_a_loop_runs_elsewhere(registry, wallets):
    """A loop running on one thread says nothing about another - dispatch's case: its sync
    handlers run on delivery threads while its handler loop runs on its own."""
    loop = HandlerLoop()
    try:
        loop.run(asyncio.sleep(0))  # the loop is running, on its thread
        done: list[Any] = []
        worker = threading.Thread(target=lambda: done.append(wallets.add(a_wallet())))
        worker.start()
        worker.join(20)
        assert len(done) == 1
    finally:
        loop.close()
    assert count(registry) == 1


def test_silent_in_a_fastapi_def_endpoint(registry, wallets):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.post("/wallets")
    def open_wallet() -> dict[str, int]:
        with registry.default.transaction():
            wallets.add(a_wallet())
        return {"count": count(registry)}

    with TestClient(app) as client:
        assert client.post("/wallets").json() == {"count": 1}


def test_a_fastapi_async_def_endpoint_that_calls_the_sync_api_is_refused(registry, wallets):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.post("/wallets")
    async def open_wallet() -> dict[str, int]:
        wallets.add(a_wallet())
        return {"count": 1}

    with TestClient(app, raise_server_exceptions=True) as client, pytest.raises(BlockingCallError):
        client.post("/wallets")
    assert count(registry) == 0


def test_silent_for_a_sync_handler_on_dispatch_s_threads(executor, pipeline, registry):
    """Sync handlers that read through the sync API, run by dispatch on its delivery threads -
    two deliveries in one batch, so dispatch hands them to its pool rather than running one
    inline on the caller's thread."""
    from fixtures.domain import WalletCreatedEvent
    from fixtures.executing import run

    seen: list[tuple[str, int]] = []

    def read() -> None:
        seen.append((threading.current_thread().name, count(registry)))

    fanout, dispatch = pipeline(
        RecordingHandler("reader-a", WalletCreatedEvent, on_call=read),
        RecordingHandler("reader-b", WalletCreatedEvent, on_call=read),
    )
    run(
        executor,
        CreateWalletAction(executor.clock),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1")),
    )
    fanout.drain_once()
    dispatch.drain_once()
    assert [n for _, n in seen] == [1, 1], "both handlers read through the sync API, unrefused"
    main = threading.main_thread().name
    assert all(name != main for name, _ in seen), f"on dispatch's threads: {seen}"


def test_silent_on_the_test_harness_s_calling_thread(registry, wallets):
    """`tests/modes.py` runs the async objects on a portal - a loop on a thread of its own -
    while the test's thread, with no loop of its own, calls the sync API freely. A sync call
    sent to the portal's loop is refused like any other."""
    from modes import Portal

    portal = Portal()
    try:
        assert portal.call(asyncio.sleep(0, "on the loop")) == "on the loop"
        wallets.add(a_wallet())  # this thread: no loop, so no refusal

        async def on_the_portal_loop() -> Any:
            return wallets.add(a_wallet())

        with pytest.raises(BlockingCallError):
            portal.call(on_the_portal_loop())
    finally:
        portal.close()
    assert count(registry) == 1


def test_silent_in_gevent_greenlets(registry, wallets):
    """Under `make test-gevent` every thread is a greenlet and no asyncio loop runs: the sync
    API is the one to use, concurrently, and is never refused."""
    if not under_gevent():
        pytest.skip("meaningful only under gevent's monkey-patching: make test-gevent")
    import gevent

    greenlets = [gevent.spawn(wallets.add, a_wallet()) for _ in range(5)]
    gevent.joinall(greenlets, timeout=30, raise_error=True)
    assert count(registry) == 5


def test_a_redis_lock_provider_built_before_fork_is_refused_in_the_child(redis_client):
    """The copy would also share the parent's client id, so its threads could pass for the
    parent's as one owner."""
    import redis

    from ekbatan.locks.redis import RedisKeyedLockProvider

    where = redis_client.connection_pool.connection_kwargs
    locks = RedisKeyedLockProvider(redis_client)
    with locks.acquire("fork:parent", HOLD):
        pass

    def child() -> list[str]:
        said: list[str] = []
        for attempt in (
            lambda: locks.acquire("fork:child", HOLD),
            lambda: locks.try_acquire("fork:child", timedelta(0), HOLD),
        ):
            try:
                attempt()
            except BaseException as error:
                said.append(f"{type(error).__name__}: {error}")
            else:
                said.append("let through")
        client = redis.Redis(host=where["host"], port=where["port"])
        try:
            lease = RedisKeyedLockProvider(client).try_acquire("fork:child", timedelta(0), HOLD)
            held = lease is not None and lease.held
            if lease is not None:
                lease.release()
        finally:
            client.close()
        return [*said, f"own provider held: {held}"]

    said = in_a_child(child)
    refused(said[:2])
    assert said[0].startswith("ForkedProcessError: RedisKeyedLockProvider was created")
    assert said[2] == "own provider held: True"
    lease = locks.try_acquire("fork:parent", timedelta(0), HOLD)
    assert lease is not None and lease.held, "the parent's provider still works"
    lease.release()


def test_an_async_redis_lock_provider_built_before_fork_is_refused_in_the_child(redis_client):
    if under_gevent():
        pytest.skip(NOT_UNDER_GEVENT)
    import redis.asyncio

    from ekbatan.aio.locks.redis import AsyncRedisKeyedLockProvider

    where = redis_client.connection_pool.connection_kwargs

    async def scenario() -> list[str]:
        client = redis.asyncio.Redis(host=where["host"], port=where["port"])
        try:
            locks = AsyncRedisKeyedLockProvider(client)
            async with locks.acquire("fork:parent", HOLD):
                pass

            async def child() -> list[str]:
                said: list[str] = []
                for attempt in (
                    lambda: locks.acquire("fork:child", HOLD),
                    lambda: locks.try_acquire("fork:child", timedelta(0), HOLD),
                ):
                    try:
                        await attempt()
                    except BaseException as error:
                        said.append(f"{type(error).__name__}: {error}")
                    else:
                        said.append("let through")
                own = redis.asyncio.Redis(host=where["host"], port=where["port"])
                try:
                    lease = await AsyncRedisKeyedLockProvider(own).try_acquire(
                        "fork:child", timedelta(0), HOLD
                    )
                    held = lease is not None and lease.held
                    if lease is not None:
                        await lease.release()
                finally:
                    await own.aclose()
                return [*said, f"own provider held: {held}"]

            said = in_a_child(lambda: asyncio.run(child()))
            lease = await locks.try_acquire("fork:parent", timedelta(0), HOLD)
            assert lease is not None and lease.held, "the parent's provider still works"
            await lease.release()
            return said
        finally:
            await client.aclose()

    said = asyncio.run(scenario())
    refused(said[:2])
    assert said[2] == "own provider held: True"
