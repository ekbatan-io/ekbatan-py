"""Async connections, transactions, and the map from a shard to its database.

The counterpart to :mod:`ekbatan.persistence`, on SQLAlchemy's public asyncio API
(``create_async_engine``, ``AsyncConnection``). The rules are not repeated here - they are the
sync module's, imported:

- the ambient transaction lives in **the same** :class:`~contextvars.ContextVar`, keyed by the
  manager, so a repository call several awaits down joins it without being handed anything. A
  ``ContextVar`` is per task, so two concurrent requests never see each other's transaction;
- which shards exist, which is the default and where an unknown one goes are answered by the
  same registry code;
- an engine is configured from a :class:`~ekbatan.config.DataSourceConfig` with the same pool
  settings and JSON serialiser as a sync one.

What is here is only what has to await: opening a connection, beginning, committing and rolling
back, and disposing of a pool.
"""

from __future__ import annotations

import shlex
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Self

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from ..config import DataSourceConfig, ShardMemberConfig
from ..errors import ValidationError
from ..fetching import FetchResult
from ..persistence import (
    _ambient,
    _engine_kwargs,
    _join,
    _log_committed,
    _log_evicting,
    _log_rolled_back,
    _log_started,
    _open,
    _ShardRegistry,
    _ShardRegistryBuilder,
    _transaction_span,
)
from ..sharding import ShardIdentifier

if TYPE_CHECKING:
    from sqlalchemy import Executable
    from sqlalchemy.engine import URL

__all__ = [
    "ASYNC_DEFAULT_DRIVERS",
    "AsyncDatabaseRegistry",
    "AsyncDatabaseRegistryBuilder",
    "AsyncDb",
    "AsyncTransaction",
    "AsyncTransactionManager",
    "async_engine_for",
    "async_url",
]


class AsyncDb(AsyncConnection):
    """A SQLAlchemy ``AsyncConnection``, with :meth:`rows` added - the async
    :class:`~ekbatan.fetching.Db`.

    Every connection ``ekbatan.aio`` hands out is one of these. Nothing about the connection
    changes: everything SQLAlchemy documents for ``AsyncConnection`` is here and means what its
    documentation says. Built through the constructor, as ``AsyncEngine.connect()`` builds it.
    """

    __slots__ = ()

    async def __aenter__(self) -> Self:
        """The base class promises an ``AsyncConnection``; this promises the subclass."""
        await self.start(is_ctxmanager=True)
        return self

    async def execution_options(self, **opt: Any) -> Self:
        """As SQLAlchemy's, which already returns ``self`` - this only says so in the type."""
        await super().execution_options(**opt)
        return self

    async def rows(self, statement: Executable) -> FetchResult:
        """Run the statement and hand back its rows, for mapping.

        The rows are already in hand when this returns - ``AsyncConnection.execute`` buffers
        them - so the terminals on :class:`~ekbatan.fetching.FetchResult` are the sync ones and
        wait on nothing.
        """
        return FetchResult(await self.execute(statement))


@dataclass(frozen=True, slots=True)
class AsyncTransaction:
    """One open async transaction: what to run statements on, and the shard they land on.

    The counterpart to :class:`~ekbatan.persistence.Transaction`.
    """

    connection: AsyncDb
    shard: ShardIdentifier
    dialect: str

    async def execute(self, statement: Any, parameters: Any = None, **kwargs: Any) -> Any:
        """Run one statement on this transaction."""
        return await self.connection.execute(statement, parameters, **kwargs)

    async def rows(self, statement: Any) -> FetchResult:
        """Run one statement and hand back its rows, for mapping."""
        return await self.connection.rows(statement)

    def __repr__(self) -> str:
        return f"AsyncTransaction(shard={self.shard}, {self.dialect})"


