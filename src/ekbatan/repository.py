"""Reading and writing rows.

A repository is bound to one model and one table. It routes by shard, enforces the optimistic
lock, hides soft-deleted rows from every read path, and maps between rows and models - by field
name, so that the common case needs no mapping code at all.

It has no opinion about transactions. If one is open on the shard it joins it; if not it opens
a short one of its own. That is what lets the same call read naturally in a unit of work and in
a script.
"""

from __future__ import annotations

import dataclasses
import typing
from collections.abc import Iterable, Sequence
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import ColumnElement, Row, Table, bindparam, delete, func, insert, select, update

from .clock import Clock, SystemClock, to_storage
from .domain import DELETED, Persistable
from .errors import EntityNotFoundError, StaleRecordError, ValidationError
from .persistence import DatabaseRegistry
from .sharding import NoSharding, ShardIdentifier, ShardingStrategy

__all__ = ["Repository"]


class Repository[M: Persistable[Any, Any], K]:
    """Rows of one table, as models.

    Subclass it, or instantiate it directly when the default field-to-column mapping is
    enough::

        wallets = Repository(Wallet, WALLETS, registry, sharding=EmbeddedBitsSharding())
    """

    def __init__(
        self,
        model_type: type[M],
        table: Table,
        registry: DatabaseRegistry,
        *,
        sharding: ShardingStrategy | None = None,
        clock: Clock | None = None,
        state_column: str = "state",
    ) -> None:
        self.model_type = model_type
        self.table = table
        self.registry = registry
        self.sharding = sharding or NoSharding()
        self.clock = clock or SystemClock()

        pk = list(table.primary_key.columns)
        if len(pk) != 1:
            raise ValidationError(
                f"{table.name} has {len(pk)} primary key columns; a repository needs exactly one"
            )
        self.id_column = pk[0]
        self.state_column = table.c.get(state_column)

        self._fields = {f.name: f for f in dataclasses.fields(model_type)}
        self._types = _concrete_types(model_type)
        self._columns = {n: table.c[n] for n in self._fields if n in table.c}
        missing = [n for n in ("id", "version") if n not in self._columns]
        if missing:
            raise ValidationError(f"{table.name} has no column(s) named {missing}")

    # ---------------------------------------------------------------- mapping

    def to_row(self, model: M) -> dict[str, Any]:
        """The model as column values. Override when names or shapes differ."""
        return {name: _to_column(getattr(model, name)) for name in self._columns}

    def from_row(self, row: Row[Any]) -> M:
        """A row as a model. Override alongside :meth:`to_row`."""
        mapping = row._mapping
        kwargs = {
            name: _from_column(mapping[name], self._types.get(name))
            for name in self._columns
            if name in mapping
        }
        return self.model_type(**kwargs)

    # ------------------------------------------------------------- addressing

    def shard_for(self, key: object) -> ShardIdentifier:
        return self.registry.effective_shard(self.sharding.shard_for(key))

    def _live(self, *criteria: ColumnElement[bool]) -> tuple[ColumnElement[bool], ...]:
        """Every read narrows to rows that are not soft-deleted.

        Applied here, once, rather than at each call site. A read path that forgets it is the
        one a reporting query will use, and its totals will disagree with the application's own
        screens for reasons nobody can reproduce.
        """
        if self.state_column is None:
            return criteria
        return (self.state_column != DELETED, *criteria)

    # ------------------------------------------------------------------ reads

    def find(self, key: K) -> M | None:
        """The row, or ``None``. The lookup that treats absence as ordinary."""
        stmt = select(self.table).where(*self._live(self.id_column == key))
        with self.registry.manager(self.shard_for(key)).connection(readonly=True) as conn:
            row = conn.execute(stmt).first()
        return self.from_row(row) if row is not None else None

    def get(self, key: K) -> M:
        """The row, or raise. The lookup for callers that cannot continue without it."""
        found = self.find(key)
        if found is None:
            raise EntityNotFoundError(self.model_type.__name__, key)
        return found

    def find_many(self, keys: Iterable[K]) -> list[M]:
        """The rows that exist, in no promised order. An empty input is an empty result -
        never ``IN ()``, and never an accidental full-table read."""
        by_shard: dict[ShardIdentifier, list[K]] = {}
        for key in keys:
            by_shard.setdefault(self.shard_for(key), []).append(key)

        found: list[M] = []
        for shard, shard_keys in by_shard.items():
            stmt = select(self.table).where(*self._live(self.id_column.in_(shard_keys)))
            with self.registry.manager(shard).connection(readonly=True) as conn:
                found += [self.from_row(r) for r in conn.execute(stmt)]
        return found

    def find_where(
        self, *criteria: ColumnElement[bool], shard: ShardIdentifier | None = None
    ) -> list[M]:
        stmt = select(self.table).where(*self._live(*criteria))
        return [self.from_row(r) for r in self._read(stmt, shard)]

    def find_one_where(
        self, *criteria: ColumnElement[bool], shard: ShardIdentifier | None = None
    ) -> M | None:
        rows = self._read(select(self.table).where(*self._live(*criteria)).limit(1), shard)
        return self.from_row(rows[0]) if rows else None

    def all(self, *, shard: ShardIdentifier | None = None) -> list[M]:
        return self.find_where(shard=shard)

    def exists(self, key: K) -> bool:
        stmt = (
            select(func.count()).select_from(self.table).where(*self._live(self.id_column == key))
        )
        with self.registry.manager(self.shard_for(key)).connection(readonly=True) as conn:
            return bool(conn.execute(stmt).scalar_one())

    def exists_where(
        self, *criteria: ColumnElement[bool], shard: ShardIdentifier | None = None
    ) -> bool:
        return self.count(*criteria, shard=shard) > 0

    def count(self, *criteria: ColumnElement[bool], shard: ShardIdentifier | None = None) -> int:
        stmt = select(func.count()).select_from(self.table).where(*self._live(*criteria))
        return sum(int(r[0]) for r in self._read(stmt, shard))

    def _read(self, stmt: Any, shard: ShardIdentifier | None) -> list[Row[Any]]:
        """Run a read on one shard, or on every shard when none is named.

        A caller holding an id gets routing for free. A caller with a predicate does not - the
        predicate says nothing about where its rows live - so this fans out, and the caller
        that knows better narrows it with ``shard=``.
        """
        shards = (shard,) if shard is not None else self.registry.shards
        rows: list[Row[Any]] = []
        for target in shards:
            with self.registry.manager(target).connection(readonly=True) as conn:
                rows += list(conn.execute(stmt))
        return rows

    # ----------------------------------------------------------------- writes

    def add(self, model: M) -> M:
        return self.add_all([model])[0]

    def add_all(self, models: Sequence[M]) -> list[M]:
        """Insert, one statement per shard however many rows there are.

        A staging area that flushes row by row has thrown away its only advantage, so the batch
        goes out as one ``executemany`` and the cost of ten rows is the cost of one round trip.
        """
        if not models:
            return []
        stamped = [self._stamp_new(m) for m in models]
        for shard, group in self._group(stamped):
            with self.registry.manager(shard).connection() as conn:
                conn.execute(insert(self.table), [self.to_row(m) for m in group])
        return stamped

    def update(self, model: M) -> M:
        return self.update_all([model])[0]

    def update_all(self, models: Sequence[M]) -> list[M]:
        """Update under the optimistic lock, all of them or none.

        Each row is written ``WHERE id = ? AND version = ?`` against the version the caller
        read. If the affected count is short, someone else got there first and the whole batch
        is refused - partial application is the worst outcome available, because the caller is
        told it failed and half of it happened anyway.
        """
        if not models:
            return []
        stamped = [self._stamp_updated(m) for m in models]

        stmt = (
            update(self.table)
            .where(self.id_column == bindparam("_id"), self.table.c.version == bindparam("_prior"))
            .values(
                {
                    name: bindparam(f"_v_{name}")
                    for name in self._columns
                    if name != self.id_column.name
                }
            )
        )

        for shard, group in self._group(stamped):
            manager = self.registry.manager(shard)
            params = [
                {
                    "_id": _to_column(m.id),
                    "_prior": m.version - 1,
                    **{f"_v_{k}": v for k, v in self.to_row(m).items() if k != self.id_column.name},
                }
                for m in group
            ]
            with manager.connection() as conn:
                affected = self._execute_locked(conn, stmt, params)
                if affected != len(params):
                    # Raised inside the transaction, so it rolls back. Raised after it, the
                    # rows that did match would already be committed and the caller would be
                    # told the batch failed while half of it had happened.
                    raise StaleRecordError(
                        f"expected to update {len(params)} {self.model_type.__name__} row(s) "
                        f"but matched {affected}; one was concurrently modified or is gone",
                        model=self.model_type.__name__,
                    )
        return stamped

    def delete(self, model: M) -> M:
        """Soft delete: move the row to ``DELETED`` so every read stops seeing it."""
        state_enum = self._types.get("state")
        if not (isinstance(state_enum, type) and issubclass(state_enum, Enum)):
            raise ValidationError(f"{self.model_type.__name__} has no state enum to mark deleted")
        try:
            deleted = state_enum[DELETED]
        except KeyError:
            raise ValidationError(
                f"{state_enum.__name__} has no {DELETED} member, so this model cannot be "
                f"soft-deleted; use hard_delete if that is what you meant"
            ) from None
        return self.update(model.evolve(state=deleted))

    def hard_delete(self, key: K) -> int:
        """Remove the row for real. Rare, and deliberately harder to reach than ``delete``."""
        stmt = delete(self.table).where(self.id_column == key)
        with self.registry.manager(self.shard_for(key)).connection() as conn:
            return int(conn.execute(stmt).rowcount)

    # --------------------------------------------------------------- internals

    def _group(self, models: Sequence[M]) -> list[tuple[ShardIdentifier, list[M]]]:
        grouped: dict[ShardIdentifier, list[M]] = {}
        for model in models:
            grouped.setdefault(self.shard_for(model.id), []).append(model)
        return list(grouped.items())

    def _stamp_new(self, model: M) -> M:
        now = to_storage(self.clock.now())
        changes: dict[str, Any] = {}
        if model.created_date is None:
            changes["created_date"] = now
        if model.updated_date is None:
            changes["updated_date"] = changes.get("created_date", now)
        return model.evolve(**changes) if changes else model

    def _stamp_updated(self, model: M) -> M:
        if model.updated_date is not None:
            return model
        return model.evolve(updated_date=to_storage(self.clock.now()))

    def _execute_locked(self, conn: Any, stmt: Any, params: list[dict[str, Any]]) -> int:
        """Run the version-guarded update and report how many rows it actually matched.

        Drivers disagree on whether an ``executemany`` can report that number. Where one
        cannot, the batch is sent row by row instead - slower, and the alternative is to skip
        the check, which would turn the optimistic lock into a decoration.
        """
        if len(params) > 1 and not conn.dialect.supports_sane_multi_rowcount:
            return sum(int(conn.execute(stmt, p).rowcount) for p in params)
        return int(conn.execute(stmt, params).rowcount)


