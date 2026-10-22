"""Connections, transactions, and the map from a shard to its database.

One idea does the work here: a transaction, once opened, is *ambient*. Repositories called
inside it join it without being handed anything, so a unit of work reads top to bottom with no
session, connection or context threaded through its arguments.

The ambient transaction lives in a :class:`~contextvars.ContextVar`, which is per-thread and
per-async-task at once. Work handed to a pool from inside a transaction does not inherit it,
which is the correct answer: another thread is another connection, and quietly sharing one
would corrupt both. A thread that *does* inherit it - ``asyncio.to_thread``, anyio's and
Starlette's thread helpers copy the caller's context - is refused with
:class:`~ekbatan.errors.TransactionThreadError`: a sync transaction belongs to the thread that
opened it. A block that ends in another context than it began in - a FastAPI sync ``yield``
dependency - raises :class:`~ekbatan.errors.TransactionContextError`.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Protocol, Self

from sqlalchemy import Connection, Engine, Table, create_engine, insert

from ._logtext import java_list, java_shard, java_shard_pair
from ._tracing import Span, span
from .config import DataSourceConfig, ShardingConfig, ShardMemberConfig
from .config import _unknown_shard_policy as _policy
from .errors import TransactionContextError, TransactionThreadError, ValidationError
from .fetching import Db, FetchResult
from .sharding import ShardIdentifier

__all__ = [
    "DatabaseRegistry",
    "DatabaseRegistryBuilder",
    "Transaction",
    "TransactionManager",
    "insert_ignoring_duplicates",
]

# Java names a logger after its class, and so do these: a logging config written for the Java
# port - `io.ekbatan.core.persistence: DEBUG` - applies here unchanged.
LOG = logging.getLogger("io.ekbatan.core.persistence.TransactionManager")
REGISTRY_LOG = logging.getLogger("io.ekbatan.core.shard.DatabaseRegistry")

# Keyed by the manager, not by its shard - as Java keeps one `ScopedValue` per manager. Two
# managers for the same shard (another registry, or one built on a member's `jobsConfig`) are two
# databases as far as anyone can tell, and keyed by shard the second would run its statements on
# the first one's connection.
#
# Default None rather than an empty dict: a mutable default on a ContextVar is shared by every
# context that never set one, and the day something mutates it in place is the day two requests
# start sharing a connection.
#
# One variable for both kinds of manager: `ekbatan.aio`'s keeps its open transactions here too,
# keyed by itself. The key is the manager, so a sync and an async manager can never find each
# other's connection - and a service is one or the other anyway.
_ambient: ContextVar[Mapping[object, Any] | None] = ContextVar("ekbatan_tx", default=None)

_NONE: Mapping[object, Any] = MappingProxyType({})


def _open() -> Mapping[object, Any]:
    """The transactions open on this thread or task."""
    return _ambient.get() or _NONE


def _join(manager: object, conn: Any) -> Token[Mapping[object, Any] | None]:
    """Make ``conn`` the ambient transaction of ``manager`` until the token is reset."""
    return _ambient.set({**_open(), manager: conn})


def _leave(token: Token[Mapping[object, Any] | None], label: str) -> None:
    """End what :func:`_join` began. A token reset in another context than it was made in -
    the block opened in one copy of the context and closed in another - is the sign that
    whatever ran in between never saw the transaction; Python's own ``ValueError`` says only
    that the token "was created in a different Context", so this says what happened."""
    try:
        _ambient.reset(token)
    except ValueError as error:
        raise TransactionContextError(
            f"the transaction on shard {label} was closed in another context than the one "
            f"that opened it, so the work in between ran outside it: each statement there "
            f"committed on its own. A FastAPI sync `yield` dependency does this - FastAPI "
            f"runs the dependency and the endpoint in separate contexts. Open the transaction "
            f"where the work runs - in the endpoint, the action or the executor - never in a "
            f"dependency."
        ) from error


@dataclass(frozen=True, slots=True)
class _Owned:
    """A sync transaction's connection, and the thread that opened it.

    The context it lives in is copied into a new thread by ``asyncio.to_thread``, so being
    found is not enough: only the opening thread may use it. Async transactions need none of
    this - their tasks share one thread and take turns on the connection.
    """

    conn: Db
    thread: int


def _owned_here(manager: TransactionManager) -> Db | None:
    """``manager``'s open transaction, if this thread opened it; refused if another did."""
    entry: _Owned | None = _open().get(manager)
    if entry is None:
        return None
    if entry.thread != threading.get_ident():
        raise TransactionThreadError(
            f"the transaction on shard {manager.label} was opened by another thread, and a "
            f"connection serves one thread at a time. Do this work in the thread that opened "
            f"the transaction, or outside it. asyncio.to_thread, anyio/Starlette's "
            f"run_in_threadpool, or anything else that copies the context carries the open "
            f"transaction into the new thread."
        )
    return entry.conn