class AsyncTransactionManager:
    """One shard's async databases, and the transaction boundary around them.

    The counterpart to :class:`~ekbatan.persistence.TransactionManager`, and the same rules:
    every :meth:`transaction` is a new one, even inside another; the replica falls back to the
    primary's *engine*; a read inside a transaction joins it.
    """

    __slots__ = ("_owns_replica", "dialect", "label", "primary", "replica", "shard")

    def __init__(
        self,
        shard: ShardIdentifier,
        primary: AsyncEngine,
        replica: AsyncEngine | None = None,
        *,
        dialect: str = "",
        label: str = "",
    ) -> None:
        self.shard = shard
        self.primary = primary
        self.replica = replica if replica is not None else primary
        self._owns_replica = replica is not None
        self.dialect = dialect or primary.dialect.name
        self.label = label or str(shard)

    @property
    def shares_one_pool(self) -> bool:
        return not self._owns_replica

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncTransaction]:
        """Open a transaction on this shard, committed or rolled back when the block ends.

        ``async with manager.transaction() as tx:`` - see the sync
        :meth:`~ekbatan.persistence.TransactionManager.transaction` for why it begins, commits
        and rolls back by hand, and why a failed rollback evicts the connection.

        A cancelled task is a failure like any other: ``CancelledError`` is a
        ``BaseException``, the transaction is rolled back, and the cancellation carries on.
        """
        with _transaction_span(self.shard):
            async with AsyncDb(self.primary) as conn:
                token = _join(self, conn)
                tx = await conn.begin()
                try:
                    _log_started(self.shard)
                    yield AsyncTransaction(conn, self.shard, self.dialect)
                    await tx.commit()
                    _log_committed(self.shard)
                except BaseException as error:
                    try:
                        await tx.rollback()
                    except BaseException:
                        _log_evicting(self.shard)
                        await conn.invalidate()
                    _log_rolled_back(self.shard, error)
                    raise
                finally:
                    _ambient.reset(token)

    def current(self) -> AsyncDb | None:
        """The transaction this manager has open in this task right now, or ``None``."""
        current: AsyncDb | None = _open().get(self)
        return current

    @asynccontextmanager
    async def connection(self, *, readonly: bool = False) -> AsyncIterator[AsyncDb]:
        """The ambient transaction if there is one, else a connection that commits each
        statement as it runs - the sync :meth:`~ekbatan.persistence.TransactionManager.connection`,
        awaited."""
        existing = self.current()
        if existing is not None:
            yield existing
            return

        engine = self.replica if readonly else self.primary
        async with AsyncDb(engine) as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            yield conn

    async def aclose(self) -> None:
        await self.primary.dispose()
        if self._owns_replica:
            await self.replica.dispose()

    def __repr__(self) -> str:
        return f"AsyncTransactionManager({self.label}, {self.dialect})"


class AsyncDatabaseRegistry(_ShardRegistry[AsyncTransactionManager]):
    """Every async database the framework can reach, keyed by shard.

    The counterpart to :class:`~ekbatan.persistence.DatabaseRegistry`; every rule about shards
    is the same code. Close it with ``await registry.aclose()`` or ``async with``.
    """

    __slots__ = ()

    @classmethod
    def builder(cls) -> AsyncDatabaseRegistryBuilder:
        return AsyncDatabaseRegistryBuilder()

    @staticmethod
    def _manager(
        shard: ShardIdentifier, member: ShardMemberConfig, dialect: str
    ) -> AsyncTransactionManager:
        primary = async_engine_for(member.primary_config())
        secondary = member.secondary_config()
        replica = async_engine_for(secondary) if secondary is not None else None
        return AsyncTransactionManager(
            shard, primary, replica, dialect=dialect, label=member.name or str(shard)
        )

    async def aclose(self) -> None:
        """Dispose every pool. One failing shard does not strand the rest."""
        first: BaseException | None = None
        for shard, manager in self._managers.items():
            try:
                await manager.aclose()
            except Exception as exc:
                self._close_failed(shard, exc)
                first = first or exc
        if first is not None:
            raise first

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


class AsyncDatabaseRegistryBuilder(
    _ShardRegistryBuilder[AsyncTransactionManager, AsyncDatabaseRegistry]
):
    """Fluent builder for :class:`AsyncDatabaseRegistry` - the sync
    :class:`~ekbatan.persistence.DatabaseRegistryBuilder`'s code, building the async registry.
    Obtain via :meth:`AsyncDatabaseRegistry.builder`."""

    __slots__ = ()
    _builds = AsyncDatabaseRegistry


#: The driver ``ekbatan.aio`` connects with when the url names no async driver - each
#: database's default async driver, as psycopg, PyMySQL and sqlite3 are the sync defaults.
ASYNC_DEFAULT_DRIVERS: Mapping[str, str] = MappingProxyType(
    {"postgres": "asyncpg", "mysql": "aiomysql", "mariadb": "aiomysql", "sqlite": "aiosqlite"}
)

