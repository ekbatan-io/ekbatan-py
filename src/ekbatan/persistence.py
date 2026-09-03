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
from types import MappingProxyType

from sqlalchemy import Connection, Engine, create_engine

from .config import ShardConfig, ShardingConfig
from .errors import ValidationError
from .sharding import ShardIdentifier

__all__ = ["DatabaseRegistry", "TransactionManager"]

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
    def transaction(self) -> Iterator[Connection]:
        """Open a transaction on this shard, or join the one already open on it.

        Joining rather than nesting is deliberate: a repository call inside a unit of work must
        land in the same transaction as everything else the unit of work did, or the promise
        that it all commits together is not one.
        """
        existing = _open().get(self.shard)
        if existing is not None:
            yield existing
            return

        with self.primary.connect() as conn:
            token = _ambient.set({**_open(), self.shard: conn})
            try:
                with conn.begin():
                    yield conn
            finally:
                _ambient.reset(token)

    @contextmanager
    def connection(self, *, readonly: bool = False) -> Iterator[Connection]:
        """A connection for one statement: the ambient transaction if there is one, else a
        short-lived connection of its own.

        A read taken outside a transaction may use the replica. A read taken inside one may
        not - it has to see the writes the transaction has already made, and a replica by
        definition has not seen them.
        """
        existing = _open().get(self.shard)
        if existing is not None:
            yield existing
            return

        engine = self.replica if readonly else self.primary
        with engine.connect() as conn:
            if readonly:
                yield conn
            else:
                with conn.begin():
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
