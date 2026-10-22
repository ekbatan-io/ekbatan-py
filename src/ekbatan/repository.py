"""Reading and writing rows.

A repository is bound to one model and one table. It routes by shard, enforces the optimistic
lock, hides soft-deleted rows from every read path, and maps between rows and models through the
``to_record`` / ``from_record`` every repository writes, as every Java one writes ``toRecord`` /
``fromRecord``. A row is a *record*: a ``TypedDict`` with a key per column - ``WalletsRecord``,
which ``schemagen`` writes beside ``WALLETS`` - so the mapping is checked by a type checker.

The names are Java's. :class:`Repository` is the promise - what the framework asks of a
repository, and all it asks. :class:`AbstractRepository` keeps that promise against a table, and
:class:`ModelRepository` / :class:`EntityRepository` are what an application extends.

It has no opinion about transactions, and opens none. If one is open on the shard it joins it;
if not, each statement stands alone and commits itself. That is what lets the same call read
naturally in a unit of work and in a script, and it is why deciding when something is atomic
stays with the caller rather than being decided here.
"""

from __future__ import annotations

import dataclasses
import sys
import types
import typing
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, Self, cast

from sqlalchemy import (
    ColumnElement,
    Row,
    Table,
    any_,
    bindparam,
    column,
    func,
    insert,
    literal,
    null,
    select,
    union_all,
    update,
    values,
)
from sqlalchemy import cast as sql_cast

from ._tracing import span
from .clock import Clock, SystemClock
from .domain import DELETED, Entity, Model, Persistable
from .errors import (
    CrossShardError,
    EkbatanError,
    EntityNotFoundError,
    StaleRecordError,
    UnsupportedError,
    ValidationError,
)
from .fetching import Db
from .ids import TypedValue
from .persistence import DatabaseRegistry, _ShardRegistry
from .sharding import NoShardingStrategy, ShardIdentifier, ShardingStrategy

if TYPE_CHECKING:
    from .aio.repository import AsyncRepository

__all__ = [
    "EntityRepository",
    "ModelRepository",
    "Repository",
    "RepositoryRegistry",
    "RepositoryRegistryBuilder",
]

#: The column carrying the version each row was read at, in a batch update's derived table.
_PRIOR = "_prior_version"

#: Large enough that SQLAlchemy never pages a RETURNING insert of its own accord.
_NO_PAGING = 1_000_000

#: SQLAlchemy otherwise splits a RETURNING insert into pages of a thousand rows. That is a
#: batching decision, and batching is the caller's - three pages outside a transaction are three
#: commits, so a failure on the third would leave two thirds written from a split nobody asked
#: for. One statement, or the server's parameter ceiling, same as the other port.
_ONE_STATEMENT = {"insertmanyvalues_page_size": _NO_PAGING}


def _no_row_mapping(target: type, missing: Sequence[str]) -> str:
    """Java declares both halves abstract; this refuses the repository where it is created.

    `AbstractRepository.fromRecord` and `toRecord` are abstract there, so a repository that has
    not written them does not compile. There is no compile step here; the nearest thing is
    refusing to create one, which is where Python's own abstract methods refuse too.

    Until `EKB-0217` the mapping defaulted to field name unless a repository said otherwise,
    which is the one piece of magic that was left in the read and write paths: rename a column
    without renaming the field and it silently stops matching. The by-name mapping is still
    here - it is one line and a dataclass already knows its fields - but now it is chosen
    rather than inherited.
    """
    name = target.__name__
    written = " and ".join(f"{m}()" for m in missing)
    verb = "has" if len(missing) == 1 else "have"
    return (
        f"{name} cannot be created: {written} {verb} not been written, and the mapping between "
        f"a row and a model is not guessed here.\n"
        f"\n"
        f"Java declares `fromRecord` and `toRecord` abstract on `AbstractRepository`, so every "
        f"repository writes both. Write both here too:\n"
        f"\n"
        f"  class {name}(...):\n"
        f"      def to_record(self, model) -> TableRecord:\n"
        f"          return self.columns_by_field_name(model)\n"
        f"\n"
        f"      def from_record(self, record: TableRecord) -> Model:\n"
        f"          return self.model_by_field_name(record)\n"
        f"\n"
        f"`TableRecord` is the table's record - `WalletsRecord` for `wallets`, which schemagen "
        f"writes beside the table.\n"
        f"\n"
        f"`columns_by_field_name` and `model_by_field_name` map every declared field to the "
        f"column of the same name - which is what this used to do without being asked. Writing "
        f"the two lines is the difference between a mapping somebody chose and one nobody can "
        f"find. Where a name or a shape differs, write the mapping out instead."
    )


class Repository[M: Persistable[Any, Any]](Protocol):
    """What the framework asks of a repository - Java's ``Repository`` interface.

    The registry, the executor and the change persister depend on this and on nothing else, so
    anything that has these members can be registered, as anything implementing Java's interface
    can - a repository that is not backed by SQL at all among them.

    Java's ten methods, in snake case, with ``find_all`` spelled ``all``. Three more that this
    port's plumbing reads where Java's reads something else: ``model_type``, because the registry
    here learns the type from the repository rather than being handed it; ``sharding``, which is
    Java's ``shardingStrategy()``; and ``shard_of``, where Java's executor asks the strategy
    itself.

    **Not a base class to extend for a table.** Extend :class:`AbstractRepository` - or, as an
    application does, :class:`ModelRepository` / :class:`EntityRepository`. A class that
    extends this one and does not write every method cannot be instantiated.
    """

    model_type: type[M]
    sharding: ShardingStrategy

    def __class_getitem__(cls, params: Any) -> Any:
        # `Repository[Wallet, WalletId]` was this port's base class, with two parameters. It is
        # Java's one-parameter interface now, and the bare "too many arguments" Python would say
        # does not tell anyone what moved where.
        if cls is Repository and isinstance(params, tuple) and len(params) == 2:
            raise TypeError(
                "Repository takes one type argument now - it is Java's Repository interface, the "
                "promise the framework asks of a repository. The class that implements it is "
                "AbstractRepository[Model, Record, Id]; an application extends "
                "ModelRepository[Model, Record, Id] or EntityRepository[Model, Record, Id]."
            )
        return super().__class_getitem__(params)  # type: ignore[misc]

    @abstractmethod
    def shard_of(self, model: M) -> ShardIdentifier: ...

    @abstractmethod
    def add(self, model: M) -> M: ...

    @abstractmethod
    def add_no_result(self, model: M) -> None: ...

    @abstractmethod
    def add_all(self, models: Sequence[M]) -> list[M]: ...

    @abstractmethod
    def add_all_no_result(self, models: Sequence[M]) -> None: ...

    @abstractmethod
    def update(self, model: M) -> M: ...

    @abstractmethod
    def update_no_result(self, model: M) -> None: ...

    @abstractmethod
    def update_all(self, models: Sequence[M]) -> list[M]: ...

    @abstractmethod
    def update_all_no_result(self, models: Sequence[M]) -> None: ...

    @abstractmethod
    def all(self, *, shard: ShardIdentifier | None = None) -> list[M]: ...


