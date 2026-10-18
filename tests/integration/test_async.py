"""What only `ekbatan.aio` does, tested natively, as coroutines on each database's async driver.

Everything the sync API also does runs both ways through `tests/modes.py`. What is here has no
sync counterpart, or behaves differently on purpose:

- **the pool is the cap** on requests waiting on a database: no semaphore of the framework's
  own, the same `pool_size` / `max_overflow` / `pool_timeout` as a sync pool, and a request that
  waits past `pool_timeout` fails fast;
- **a read across shards asks them all at once**, with the sync read's answer and order; one
  failing shard fails the whole read, cancels the others and is named;
- **a timeout** around a unit of work behaves as a cancellation: nothing is committed and the
  connection goes back clean;
- **each session is put on UTC** by the documented `data_source_properties`, on every async
  driver - asyncpg included, which has no libpq `options` of its own.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy.exc
from sqlalchemy import create_engine, event, func, select, text

from conftest import GLOBAL, MEXICO, _registry
from ekbatan.aio import AsyncActionExecutor, AsyncDatabaseRegistry, AsyncDb
from ekbatan.ids import ShardedId
from ekbatan.sharding import EmbeddedBitsShardingStrategy
from fixtures.actions import (
    AsyncCreateWalletAction,
    AsyncDepositMoneyAction,
    CreateWalletParams,
    DepositParams,
    TwinAction,
)
from fixtures.domain import NOW, Wallet
from fixtures.logins import data_source, single
from fixtures.repositories import AsyncWalletRepository, WalletRepository
from fixtures.schema import EVENTS, WALLETS, metadata


def a_wallet(shard: Any = GLOBAL, balance: str = "10") -> Wallet:
    return Wallet.open(
        id=ShardedId[Wallet].generate(shard),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal(balance),
        created_date=NOW,
    )


def checked_out(registry: AsyncDatabaseRegistry) -> int:
    return sum(registry.manager(s).primary.sync_engine.pool.checkedout() for s in registry.shards)


def one_database(backend: Any, name: str, **settings: Any) -> AsyncDatabaseRegistry:
    """An async registry over one freshly emptied database, with these pool settings."""
    backend.prepare(name, metadata)
    backend.truncate(name, metadata)
    return AsyncDatabaseRegistry.from_config(single(backend.url(name), **settings))


def executor_over(registry: AsyncDatabaseRegistry, wallets: Any, clock: Any) -> Any:
    return AsyncActionExecutor(
        namespace="example.wallet",
        registry=registry,
        repositories=[wallets],
        actions=[AsyncCreateWalletAction(clock), AsyncDepositMoneyAction(wallets, clock)],
        events=EVENTS,
        clock=clock,
    )


def open_params(balance: str = "0") -> CreateWalletParams:
    return CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal(balance))


# ------------------------------------------------------------------- the pool is the cap


def test_a_request_waiting_past_pool_timeout_fails_fast(backend, dialect, clock):
    """Two connections, both held: the third request waits `pool_timeout` and fails with
    SQLAlchemy's TimeoutError - it does not queue for ever, and it repeats no password."""
    if dialect == "sqlite":
        pytest.skip("SQLite has no connection pool to size: pool settings are for the servers")
    password = data_source(backend.url("ekbatan_async_pool")).password

    async def scenario() -> None:
        registry = one_database(
            backend, "ekbatan_async_pool", pool_size=2, max_overflow=0, pool_timeout=0.5
        )
        try:
            wallets = AsyncWalletRepository(Wallet, WALLETS, registry, clock=clock)
            executor = executor_over(registry, wallets, clock)
            manager = registry.default
            holding, release = asyncio.Event(), asyncio.Event()
            held = 0

            async def hold() -> None:
                nonlocal held
                async with manager.transaction():
                    held += 1
                    if held == 2:
                        holding.set()
                    await release.wait()

            holders = [asyncio.create_task(hold()) for _ in range(2)]
            await asyncio.wait_for(holding.wait(), 10)
            assert checked_out(registry) == 2

            started = time.monotonic()
            with pytest.raises(sqlalchemy.exc.TimeoutError) as refused:
                await executor.execute(AsyncCreateWalletAction, open_params())
            waited = time.monotonic() - started

            assert 0.4 <= waited < 3, f"failed after {waited:.2f}s, not after pool_timeout"
            message = str(refused.value)
            assert "limit of size 2 overflow 0 reached" in message
            assert password is None or password not in message

            release.set()
            await asyncio.gather(*holders)
            # The pool recovers: the next request gets a connection.
            await executor.execute(AsyncCreateWalletAction, open_params())
            assert checked_out(registry) == 0
            assert await wallets.count() == 1, "only the request that got a connection wrote"
        finally:
            await registry.aclose()

    asyncio.run(scenario())


