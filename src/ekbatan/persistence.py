"""Connections, transactions, and the map from a shard to its database.

One idea does the work here: a transaction, once opened, is *ambient*. Repositories called
inside it join it without being handed anything, so a unit of work reads top to bottom with no
session, connection or context threaded through its arguments.

The ambient transaction lives in a :class:`~contextvars.ContextVar`, which is per-thread and
per-async-task at once. Work handed to a pool from inside a transaction does not inherit it,
which is the correct answer: another thread is another connection, and quietly sharing one
would corrupt both.
"""

from __future__ import annotations

import logging
import threading
import time
import weakref
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Self

from sqlalchemy import Connection, Engine, Table, create_engine, insert
from sqlalchemy import exc as sa_exc
from sqlalchemy.pool import ConnectionPoolEntry, QueuePool
from sqlalchemy.util import queue as sa_queue

from ._tracing import span
from .config import DataSourceConfig, ShardingConfig, ShardMemberConfig
from .config import _unknown_shard_policy as _policy
from .errors import ValidationError
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
POOL_LOG = logging.getLogger("io.ekbatan.core.persistence.ConnectionProvider")

# Keyed by the manager, not by its shard - as Java keeps one `ScopedValue` per manager. Two
# managers for the same shard (another registry, or one built on a member's `jobsConfig`) are two
# databases as far as anyone can tell, and keyed by shard the second would run its statements on
# the first one's connection.
#
# Default None rather than an empty dict: a mutable default on a ContextVar is shared by every
# context that never set one, and the day something mutates it in place is the day two requests
# start sharing a connection.
_ambient: ContextVar[Mapping[TransactionManager, Db] | None] = ContextVar(
    "ekbatan_tx", default=None
)

_NONE: Mapping[TransactionManager, Db] = MappingProxyType({})


def _open() -> Mapping[TransactionManager, Db]:
    """The transactions open on this thread or task."""
    return _ambient.get() or _NONE


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
        with (
            span(
                "ekbatan.transaction",
                {
                    "ekbatan.shard.group": self.shard.group,
                    "ekbatan.shard.member": self.shard.member,
                },
            ),
            Db(self.primary) as conn,
        ):
            token = _ambient.set({**_open(), self: conn})
            # Begin, commit and roll back by hand rather than through `with conn.begin()`. The
            # context manager rolls back for us, but it has nowhere to say that the rollback
            # *itself* failed - and SQLAlchemy then returns that connection to the pool and
            # hands it to the next caller, still carrying whatever the rollback did not undo.
            # Java refuses to do that: a failed rollback marks the transaction dirty and the
            # connection is evicted rather than released.
            tx = conn.begin()
            try:
                LOG.debug("transaction started [shard=%s]", self.shard)
                yield Transaction(conn, self.shard, self.dialect)
                # Inside the try, as Java's is: a commit that fails is a transaction that has
                # to be rolled back like any other, and if that rollback fails too the
                # connection is in the same unknown state.
                tx.commit()
                LOG.debug("transaction committed [shard=%s]", self.shard)
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
                    LOG.warning(
                        "rollback failed [shard=%s]; discarding the connection rather than "
                        "returning it to the pool",
                        self.shard,
                        exc_info=True,
                    )
                    conn.invalidate()
                LOG.warning(
                    "transaction rolled back [shard=%s]: %s",
                    self.shard,
                    type(error).__name__,
                )
                raise
            finally:
                _ambient.reset(token)

    def current(self) -> Db | None:
        """The transaction this manager has open right now, or ``None``.

        The counterpart to Java's ``currentTransactionDbContext``. Where the ambient
        transaction is kept is this module's business, so asking is a method rather than a
        reach into it from outside.
        """
        return _open().get(self)

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
        existing = _open().get(self)
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