def _concrete_types(model_type: type) -> dict[str, Any]:
    """Field types with the class's type variables filled in.

    ``Wallet`` declares ``state: S``; that ``S`` is bound to ``WalletState`` by
    ``Model[UUID, WalletState]`` on the subclass. Without resolving it the mapper has no idea
    which enum to rebuild, and a state column comes back as the string it was stored as -
    equal to nothing the application compares it against.
    """
    hints = typing.get_type_hints(model_type)
    bindings: dict[Any, Any] = {}
    # Most derived first. Each class declares its own type variables, so `Persistable`'s S is
    # not `Model`'s S - the binding arrives one link at a time and resolving from the outside
    # in lets each link look up what the previous one already knew.
    for base in model_type.__mro__:
        for orig in getattr(base, "__orig_bases__", ()):
            origin = typing.get_origin(orig)
            declared = getattr(origin, "__type_params__", None)
            params: tuple[Any, ...] = tuple(
                declared if declared else getattr(origin, "__parameters__", ())
            )
            for param, arg in zip(params, typing.get_args(orig), strict=False):
                bindings[param] = bindings.get(arg, arg)
    return {name: bindings.get(hint, hint) for name, hint in hints.items()}


def _to_column(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, datetime):
        return to_storage(value)
    return value


def _from_column(value: Any, hint: Any) -> Any:
    if value is None:
        return None
    if isinstance(hint, type) and issubclass(hint, Enum) and not isinstance(value, Enum):
        return hint[str(value)]
    return value
