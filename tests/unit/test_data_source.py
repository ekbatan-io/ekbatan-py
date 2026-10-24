"""`DataSource` and `AsyncDataSource`: one database's pool, built from its configuration and in no
other way - Java's `ConnectionProvider`, whose one public factory is
`hikariConnectionProvider(config)`.

No server: building a pool contacts nothing, so the hosts below deliberately do not resolve, and
the SQLite pools are in memory. Used or closed in a process `fork()` copied it into, a data source
is refused - proven with real forks, on all four databases, in `tests/integration/test_refusals.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

import ekbatan
import ekbatan.aio
from ekbatan import DEFAULT_SHARD, DataSource, TransactionManager
from ekbatan.aio import AsyncDataSource, AsyncTransactionManager
from ekbatan.aio.locks import AsyncPostgresKeyedLockProvider
from ekbatan.config import DataSourceConfig
from ekbatan.distributedjobs import job_registry
from ekbatan.errors import ValidationError
from ekbatan.locks import PostgresKeyedLockProvider

MARKER = "Mk-5d2e-SECRET"
HOST = "db-5d2e"

#: A login with a real password: what must never be printed, and must still be used.
LOGIN = DataSourceConfig(
    url=f"postgresql+psycopg://{HOST}:5432/wallet", username="app", password=MARKER
)
MEMORY = DataSourceConfig(url="sqlite://")

KINDS = (DataSource, AsyncDataSource)


def close(source: DataSource | AsyncDataSource) -> None:
    if isinstance(source, AsyncDataSource):
        asyncio.run(source.aclose())
    else:
        source.close()


@pytest.fixture
def sync_source() -> Iterator[DataSource]:
    source = DataSource.from_config(LOGIN)
    yield source
    source.close()


@pytest.fixture
def async_source() -> Iterator[AsyncDataSource]:
    source = AsyncDataSource.from_config(LOGIN)
    yield source
    asyncio.run(source.aclose())


# -- how one is built --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_a_data_source_is_built_from_its_config_and_in_no_other_way(kind):
    """Java's constructor is private: `hikariConnectionProvider(config)` is the only way to get
    one, so a pool is always configured from a `DataSourceConfig`."""
    built = kind.from_config(MEMORY)
    try:
        assert type(built) is kind
    finally:
        close(built)

    for attempt in (lambda: kind(), lambda: kind(create_engine("sqlite://")), lambda: kind(MEMORY)):
        with pytest.raises(TypeError) as refused:
            attempt()
        assert str(refused.value) == (
            f"build a {kind.__name__} with {kind.__name__}.from_config(config)"
        )


def test_the_names_it_replaces_are_gone():
    """Renamed outright: there was no release with the old names, so no alias was kept."""
    assert not hasattr(ekbatan, "engine_for")
    assert not hasattr(ekbatan.aio, "async_engine_for")
    assert "DataSource" in ekbatan.__all__ and "AsyncDataSource" in ekbatan.aio.__all__


# -- what it says about itself -----------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_it_never_prints_its_password_and_still_connects_with_it(kind):
    source = kind.from_config(LOGIN)
    try:
        shown = (
            repr(source),
            str(source),
            f"{source}",
            repr(source.url),
            str(source.url),
            repr(source.sqlalchemy_engine),
        )
        for text in shown:
            assert MARKER not in text, text
        assert repr(source) == (
            f"{kind.__name__}({source.url.drivername}://app:***@{HOST}:5432/wallet)"
        )
        assert source.url.password == MARKER, "hidden when printed, not when used"
    finally:
        close(source)


@pytest.mark.parametrize(
    ("url", "named"),
    [
        ("postgresql+psycopg://h-5d2e/db", "postgres"),
        ("mysql+pymysql://h-5d2e/db", "mysql"),
        ("mariadb+pymysql://h-5d2e/db", "mariadb"),
        ("sqlite://", "sqlite"),
    ],
)
@pytest.mark.parametrize("kind", KINDS)
def test_it_names_its_database_as_ekbatan_names_it(kind, url, named):
    source = kind.from_config(DataSourceConfig(url=url))
    try:
        assert source.dialect == named
    finally:
        close(source)


@pytest.mark.parametrize(
    ("kind", "manager"),
    [(DataSource, TransactionManager), (AsyncDataSource, AsyncTransactionManager)],
)
def test_a_transaction_manager_speaks_its_data_sources_dialect(kind, manager):
    """`postgres`, as the layout says it - not SQLAlchemy's `postgresql` - whether the manager
    was built by the registry or by hand."""
    source = kind.from_config(DataSourceConfig(url="postgresql+psycopg://h-5d2e/db"))
    try:
        assert manager(DEFAULT_SHARD, source).dialect == "postgres"
    finally:
        close(source)


# -- closing -----------------------------------------------------------------------------------


def test_closing_twice_is_harmless_and_a_with_block_closes_it():
    with DataSource.from_config(MEMORY) as source:
        before = source.sqlalchemy_engine.pool
    assert source.sqlalchemy_engine.pool is not before, "leaving the block closed the pool"
    source.close()
    source.close()


def test_an_async_one_closes_with_aclose_and_async_with():
    async def scenario() -> None:
        async with AsyncDataSource.from_config(MEMORY) as source:
            before = source.sqlalchemy_engine.sync_engine.pool
        assert source.sqlalchemy_engine.sync_engine.pool is not before, "the block closed it"
        await source.aclose()
        await source.aclose()

    asyncio.run(scenario())


def test_a_transaction_manager_closes_the_data_sources_it_was_given():
    """As Java's `close()` closes both of its providers. A lock provider closes nothing."""
    primary, replica = DataSource.from_config(MEMORY), DataSource.from_config(MEMORY)
    pools = primary.sqlalchemy_engine.pool, replica.sqlalchemy_engine.pool

    TransactionManager(DEFAULT_SHARD, primary, replica).close()

    assert primary.sqlalchemy_engine.pool is not pools[0]
    assert replica.sqlalchemy_engine.pool is not pools[1]

    locked = DataSource.from_config(LOGIN)
    try:
        before = locked.sqlalchemy_engine.pool
        PostgresKeyedLockProvider(locked).close()
        assert locked.sqlalchemy_engine.pool is before, "the lock provider closed its pool"
    finally:
        locked.close()