class DatabaseRegistry:
    """Every database the framework can reach, keyed by shard."""

    __slots__ = ("_managers", "default_shard", "dialect", "unknown_shard_policy")

    def __init__(
        self,
        managers: Mapping[ShardIdentifier, TransactionManager],
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
    def builder(cls) -> DatabaseRegistryBuilder:
        return DatabaseRegistryBuilder()

    @classmethod
    def from_config(cls, config: ShardingConfig) -> DatabaseRegistry:
        """Build the registry. ``ShardingConfig`` has already refused a default shard that
        names nothing, so no engine is created for a layout that cannot work."""
        managers = {
            shard: cls._manager(shard, member, config.dialect) for shard, member in config.members()
        }
        return cls(managers, config.default_shard, config.unknown_shard_policy)

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

    def manager(self, shard: ShardIdentifier) -> TransactionManager:
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
            "shard %s is not registered, routing to default %s [registered=%s]",
            shard,
            self.default_shard,
            sorted(str(s) for s in self._managers),
        )
        return self.default_shard

    @property
    def shards(self) -> tuple[ShardIdentifier, ...]:
        return tuple(self._managers)

    @property
    def default(self) -> TransactionManager:
        return self._managers[self.default_shard]

    def close(self) -> None:
        """Dispose every pool. One failing shard does not strand the rest."""
        first: BaseException | None = None
        for shard, manager in self._managers.items():
            try:
                manager.close()
            except Exception as exc:
                REGISTRY_LOG.warning("failed to close shard %s: %s", shard, exc)
                first = first or exc
        if first is not None:
            raise first

    def __enter__(self) -> DatabaseRegistry:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"DatabaseRegistry({len(self._managers)} shard(s), default={self.default_shard})"


# Hikari's numbers, as Java's pool runs them: a failed connection attempt is retried after 10 ms,
# then twice as long each time up to 5 s; a connection just added is followed by a 30 ms pause;
# and with nothing to do the filler looks again every 30 s, as Hikari's housekeeper does.
_FIRST_BACKOFF = 0.01
_MAX_BACKOFF = 5.0
_AFTER_ADD = 0.03
_HOUSEKEEPING = 30.0