def test_requests_beyond_the_pool_wait_their_turn_and_all_commit(backend, dialect, clock):
    """Twenty units of work at once on a pool of two: no more than two connections are ever
    out, and every one commits - the pool queues them, with no semaphore of the framework's."""
    if dialect == "sqlite":
        pytest.skip("SQLite has no connection pool to size: pool settings are for the servers")

    async def scenario() -> None:
        registry = one_database(
            backend, "ekbatan_async_queue", pool_size=2, max_overflow=0, pool_timeout=30
        )
        out = most = 0

        def checkout(*_: Any) -> None:
            nonlocal out, most
            out += 1
            most = max(most, out)

        def checkin(*_: Any) -> None:
            nonlocal out
            out -= 1

        pool = registry.default.primary.sync_engine.pool
        event.listen(pool, "checkout", checkout)
        event.listen(pool, "checkin", checkin)
        try:
            wallets = AsyncWalletRepository(Wallet, WALLETS, registry, clock=clock)
            executor = executor_over(registry, wallets, clock)
            made = await asyncio.gather(
                *(executor.execute(AsyncCreateWalletAction, open_params(str(i))) for i in range(20))
            )
            assert len({w.id for w in made}) == 20
            assert await wallets.count() == 20
            assert most == 2, f"{most} connections were out at once on a pool of two"
        finally:
            event.remove(pool, "checkout", checkout)
            event.remove(pool, "checkin", checkin)
            await registry.aclose()

    asyncio.run(scenario())


# ------------------------------------------------------------- reading every shard at once