# -- what takes one takes nothing else ---------------------------------------------------------

#: Each place a sync data source goes in: who says so, and how it is handed over (``own`` is a
#: data source of the right kind, for the replica's place). A replica of ``None`` means none.
SYNC_TAKERS: list[tuple[str, Callable[[Any, Any], object], bool]] = [
    ("TransactionManager", lambda given, _: TransactionManager(DEFAULT_SHARD, given), True),
    (
        "TransactionManager",
        lambda given, own: TransactionManager(DEFAULT_SHARD, own, given),
        False,
    ),
    ("PostgresKeyedLockProvider", lambda given, _: PostgresKeyedLockProvider(given), True),
    (
        "PostgresKeyedLockProvider",
        lambda given, _: PostgresKeyedLockProvider.builder().data_source(given),
        True,
    ),
    ("JobRegistry", lambda given, _: job_registry().data_source(given), True),
]

ASYNC_TAKERS: list[tuple[str, Callable[[Any, Any], object], bool]] = [
    (
        "AsyncTransactionManager",
        lambda given, _: AsyncTransactionManager(DEFAULT_SHARD, given),
        True,
    ),
    (
        "AsyncTransactionManager",
        lambda given, own: AsyncTransactionManager(DEFAULT_SHARD, own, given),
        False,
    ),
    (
        "AsyncPostgresKeyedLockProvider",
        lambda given, _: AsyncPostgresKeyedLockProvider(given),
        True,
    ),
    (
        "AsyncPostgresKeyedLockProvider",
        lambda given, _: AsyncPostgresKeyedLockProvider.builder().data_source(given),
        True,
    ),
]


def refusals(take: Callable[[Any, Any], object], wrong: dict[str, Any], own: Any) -> dict[str, str]:
    """What ``take`` said to each wrong thing, by the name of its type."""
    said: dict[str, str] = {}
    for name, given in wrong.items():
        with pytest.raises(ValidationError) as refused:
            take(given, own)
        said[name] = str(refused.value)
    return said


@pytest.fixture
def engines(async_source: AsyncDataSource) -> Iterator[dict[str, Any]]:
    """SQLAlchemy's own engines on the same database: what an application might pass instead."""
    engine = create_engine(LOGIN.sqlalchemy_url())
    async_engine = create_async_engine(async_source.url)
    yield {"Engine": engine, "AsyncEngine": async_engine}
    engine.dispose()
    asyncio.run(async_engine.dispose())


@pytest.mark.parametrize(("taker", "take", "none_is_wrong"), SYNC_TAKERS)
def test_what_takes_a_data_source_refuses_an_engine_and_an_async_one(
    taker, take, none_is_wrong, sync_source, async_source, engines
):
    wrong = {**engines, "AsyncDataSource": async_source}
    if none_is_wrong:
        wrong["NoneType"] = None

    for given, message in refusals(take, wrong, sync_source).items():
        assert message == (
            f"{taker} takes a DataSource, and was given {given}. "
            f"Build one with DataSource.from_config(config)."
        )
        assert MARKER not in message and HOST not in message


@pytest.mark.parametrize(("taker", "take", "none_is_wrong"), ASYNC_TAKERS)
def test_what_takes_an_async_data_source_refuses_an_engine_and_a_sync_one(
    taker, take, none_is_wrong, sync_source, async_source, engines
):
    wrong = {**engines, "DataSource": sync_source}
    if none_is_wrong:
        wrong["NoneType"] = None

    for given, message in refusals(take, wrong, async_source).items():
        assert message == (
            f"{taker} takes an AsyncDataSource, and was given {given}. "
            f"Build one with AsyncDataSource.from_config(config)."
        )
        assert MARKER not in message and HOST not in message