class _FillingPool(QueuePool):
    """SQLAlchemy's ``QueuePool``, run the way Java's Hikari pool runs with Ekbatan's settings.

    Two things Hikari does that ``QueuePool`` does not, both measured on Java's pool:

    - **It fills to the maximum in the background.** Hikari's ``minimumIdle`` defaults to the
      maximum pool size, so a pool of 3 holds 3 open connections a moment after it is built,
      whether or not anything has asked for one. ``QueuePool`` opens one per request. Here a
      thread fills the pool to ``pool_size`` (overflow connections still open on demand, as
      ``max_overflow`` says) and refills it when a connection is thrown away.
    - **A connection that cannot be opened is retried until** ``pool_timeout``. Hikari's
      ``connectionTimeout`` (30 s) is how long a caller waits for a connection, and while it
      waits the pool keeps trying - a wrong password fails after 30 s, not at once. Here the
      caller's own attempt failing makes it wait, up to ``pool_timeout``, for the filler - which
      retries with Hikari's backoff - and only then raises the driver's error.

    The filler keeps trying for as long as the pool is open, as Hikari's does: a pool with a
    wrong password logs in again every 5 seconds until it is disposed.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._filler: threading.Thread | None = None
        self._filler_lock = threading.Lock()
        self._closed = False
        # A connection the filler is opening is counted by `QueuePool` as checked out - it has
        # taken its slot - but nobody holds it. Reported apart, so `checkedout()` means what it
        # says while the pool fills.
        self._opening = 0
        # Held by the filler thread as well as the pool, so the thread can wait on them without
        # holding the pool itself - an abandoned pool is then collected and its thread ends.
        self._stop = threading.Event()
        self._wake = threading.Event()

    def start_filling(self) -> None:
        """Start the background filler, once. :func:`engine_for` calls it as the pool is built,
        as Hikari starts filling at construction; a pool recreated by ``engine.dispose()`` starts
        it on its first checkout instead, so disposing does not reopen what it just closed."""
        if self._filler is not None and self._filler.is_alive():
            return
        with self._filler_lock:
            if self._closed or (self._filler is not None and self._filler.is_alive()):
                return
            self._filler = threading.Thread(
                target=_fill,
                args=(weakref.ref(self), self._stop, self._wake),
                name="ekbatan-pool-filler",
                daemon=True,
            )
            self._filler.start()

    def _do_get(self) -> ConnectionPoolEntry:
        self.start_filling()
        deadline = time.monotonic() + self._timeout
        try:
            return super()._do_get()
        except sa_exc.TimeoutError:
            # Every connection is out and none came back in `pool_timeout`: already waited.
            raise
        except Exception:
            # The attempt to open one failed. Hikari's caller would still be waiting, and the
            # pool still trying: wait for the filler, which retries with backoff, until the
            # caller's time is up - then raise what the driver said.
            self._wake.set()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._closed:
                    raise
                try:
                    return self._pool.get(True, remaining)
                except sa_queue.Empty:
                    continue

    def _dec_overflow(self) -> Any:
        # A connection was thrown away (or never opened): the filler has one to replace.
        done = super()._dec_overflow()
        self._wake.set()
        return done

    def dispose(self) -> None:
        self._closed = True
        self._stop.set()
        self._wake.set()
        super().dispose()

    def checkedout(self) -> int:
        return super().checkedout() - self._opening

    def _wanted(self) -> bool:
        """Hikari's ``shouldContinueCreating`` with ``minimumIdle`` at the maximum: open while the
        pool holds fewer than ``pool_size`` connections."""
        return not self._closed and self._overflow < 0

    def _add_one(self) -> bool:
        """Open one connection into the pool. False when the attempt failed."""
        if not self._inc_overflow():
            return True
        self._opening += 1
        try:
            record = self._create_connection()
        except Exception as error:
            self._opening -= 1
            super()._dec_overflow()  # not through ours: a failure is not a reason to wake
            # The driver's message names the user and the server, never the password; it is
            # still logged without its traceback, at DEBUG, as Hikari logs each attempt.
            POOL_LOG.debug(
                "connection add failed, retrying with backoff: %s: %s",
                type(error).__name__,
                error,
            )
            return False
        # Before the put, so the count can only ever be one too high for an instant, never low.
        self._opening -= 1
        if self._closed:
            record.close()
            super()._dec_overflow()
            return True
        try:
            self._pool.put(record, False)
        except sa_queue.Full:
            record.close()
            super()._dec_overflow()
        return True


def _fill(
    pool_ref: weakref.ref[_FillingPool], stop: threading.Event, wake: threading.Event
) -> None:
    """The filler: Hikari's ``PoolEntryCreator`` loop, plus its housekeeper's periodic look."""
    backoff = _FIRST_BACKOFF
    while not stop.is_set():
        pool = pool_ref()
        if pool is None:
            return
        if pool._wanted():
            added = pool._add_one()
            del pool
            if added:
                backoff = _FIRST_BACKOFF
                stop.wait(_AFTER_ADD)
            else:
                stop.wait(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)
            continue
        del pool
        wake.wait(_HOUSEKEEPING)
        wake.clear()


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
    from ._json import dumps, loads

    kwargs: dict[str, object] = {
        "echo": config.echo,
        "future": True,
        "connect_args": dict(config.data_source_properties),
        # The JSON Java's Ekbatan writes, byte for byte (Java's field names, decimals as
        # numbers), and read back with every number as a Decimal. See ekbatan._json.
        "json_serializer": dumps,
        "json_deserializer": loads,
    }
    if config.dialect == "sqlite":
        return create_engine(config.sqlalchemy_url(), **kwargs)
    kwargs |= {
        "poolclass": _FillingPool,
        "pool_size": config.pool_size,
        "max_overflow": config.max_overflow,
        "pool_timeout": config.pool_timeout,
        "pool_recycle": config.pool_recycle,
        "pool_pre_ping": config.pool_pre_ping,
    }
    engine = create_engine(config.sqlalchemy_url(), **kwargs)
    # Filling from the start, as Hikari does: the pool holds `pool_size` open connections a
    # moment after it is built, and a database it cannot reach is retried from then on.
    pool = engine.pool
    if isinstance(pool, _FillingPool):
        pool.start_filling()
    return engine


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


class DatabaseRegistryBuilder:
    """Fluent builder for :class:`DatabaseRegistry`. Obtain via :meth:`DatabaseRegistry.builder`.

    Each manager is filed under the shard it says it is, so the key and the manager cannot
    disagree - the mistake the constructor has to check for cannot be written here.
    """

    __slots__ = ("_default_shard", "_managers", "_unknown_shard_policy")

    def __init__(self) -> None:
        self._managers: dict[ShardIdentifier, TransactionManager] = {}
        self._default_shard: ShardIdentifier | None = None
        self._unknown_shard_policy: Literal["use_default_shard", "fail"] = "use_default_shard"

    def with_database(self, manager: TransactionManager) -> Self:
        self._managers[manager.shard] = manager
        return self

    def with_default_database(self, manager: TransactionManager) -> Self:
        self._managers[manager.shard] = manager
        self._default_shard = manager.shard
        return self

    def with_unknown_shard_policy(
        self, unknown_shard_policy: Literal["use_default_shard", "fail"] | None
    ) -> Self:
        """``None`` means the default, ``"use_default_shard"`` - as Java's null does."""
        self._unknown_shard_policy = _policy(unknown_shard_policy)
        return self

    def build(self) -> DatabaseRegistry:
        return DatabaseRegistry(self._managers, self._default_shard, self._unknown_shard_policy)