@contextmanager
def slow_reads(delays: dict[str, float], seen: dict[str, Any]) -> Iterator[None]:
    """Make each SELECT on a database wait first - by database name - and record how many ran
    at once, and which were cancelled."""
    real = AsyncDb.execute
    running = 0
    seen.update(most=0, cancelled=set())

    async def execute(self: AsyncDb, statement: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal running
        database = str(self.sync_engine.url.database).rsplit("/", 1)[-1].removesuffix(".db")
        if str(statement).lstrip().upper().startswith("SELECT"):
            running += 1
            seen["most"] = max(seen["most"], running)
            try:
                await asyncio.sleep(delays.get(database, 0))
            except asyncio.CancelledError:
                seen["cancelled"].add(database)
                raise
            finally:
                running -= 1
        return await real(self, statement, *args, **kwargs)

    AsyncDb.execute = execute  # type: ignore[method-assign]
    try:
        yield
    finally:
        AsyncDb.execute = real  # type: ignore[method-assign]


@asynccontextmanager
async def two_shards(backend: Any, clock: Any, prefix: str) -> AsyncIterator[Any]:
    names = {GLOBAL: f"{prefix}_global", MEXICO: f"{prefix}_mexico"}
    registry = _registry(backend, names, kind=AsyncDatabaseRegistry)
    try:
        yield (
            registry,
            names,
            AsyncWalletRepository(
                Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
            ),
        )
    finally:
        await registry.aclose()


def test_a_read_across_shards_asks_them_at_once_and_keeps_the_sync_order(backend, clock):
    """Each shard's SELECT waits 0.3 s, the first shard longer: together they take about one
    wait, both are running at once, and the rows still come back first shard first."""

    async def scenario() -> None:
        async with two_shards(backend, clock, "ekbatan_async_scatter") as (_, names, wallets):
            here = await wallets.add(a_wallet(GLOBAL))
            there = await wallets.add(a_wallet(MEXICO))
            seen: dict[str, Any] = {}
            with slow_reads({names[GLOBAL]: 0.45, names[MEXICO]: 0.3}, seen):
                started = time.monotonic()
                found = await wallets.find_where()
                took = time.monotonic() - started
                counted = await wallets.count()
                anywhere = await wallets.exists_where(WALLETS.c.id == there.id.value)
                one = await wallets.find_one_where(WALLETS.c.id == there.id.value)
                many = await wallets.find_many([there.id, here.id])

        assert seen["most"] == 2, "the two shards were read at the same time"
        assert took < 0.7, f"{took:.2f}s - one after another would be 0.75s or more"
        assert [w.id for w in found] == [here.id, there.id], "first shard first, as in sync"
        assert counted == 2
        assert anywhere is True
        assert one is not None and one.id == there.id
        assert {w.id for w in many} == {here.id, there.id}

    asyncio.run(scenario())


def test_one_failing_shard_fails_the_read_names_the_shard_and_cancels_the_rest(backend, clock):
    """The other shard is slow; the failing one fails at once. The read raises the failing
    shard's own error - the one the sync read raises - with a note naming the shard, the slow
    read is cancelled rather than waited for, and both connections go back to their pools."""

    async def scenario() -> tuple[BaseException, dict[str, Any], float]:
        async with two_shards(backend, clock, "ekbatan_async_broken") as (registry, names, w):
            await w.add(a_wallet(GLOBAL))
            # A real failure from the database: the table is gone from the second shard.
            engine = create_engine(backend.url(names[MEXICO]))
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE wallets"))
            engine.dispose()

            seen: dict[str, Any] = {}
            with slow_reads({names[GLOBAL]: 5}, seen):
                started = time.monotonic()
                with pytest.raises(sqlalchemy.exc.DBAPIError) as failed:
                    await w.find_where()
                took = time.monotonic() - started

            assert checked_out(registry) == 0, "both connections went back"
            assert len(await w.find_where(shard=GLOBAL)) == 1, "and the first shard still reads"
            return failed.value, seen, took

    error, seen, took = asyncio.run(scenario())

    assert took < 2, f"{took:.2f}s: the slow shard's read was waited for, not cancelled"
    assert seen["cancelled"] == {"ekbatan_async_broken_global"}
    notes = "\n".join(getattr(error, "__notes__", []))
    assert "reading shard 1:0 (ekbatan_async_broken_mexico) failed" in notes
    assert "cancelled" in notes
    assert not isinstance(error, BaseExceptionGroup), "the shard's own error, not a group"

    # The same kind of error as the sync read raises over the same two databases.
    sync_registry = _broken_sync_registry(backend)
    try:
        sync = WalletRepository(
            Wallet, WALLETS, sync_registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
        )
        with pytest.raises(type(error)):
            sync.find_where()
    finally:
        sync_registry.close()


def _broken_sync_registry(backend: Any) -> Any:
    """A sync registry over the two databases as they are - the shared helper would migrate the
    dropped table back."""
    from ekbatan.config import ShardGroupConfig, ShardingConfig, ShardMemberConfig
    from ekbatan.persistence import DatabaseRegistry

    groups = [
        ShardGroupConfig.builder()
        .group(shard.group)
        .name(f"group-{shard.group}")
        .with_member(
            ShardMemberConfig.builder()
            .member(shard.member)
            .name(f"ekbatan_async_broken_{part}")
            .primary_config(data_source(backend.url(f"ekbatan_async_broken_{part}")))
            .build()
        )
        .build()
        for shard, part in ((GLOBAL, "global"), (MEXICO, "mexico"))
    ]
    return DatabaseRegistry.from_config(
        ShardingConfig.builder().default_shard(GLOBAL).groups(groups).build()
    )


def test_dbs_is_one_shard_at_a_time_and_opens_only_what_is_asked_for(backend, clock):
    """The scatter accessors hand connections to the caller's own code, so they stay as in
    sync: lazy and one after another. Stopping at the first opens nothing on the second."""

    async def scenario() -> None:
        async with two_shards(backend, clock, "ekbatan_async_dbs") as (registry, _, wallets):
            opened: list[Any] = []
            for shard in registry.shards:
                event.listen(
                    registry.manager(shard).primary.sync_engine,
                    "connect",
                    lambda *_, shard=shard: opened.append(shard),
                )
            async for shard, conn in wallets.dbs():
                assert shard == GLOBAL
                assert isinstance(conn, AsyncDb)
                break
            assert opened == [GLOBAL]
            assert [shard async for shard, _ in wallets.readonly_dbs()] == [GLOBAL, MEXICO]

    asyncio.run(scenario())


# ------------------------------------------------------------------------------- timeouts


class SlowDepositAction(TwinAction[DepositParams, Wallet]):
    def __init__(self, wallets: Any, clock: Any) -> None:
        super().__init__(clock)
        self.wallets = wallets

    async def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        wallet = await self.wallets.get(params.wallet_id)
        await asyncio.sleep(5)
        return self.plan.update(wallet.deposit(params.amount))


class SlowOutbox:
    """Holds the transaction open after the rows are written - so a timeout lands inside it."""

    async def persist_action_events(self, *args: Any) -> None:
        await asyncio.sleep(5)


@pytest.mark.parametrize("where", ["in perform", "inside the transaction"])
def test_a_timeout_around_a_unit_of_work_commits_nothing(backend, clock, where):
    async def scenario() -> None:
        registry = one_database(backend, "ekbatan_async_timeout")
        try:
            wallets = AsyncWalletRepository(Wallet, WALLETS, registry, clock=clock)
            stored = await wallets.add(a_wallet())
            executor = AsyncActionExecutor(
                namespace="example.wallet",
                registry=registry,
                repositories=[wallets],
                actions=[
                    SlowDepositAction(wallets, clock),
                    AsyncDepositMoneyAction(wallets, clock),
                ],
                outbox=SlowOutbox() if where == "inside the transaction" else None,
                events=EVENTS,
                clock=clock,
            )
            action = SlowDepositAction if where == "in perform" else AsyncDepositMoneyAction
            params = DepositParams(wallet_id=stored.id, amount=Decimal("5"), recipient="ada")

            started = time.monotonic()
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.3):
                    await executor.execute(action, params)
            assert time.monotonic() - started < 2

            assert registry.default.current() is None, "nothing is left ambient"
            assert checked_out(registry) == 0, "the connection went back"
            assert (await wallets.get(stored.id)).balance == Decimal("10.0000"), "not committed"
            async with registry.default.connection() as conn:
                events = await conn.execute(select(func.count()).select_from(EVENTS))
                assert events.scalar_one() == 0
        finally:
            await registry.aclose()

    asyncio.run(scenario())


