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
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Connection,
    Row,
    Table,
    any_,
    bindparam,
    column,
    delete,
    func,
    insert,
    literal,
    select,
    union_all,
    update,
    values,
)

from .clock import Clock, SystemClock, to_storage
from .domain import DELETED, Model, Persistable
from .errors import (
    CrossShardError,
    EntityNotFoundError,
    StaleRecordError,
    UnsupportedError,
    ValidationError,
)
from .ids import TypedValue
from .persistence import DatabaseRegistry, TransactionManager
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
        batch_size: int = 500,
    ) -> None:
        self.model_type = model_type
        self.table = table
        self.registry = registry
        self.sharding = sharding or NoSharding()
        self.clock = clock or SystemClock()
        self.batch_size = batch_size
        self._single_stmt: Any = None

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

    # ------------------------------------------------------- where the queries run

    def shard_for(self, key: K) -> ShardIdentifier:
        """The shard an id names, after falling back for one this deployment does not have."""
        self._require_id_based()
        return self.registry.effective_shard(self.sharding.shard_for_id(key))

    def shard_of(self, model: M) -> ShardIdentifier:
        """The shard a row belongs on. A strategy may read any field, not only the id."""
        return self.registry.effective_shard(self.sharding.shard_for(model))

    def shard_of_all(self, models: Sequence[M]) -> ShardIdentifier:
        """The one shard a batch belongs on, or a refusal.

        A batch that spans shards would need a transaction that spans databases, and there
        isn't one. Refusing here is what keeps a repository call from quietly becoming several
        independent commits - the caller can always split it themselves and mean it.
        """
        if isinstance(self.sharding, NoSharding) or not models:
            return self.registry.default_shard
        shards = {self.shard_of(m) for m in models}
        if len(shards) > 1:
            raise CrossShardError(sorted(str(s) for s in shards))
        return shards.pop()

    @contextmanager
    def db(
        self, *, key: K | None = None, model: M | None = None, shard: ShardIdentifier | None = None
    ) -> Iterator[Connection]:
        """A writer connection for one shard, in a transaction of its own.

        It does **not** join an open transaction. That is occasionally what you want - a write
        that must survive the surrounding rollback, an audit row - and almost always not, so
        reach for :meth:`tx_db_else_db` unless you specifically mean to be outside. Opening a
        second connection to a shard you are already writing to can also deadlock, which is the
        other reason this is the deliberate choice rather than the default one.
        """
        with self._manager(key, model, shard).primary.connect() as conn, conn.begin():
            yield conn

    def dbs(self) -> Iterator[tuple[ShardIdentifier, Connection]]:
        """Every shard's writer, one at a time, each in its own transaction.

        For work that is genuinely per-database - a backfill, a repair. Each shard commits on
        its own, so a failure part way leaves the earlier ones committed.
        """
        for shard in self.registry.shards:
            with self.db(shard=shard) as conn:
                yield shard, conn

    @contextmanager
    def readonly_db(
        self, *, key: K | None = None, shard: ShardIdentifier | None = None
    ) -> Iterator[Connection]:
        """A reader connection, which may be a replica.

        Inside an open transaction it hands back that transaction instead: a read there has to
        see the writes it has already made, and a replica by definition has not.
        """
        with self._manager(key, None, shard).connection(readonly=True) as conn:
            yield conn

    def readonly_dbs(self) -> Iterator[tuple[ShardIdentifier, Connection]]:
        """Every shard's reader, one at a time."""
        for shard in self.registry.shards:
            with self.readonly_db(shard=shard) as conn:
                yield shard, conn

    def tx_db(
        self, *, key: K | None = None, model: M | None = None, shard: ShardIdentifier | None = None
    ) -> Connection | None:
        """The transaction already open on this shard, or ``None``.

        For code that must behave differently depending on whether it is inside one. Most code
        should not care, and should call :meth:`tx_db_else_db`.
        """
        from .persistence import _open

        return _open().get(self._manager(key, model, shard).shard)

    @contextmanager
    def tx_db_else_db(
        self, *, key: K | None = None, model: M | None = None, shard: ShardIdentifier | None = None
    ) -> Iterator[Connection]:
        """The transaction open on this shard, or a short-lived one if there is none.

        What every built-in read and write uses, and what a subclass writing its own query
        should use. It is why the same repository call reads naturally inside a unit of work
        and in a script: inside one it joins, outside one it stands alone.
        """
        with self._manager(key, model, shard).connection() as conn:
            yield conn

    def dialect(self, *, shard: ShardIdentifier | None = None) -> str:
        """Which database this shard speaks, for the rare query that has to differ.

        Reach for it sparingly. A dialect difference visible in a repository is one the
        framework failed to hide, and the next dialect will need the same branch again.
        """
        return self._manager(None, None, shard).dialect

    def id_matches(self, key: K) -> Any:
        """``id = ?``, with a typed id unwrapped on the way to the column.

        For a subclass writing its own statement. ``self.id_column == key`` would hand the
        wrapper straight to the driver, which knows nothing about it.
        """
        return self.id_column == _to_column(key)

    def column(self, name: str) -> Any:
        """One of this table's columns, by name - for building a predicate in a subclass
        without importing the table."""
        try:
            return self.table.c[name]
        except KeyError:
            raise ValidationError(
                f"{self.table.name} has no column {name!r}; it has {sorted(self.table.c.keys())}"
            ) from None

    def _manager(
        self, key: K | None, model: M | None, shard: ShardIdentifier | None
    ) -> TransactionManager:
        if shard is not None:
            return self.registry.manager(self.registry.effective_shard(shard))
        if model is not None:
            return self.registry.manager(self.shard_of(model))
        if key is not None:
            return self.registry.manager(self.shard_for(key))
        return self.registry.manager(self.registry.default_shard)

    def _require_id_based(self) -> None:
        if not self.sharding.uses_shard_aware_id:
            raise UnsupportedError(
                f"{type(self.sharding).__name__} cannot resolve a shard from an id alone, so "
                f"{type(self).__name__} has to override shard_for() to say where a row lives."
            )

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
        stmt = select(self.table).where(*self._live(self.id_column == _to_column(key)))
        with self.readonly_db(key=key) as conn:
            row = conn.execute(stmt).first()
        return self.from_row(row) if row is not None else None

    def get(self, key: K) -> M:
        """The row, or raise. The lookup for callers that cannot continue without it."""
        found = self.find(key)
        if found is None:
            raise EntityNotFoundError(self.model_type.__name__, key)
        return found

    def find_many(self, keys: Iterable[K]) -> list[M]:
        """The rows that exist, in no promised order.

        Duplicates are collapsed before the query - asking twice for the same id is a caller's
        accident, not an instruction to fetch it twice. An empty input is an empty result:
        never ``IN ()``, and never an accidental full-table read.
        """
        unique = list(dict.fromkeys(_to_column(k) for k in keys))
        if not unique:
            return []

        by_shard: dict[ShardIdentifier, list[Any]] = {}
        for key in unique:
            by_shard.setdefault(self.shard_for(key), []).append(key)

        found: list[M] = []
        for shard, shard_keys in by_shard.items():
            with self.readonly_db(shard=shard) as conn:
                for chunk in _chunks(shard_keys, self.batch_size):
                    stmt = select(self.table).where(*self._live(self._id_among(conn, chunk)))
                    found += [self.from_row(r) for r in conn.execute(stmt)]
        return found

    def _id_among(self, conn: Any, keys: Sequence[Any]) -> Any:
        """``id = ANY(:array)`` on Postgres, ``id IN (…)`` elsewhere.

        Not a stylistic preference. ``IN`` expands to one placeholder per id, so the statement
        text changes with the length of the list and a server-side plan cache misses on every
        new length - a service looking up two ids, then three, then two, re-plans each time.
        ``ANY`` takes the whole list as a single array parameter: one statement text, one
        cached plan, one bind. Only Postgres has it, so everything else keeps ``IN`` and the
        chunking above keeps that from meeting a parameter limit.
        """
        if conn.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import ARRAY

            return self.id_column == any_(literal(list(keys), ARRAY(self.id_column.type)))
        return self.id_column.in_(keys)

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
            select(func.count())
            .select_from(self.table)
            .where(*self._live(self.id_column == _to_column(key)))
        )
        with self.readonly_db(key=key) as conn:
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
        if shard is not None:
            with self.readonly_db(shard=shard) as conn:
                return list(conn.execute(stmt))
        return [row for _, conn in self.readonly_dbs() for row in conn.execute(stmt)]

    # ----------------------------------------------------------------- writes

    def add(self, model: M) -> M:
        """Insert one row, and hand back what was written."""
        return self.add_all([model])[0]

    def add_no_result(self, model: M) -> None:
        """Insert one row without building the result.

        Present because the Java implementation distinguishes them - there, returning the row
        costs a ``RETURNING`` clause or a re-read. Here the value is already in hand and this
        saves nothing, so use it to say the result is not wanted, not to go faster.
        """
        self.add_all([model])

    def add_all(self, models: Sequence[M]) -> list[M]:
        """Insert, one statement however many rows there are.

        A staging area that flushes row by row has thrown away its only advantage, so the batch
        goes out as one ``executemany`` and the cost of ten rows is the cost of one round trip.
        """
        if not models:
            return []
        shard = self.shard_of_all(models)
        stamped = [self._stamp_new(m) for m in models]
        with self.tx_db_else_db(shard=shard) as conn:
            conn.execute(insert(self.table), [self.to_row(m) for m in stamped])
        return stamped

    def add_all_no_result(self, models: Sequence[M]) -> None:
        self.add_all(models)

    def update(self, model: M) -> M:
        """Update one row under the optimistic lock."""
        return self.update_all([model])[0]

    def update_no_result(self, model: M) -> None:
        self.update_all([model])

    def update_all(self, models: Sequence[M]) -> list[M]:
        """Update under the optimistic lock, all of them or none, in one statement per chunk.

        Not ``executemany``. SQLAlchemy rewrites a batched INSERT into a single multi-row
        statement, but there is no equivalent for UPDATE - an executemany there is N statements,
        pipelined at best, and N server-side plans. So the batch is turned into one derived
        table joined against the target, which is one statement, one plan, and one pass.

        Each row is matched on ``id`` and the version the caller read. If the affected count is
        short, someone else got there first and the whole batch is refused - partial application
        is the worst outcome available, because the caller is told it failed and half of it
        happened anyway. The count is trustworthy because every row's version changes, so no
        row can be silently reported as unaffected for having been written its own values.
        """
        if not models:
            return []
        shard = self.shard_of_all(models)
        stamped = [self._stamp_updated(m) for m in models]

        with self.tx_db_else_db(shard=shard) as conn:
            if len(stamped) == 1:
                # One row does not need a derived table, and building one would cost more than
                # it saves.
                affected = conn.execute(self._single_update(), self._bind(stamped[0])).rowcount
            else:
                affected = sum(
                    self._batch_update(conn, chunk) for chunk in _chunks(stamped, self.batch_size)
                )
            if affected != len(stamped):
                # Raised inside the transaction, so it rolls back. Raised after it, the rows
                # that did match would already be committed and the caller would be told the
                # batch failed while half of it had happened.
                raise StaleRecordError(
                    f"expected to update {len(stamped)} {self.model_type.__name__} row(s) "
                    f"but matched {affected}; one was concurrently modified or is gone",
                    model=self.model_type.__name__,
                )
        return stamped

    def update_all_no_result(self, models: Sequence[M]) -> None:
        self.update_all(models)

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
        stmt = delete(self.table).where(self.id_column == _to_column(key))
        with self.tx_db_else_db(key=key) as conn:
            return int(conn.execute(stmt).rowcount)

    # --------------------------------------------------------------- internals

    def _stamp_new(self, model: M) -> M:
        """Fill in the timestamps a model has and an entity does not."""
        if not isinstance(model, Model):
            return model
        now = to_storage(self.clock.now())
        changes: dict[str, Any] = {}
        if model.created_date is None:
            changes["created_date"] = now
        if model.updated_date is None:
            changes["updated_date"] = changes.get("created_date", now)
        return model.evolve(**changes) if changes else model

    def _stamp_updated(self, model: M) -> M:
        if not isinstance(model, Model) or model.updated_date is not None:
            return model
        return model.evolve(updated_date=to_storage(self.clock.now()))

    def _single_update(self) -> Any:
        """``UPDATE … SET … WHERE id = ? AND version = ?``, built once and reused."""
        if self._single_stmt is None:
            self._single_stmt = (
                update(self.table)
                .where(
                    self.id_column == bindparam("_id"),
                    self.table.c.version == bindparam("_prior"),
                )
                .values(
                    {
                        name: bindparam(f"_v_{name}")
                        for name in self._columns
                        if name != self.id_column.name
                    }
                )
            )
        return self._single_stmt

    def _bind(self, model: M) -> dict[str, Any]:
        row = self.to_row(model)
        return {
            "_id": row[self.id_column.name],
            "_prior": model.version - 1,
            **{f"_v_{k}": v for k, v in row.items() if k != self.id_column.name},
        }

    def _batch_update(self, conn: Any, models: Sequence[M]) -> int:
        """One UPDATE for the whole chunk, joined against a derived table of the new values.

        Two shapes, because the dialects genuinely differ. Postgres has a ``VALUES`` table
        constructor with named columns, and its planner reads one scan node from it. MySQL's
        equivalent needs ``VALUES ROW(...)`` and only from 8.0.19, and SQLite's cannot be given
        column names at all - so everything else gets a ``UNION ALL`` of selects, which every
        version of every one of them understands.

        Every value is bound against its target column's type, never inferred from the value's
        runtime class. Inference is how a timestamp acquires a zone it never had and the
        database shifts it by the session offset - invisibly, and only outside UTC.
        """
        prior = "_prior_version"
        names = [prior, *self._columns]
        rows = [(m.version - 1, *(self.to_row(m)[name] for name in self._columns)) for m in models]
        types = {prior: self.table.c.version.type} | {
            name: col.type for name, col in self._columns.items()
        }

        source: Any
        if conn.dialect.name != "postgresql":
            source = union_all(
                *[
                    select(
                        *[
                            literal(value, types[name]).label(name)
                            for name, value in zip(names, row, strict=True)
                        ]
                    )
                    for row in rows
                ]
            ).subquery("v")
        else:
            source = values(*[column(name, types[name]) for name in names], name="v").data(rows)

        assigned = {name: source.c[name] for name in self._columns if name != self.id_column.name}
        stmt = (
            update(self.table)
            .values(assigned)
            .where(
                self.id_column == source.c[self.id_column.name],
                self.table.c.version == source.c[prior],
            )
        )
        return int(conn.execute(stmt).rowcount)


def _chunks[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    """A statement carries one bind parameter per column per row, and every server has a
    ceiling on how many it will take - 65535 on Postgres, a packet size on MySQL. Chunking is
    what keeps a large batch from becoming a failure that only appears in production."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


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
    if isinstance(value, TypedValue):
        return _to_column(value.value)
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, datetime):
        return to_storage(value)
    return value


def _from_column(value: Any, hint: Any) -> Any:
    if value is None:
        return None
    # `Id[Wallet]` is a generic alias, so the class to rebuild is its origin.
    origin = typing.get_origin(hint) or hint
    if isinstance(origin, type) and issubclass(origin, TypedValue):
        return value if isinstance(value, origin) else origin(value)
    if isinstance(hint, type) and issubclass(hint, Enum) and not isinstance(value, Enum):
        return hint[str(value)]
    return value