def _transaction_span(shard: ShardIdentifier) -> AbstractContextManager[Span]:
    """The span around one transaction - the same name and attributes for sync and async."""
    return span(
        "ekbatan.transaction",
        {"ekbatan.shard.group": shard.group, "ekbatan.shard.member": shard.member},
    )


def _log_started(shard: ShardIdentifier) -> None:
    LOG.debug("Transaction started [shard=%s]", java_shard_pair(shard))


def _log_committed(shard: ShardIdentifier) -> None:
    LOG.debug("Transaction committed [shard=%s]", java_shard_pair(shard))


def _log_evicting(shard: ShardIdentifier) -> None:
    LOG.warning(
        "Evicting connection from pool - rollback or autocommit-reset failed "
        "leaving it in unknown state [shard=%s]",
        java_shard_pair(shard),
    )


def _log_rolled_back(shard: ShardIdentifier, error: BaseException) -> None:
    LOG.warning(
        "Transaction rolled back [shard=%s]: %s",
        java_shard_pair(shard),
        type(error).__name__,
    )


@dataclass(frozen=True, slots=True)
class Transaction:
    """One open transaction: what to run statements on, and the shard they will land on.

    The counterpart to the ``DSLContext`` Java hands the block inside ``inTransaction``. Both
    say the same thing - run your statements here and they land on this shard, inside this
    transaction - and both carry the dialect, for the query that has to differ.

    ``execute`` is forwarded, so a block reads the way jOOQ's does rather than reaching through
    a connection attribute for every statement. :attr:`connection` is there for everything
    else SQLAlchemy offers.
    """

    connection: Db
    shard: ShardIdentifier
    dialect: str

    def execute(self, statement: Any, parameters: Any = None, **kwargs: Any) -> Any:
        """Run one statement on this transaction."""
        return self.connection.execute(statement, parameters, **kwargs)

    def rows(self, statement: Any) -> FetchResult:
        """Run one statement and hand back its rows, for mapping.

        Forwarded for the same reason :meth:`execute` is: a block inside ``with
        registry.manager(shard).transaction()`` - or ``registry.default.transaction()`` - should
        read the way one inside a repository does, rather than reaching through
        :attr:`connection` for every statement.
        """
        return self.connection.rows(statement)

    def __repr__(self) -> str:
        return f"Transaction(shard={self.shard}, {self.dialect})"