#: Sync drivers a url may name. A url naming one of these - or no driver at all - is read by
#: ``ekbatan.aio`` as naming the database only, and connects through the async default. Any
#: other driver is taken as written, so an async driver chosen on purpose is kept.
_SYNC_DRIVERS = frozenset(
    {"", "psycopg", "psycopg2", "pg8000", "pymysql", "mysqldb", "mysqlconnector", "pysqlite"}
)


def async_url(config: DataSourceConfig) -> URL:
    """The url ``ekbatan.aio`` connects to for ``config``: its own, with the login set, and the
    driver swapped for the database's async default when it names a sync driver or none.

    So one layout serves a service's async request path and its sync event handlers and jobs:
    ``postgresql+psycopg://`` is psycopg for the sync registry and asyncpg here,
    ``mysql+pymysql://`` is aiomysql here, ``sqlite:///`` is aiosqlite. A url that names an async
    driver - ``postgresql+asyncpg://``, or ``postgresql+psycopg_async://`` for psycopg's own
    async mode - is used as written.
    """
    url = config.sqlalchemy_url()
    backend, _, driver = url.drivername.partition("+")
    if driver.lower() in _SYNC_DRIVERS:
        url = url.set(drivername=f"{backend}+{ASYNC_DEFAULT_DRIVERS[config.dialect]}")
    return url


def async_engine_for(config: DataSourceConfig) -> AsyncEngine:
    """An async pool for one :class:`~ekbatan.config.DataSourceConfig` - the async
    :func:`~ekbatan.persistence.engine_for`, built with the same settings.

    The same ``pool_size``, ``max_overflow``, ``pool_timeout``, ``pool_recycle`` and pre-ping
    as a sync pool: the pool is what caps the requests waiting on this database, and a request
    that waits longer than ``pool_timeout`` for a connection fails with SQLAlchemy's
    ``TimeoutError`` rather than queueing for ever.

    The driver is :func:`async_url`'s. ``data_source_properties`` reach it as given, with one
    translation: asyncpg has no libpq ``options``, so ``-c name=value`` settings there become
    asyncpg's ``server_settings`` - which is what keeps the documented UTC setup,
    ``options: -c TimeZone=UTC``, working on asyncpg.
    """
    url = async_url(config)
    kwargs = _engine_kwargs(config)
    if url.get_driver_name() == "asyncpg":
        kwargs["connect_args"] = _asyncpg_connect_args(kwargs["connect_args"])
    return create_async_engine(url, **kwargs)


def _asyncpg_connect_args(given: Mapping[str, Any]) -> dict[str, Any]:
    """``data_source_properties`` for asyncpg: libpq's ``options`` read into ``server_settings``.

    Only ``-c name=value`` (and ``--name=value``) is understood - it is the only thing
    ``options`` carries that asyncpg has a place for. Anything else in it is refused by what it
    is, never by repeating it: every ``data_source_properties`` value is treated as a secret.
    """
    args = dict(given)
    options = args.pop("options", None)
    if options is None:
        return args
    if not isinstance(options, str):
        raise ValidationError(
            "data_source_properties 'options' must be text for asyncpg: libpq's "
            "'-c name=value' settings"
        )
    settings: dict[str, str] = {}
    try:
        words = shlex.split(options)
    except ValueError:
        raise ValidationError(
            "data_source_properties 'options' has an unclosed quote or a trailing backslash"
        ) from None
    while words:
        word = words.pop(0)
        if word == "-c" and words:
            word = words.pop(0)
        elif word.startswith(("-c", "--")):
            word = word[2:]
        else:
            raise ValidationError(
                "data_source_properties 'options' holds something other than '-c name=value' "
                "settings, which asyncpg has no place for; give asyncpg its own "
                "'server_settings' instead"
            )
        name, equals, value = word.partition("=")
        if not equals or not name.strip():
            raise ValidationError(
                "data_source_properties 'options' holds a '-c' setting without 'name=value'"
            )
        settings[name.strip().replace("-", "_")] = value
    given_settings = args.get("server_settings") or {}
    if not isinstance(given_settings, Mapping):
        raise ValidationError(
            "data_source_properties 'server_settings' must be a group of name: value"
        )
    both = sorted(set(settings) & set(given_settings))
    if both:
        raise ValidationError(
            f"data_source_properties sets {', '.join(both)} in both 'options' and "
            f"'server_settings'; set each in one place"
        )
    args["server_settings"] = {**given_settings, **settings}
    return args