# --------------------------------------------------------------------------- UTC sessions


ZONE_QUERY = {
    "postgres": "SELECT current_setting('TimeZone')",
    "mysql": "SELECT @@session.time_zone",
    "mariadb": "SELECT @@session.time_zone",
}

UTC_RECIPE = {
    "postgres": {"options": "-c TimeZone=UTC"},
    "mysql": {"init_command": "SET time_zone = '+00:00'"},
    "mariadb": {"init_command": "SET time_zone = '+00:00'"},
}


@contextmanager
def server_off_utc(backend: Any, name: str) -> Iterator[str]:
    """The database's own default zone moved off UTC - Tehran, as the docs measured - so a
    session that reports UTC got there by the recipe, not by the server's default."""
    url = backend.url(name)
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            if backend.dialect == "postgres":
                conn.execute(text(f"ALTER DATABASE {name} SET TimeZone = 'Asia/Tehran'"))
                yield "Asia/Tehran"
                conn.execute(text(f"ALTER DATABASE {name} RESET TimeZone"))
            else:
                previous = conn.execute(text("SELECT @@global.time_zone")).scalar_one()
                conn.execute(text("SET GLOBAL time_zone = '+03:30'"))
                try:
                    yield "+03:30"
                finally:
                    conn.execute(text(f"SET GLOBAL time_zone = '{previous}'"))
    finally:
        engine.dispose()


def session_zone(backend: Any, name: str, properties: dict[str, Any]) -> str:
    async def ask() -> str:
        registry = AsyncDatabaseRegistry.from_config(
            single(backend.url(name), data_source_properties=properties)
        )
        try:
            async with registry.default.connection() as conn:
                return str((await conn.execute(text(ZONE_QUERY[backend.dialect]))).scalar_one())
        finally:
            await registry.aclose()

    return asyncio.run(ask())


def test_the_documented_utc_setup_puts_each_async_session_on_utc(backend, dialect):
    """`options: -c TimeZone=UTC` on asyncpg too - which has no libpq `options` and takes it as
    `server_settings` - and `init_command` on aiomysql. Measured against a database whose own
    default is Tehran, with the control beside it."""
    if dialect == "sqlite":
        pytest.skip("SQLite has no session time zone")
    name = "ekbatan_async_utc"
    with server_off_utc(backend, name) as tehran:
        assert session_zone(backend, name, {}) == tehran, "the control: off UTC without it"
        assert session_zone(backend, name, UTC_RECIPE[dialect]) in ("UTC", "+00:00")
        if dialect == "postgres":
            # asyncpg's own spelling works as well, for a layout only async code reads.
            native = {"server_settings": {"TimeZone": "UTC"}}
            assert session_zone(backend, name, native) == "UTC"