class TransactionManager:
    """One shard's databases, and the transaction boundary around them."""

    __slots__ = ("_owns_replica", "dialect", "label", "primary", "replica", "shard")

    def __init__(
        self,
        shard: ShardIdentifier,
        primary: Engine,
        replica: Engine | None = None,
        *,
        dialect: str = "",
        label: str = "",
    ) -> None:
        self.shard = shard
        self.primary = primary
        # Falling back to the primary *engine*, never to its configuration. Identical settings
        # do not produce the same pool - they produce a second one against the same database,
        # with its own connections and its own housekeeping. A member with no replica would
        # then quietly hold twice the configured maximum, and nothing would look wrong because
        # both pools are healthy.
        self.replica = replica if replica is not None else primary
        self._owns_replica = replica is not None
        self.dialect = dialect or primary.dialect.name
        self.label = label or str(shard)

    @property
    def shares_one_pool(self) -> bool:
        return not self._owns_replica

    @contextmanager
    def transaction(self) -> Iterator[Transaction]:
        """Open a transaction on this shard: a connection of its own, committed or rolled back
        when the block ends.

        The counterpart to Java's ``inTransaction`` - a ``with`` block rather than a callback,
        which is why there is one of these where Java needs four overloads for value-returning,
        void, checked and unchecked blocks.

        **Every call opens a new transaction, as Java's does - even inside another one.** The
        inner block borrows a second connection, commits or rolls back on its own, and does not
        see the outer block's uncommitted rows; inside it, ``tx_db`` and ``tx_db_else_db`` find
        the inner transaction, and after it the outer one again. So do not nest: write all the
        work of one unit in one block. An inner block that writes a row the outer one has
        locked waits for a lock the same thread holds, until the database's lock timeout - or
        for ever, where there is none.

        Anything inside the block reaching for ``tx_db`` or ``tx_db_else_db`` on this shard
        finds this transaction and joins it, without being handed anything.
        """
        with _transaction_span(self.shard), Db(self.primary) as conn:
            token = _join(self, _Owned(conn, threading.get_ident()))
            # Begin, commit and roll back by hand rather than through `with conn.begin()`. The
            # context manager rolls back for us, but it has nowhere to say that the rollback
            # *itself* failed - and SQLAlchemy then returns that connection to the pool and
            # hands it to the next caller, still carrying whatever the rollback did not undo.
            # Java refuses to do that: a failed rollback marks the transaction dirty and the
            # connection is evicted rather than released.
            tx = conn.begin()
            try:
                _log_started(self.shard)
                yield Transaction(conn, self.shard, self.dialect)
                # Inside the try, as Java's is: a commit that fails is a transaction that has
                # to be rolled back like any other, and if that rollback fails too the
                # connection is in the same unknown state.
                tx.commit()
                _log_committed(self.shard)
            except BaseException as error:
                # BaseException: a cancellation still rolled the transaction back, and a log
                # that omitted it would be worse than none. The span needs nothing here - an
                # exception leaving the `with` is recorded by OpenTelemetry itself.
                try:
                    tx.rollback()
                except BaseException:
                    # Unknown state: either the rollback never reached the server, or it did
                    # and failed. Invalidating closes the physical connection, which makes the
                    # server discard anything still open on it - and stops the pool offering
                    # it to somebody else.
                    _log_evicting(self.shard)
                    conn.invalidate()
                _log_rolled_back(self.shard, error)
                raise
            finally:
                _leave(token, self.label)

    def current(self) -> Db | None:
        """The transaction this manager has open right now, or ``None``.

        The counterpart to Java's ``currentTransactionDbContext``. Where the ambient
        transaction is kept is this module's business, so asking is a method rather than a
        reach into it from outside.
        """
        return _owned_here(self)

    @contextmanager
    def connection(self, *, readonly: bool = False) -> Iterator[Db]:
        """A connection for one statement: the ambient transaction if there is one, else a
        connection that commits each statement as it runs.

        Outside a transaction this opens **none**. A statement stands on its own and commits
        itself, which is what the Java implementation does - its ``db()`` is a context over a
        DataSource with auto-commit left on, so nothing between ``inTransaction`` blocks is
        ever inside one. Transaction management belongs to the caller in both ports.

        SQLAlchemy will not do that by default: an ``execute`` outside a transaction quietly
        begins one, and a connection closed without ``commit`` rolls it back, so a write would
        vanish. ``AUTOCOMMIT`` is how you ask for the behaviour Java gets from the driver.

        ``readonly`` asks for the replica, exactly as Java's ``readonlyDb()`` does, and falls
        back to the primary when a shard has no replica configured. It is never the default:
        choosing to accept a replica's lag belongs to the caller, who is the only one who knows
        whether they just wrote the row they are about to read.

        A read taken inside a transaction ignores it and joins the transaction - it has to see
        the writes that transaction has already made, and a replica by definition has not.
        """
        existing = _owned_here(self)
        if existing is not None:
            yield existing
            return

        engine = self.replica if readonly else self.primary
        with Db(engine).execution_options(isolation_level="AUTOCOMMIT") as conn:
            yield conn

    def close(self) -> None:
        self.primary.dispose()
        if self._owns_replica:
            self.replica.dispose()

    def __repr__(self) -> str:
        return f"TransactionManager({self.label}, {self.dialect})"


