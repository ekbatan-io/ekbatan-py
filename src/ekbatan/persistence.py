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
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from sqlalchemy import Connection, Engine, create_engine

from ._tracing import span
from .config import ShardConfig, ShardingConfig
from .errors import ValidationError
from .sharding import ShardIdentifier

__all__ = ["DatabaseRegistry", "Transaction", "TransactionManager"]

LOG = logging.getLogger("ekbatan.persistence")

# Default None rather than an empty dict: a mutable default on a ContextVar is shared by every
# context that never set one, and the day something mutates it in place is the day two requests
# start sharing a connection.
_ambient: ContextVar[Mapping[ShardIdentifier, Connection] | None] = ContextVar(
    "ekbatan_tx", default=None
)

_NONE: Mapping[ShardIdentifier, Connection] = MappingProxyType({})


def _open() -> Mapping[ShardIdentifier, Connection]:
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

    connection: Connection
    shard: ShardIdentifier
    dialect: str

    def execute(self, statement: Any, parameters: Any = None, **kwargs: Any) -> Any:
        """Run one statement on this transaction."""
        return self.connection.execute(statement, parameters, **kwargs)

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
        """Open a transaction on this shard, or join the one already open on it.

        The counterpart to Java's ``inTransaction`` - a ``with`` block rather than a callback,
        which is why there is one of these where Java needs four overloads for value-returning,
        void, checked and unchecked blocks.

        Joining rather than nesting is deliberate: a repository call inside a unit of work must
        land in the same transaction as everything else the unit of work did, or the promise
        that it all commits together is not one. Java's opens a second transaction on a second
        connection instead, which can block against the first one's locks.

        Anything inside the block reaching for ``tx_db`` or ``tx_db_else_db`` on this shard
        finds this transaction and joins it, without being handed anything.
        """
        existing = self.current()
        if existing is not None:
            # No span: joining is not a transaction boundary, and one here would report a
            # nested duration for something that neither began nor committed.
            yield Transaction(existing, self.shard, self.dialect)
            return

        with (
            span(
                "ekbatan.transaction",
                {
                    "ekbatan.shard.group": self.shard.group,
                    "ekbatan.shard.member": self.shard.member,
                },
            ),
            self.primary.connect() as conn,
        ):
            token = _ambient.set({**_open(), self.shard: conn})
            try:
                with conn.begin():
                    LOG.debug("transaction started [shard=%s]", self.shard)
                    yield Transaction(conn, self.shard, self.dialect)
            except BaseException as error:
                # BaseException: a cancellation still rolled the transaction back, and a log
                # that omitted it would be worse than none. The span needs nothing here - an
                # exception leaving the `with` is recorded by OpenTelemetry itself.
                LOG.warning(
                    "transaction rolled back [shard=%s]: %s",
                    self.shard,
                    type(error).__name__,
                )
                raise
            else:
                LOG.debug("transaction committed [shard=%s]", self.shard)
            finally:
                _ambient.reset(token)

    def current(self) -> Connection | None:
        """The transaction open on this shard right now, or ``None``.

        The counterpart to Java's ``currentTransactionDbContext``. Where the ambient
        transaction is kept is this module's business, so asking is a method rather than a
        reach into it from outside.
        """
        return _open().get(self.shard)

    @contextmanager
    def connection(self, *, readonly: bool = False, replica: bool = False) -> Iterator[Connection]:
        """A connection for one statement: the ambient transaction if there is one, else a
        connection that commits each statement as it runs.

        Outside a transaction this opens **none**. A statement stands on its own and commits
        itself, which is what the Java implementation does - its ``db()`` is a context over a
        DataSource with auto-commit left on, so nothing between ``inTransaction`` blocks is
        ever inside one. Transaction management belongs to the caller in both ports.

        SQLAlchemy will not do that by default: an ``execute`` outside a transaction quietly
        begins one, and a connection closed without ``commit`` rolls it back, so a write would
        vanish. ``AUTOCOMMIT`` is how you ask for the behaviour Java gets from the driver.

        ``replica`` accepts a replica's lag, and is never the default: a write followed by a
        read has to find the row. Ask for it where lag is genuinely acceptable.

        A read taken inside a transaction ignores both and joins it - it has to see the writes
        that transaction has already made, and a replica by definition has not seen them.
        """
        existing = _open().get(self.shard)
        if existing is not None:
            yield existing
            return

        engine = self.replica if (readonly and replica) else self.primary
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            yield conn

    def close(self) -> None:
        self.primary.dispose()
        if self._owns_replica:
            self.replica.dispose()

    def __repr__(self) -> str:
        return f"TransactionManager({self.label}, {self.dialect})"


class DatabaseRegistry:
    """Every database the framework can reach, keyed by shard."""

    __slots__ = ("_managers", "default_shard", "dialect")

    def __init__(
        self,
        managers: Mapping[ShardIdentifier, TransactionManager],
        default_shard: ShardIdentifier,
    ) -> None:
        if not managers:
            raise ValidationError("at least one database is required")
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
        self.dialect = self._managers[default_shard].dialect

    @classmethod
    def from_config(cls, config: ShardingConfig) -> DatabaseRegistry:
        """Build the registry. ``ShardingConfig`` has already refused a default shard that
        names nothing, so no engine is created for a layout that cannot work."""
        managers = {s.shard: cls._manager(s, config.dialect) for s in config.shards}
        return cls(managers, config.default_shard)

    @staticmethod
    def _manager(shard: ShardConfig, dialect: str) -> TransactionManager:
        primary = _engine(shard.primary)
        replica = _engine(shard.replica) if shard.replica is not None else None
        return TransactionManager(shard.shard, primary, replica, dialect=dialect, label=shard.label)

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

        An id minted for a shard this deployment has not configured routes to the default
        rather than failing. Shard layouts differ between environments and grow over time; a
        staging database with one shard has to be able to read production's ids.
        """
        if shard is None or shard not in self._managers:
            return self.default_shard
        return shard

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
                LOG.warning("failed to close shard %s: %s", shard, exc)
                first = first or exc
        if first is not None:
            raise first

    def __enter__(self) -> DatabaseRegistry:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"DatabaseRegistry({len(self._managers)} shard(s), default={self.default_shard})"


def _engine(config: object) -> Engine:
    from .config import DataSourceConfig

    assert isinstance(config, DataSourceConfig)
    from ._json import dumps, loads

    kwargs: dict[str, object] = {
        "echo": config.echo,
        "future": True,
        "connect_args": dict(config.connect_args),
        # Canonical form is not a preference: decimals as strings at their stored scale,
        # timestamps zone-less UTC to the microsecond, keys sorted. Two ports must produce
        # byte-identical JSON for the same event or a consumer cannot join them.
        "json_serializer": dumps,
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
    return create_engine(config.url, **kwargs)