class _RepositoryCore[M: Persistable[Any, Any], R: Mapping[str, Any], K](ABC):
    """Everything a repository is apart from waiting on the database - shared by
    :class:`AbstractRepository` and ``ekbatan.aio``'s ``AbstractAsyncRepository``.

    The construction checks, the mapping, the shard routing, the soft-delete filter, every
    statement and what its answer means: one copy. A subclass adds only the methods that run a
    statement, and those are the only lines in which the sync and async repositories differ.
    """

    #: Named in the refusal a half-written subclass gets.
    _EXTEND = "ModelRepository or EntityRepository"

    def __new__(cls, *args: Any, **kwargs: Any) -> Self:
        # Python's own refusal names the missing methods and stops there. This one names them and
        # says what to write, which is the part a reader needs - still a TypeError, as Python's is.
        missing = sorted(getattr(cls, "__abstractmethods__", ()))
        if missing:
            if set(missing) <= {"to_record", "from_record"}:
                raise TypeError(_no_row_mapping(cls, missing))
            raise TypeError(
                f"{cls.__name__} cannot be created: it does not implement {', '.join(missing)}. "
                f"Extend {cls._EXTEND}, which implement everything but "
                f"to_record and from_record."
            )
        return super().__new__(cls)

    def __init__(
        self,
        model_type: type[M],
        table: Table,
        registry: _ShardRegistry[Any],
        *,
        sharding: ShardingStrategy | None = None,
        clock: Clock | None = None,
        state_column: str = "state",
    ) -> None:
        self.model_type = model_type
        self.table = table
        self.registry = registry
        self.sharding = sharding or NoShardingStrategy()
        self.clock = clock or SystemClock()
        self._single_stmts: dict[tuple[tuple[str, ...], bool], Any] = {}
        self._union_stmts: dict[tuple[int, bool, tuple[str, ...]], Any] = {}
        self._column_sets: dict[frozenset[str], tuple[str, ...]] = {}

        pk = list(table.primary_key.columns)
        if len(pk) != 1:
            raise ValidationError(
                f"{table.name} has {len(pk)} primary key columns; a repository needs exactly one"
            )
        self.id_column = pk[0]
        self.state_column = table.c.get(state_column)

        self._fields = {f.name: f for f in dataclasses.fields(model_type)}
        self._types = _concrete_types(model_type)
        # The fields that have a column of the same name. Only the by-name mapping
        # (`columns_by_field_name` / `model_by_field_name`) reads this. What a write sends is
        # the record `to_record` returns, key for key - never this. Until `EKB-0480` an update
        # wrote only these columns, so a column mapped from a field of another name was
        # inserted by `add` and silently never updated.
        self._same_name_columns = {n: table.c[n] for n in self._fields if n in table.c}
        missing = [n for n in ("id", "version") if n not in table.c]
        if missing:
            raise ValidationError(f"{table.name} has no column(s) named {missing}")
        if self.state_column is None:
            # A row without its state is not a row this framework can write. Unmapped, the
            # column is dropped from every INSERT in silence, no read filters soft-deleted
            # rows, and the first thing anyone hears about it is a constructor blowing up on
            # a row read back later - by which time the rows are already wrong.
            raise ValidationError(
                f"{table.name} has no column named {state_column!r}; every row carries a "
                f"lifecycle state and it has to be stored. Pass state_column= if the column "
                f"is named something else."
            )
        _must_hold(table.c["version"], int, "an integer", "the optimistic lock counts on it")
        _must_hold(self.state_column, str, "text", "reads compare it against 'DELETED'")
        if issubclass(model_type, Model):
            # A model carries timestamps, so its table has to have somewhere to put them.
            # Checked here, at construction, because the alternative is a row written without
            # them - or an insert that fails on the first write of the day, in production,
            # far from the mapping that was wrong.
            absent = [n for n in ("created_date", "updated_date") if n not in table.c]
            if absent:
                raise ValidationError(
                    f"{model_type.__name__} is a Model, so {table.name} must have "
                    f"{absent} - a model records when it changed. An Entity does not; if "
                    f"this is one, subclass Entity rather than Model."
                )

        # Which conversion each column needs is a property of the model, not of the row, so it
        # is decided once here rather than re-derived per field per row. Reading ten thousand
        # rows otherwise runs ninety thousand isinstance chains and as many typing.get_origin
        # calls, for an answer that never changes.
        self._to_col = {
            name: _writer_for(self._types.get(name)) for name in self._same_name_columns
        }
        self._from_col = {
            name: _reader_for(self._types.get(name)) for name in self._same_name_columns
        }

    # ---------------------------------------------------------------- mapping

    @abstractmethod
    def to_record(self, model: M) -> R:
        """The model as its table's record. **Write this.**

        Java's `AbstractRepository` declares `toRecord` abstract and every repository there
        implements it. The record is the table's ``TypedDict`` - ``WalletsRecord``, generated
        beside ``WALLETS`` - so a column left out, misspelt or given the wrong type is a type
        error, as it is a compile error in Java::

            def to_record(self, wallet: Wallet) -> WalletsRecord:
                return WalletsRecord(id=wallet.id.value, version=wallet.version, ...)

        Build it with the record class's keyword form. A column whose name is not a valid
        Python identifier, or is a keyword (``order date``, ``class``, a name starting
        ``__``), cannot be a keyword argument, so such a record is built as a dict literal -
        both forms produce the same ``dict``, and mypy checks both.

        Where the names line up, say so in a line rather than inheriting it in silence:
        ``return self.columns_by_field_name(wallet)``.
        """
        raise NotImplementedError(_no_row_mapping(type(self), ["to_record"]))

    @abstractmethod
    def from_record(self, record: R) -> M:
        """A record as a model. **Write this**, alongside :meth:`to_record`.

        The counterpart to Java's abstract `fromRecord`. The framework hands it a row read by
        column name - a result row's mapping, or, where an insert or update has no
        ``RETURNING``, the record :meth:`to_record` wrote::

            def from_record(self, record: WalletsRecord) -> Wallet:
                return Wallet(id=ShardedId[Wallet](record["id"]), ...)
        """
        raise NotImplementedError(_no_row_mapping(type(self), ["from_record"]))

    # ------------------------------------------- the by-name mapping, asked for rather than assumed

    def columns_by_field_name(self, model: M) -> R:
        """Every declared field, under the column of the same name, as the table's record.

        What `to_record` used to do without being asked. A dataclass knows its own fields, so
        this needs writing once in the framework rather than once per repository - but
        *choosing* it is a line in your repository, and a column that stops matching is then a
        line somebody can find. The type checker takes the record on trust here - it is built
        from the fields at run time - so a mapping written out key by key is the checked one.
        """
        return cast(R, {name: write(getattr(model, name)) for name, write in self._to_col.items()})

    def model_by_field_name(self, record: R) -> M:
        """Every column, into the field of the same name. The counterpart to
        :meth:`columns_by_field_name`."""
        mapping: Mapping[str, Any] = record
        try:
            # The common case is a full row, and asking "is this column present?" once per
            # column per row is forty thousand questions to read five thousand rows, all with
            # the same answer.
            kwargs = {name: read(mapping[name]) for name, read in self._from_col.items()}
        except KeyError:
            kwargs = {
                name: read(mapping[name])
                for name, read in self._from_col.items()
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
        if isinstance(self.sharding, NoShardingStrategy) or not models:
            return self.registry.default_shard
        shards = {self.shard_of(m) for m in models}
        if len(shards) > 1:
            first, second, *_ = sorted(shards)
            raise CrossShardError(first, second)
        return shards.pop()

    def dialect(self, *, shard: ShardIdentifier | None = None) -> str:
        """Which database this shard speaks, for the rare query that has to differ.

        Reach for it sparingly. A dialect difference visible in a repository is one the
        framework failed to hide, and the next dialect will need the same branch again.
        """
        dialect: str = self._manager(None, None, shard).dialect
        return dialect

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

    def _manager(self, key: K | None, model: M | None, shard: ShardIdentifier | None) -> Any:
        """The manager for whichever of shard, row or id was given - a sync one or an async
        one, as the registry holds."""
        if shard is not None:
            return self.registry.manager(self.registry.effective_shard(shard))
        if model is not None:
            return self.registry.manager(self.shard_of(model))
        if key is not None:
            return self.registry.manager(self.shard_for(key))
        return self.registry.manager(self.registry.default_shard)

    def _require_id_based(self) -> None:
        # A single-database deployment resolves nothing from the id and needs to resolve
        # nothing: every row is on the one shard. Java's `rejectNonIdBasedStrategy` opens with
        # the same check, for the same reason.
        if isinstance(self.sharding, NoShardingStrategy):
            return
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

    def _id_among(self, conn: Any, keys: Sequence[Any]) -> Any:
        """``id = ANY(:array)`` on Postgres, ``id IN (…)`` elsewhere.

        Not a stylistic preference. ``IN`` expands to one placeholder per id, so the statement
        text changes with the length of the list and a server-side plan cache misses on every
        new length - a service looking up two ids, then three, then two, re-plans each time.
        ``ANY`` takes the whole list as a single array parameter: one statement text, one
        cached plan, one bind. Only Postgres has it, so everything else keeps ``IN`` - where a
        long enough list will meet the server's parameter limit, and the caller splits it.
        """
        if conn.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import ARRAY

            return self.id_column == any_(literal(list(keys), ARRAY(self.id_column.type)))
        return self.id_column.in_(keys)

    def _presence(self, *criteria: ColumnElement[bool]) -> Any:
        return select(literal(1)).select_from(self.table).where(*self._live(*criteria)).limit(1)

    def _kind(self) -> str:
        """Java's ``getDomainTypeName()``: what its null messages call the row."""
        return "Model" if issubclass(self.model_type, Model) else "Entity"

    def _one(self, model: M) -> M:
        """Java's ``Validate.notNull(domainObject, "Model cannot be null")``."""
        if model is None:
            raise ValidationError(f"{self._kind()} cannot be null")
        return model

    def _all(self, models: Sequence[M]) -> Sequence[M]:
        """Java's checks on a batch: the collection, then each element.

        ``None`` is refused rather than read as empty - a caller's bug, shown where it is. A
        batch of one is checked as Java's is, which hands it to the single-row call.
        """
        if models is None:
            name = "Entities" if self._kind() == "Entity" else "Models"
            raise ValidationError(f"{name} collection cannot be null")
        if len(models) == 1:
            self._one(models[0])
        elif any(model is None for model in models):
            raise ValidationError(f"{self._kind()} in collection cannot be null")
        return models

    @contextmanager
    def _writing(self, operation: str, size: int) -> Iterator[None]:
        """A span around a batch write, named as the Java implementation names it.

        These two calls are what a unit of work spends its time on, so a persist span with no
        children under it tells an operator that the write was slow and nothing about which
        table, or how many rows.
        """
        with span(
            "ekbatan.repository",
            {
                "db.operation.name": operation,
                "ekbatan.entity.type": self.model_type.__name__,
                "ekbatan.batch.size": size,
            },
        ):
            yield

    # --------------------------------------------------------------- internals

    def _next_version(self, model: M) -> M:
        """Move the version on. Nothing else.

        The repository owns the optimistic lock, so the repository moves the version: a
        caller hands in the row as they read it and gets it back one version later. Asking
        them to move it first would mean the obvious call - ``update(wallet.deposit(10))`` -
        was the one that fails, and it would fail as a lost race, which it is not.

        ``updated_date`` is **not** touched, as Java does not touch it: nothing in
        ``ekbatan-core`` writes ``updatedDate``, and ``nextVersion()`` carries whatever the
        row held forward. A framework that quietly rewrote a column the application set is a
        framework you cannot use to record when *your* change happened - so it is yours to
        set, through ``copy().updated_date(...)``, on the changes where it means something.
        """
        return model.next_version()

    def _written_columns(
        self, records: Sequence[Mapping[str, Any]], *, updating: bool
    ) -> tuple[str, ...]:
        """The columns a write sends: every key the records carry, in the table's order.

        Taken from the records ``to_record`` returned - never from the model's field names - so a
        column mapped from a field of another name is written like any other, by an update
        exactly as by an insert. Java's update is built from ``toRecord(model)`` the same way. A
        column the table has and the record leaves out (one the database fills) is not written.

        One statement writes one set of columns, so every record in a batch has to carry the
        same keys. A batch that does not is refused: binding the first record's keys and
        dropping the rest is how a value goes missing without a word.
        """
        first = records[0]
        for i, record in enumerate(records):
            if record.keys() != first.keys():
                missing = sorted(set(first) - set(record))
                extra = sorted(set(record) - set(first))
                differs = " and ".join(
                    part
                    for part in (
                        f"lacks {missing}" if missing else "",
                        f"has {extra} besides" if extra else "",
                    )
                    if part
                )
                raise ValidationError(
                    f"to_record returned different columns for rows of one "
                    f"{self.model_type.__name__} batch: row {i} {differs} what row 0 has. One "
                    f"statement writes one set of columns, so to_record has to return the same "
                    f"keys for every row of a batch; nothing was written."
                )
        keys = frozenset(first)
        columns = self._column_sets.get(keys)
        if columns is None:
            unknown = sorted(keys - set(self.table.c.keys()))
            if unknown:
                raise ValidationError(
                    f"to_record returned {unknown}, which {self.table.name} has no column for; "
                    f"it has {sorted(self.table.c.keys())}"
                )
            columns = tuple(c.key for c in self.table.c if c.key in keys)
            if len(self._column_sets) < 32:
                self._column_sets[keys] = columns
        if updating:
            absent = [n for n in (self.id_column.name, "version") if n not in keys]
            if absent:
                raise ValidationError(
                    f"to_record returned no {absent} for a {self.model_type.__name__} being "
                    f"updated; an update finds its row by id and moves the version on, so the "
                    f"record has to carry both."
                )
        return columns

    def _single_update(self, columns: tuple[str, ...], *, returning: bool = False) -> Any:
        """``UPDATE … SET … WHERE id = ? AND version = ?``, built once per column set and reused."""
        stmt = self._single_stmts.get((columns, returning))
        if stmt is None:
            stmt = (
                update(self.table)
                .where(
                    self.id_column == bindparam("_id"),
                    self.table.c.version == bindparam("_prior"),
                )
                .values(
                    {
                        name: bindparam(f"_v_{name}")
                        for name in columns
                        if name != self.id_column.name
                    }
                )
            )
            if returning:
                stmt = stmt.returning(self.table)
            if len(self._single_stmts) < 32:
                self._single_stmts[(columns, returning)] = stmt
        return stmt

    def _read_back(self, row: Row[Any]) -> M:
        """A result row, as a model: :meth:`from_record` is handed the row's mapping, read by
        column name. The row's own mapping, not a copy - reading ten thousand rows makes no
        dicts."""
        return self.from_record(cast(R, row._mapping))

    def _bind(
        self, model: M, record: Mapping[str, Any], columns: tuple[str, ...]
    ) -> dict[str, Any]:
        return {
            "_id": record[self.id_column.name],
            "_prior": model.version - 1,
            **{f"_v_{name}": record[name] for name in columns if name != self.id_column.name},
        }

    def _derived_types(self, columns: tuple[str, ...]) -> dict[str, Any]:
        return {_PRIOR: self.table.c.version.type} | {
            name: self.table.c[name].type for name in columns
        }

    def _assignments(self, source: Any, columns: tuple[str, ...]) -> dict[str, Any]:
        return {name: source.c[name] for name in columns if name != self.id_column.name}

    def _joined(self, source: Any, columns: tuple[str, ...]) -> Any:
        return (
            update(self.table)
            .values(self._assignments(source, columns))
            .where(
                self.id_column == source.c[self.id_column.name],
                self.table.c.version == source.c[_PRIOR],
            )
        )

    def _values_update(
        self, models: Sequence[M], records: Sequence[Mapping[str, Any]], columns: tuple[str, ...]
    ) -> Any:
        """Postgres: ``UPDATE … FROM (VALUES …) AS v (cols)``.

        A ``None`` is cast rather than passed through. SQLAlchemy renders a bare Python ``None``
        inside ``VALUES`` as the literal ``NULL`` - not as a bound parameter - so the column's
        declared type is dropped on the floor, and Postgres then types the derived column by
        inference. With a nullable column that is null in *every* row of the batch there is
        nothing to infer from, Postgres settles on ``text``, and the assignment fails with
        "column is of type integer but expression is of type text".

        It only bites on a column whose type is not text, which is why it survived a suite that
        had one nullable text column and no other: an uncast NULL infers as text, so the one
        case anybody had written happened to be the one case that works.
        """
        types = self._derived_types(columns)
        rows = [
            tuple(
                sql_cast(null(), types[name]) if value is None else value
                for name, value in zip(types, raw, strict=True)
            )
            for raw in (
                (m.version - 1, *(record[name] for name in columns))
                for m, record in zip(models, records, strict=True)
            )
        ]
        source = values(*[column(name, types[name]) for name in types], name="v").data(rows)
        return self._joined(source, columns)

    def _union_update(self, size: int, columns: tuple[str, ...]) -> Any:
        """Everyone else: ``UPDATE … JOIN (SELECT … UNION ALL SELECT …) v``, bound by name."""
        types = self._derived_types(columns)
        source = union_all(
            *[
                select(
                    *[
                        bindparam(f"b{i}_{name}", type_=coltype).label(name)
                        for name, coltype in types.items()
                    ]
                )
                for i in range(size)
            ]
        ).subquery("v")
        return self._joined(source, columns)

    def _union_binds(
        self, models: Sequence[M], records: Sequence[Mapping[str, Any]], columns: tuple[str, ...]
    ) -> dict[str, Any]:
        binds: dict[str, Any] = {}
        for i, (model, record) in enumerate(zip(models, records, strict=True)):
            binds[f"b{i}_{_PRIOR}"] = model.version - 1
            for name in columns:
                binds[f"b{i}_{name}"] = record[name]
        return binds

    # ------------------------------------------- what a read or a write is, before it runs
    #
    # Everything below decides *what* to send and *what the answer means*, and runs nothing.
    # The sync repository here and the async one in ``ekbatan.aio`` both call these, so the
    # statements, the routing and the optimistic-lock rules exist once; the two differ only in
    # whether they await the connection.

    def _scatter_shards(self) -> tuple[ShardIdentifier, ...]:
        """The shards a read with no shard named visits.

        An unsharded repository visits **only the default shard**, even when the registry holds
        more - the same guard Java applies in ``dbs()`` and ``readonlyDbs()``. Without it a
        repository that opted out of sharding would still scatter across every database a
        sibling repository happens to use.
        """
        if isinstance(self.sharding, NoShardingStrategy):
            return (self.registry.default_shard,)
        return tuple(self.registry.shards)

    def _shards(self, shard: ShardIdentifier | None) -> tuple[ShardIdentifier, ...]:
        """The shard named, or every shard a read with none named visits."""
        return (shard,) if shard is not None else self._scatter_shards()

    def _shard_failure(
        self,
        shards: Sequence[ShardIdentifier],
        failures: Mapping[ShardIdentifier, Exception],
        *,
        cancelled: bool = False,
    ) -> Exception:
        """The first failed shard's error, in shard order, with notes saying which shard it
        was and which others failed too - by kind only, never by message. What a read across
        more than one shard raises, in both modes; ``cancelled`` says the reads still running
        on the other shards were stopped, which only the async read can do."""
        failed = [shard for shard in shards if shard in failures]
        first = failures[failed[0]]
        note = (
            f"ekbatan: reading shard {failed[0]} ({self.registry.manager(failed[0]).label}) "
            f"failed, so the whole read failed"
        )
        if cancelled:
            note += " and the reads still running on the other shards were cancelled"
        first.add_note(note)
        for other in failed[1:]:
            first.add_note(f"ekbatan: shard {other} failed too: {type(failures[other]).__name__}")
        return first

    def _by_id(self, key: K) -> Any:
        """``SELECT … WHERE id = ?`` on a live row."""
        return select(self.table).where(*self._live(self.id_column == _to_column(key)))

    def _where(self, *criteria: ColumnElement[bool]) -> Any:
        """``SELECT … WHERE …`` on live rows."""
        return select(self.table).where(*self._live(*criteria))

    def _one_where(self, *criteria: ColumnElement[bool]) -> Any:
        """``find_one_where``'s statement. Two rows, not one: a query for a single row can never
        report that there was a second."""
        return select(self.table).where(*self._live(*criteria)).limit(2)

    def _unique(self, rows: Sequence[Row[Any]]) -> M | None:
        """What one shard's answer to :meth:`_one_where` means: the row, none, or a refusal."""
        if len(rows) > 1:
            raise ValidationError(
                f"find_one_where matched more than one {self.model_type.__name__}: the "
                f"predicate is not unique. Use find_where when several rows are expected."
            )
        return self._read_back(rows[0]) if rows else None

    def _keys_by_shard(self, keys: Iterable[K]) -> dict[ShardIdentifier, list[Any]]:
        """``find_many``'s keys, duplicates dropped, grouped by the shard each lives on - in the
        order each shard was first named."""
        by_shard: dict[ShardIdentifier, list[Any]] = {}
        for key in dict.fromkeys(_to_column(k) for k in keys):
            by_shard.setdefault(self.shard_for(key), []).append(key)
        return by_shard

    def _among(self, conn: Any, keys: Sequence[Any]) -> Any:
        """``find_many``'s statement for one shard's keys."""
        return select(self.table).where(*self._live(self._id_among(conn, keys)))

    def _count(self, *criteria: ColumnElement[bool]) -> Any:
        return select(func.count()).select_from(self.table).where(*self._live(*criteria))

    def _found(self, found: M | None, key: K) -> M:
        """What ``get`` makes of ``find``'s answer: the row, or a refusal naming it."""
        if found is None:
            raise EntityNotFoundError(self.model_type.__name__, key)
        return found

    def _insert_rows(self, models: Sequence[M]) -> tuple[ShardIdentifier, list[R]]:
        """The shard a batch goes to, and its rows.

        The rows are checked as an update's are: one INSERT for the batch is compiled from the
        first row's keys, so a later row carrying a key the first lacks would have it dropped
        without a word.
        """
        shard = self.shard_of_all(models)
        rows = [self.to_record(m) for m in models]
        if rows:
            self._written_columns(rows, updating=False)
        return shard, rows

    def _insert_returning(self) -> Any:
        """An insert that reads its rows back. Run it with ``execution_options=_ONE_STATEMENT``."""
        return insert(self.table).returning(self.table, sort_by_parameter_order=True)

    def _stamped(self, models: Sequence[M]) -> tuple[ShardIdentifier, list[M]]:
        """The shard a batch of updates goes to, and each row one version on."""
        return self.shard_of_all(models), [self._next_version(m) for m in models]

    def _update_statement(
        self, dialect: str, stamped: Sequence[M], *, returning: bool
    ) -> tuple[Any, dict[str, Any] | None]:
        """The one UPDATE for a batch, and its binds - ``None`` when they are in the statement.

        One row: ``UPDATE … WHERE id = ? AND version = ?``, built once and re-bound. More: one
        UPDATE joined against a derived table of the new values - see :meth:`_values_update`
        and :meth:`_union_update` for why there are two shapes.
        """
        records = [self.to_record(m) for m in stamped]
        columns = self._written_columns(records, updating=True)
        if len(stamped) == 1:
            return (
                self._single_update(columns, returning=returning),
                self._bind(stamped[0], records[0], columns),
            )
        if dialect == "postgresql":
            stmt = self._values_update(stamped, records, columns)
            return (stmt.returning(self.table) if returning else stmt), None

        # Building a 500-row UNION ALL costs about 25ms, against 5ms for the VALUES form, so
        # one is composed per batch size and then only re-bound. A service that writes the
        # same shapes repeatedly pays that cost once; the cache is capped so a caller passing
        # a different length every time cannot grow it without bound.
        size = len(stamped)
        stmt = self._union_stmts.get((size, returning, columns))
        if stmt is None:
            stmt = self._union_update(size, columns)
            if returning:
                stmt = stmt.returning(self.table)
            if len(self._union_stmts) < 32:
                self._union_stmts[(size, returning, columns)] = stmt
        return stmt, self._union_binds(stamped, records, columns)

    def _checked(self, result: Any, stamped: Sequence[M], *, returning: bool) -> list[Row[Any]]:
        """The rows an UPDATE returned, once the optimistic lock has been checked against it.

        Takes the driver's result, already in hand: a sync connection and an async one both
        give back a buffered result here, so reading it waits on nothing.
        """
        written: list[Row[Any]] = list(result) if returning else []
        affected = len(written) if returning else int(result.rowcount)
        if affected > len(stamped):
            # Not a lost race, and so deliberately not a StaleRecordError: the executor
            # retries those, and retrying this would only burn the attempt. An update
            # keyed on `id = ? AND version = ?` cannot match twice unless the primary key
            # is not unique. Java raises IllegalStateException here for the same reason.
            raise EkbatanError(
                f"updating {len(stamped)} {self.model_type.__name__} row(s) matched "
                f"{affected}; `id` is not unique in {self.table.name}"
            )
        if affected < len(stamped):
            # Inside a unit of work this rolls the whole thing back. Outside one the
            # statement has already committed whatever it matched - there is no
            # transaction here to undo it, by design.
            raise StaleRecordError(
                f"expected to update {len(stamped)} {self.model_type.__name__} row(s) "
                f"but matched {affected}; one was concurrently modified or is gone",
                model=self.model_type.__name__,
            )
        return written

    def _updated(
        self, stamped: Sequence[M], written: list[Row[Any]], *, read_back: bool, returning: bool
    ) -> list[M] | None:
        """What an update hands back: nothing, the returned rows, or the staged ones rebuilt."""
        if not read_back:
            return None
        if not returning:
            # Rebuilt through the same readers a query would use, not handed back as it came
            # in. Without this, MySQL - which has no `UPDATE ... RETURNING` - returns the very
            # object the caller passed, still carrying the events it was about to have
            # persisted, and the next update writes them a second time. Every other dialect
            # returns a row, and a row has no events. `add_all` takes the same care for the
            # same reason.
            return [self.from_record(self.to_record(m)) for m in stamped]
        # RETURNING makes no promise about order, and a caller handing in a list expects the
        # same list back.
        by_id = {row._mapping[self.id_column.name]: row for row in written}
        return [self._read_back(by_id[self.to_record(m)[self.id_column.name]]) for m in stamped]


class AbstractRepository[M: Persistable[Any, Any], R: Mapping[str, Any], K](
    _RepositoryCore[M, R, K], Repository[M]
):
    """Rows of one table, as models - Java's ``AbstractRepository``.

    Generic in the model, the table's record and the id - ``ModelRepository[Wallet,
    WalletsRecord, ShardedId[Wallet]]`` - as Java's is in the model, the record, the table and
    the id; the table here is a value, not a type, so it is not a parameter.

    Abstract for Java's reason: ``to_record`` and ``from_record`` are the repository's to
    write, and a repository without them cannot be created at all - the nearest this language
    comes to the compile error Java gives. Applications extend :class:`ModelRepository` or
    :class:`EntityRepository`, which say which kind of thing the table holds.
    """

    registry: DatabaseRegistry

    @contextmanager
    def db(
        self, *, key: K | None = None, model: M | None = None, shard: ShardIdentifier | None = None
    ) -> Iterator[Db]:
        """A writer connection for one shard, outside any transaction. Each statement commits.

        It does **not** join an open transaction. That is occasionally what you want - a write
        that must survive the surrounding rollback, an audit row - and almost always not, so
        reach for :meth:`tx_db_else_db` unless you specifically mean to be outside. Writing to
        a row your own open transaction already holds will also block until one of them gives
        up, which is the other reason this is the deliberate choice rather than the default.
        """
        with Db(self._manager(key, model, shard).primary).execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            yield conn

    def dbs(self) -> Iterator[tuple[ShardIdentifier, Db]]:
        """Every shard's writer, one at a time, outside any transaction.

        An unsharded repository visits **only the default shard**, even when the registry holds
        more - the same guard Java applies in ``dbs()`` and ``readonlyDbs()``. Without it a
        repository that opted out of sharding would still scatter across every database a
        sibling repository happens to use.

        Lazy, so a caller who stops at the first shard that answers has not queried the rest.
        """
        for shard in self._scatter_shards():
            with self.db(shard=shard) as conn:
                yield shard, conn

    @contextmanager
    def _reader(
        self, *, key: K | None = None, shard: ShardIdentifier | None = None
    ) -> Iterator[Db]:
        """What every built-in read uses: :meth:`db` - the **primary**, outside any transaction.

        Exactly Java's: ``findById``, ``findAll``, ``existsById``, ``count`` and the rest go to
        ``db()``, which never joins an open transaction. So a built-in read inside a
        transaction does **not** see that transaction's uncommitted writes, and takes a second
        connection from the pool while the transaction holds the first. A read that must see
        them is a query of your own on :meth:`tx_db_else_db`.

        Private, because it is not a choice - it is the framework reading on your behalf. The
        public accessors are where *you* choose, and :meth:`readonly_db` is how you say you can
        live with lag.
        """
        with self.db(key=key, shard=shard) as conn:
            yield conn

    @contextmanager
    def readonly_db(
        self, *, key: K | None = None, shard: ShardIdentifier | None = None
    ) -> Iterator[Db]:
        """The **replica**, for a read that can live with replication lag.

        The same meaning as Java's ``readonlyDb()``, including the fallback: a shard with no
        replica configured hands back its primary, so this is always safe to call.

        Choosing it is the caller's, and deliberately so. A replica answers with what it has
        replicated so far, so a read that follows a write of the same row can come back empty.
        Whether that matters is something only the caller knows - if you care, use :meth:`db`;
        if you do not, use this. The framework does not guess on your behalf.

        It does **not** join an open transaction, and that is deliberate rather than an
        oversight: a replica has not seen what the open transaction wrote, so joining would
        be a promise this connection cannot keep. Java's ``readonlyDb()`` is a plain context
        on the secondary for the same reason. If you need to see the transaction's own writes,
        that is what :meth:`tx_db_else_db` is for.
        """
        with Db(self._manager(key, None, shard).replica).execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            yield conn

    def readonly_dbs(self) -> Iterator[tuple[ShardIdentifier, Db]]:
        """Every shard's **replica**, one at a time.

        An unsharded repository visits **only the default shard**, even when the registry holds
        more - the same guard Java applies in ``dbs()`` and ``readonlyDbs()``. Without it a
        repository that opted out of sharding would still scatter across every database a
        sibling repository happens to use.

        Lazy, so a caller who stops at the first shard that answers has not queried the rest.
        """
        for shard in self._scatter_shards():
            with self.readonly_db(shard=shard) as conn:
                yield shard, conn

    def tx_db(
        self, *, key: K | None = None, model: M | None = None, shard: ShardIdentifier | None = None
    ) -> Db | None:
        """The transaction already open on this shard, or ``None``.

        For code that must behave differently depending on whether it is inside one. Most code
        should not care, and should call :meth:`tx_db_else_db`.
        """
        current: Db | None = self._manager(key, model, shard).current()
        return current

    @contextmanager
    def tx_db_else_db(
        self, *, key: K | None = None, model: M | None = None, shard: ShardIdentifier | None = None
    ) -> Iterator[Db]:
        """The transaction open on this shard, or no transaction at all if there is none.

        What every built-in write uses, and what a subclass writing its own query should use
        when it must see what the open transaction wrote - the built-in reads do not, as
        Java's do not. Inside a unit of work it joins, outside one each statement commits itself.

        It never opens one. A framework that opened a transaction here would be deciding what
        is atomic on the caller's behalf, and getting it wrong in both directions - wrapping a
        single statement that needed nothing, and quietly making a multi-statement call atomic
        so that nobody noticed it had become one.
        """
        with self._manager(key, model, shard).connection() as conn:
            yield conn

    # ------------------------------------------------------------------ reads

    def find(self, key: K) -> M | None:
        """The row, or ``None``. The lookup that treats absence as ordinary."""
        stmt = self._by_id(key)
        with self._reader(key=key) as conn:
            row = conn.execute(stmt).first()
        return self._read_back(row) if row is not None else None

    def get(self, key: K) -> M:
        """The row, or raise. The lookup for callers that cannot continue without it."""
        return self._found(self.find(key), key)

    def find_many(self, keys: Iterable[K]) -> list[M]:
        """The rows that exist, in no promised order.

        Duplicates are collapsed before the query - asking twice for the same id is a caller's
        accident, not an instruction to fetch it twice. An empty input is an empty result:
        never ``IN ()``, and never an accidental full-table read.
        """
        by_shard = self._keys_by_shard(keys)

        def read(shard: ShardIdentifier) -> list[M]:
            with self._reader(shard=shard) as conn:
                return [
                    self._read_back(r) for r in conn.execute(self._among(conn, by_shard[shard]))
                ]

        return [model for found in self._on_each(list(by_shard), read) for model in found]

    def find_where(
        self, *criteria: ColumnElement[bool], shard: ShardIdentifier | None = None
    ) -> list[M]:
        stmt = self._where(*criteria)
        return [self._read_back(r) for r in self._read(stmt, shard)]

    def find_one_where(
        self, *criteria: ColumnElement[bool], shard: ShardIdentifier | None = None
    ) -> M | None:
        """The one row matching, or ``None``. For a predicate you believe is unique.

        Stops at the first shard that answers, so a lookup by email across sixteen shards is
        one query when it is lucky rather than sixteen every time.

        A shard holding two matches is refused rather than resolved. Picking one would be
        picking arbitrarily, and whoever wrote a predicate they thought was unique is exactly
        the person who will not notice that it was not.
        """
        stmt = self._one_where(*criteria)

        def read(each: ShardIdentifier) -> M | None:
            with self._reader(shard=each) as conn:
                return self._unique(list(conn.execute(stmt)))

        return self._first_answer(self._shards(shard), read)

    def all(self, *, shard: ShardIdentifier | None = None) -> list[M]:
        return self.find_where(shard=shard)

    def exists(self, key: K) -> bool:
        stmt = self._presence(self.id_column == _to_column(key))
        with self._reader(key=key) as conn:
            return conn.execute(stmt).first() is not None

    def exists_where(
        self, *criteria: ColumnElement[bool], shard: ShardIdentifier | None = None
    ) -> bool:
        """Whether any row matches - which is not the same question as how many do.

        Counting reads every matching row to answer yes or no. On a predicate matching a
        million rows that is a million rows of work for one bit of information, and it is the
        shape a "does this customer have any orders" check most often takes.
        """
        stmt = self._presence(*criteria)

        def read(each: ShardIdentifier) -> bool | None:
            with self._reader(shard=each) as conn:
                return True if conn.execute(stmt).first() is not None else None

        return self._first_answer(self._shards(shard), read) is not None

    def count(self, *criteria: ColumnElement[bool], shard: ShardIdentifier | None = None) -> int:
        return sum(int(r[0]) for r in self._read(self._count(*criteria), shard))

    def _read(self, stmt: Any, shard: ShardIdentifier | None) -> list[Row[Any]]:
        """Run a read on one shard, or on every shard when none is named.

        A caller holding an id gets routing for free. A caller with a predicate does not - the
        predicate says nothing about where its rows live - so this fans out, and the caller
        that knows better narrows it with ``shard=``.
        """

        def read(each: ShardIdentifier) -> list[Row[Any]]:
            with self._reader(shard=each) as conn:
                return list(conn.execute(stmt))

        return [row for rows in self._on_each(self._shards(shard), read) for row in rows]

    def _on_each[T](
        self, shards: Sequence[ShardIdentifier], read: Callable[[ShardIdentifier], T]
    ) -> list[T]:
        """``read`` on every shard, one after another, the answers in shard order.

        All or nothing: the first shard that fails fails the read, with a note naming the
        shard when the read spans more than one. There are no partial results.
        """
        return [self._read_one(shards, each, read) for each in shards]

    def _first_answer[T](
        self, shards: Sequence[ShardIdentifier], read: Callable[[ShardIdentifier], T | None]
    ) -> T | None:
        """``read`` shard by shard, in shard order, stopping at the first that answers - so a
        lookup across sixteen shards is one query when it is lucky, and a shard after the one
        that answered is never asked. ``None`` is "not here"."""
        for each in shards:
            found = self._read_one(shards, each, read)
            if found is not None:
                return found
        return None

    def _read_one[T](
        self,
        shards: Sequence[ShardIdentifier],
        shard: ShardIdentifier,
        read: Callable[[ShardIdentifier], T],
    ) -> T:
        try:
            return read(shard)
        except Exception as error:
            if len(shards) > 1:
                self._shard_failure(shards, {shard: error})
            raise

    # ----------------------------------------------------------------- writes

    def add(self, model: M) -> M:
        """Insert one row and hand back what the database now holds."""
        return self.add_all([self._one(model)])[0]

    def add_no_result(self, model: M) -> None:
        """Insert one row without reading it back."""
        self.add_all_no_result([self._one(model)])

    def add_all(self, models: Sequence[M]) -> list[M]:
        """Insert, one statement however many rows there are, and read the rows back.

        Reading back is ``RETURNING`` where the dialect has it, so it costs no extra round
        trip - but it does put every inserted row on the wire, which for a large batch is real
        money. That is what :meth:`add_all_no_result` is for: same write, no result set. The
        choice is the caller's, because only the caller knows whether anything is going to look
        at the rows.

        Where there is no ``RETURNING`` - MySQL - the staged values come back instead, rebuilt
        through the same readers a query would use, so the two paths differ only in whether a
        server-side default or trigger is visible. Java takes the same branch
        (``if MYSQL: insert.execute(); return List.copyOf(domainObjects)``) and does not need
        the rebuild, because there a typed id is the only thing that compiles. Here a raw
        ``uuid4()`` passed where an ``Id[Notification]`` belongs would otherwise come back raw on
        MySQL and wrapped on every other database - the same call, two shapes.
        """
        if not self._all(models):
            return []
        shard, rows = self._insert_rows(models)

        with self.tx_db_else_db(shard=shard) as conn:
            if conn.dialect.insert_returning:
                written = conn.execute(
                    self._insert_returning(), rows, execution_options=_ONE_STATEMENT
                )
                return [self._read_back(r) for r in written]
            conn.execute(insert(self.table), rows)
        return [self.from_record(r) for r in rows]

    def add_all_no_result(self, models: Sequence[M]) -> None:
        """Insert without reading anything back.

        For the thousand-row import whose rows nobody is going to read. Never asks for
        ``RETURNING``, so nothing crosses the wire on the way home.
        """
        if not self._all(models):
            return
        with self._writing("BATCH_INSERT", len(models)):
            shard, rows = self._insert_rows(models)
            with self.tx_db_else_db(shard=shard) as conn:
                conn.execute(insert(self.table), rows)

    def update(self, model: M) -> M:
        """Update one row under the optimistic lock, and hand back what the database holds."""
        return self.update_all([self._one(model)])[0]

    def update_no_result(self, model: M) -> None:
        self.update_all_no_result([self._one(model)])

    def update_all(self, models: Sequence[M]) -> list[M]:
        """Update under the optimistic lock and read the rows back.

        **One statement**, however many rows. Not ``executemany``: SQLAlchemy rewrites a
        batched INSERT into a single multi-row statement, but there is no equivalent for
        UPDATE - an executemany there is N statements and N server-side plans, pipelined at
        best. So the batch becomes one derived table joined against the target.

        One statement is also all this framework will send. Splitting a batch that exceeds the
        server's parameter ceiling - 65535 binds on Postgres, a packet size on MySQL - is the
        caller's, because only the caller knows what the pieces mean if one of them fails.

        If the affected count is short, someone else got there first and the call raises. Inside
        a unit of work that rolls the whole thing back. Outside one there is no transaction to
        roll back and the statement has already committed what it matched, so a caller batching
        by hand takes that on: it is the same on both ports, and the reason to run a batch
        inside a unit of work when the pieces belong together. The count is trustworthy because
        every row's version changes, so no row can be reported unaffected for having been
        written its own values.
        """
        written = self._apply_updates(self._all(models), read_back=True)
        return written if written is not None else []

    def update_all_no_result(self, models: Sequence[M]) -> None:
        """Update without reading anything back."""
        if not self._all(models):
            return
        with self._writing("BATCH_UPDATE", len(models)):
            self._apply_updates(models, read_back=False)

    def _apply_updates(self, models: Sequence[M], *, read_back: bool) -> list[M] | None:
        if not models:
            return [] if read_back else None
        shard, stamped = self._stamped(models)

        with self.tx_db_else_db(shard=shard) as conn:
            returning = read_back and conn.dialect.update_returning
            stmt, binds = self._update_statement(conn.dialect.name, stamped, returning=returning)
            result = conn.execute(stmt) if binds is None else conn.execute(stmt, binds)
            written = self._checked(result, stamped, returning=returning)

        return self._updated(stamped, written, read_back=read_back, returning=returning)


def _must_hold(col: Any, wanted: type, described: str, why: str) -> None:
    """Refuse a column that provably cannot hold what the framework puts in it.

    Only what can be proven: a type that declines to name its Python equivalent - a custom
    ``TypeDecorator``, a dialect type SQLAlchemy has no mapping for - is left alone. Refusing
    what cannot be checked would turn a startup guard into a reason to stop using one.
    """
    try:
        actual = col.type.python_type
    except (NotImplementedError, AttributeError):
        return
    if isinstance(actual, type) and issubclass(actual, Enum):
        actual = str
    if actual is bool or not (isinstance(actual, type) and issubclass(actual, wanted)):
        raise ValidationError(
            f"column {col.name!r} on {col.table.name} is {col.type}, which holds "
            f"{actual.__name__ if isinstance(actual, type) else actual}; it has to be "
            f"{described}, because {why}."
        )


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
        namespace = vars(sys.modules[base.__module__]) if base.__module__ in sys.modules else {}
        for orig in getattr(base, "__orig_bases__", ()):
            origin = typing.get_origin(orig)
            declared = getattr(origin, "__type_params__", None)
            params: tuple[Any, ...] = tuple(
                declared if declared else getattr(origin, "__parameters__", ())
            )
            for param, arg in zip(params, typing.get_args(orig), strict=False):
                bound = bindings.get(arg, arg)
                bindings[param] = _resolve_refs(bound, namespace)
    return {name: bindings.get(hint, hint) for name, hint in hints.items()}


def _resolve_refs(hint: Any, namespace: Mapping[str, Any]) -> Any:
    """``ShardedId["Wallet"]`` with the ``"Wallet"`` looked up, as a field annotation would be.

    A type argument on a class line is never evaluated. ``class Wallet(Model[ShardedId["Wallet"],
    ...])`` has to quote the name - the class does not exist yet - and the argument then stays a
    ``ForwardRef`` for good, where ``get_type_hints`` resolves the same string in a field
    annotation. Left alone, anything reading the id type sees a model it cannot name: the
    builder generator wrote ``ShardedId[Any]`` for it, which a checker then accepts for every
    model's id.

    Resolved in the namespace of the module whose class line wrote it. A name that is not there
    - a class defined inside a function - is left as it was, which is what this did before.
    """
    if isinstance(hint, typing.ForwardRef):
        target = namespace.get(hint.__forward_arg__)
        return target if target is not None else hint
    args = typing.get_args(hint)
    if not args:
        return hint
    resolved = tuple(_resolve_refs(arg, namespace) for arg in args)
    if resolved == args:
        return hint
    copy_with = getattr(hint, "copy_with", None)
    return copy_with(resolved) if copy_with is not None else hint


#: Types the driver takes as they are. Nothing to convert, so nothing to check per row. A
#: ``datetime`` is one of them: it goes to the driver, and comes back, exactly as it is.
_VERBATIM = (str, int, float, bool, bytes, uuid.UUID, Decimal, date, datetime, time)


def _bare(hint: Any) -> Any:
    """`Instant | None` is a union, and the interesting half is the half that is not None. A
    `NewType` label - `Instant` - stands for the class under it, `datetime`."""
    if typing.get_origin(hint) in (typing.Union, types.UnionType):
        rest = [a for a in typing.get_args(hint) if a is not type(None)]
        hint = rest[0] if len(rest) == 1 else None
    while getattr(hint, "__supertype__", None) is not None:
        hint = hint.__supertype__
    return hint


def _writer_for(hint: Any) -> Callable[[Any], Any]:
    """How to hand this column's values to the driver, decided once from the declared type."""
    hint = _bare(hint)
    origin = typing.get_origin(hint) or hint
    if isinstance(origin, type):
        if issubclass(origin, TypedValue):
            return lambda v: v.value if isinstance(v, TypedValue) else v
        if issubclass(origin, Enum):
            return lambda v: v.name if isinstance(v, Enum) else v
        if origin in _VERBATIM:
            return _identity
    # An untyped or unrecognised column: fall back to inspecting each value.
    return _to_column


def _reader_for(hint: Any) -> Callable[[Any], Any]:
    """And how to rebuild them, likewise."""
    hint = _bare(hint)
    origin = typing.get_origin(hint) or hint
    if isinstance(origin, type):
        if issubclass(origin, TypedValue):
            return lambda v: None if v is None else (v if isinstance(v, origin) else origin(v))
        if issubclass(origin, Enum):
            return lambda v: None if v is None else (v if isinstance(v, Enum) else origin[str(v)])
    return _identity


def _identity(value: Any) -> Any:
    return value


def _to_column(value: Any) -> Any:
    if isinstance(value, TypedValue):
        return _to_column(value.value)
    if isinstance(value, Enum):
        return value.name
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


def _must_be_model[T](model_type: type[T]) -> type[T]:
    """What a model repository, sync or async, checks before anything else."""
    if not (isinstance(model_type, type) and issubclass(model_type, Model)):
        raise ValidationError(
            f"{model_type.__name__} is not a Model, so it has no events and no timestamps. "
            f"Use EntityRepository, or make it a Model if its changes are worth telling "
            f"the rest of the system about."
        )
    return model_type


def _must_be_entity[T](model_type: type[T]) -> type[T]:
    """And what an entity repository checks."""
    if not (isinstance(model_type, type) and issubclass(model_type, Entity)):
        raise ValidationError(
            f"{model_type.__name__} is not an Entity. Use ModelRepository if its changes "
            f"belong in the event stream."
        )
    return model_type


class ModelRepository[M: Model[Any, Any], R: Mapping[str, Any], K](AbstractRepository[M, R, K]):
    """Rows of a :class:`~ekbatan.domain.Model` - state whose changes are events.

    Everything is inherited; what this adds is the declaration. Saying which of the two you
    meant lets the framework hold you to it, and a mismatch is caught where the repository is
    built rather than at the first write.
    """

    def __init__(self, model_type: type[M], *args: Any, **kwargs: Any) -> None:
        super().__init__(_must_be_model(model_type), *args, **kwargs)


class EntityRepository[M: Entity[Any, Any], R: Mapping[str, Any], K](AbstractRepository[M, R, K]):
    """Rows of an :class:`~ekbatan.domain.Entity` - state whose changes are not events.

    The counterpart to :class:`ModelRepository`, and the same bargain: declare which one it is
    and be held to it.
    """

    def __init__(self, model_type: type[M], *args: Any, **kwargs: Any) -> None:
        super().__init__(_must_be_entity(model_type), *args, **kwargs)


#: What a :class:`RepositoryRegistry` holds: a sync repository, or an ``ekbatan.aio`` one. A
#: service's executor writes through one kind; the registry is a map from model type to
#: repository and calls neither, so one class serves both.
type AnyRepository = Repository[Any] | AsyncRepository[Any]


class RepositoryRegistry:
    """Which repository writes which model.

    The executor consults it to turn a staged change into a write. Registration is per model
    type, not per shard: one repository serves every shard it is configured for, through its
    own sharding strategy.

    Built from a collection when that is all you have, or through :meth:`builder` when you
    want each registration to say what it is::

        RepositoryRegistry([wallets, alerts])

        (RepositoryRegistry.builder()
            .with_model_repository(wallets)
            .with_entity_repository(alerts)
            .build())
    """

    __slots__ = ("_by_model",)

    def __init__(self, repositories: Iterable[AnyRepository] = ()) -> None:
        self._by_model: dict[type, AnyRepository] = {}
        for repo in repositories:
            self._add(repo)

    @classmethod
    def builder(cls) -> RepositoryRegistryBuilder:
        """A builder, for registrations that declare what they are."""
        return RepositoryRegistryBuilder()

    def _add(self, repo: AnyRepository) -> None:
        model_type = repo.model_type
        existing = self._by_model.get(model_type)
        if existing is not None and existing is not repo:
            # Silently keeping the last one means half the application writes through a
            # repository the other half does not know about - different clock, different
            # sharding, and no way to tell from a stack trace which one ran.
            raise ValidationError(
                f"two repositories were given for {model_type.__name__}: "
                f"{type(existing).__name__} and {type(repo).__name__}. One model, one "
                f"repository - the executor has no way to choose."
            )
        self._by_model[model_type] = repo

    def repository(self, model_type: type) -> AnyRepository:
        """The repository that writes this model, or a refusal naming it.

        Java's returns null here, which reaches the caller as a NullPointerException naming
        whatever it dereferenced first. Raising with the type in the message is the whole
        difference, and it is why this signature says ``Repository`` and not ``Repository |
        None``.
        """
        try:
            return self._by_model[model_type]
        except KeyError:
            raise ValidationError(
                f"no repository is registered for {model_type.__name__}, so a change staged "
                f"for it cannot be written. Register one on the RepositoryRegistry the "
                f"executor was built with; it currently holds "
                f"{sorted(m.__name__ for m in self._by_model) or 'nothing'}."
            ) from None

    @property
    def models(self) -> tuple[type, ...]:
        """Every model type registered, in registration order."""
        return tuple(self._by_model)

    def __contains__(self, model_type: object) -> bool:
        return model_type in self._by_model

    def __iter__(self) -> Iterator[AnyRepository]:
        return iter(self._by_model.values())

    def __len__(self) -> int:
        return len(self._by_model)

    def __repr__(self) -> str:
        return f"RepositoryRegistry({[m.__name__ for m in self._by_model]})"


class RepositoryRegistryBuilder:
    """Collects repositories, then builds the registry. Obtain via
    :meth:`RepositoryRegistry.builder`."""

    __slots__ = ("_repositories",)

    def __init__(self) -> None:
        self._repositories: list[AnyRepository] = []

    def with_model_repository(self, repository: AnyRepository) -> Self:
        """Register a repository for a :class:`~ekbatan.domain.Model`.

        The model type is not passed alongside it - it is already on the repository, and two
        ways to say which model this writes would eventually disagree. What this adds over
        :meth:`with_repositories` is the declaration: saying model here and being wrong is
        caught now rather than by a missing outbox row later.
        """
        return self._checked(repository, Model, "Model", "with_entity_repository")

    def with_entity_repository(self, repository: AnyRepository) -> Self:
        """Register a repository for an :class:`~ekbatan.domain.Entity` - state whose changes
        are not events."""
        return self._checked(repository, Entity, "Entity", "with_model_repository")

    def with_repositories(self, repositories: Iterable[AnyRepository]) -> Self:
        """Register a collection, sorting each into model or entity by what it holds.

        For a container that has already collected them, where nobody is in a position to
        declare one from the other.
        """
        for repository in repositories:
            model_type = repository.model_type
            if not issubclass(model_type, Model | Entity):
                raise ValidationError(
                    f"{model_type.__name__} is neither a Model nor an Entity, so "
                    f"{type(repository).__name__} cannot be registered. A persistable is one "
                    f"or the other: a Model if its changes belong in the event stream."
                )
            self._repositories.append(repository)
        return self

    def _checked(
        self,
        repository: AnyRepository,
        expected: type,
        named: str,
        instead: str,
    ) -> Self:
        model_type = repository.model_type
        if not issubclass(model_type, expected):
            article = "an" if named == "Entity" else "a"
            raise ValidationError(
                f"{model_type.__name__} is not {article} {named}, so it cannot be registered "
                f"that way - call {instead} instead."
            )
        self._repositories.append(repository)
        return self

    def build(self) -> RepositoryRegistry:
        """The registry. Refuses two repositories for one model."""
        return RepositoryRegistry(self._repositories)