class _ShardManager(Protocol):
    """What the registry needs of a manager - true of a sync and an async one alike."""

    @property
    def shard(self) -> ShardIdentifier: ...

    @property
    def dialect(self) -> str: ...


class _ShardRegistry[TM: _ShardManager]:
    """The map from a shard to its manager, and every rule about it, for both kinds of registry.

    :class:`DatabaseRegistry` holds :class:`TransactionManager` s and ``ekbatan.aio``'s
    ``AsyncDatabaseRegistry`` holds async ones. Which shards exist, which is the default, where
    an unknown one goes and what a misfiled one is are the same questions with the same answers,
    so they are answered once, here. Only building a manager and closing one differ.
    """

    __slots__ = ("_managers", "default_shard", "dialect", "unknown_shard_policy")

    def __init__(
        self,
        managers: Mapping[ShardIdentifier, TM],
        default_shard: ShardIdentifier | None = None,
        unknown_shard_policy: Literal["use_default_shard", "fail"] = "use_default_shard",
    ) -> None:
        if not managers:
            raise ValidationError("at least one database is required")
        if default_shard is None:
            # One database needs no election, and saying which of one is the default is a line
            # that can only be written correctly. Java's builder does the same, and refuses the
            # same way once there is more than one to choose between.
            if len(managers) > 1:
                raise ValidationError(
                    f"default_shard is required when more than one database is registered; "
                    f"registered: {sorted(str(s) for s in managers)}"
                )
            default_shard = next(iter(managers))
        if default_shard not in managers:
            raise ValidationError(f"default_shard {default_shard} names no registered database")
        misfiled = [(key, m.shard) for key, m in managers.items() if key != m.shard]
        if misfiled:
            # Two answers to "which shard is this?" - the key routing uses, and the one the
            # manager stamps on every transaction it opens. Nothing downstream can tell them
            # apart: rows would be written to the database filed under one and the outbox row
            # would name the other, and `shard` on that row is what tells a consumer which
            # database an event came from.
            listed = ", ".join(f"{key} holds a manager for {actual}" for key, actual in misfiled)
            raise ValidationError(f"a shard is registered under the wrong key: {listed}")
        self._managers = dict(managers)
        self.default_shard = default_shard
        self.unknown_shard_policy = _policy(unknown_shard_policy)
        self.dialect = self._managers[default_shard].dialect

    @classmethod
    def from_config(cls, config: ShardingConfig) -> Self:
        """Build the registry. ``ShardingConfig`` has already refused a default shard that
        names nothing, so no engine is created for a layout that cannot work."""
        managers = {
            shard: cls._manager(shard, member, config.dialect) for shard, member in config.members()
        }
        return cls(managers, config.default_shard, config.unknown_shard_policy)

    @staticmethod
    def _manager(shard: ShardIdentifier, member: ShardMemberConfig, dialect: str) -> TM:
        """One member's manager, built from its config. Each kind of registry writes this."""
        raise NotImplementedError

    def manager(self, shard: ShardIdentifier) -> TM:
        try:
            return self._managers[shard]
        except KeyError:
            raise ValidationError(
                f"no database registered for shard {shard}; "
                f"registered: {sorted(str(s) for s in self._managers)}"
            ) from None

    def effective_shard(self, shard: ShardIdentifier | None) -> ShardIdentifier:
        """The shard to actually use.

        ``None`` means nothing proposed one - an unsharded repository - and takes the default
        silently, which is not a fallback and not worth a word.

        A shard this deployment has not configured is the interesting case. It routes to the
        default, as Java's `effectiveShard` does, because layouts differ between environments
        and grow over time: a staging database with one shard has to be able to read
        production's ids. It is also exactly what a mistyped shard identifier looks like, and
        that one writes to the wrong database.

        Nothing can tell those two apart here, so this does not try. It says what it did, every
        time, and `unknown_shard_policy="fail"` is for a deployment that holds the whole
        layout and can therefore be sure the second case is the only one.
        """
        if shard is None:
            return self.default_shard
        if shard in self._managers:
            return shard
        if self.unknown_shard_policy == "fail":
            raise ValidationError(
                f"shard {shard} is not registered and unknown_shard_policy is 'fail'; "
                f"registered: {sorted(str(s) for s in self._managers)}"
            )
        REGISTRY_LOG.warning(
            "Shard %s is not registered, routing to default %s [registered=%s]",
            java_shard(shard),
            java_shard(self.default_shard),
            java_list(java_shard(s) for s in self._managers),
        )
        return self.default_shard

    @property
    def shards(self) -> tuple[ShardIdentifier, ...]:
        return tuple(self._managers)

    @property
    def default(self) -> TM:
        return self._managers[self.default_shard]

    @staticmethod
    def _close_failed(shard: ShardIdentifier, exc: BaseException) -> None:
        REGISTRY_LOG.warning(
            "Failed to close TransactionManager for shard %s: %s",
            java_shard(shard),
            f"{type(exc).__name__}: {exc}",
        )

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}({len(self._managers)} shard(s), default={self.default_shard})"
        )


class DatabaseRegistry(_ShardRegistry[TransactionManager]):
    """Every database the framework can reach, keyed by shard."""

    __slots__ = ()

    @classmethod
    def builder(cls) -> DatabaseRegistryBuilder:
        return DatabaseRegistryBuilder()

    @staticmethod
    def _manager(
        shard: ShardIdentifier, member: ShardMemberConfig, dialect: str
    ) -> TransactionManager:
        """Java's loop body in ``fromConfig``: the primary's pool, and the secondary's if the
        member has one. Any other named config on the member is the application's to build."""
        primary = engine_for(member.primary_config())
        secondary = member.secondary_config()
        replica = engine_for(secondary) if secondary is not None else None
        return TransactionManager(
            shard, primary, replica, dialect=dialect, label=member.name or str(shard)
        )

    def close(self) -> None:
        """Dispose every pool. One failing shard does not strand the rest."""
        first: BaseException | None = None
        for shard, manager in self._managers.items():
            try:
                manager.close()
            except Exception as exc:
                self._close_failed(shard, exc)
                first = first or exc
        if first is not None:
            raise first

    def __enter__(self) -> DatabaseRegistry:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def engine_for(config: DataSourceConfig) -> Engine:
    """A pool for one :class:`~ekbatan.config.DataSourceConfig` - Java's
    ``ConnectionProvider.hikariConnectionProvider(config)``.

    What the registry builds a shard's pools with, and what an application builds any other
    named config on a member with - a ``"jobsConfig"`` for the scheduler, a ``"lockConfig"`` for
    the locks::

        member = layout.groups[0].members[0]
        jobs_engine = engine_for(member.config_for("jobsConfig"))

    The engine is the caller's: dispose it when you are done with it.
    """
    return create_engine(config.sqlalchemy_url(), **_engine_kwargs(config))


def _engine_kwargs(config: DataSourceConfig) -> dict[str, Any]:
    """Everything but the url an engine is built with - one answer for a sync engine and for
    ``ekbatan.aio``'s async one, so the two pools cannot be configured differently."""
    from ._json import column_dumps, loads

    kwargs: dict[str, Any] = {
        "echo": config.echo,
        "future": True,
        "connect_args": dict(config.data_source_properties),
        # The JSON Java's Ekbatan writes, byte for byte (Java's field names, decimals as
        # numbers), and read back with every number as a Decimal. See ekbatan._json. A datetime
        # with no declared kind (one in a dict in the application's own JSON column) is written
        # as it is given: the outbox has already refused that case for events and params.
        "json_serializer": column_dumps,
        "json_deserializer": loads,
    }
    if config.dialect != "sqlite":
        kwargs |= {
            "pool_size": config.pool_size,
            "max_overflow": config.max_overflow,
            "pool_timeout": config.pool_timeout,
            "pool_recycle": config.pool_recycle,
            "pool_pre_ping": config.pool_pre_ping,
        }
    return kwargs


def insert_ignoring_duplicates(
    conn: Connection, table: Table, rows: Sequence[Mapping[str, object]]
) -> None:
    """Insert, letting the unique constraint drop the rows that are already there.

    For a row several instances may each try to create: the constraint decides, not whichever
    of them read the table first. A read-then-insert loses that race by construction - both
    read nothing, both insert, and one of them takes a unique violation on a perfectly ordinary
    startup.
    """
    name = conn.dialect.name
    if name in ("postgresql", "sqlite"):
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        maker = pg_insert if name == "postgresql" else sqlite_insert
        conn.execute(maker(table).on_conflict_do_nothing(), list(rows))
    elif name in ("mysql", "mariadb"):
        conn.execute(insert(table).prefix_with("IGNORE"), list(rows))
    else:  # pragma: no cover - every supported dialect is covered above
        conn.execute(insert(table), list(rows))


class _ShardRegistryBuilder[TM: _ShardManager, R]:
    """The builder for both kinds of registry: :class:`DatabaseRegistryBuilder` and
    ``ekbatan.aio``'s ``AsyncDatabaseRegistryBuilder`` differ only in what they build.

    Each manager is filed under the shard it says it is, so the key and the manager cannot
    disagree - the mistake the constructor has to check for cannot be written here.
    """

    __slots__ = ("_default_shard", "_managers", "_unknown_shard_policy")

    #: The registry ``build()`` makes.
    _builds: Any

    def __init__(self) -> None:
        self._managers: dict[ShardIdentifier, TM] = {}
        self._default_shard: ShardIdentifier | None = None
        self._unknown_shard_policy: Literal["use_default_shard", "fail"] = "use_default_shard"

    def with_database(self, manager: TM) -> Self:
        self._managers[manager.shard] = manager
        return self

    def with_default_database(self, manager: TM) -> Self:
        self._managers[manager.shard] = manager
        self._default_shard = manager.shard
        return self

    def with_unknown_shard_policy(
        self, unknown_shard_policy: Literal["use_default_shard", "fail"] | None
    ) -> Self:
        """``None`` means the default, ``"use_default_shard"`` - as Java's null does."""
        self._unknown_shard_policy = _policy(unknown_shard_policy)
        return self

    def build(self) -> R:
        built: R = self._builds(self._managers, self._default_shard, self._unknown_shard_policy)
        return built


class DatabaseRegistryBuilder(_ShardRegistryBuilder[TransactionManager, DatabaseRegistry]):
    """Fluent builder for :class:`DatabaseRegistry`. Obtain via :meth:`DatabaseRegistry.builder`.

    Each manager is filed under the shard it says it is, so the key and the manager cannot
    disagree - the mistake the constructor has to check for cannot be written here.
    """

    __slots__ = ()
    _builds = DatabaseRegistry
