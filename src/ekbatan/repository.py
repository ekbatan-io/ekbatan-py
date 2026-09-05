"""Reading and writing rows.

A repository is bound to one model and one table. It routes by shard, enforces the optimistic
lock, hides soft-deleted rows from every read path, and maps between rows and models - by field
name, so that the common case needs no mapping code at all.

It has no opinion about transactions, and opens none. If one is open on the shard it joins it;
if not, each statement stands alone and commits itself. That is what lets the same call read
naturally in a unit of work and in a script, and it is why deciding when something is atomic
stays with the caller rather than being decided here.
"""

from __future__ import annotations

import dataclasses
import types
import typing
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
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
    func,
    insert,
    literal,
    select,
    union_all,
    update,
    values,
)

from ._tracing import span
from .clock import Clock, SystemClock, to_storage
from .domain import DELETED, Entity, Model, Persistable
from .errors import (
    CrossShardError,
    EntityNotFoundError,
    StaleRecordError,
    UnsupportedError,
    ValidationError,
)
from .ids import TypedValue
from .persistence import DatabaseRegistry, TransactionManager
from .sharding import NoShardingStrategy, ShardIdentifier, ShardingStrategy

__all__ = ["EntityRepository", "ModelRepository", "Repository"]

#: The column carrying the version each row was read at, in a batch update's derived table.
_PRIOR = "_prior_version"

#: Large enough that SQLAlchemy never pages a RETURNING insert of its own accord.
_NO_PAGING = 1_000_000


class Repository[M: Persistable[Any, Any], K]:
    """Rows of one table, as models.

    Subclass it, or instantiate it directly when the default field-to-column mapping is
    enough::

        wallets = Repository(Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy())
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
        self.sharding = sharding or NoShardingStrategy()
        self.clock = clock or SystemClock()
        self._single_stmt: dict[bool, Any] = {}
        self._union_stmts: dict[tuple[int, bool], Any] = {}

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
        _must_hold(self._columns["version"], int, "an integer", "the optimistic lock counts on it")
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
        self._to_col = {name: _writer_for(self._types.get(name)) for name in self._columns}
        self._from_col = {name: _reader_for(self._types.get(name)) for name in self._columns}

    # ---------------------------------------------------------------- mapping

    def to_row(self, model: M) -> dict[str, Any]:
        """The model as column values. Override when names or shapes differ."""
        return {name: write(getattr(model, name)) for name, write in self._to_col.items()}

    def from_row(self, row: Row[Any]) -> M:
        """A row as a model. Override alongside :meth:`to_row`."""
        mapping = row._mapping
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
            raise CrossShardError(sorted(str(s) for s in shards))
        return shards.pop()

    @contextmanager
    def db(
        self, *, key: K | None = None, model: M | None = None, shard: ShardIdentifier | None = None
    ) -> Iterator[Connection]:
        """A writer connection for one shard, outside any transaction. Each statement commits.

        It does **not** join an open transaction. That is occasionally what you want - a write
        that must survive the surrounding rollback, an audit row - and almost always not, so
        reach for :meth:`tx_db_else_db` unless you specifically mean to be outside. Writing to
        a row your own open transaction already holds will also block until one of them gives
        up, which is the other reason this is the deliberate choice rather than the default.
        """
        with (
            self._manager(key, model, shard)
            .primary.connect()
            .execution_options(isolation_level="AUTOCOMMIT") as conn
        ):
            yield conn

    def dbs(self) -> Iterator[tuple[ShardIdentifier, Connection]]:
        """Every shard's writer, one at a time, outside any transaction.

        For work that is genuinely per-database - a backfill, a repair. Nothing is opened and
        each statement commits itself, so a failure part way leaves everything before it
        written. Open a transaction per shard yourself if the work on one shard has to land
        together.
        """
        for shard in self.registry.shards:
            with self.db(shard=shard) as conn:
                yield shard, conn

    @contextmanager
    def readonly_db(
        self,
        *,
        key: K | None = None,
        shard: ShardIdentifier | None = None,
        replica: bool = False,
    ) -> Iterator[Connection]:
        """A connection for a read: no write transaction to commit, and the primary by default.

        What every built-in read uses. The primary rather than a replica because a replica
        answers with what it has replicated so far - ``add()`` then ``find()`` would come back
        empty for a reason the caller cannot see and cannot reproduce.

        ``replica=True`` asks for the replica anyway, which is the right trade for a report or
        a dashboard and the wrong one for anything that just wrote. Inside an open transaction
        it is ignored: a read there has to see the writes that transaction has made, and a
        replica by definition has not.
        """
        with self._manager(key, None, shard).connection(readonly=True, replica=replica) as conn:
            yield conn

    def readonly_dbs(
        self, *, replica: bool = False
    ) -> Iterator[tuple[ShardIdentifier, Connection]]:
        """Every shard's reader, one at a time, lazily.

        Lazy so that a caller who stops at the first shard that answers has not queried the
        rest - which is what makes :meth:`find_one_where` one query rather than one per shard.
        """
        for shard in self.registry.shards:
            with self.readonly_db(shard=shard, replica=replica) as conn:
                yield shard, conn

    def tx_db(
        self, *, key: K | None = None, model: M | None = None, shard: ShardIdentifier | None = None
    ) -> Connection | None:
        """The transaction already open on this shard, or ``None``.

        For code that must behave differently depending on whether it is inside one. Most code
        should not care, and should call :meth:`tx_db_else_db`.
        """
        return self._manager(key, model, shard).current()

    @contextmanager
    def tx_db_else_db(
        self, *, key: K | None = None, model: M | None = None, shard: ShardIdentifier | None = None
    ) -> Iterator[Connection]:
        """The transaction open on this shard, or no transaction at all if there is none.

        What every built-in read and write uses, and what a subclass writing its own query
        should use. It is why the same repository call reads naturally inside a unit of work
        and in a script: inside one it joins, outside one each statement commits itself.

        It never opens one. A framework that opened a transaction here would be deciding what
        is atomic on the caller's behalf, and getting it wrong in both directions - wrapping a
        single statement that needed nothing, and quietly making a multi-statement call atomic
        so that nobody noticed it had become one.
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
                stmt = select(self.table).where(*self._live(self._id_among(conn, shard_keys)))
                found += [self.from_row(r) for r in conn.execute(stmt)]
        return found

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

    def find_where(
        self, *criteria: ColumnElement[bool], shard: ShardIdentifier | None = None
    ) -> list[M]:
        stmt = select(self.table).where(*self._live(*criteria))
        return [self.from_row(r) for r in self._read(stmt, shard)]

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
        # Two, not one: a query for a single row can never report that there was a second.
        stmt = select(self.table).where(*self._live(*criteria)).limit(2)
        for conn in self._one_or_every(shard):
            rows = list(conn.execute(stmt))
            if len(rows) > 1:
                raise ValidationError(
                    f"find_one_where matched more than one {self.model_type.__name__}: the "
                    f"predicate is not unique. Use find_where when several rows are expected."
                )
            if rows:
                return self.from_row(rows[0])
        return None

    def _one_or_every(self, shard: ShardIdentifier | None) -> Iterator[Connection]:
        """The shard named, or every shard lazily, so a caller can stop at the first answer."""
        if shard is not None:
            with self.readonly_db(shard=shard) as conn:
                yield conn
        else:
            for _, conn in self.readonly_dbs():
                yield conn

    def all(self, *, shard: ShardIdentifier | None = None) -> list[M]:
        return self.find_where(shard=shard)

    def exists(self, key: K) -> bool:
        stmt = self._presence(self.id_column == _to_column(key))
        with self.readonly_db(key=key) as conn:
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
        if shard is not None:
            with self.readonly_db(shard=shard) as conn:
                return conn.execute(stmt).first() is not None
        return any(conn.execute(stmt).first() is not None for _, conn in self.readonly_dbs())

    def _presence(self, *criteria: ColumnElement[bool]) -> Any:
        return select(literal(1)).select_from(self.table).where(*self._live(*criteria)).limit(1)

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
        """Insert one row and hand back what the database now holds."""
        return self.add_all([model])[0]

    def add_no_result(self, model: M) -> None:
        """Insert one row without reading it back."""
        self.add_all_no_result([model])

    def add_all(self, models: Sequence[M]) -> list[M]:
        """Insert, one statement however many rows there are, and read the rows back.

        Reading back is ``RETURNING`` where the dialect has it, so it costs no extra round
        trip - but it does put every inserted row on the wire, which for a large batch is real
        money. That is what :meth:`add_all_no_result` is for: same write, no result set. The
        choice is the caller's, because only the caller knows whether anything is going to look
        at the rows.

        Where there is no ``RETURNING`` - MySQL and MariaDB - the staged values come back
        instead, so the two paths differ only in whether a server-side default or trigger is
        visible.
        """
        if not models:
            return []
        shard = self.shard_of_all(models)
        now = to_storage(self.clock.now())
        stamped = [self._stamp_new(m, now) for m in models]
        rows = [self.to_row(m) for m in stamped]

        with self.tx_db_else_db(shard=shard) as conn:
            if conn.dialect.insert_returning:
                written = conn.execute(
                    insert(self.table).returning(self.table, sort_by_parameter_order=True),
                    rows,
                    # SQLAlchemy otherwise splits a RETURNING insert into pages of a thousand
                    # rows. That is a batching decision, and batching is the caller's - three
                    # pages outside a transaction are three commits, so a failure on the third
                    # would leave two thirds written from a split nobody asked for. One
                    # statement, or the server's parameter ceiling, same as the other port.
                    execution_options={"insertmanyvalues_page_size": _NO_PAGING},
                )
                return [self.from_row(r) for r in written]
            conn.execute(insert(self.table), rows)
        return stamped

    def add_all_no_result(self, models: Sequence[M]) -> None:
        """Insert without reading anything back.

        For the thousand-row import whose rows nobody is going to read. Never asks for
        ``RETURNING``, so nothing crosses the wire on the way home.
        """
        if not models:
            return
        with self._writing("BATCH_INSERT", len(models)):
            shard = self.shard_of_all(models)
            now = to_storage(self.clock.now())
            rows = [self.to_row(self._stamp_new(m, now)) for m in models]
            with self.tx_db_else_db(shard=shard) as conn:
                conn.execute(insert(self.table), rows)

    def update(self, model: M) -> M:
        """Update one row under the optimistic lock, and hand back what the database holds."""
        return self.update_all([model])[0]

    def update_no_result(self, model: M) -> None:
        self.update_all_no_result([model])

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
        written = self._apply_updates(models, read_back=True)
        return written if written is not None else []

    def update_all_no_result(self, models: Sequence[M]) -> None:
        """Update without reading anything back."""
        if not models:
            return
        with self._writing("BATCH_UPDATE", len(models)):
            self._apply_updates(models, read_back=False)

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

    def _apply_updates(self, models: Sequence[M], *, read_back: bool) -> list[M] | None:
        if not models:
            return [] if read_back else None
        shard = self.shard_of_all(models)
        now = to_storage(self.clock.now())
        stamped = [self._stamp_updated(m, now) for m in models]

        with self.tx_db_else_db(shard=shard) as conn:
            returning = read_back and conn.dialect.update_returning
            written: list[Row[Any]] = []
            affected = 0

            if len(stamped) == 1:
                stmt = self._single_update(returning=returning)
                result = conn.execute(stmt, self._bind(stamped[0]))
                if returning:
                    written = list(result)
                    affected = len(written)
                else:
                    affected = result.rowcount
            else:
                written, affected = self._batch_update(conn, stamped, returning=returning)

            if affected != len(stamped):
                # Inside a unit of work this rolls the whole thing back. Outside one the
                # statement has already committed whatever it matched - there is no
                # transaction here to undo it, by design.
                raise StaleRecordError(
                    f"expected to update {len(stamped)} {self.model_type.__name__} row(s) "
                    f"but matched {affected}; one was concurrently modified or is gone",
                    model=self.model_type.__name__,
                )

        if not read_back:
            return None
        if not returning:
            return stamped
        # RETURNING makes no promise about order, and a caller handing in a list expects the
        # same list back.
        by_id = {row._mapping[self.id_column.name]: row for row in written}
        return [self.from_row(by_id[self.to_row(m)[self.id_column.name]]) for m in stamped]

    # --------------------------------------------------------------- internals

    def _stamp_new(self, model: M, now: datetime) -> M:
        """Fill in the timestamps a model has and an entity does not.

        The instant is read once per batch and passed in: a thousand rows written together
        were written at one moment, and asking the clock a thousand times both costs more and
        says something less true.
        """
        if not isinstance(model, Model):
            return model
        changes: dict[str, Any] = {}
        if model.created_date is None:
            changes["created_date"] = now
        if model.updated_date is None:
            changes["updated_date"] = changes.get("created_date", now)
        return model.evolve(**changes) if changes else model

    def _stamp_updated(self, model: M, now: datetime) -> M:
        """Move the version on, and mark when the row changed.

        The repository owns the optimistic lock, so the repository moves the version: a
        caller hands in the row as they read it and gets it back one version later. Asking
        them to move it first would mean the obvious call - ``update(wallet.deposit(10))`` -
        was the one that fails, and it would fail as a lost race, which it is not.
        """
        if not isinstance(model, Model):
            return model.next_version()
        return model.evolve(version=model.version + 1, updated_date=now)

    def _single_update(self, *, returning: bool = False) -> Any:
        """``UPDATE … SET … WHERE id = ? AND version = ?``, built once and reused."""
        if self._single_stmt.get(returning) is None:
            stmt = (
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
            self._single_stmt[returning] = stmt.returning(self.table) if returning else stmt
        return self._single_stmt[returning]

    def _bind(self, model: M) -> dict[str, Any]:
        row = self.to_row(model)
        return {
            "_id": row[self.id_column.name],
            "_prior": model.version - 1,
            **{f"_v_{k}": v for k, v in row.items() if k != self.id_column.name},
        }

    def _batch_update(
        self, conn: Any, models: Sequence[M], *, returning: bool = False
    ) -> tuple[list[Row[Any]], int]:
        """One UPDATE for the whole batch, joined against a derived table of the new values.

        Two shapes, because the dialects genuinely differ. Postgres has a ``VALUES`` table
        constructor with named columns and its planner reads one scan node from it. MySQL's
        equivalent needs ``VALUES ROW(...)`` and only from 8.0.19, and SQLite's cannot be given
        column names at all - so everything else gets a ``UNION ALL`` of selects, which every
        version of every one of them understands.

        Every value is bound against its target column's type, never inferred from the value's
        runtime class. Inference is how a timestamp acquires a zone it never had and the
        database shifts it by the session offset - invisibly, and only outside UTC.
        """
        if conn.dialect.name == "postgresql":
            stmt = self._values_update(models)
            return self._run_update(conn, stmt, None, returning)

        # Building a 500-row UNION ALL costs about 25ms, against 5ms for the VALUES form, so
        # one is composed per batch size and then only re-bound. A service that writes the
        # same shapes repeatedly pays that cost once; the cache is capped so a caller passing
        # a different length every time cannot grow it without bound.
        size = len(models)
        stmt = self._union_stmts.get((size, returning))
        if stmt is None:
            stmt = self._union_update(size)
            if returning:
                stmt = stmt.returning(self.table)
            if len(self._union_stmts) < 32:
                self._union_stmts[(size, returning)] = stmt
        return self._run_update(conn, stmt, self._union_binds(models), returning)

    def _run_update(
        self, conn: Any, stmt: Any, binds: dict[str, Any] | None, returning: bool
    ) -> tuple[list[Row[Any]], int]:
        stmt = stmt.returning(self.table) if returning and binds is None else stmt
        result = conn.execute(stmt) if binds is None else conn.execute(stmt, binds)
        if returning:
            rows = list(result)
            return rows, len(rows)
        return [], int(result.rowcount)

    def _derived_types(self) -> dict[str, Any]:
        return {_PRIOR: self.table.c.version.type} | {
            name: col.type for name, col in self._columns.items()
        }

    def _assignments(self, source: Any) -> dict[str, Any]:
        return {name: source.c[name] for name in self._columns if name != self.id_column.name}

    def _joined(self, source: Any) -> Any:
        return (
            update(self.table)
            .values(self._assignments(source))
            .where(
                self.id_column == source.c[self.id_column.name],
                self.table.c.version == source.c[_PRIOR],
            )
        )

    def _values_update(self, models: Sequence[M]) -> Any:
        """Postgres: ``UPDATE … FROM (VALUES …) AS v (cols)``."""
        types = self._derived_types()
        rows = [(m.version - 1, *(self.to_row(m)[name] for name in self._columns)) for m in models]
        source = values(*[column(name, types[name]) for name in types], name="v").data(rows)
        return self._joined(source)

    def _union_update(self, size: int) -> Any:
        """Everyone else: ``UPDATE … JOIN (SELECT … UNION ALL SELECT …) v``, bound by name."""
        types = self._derived_types()
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
        return self._joined(source)

    def _union_binds(self, models: Sequence[M]) -> dict[str, Any]:
        binds: dict[str, Any] = {}
        for i, model in enumerate(models):
            row = self.to_row(model)
            binds[f"b{i}_{_PRIOR}"] = model.version - 1
            for name in self._columns:
                binds[f"b{i}_{name}"] = row[name]
        return binds


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
        for orig in getattr(base, "__orig_bases__", ()):
            origin = typing.get_origin(orig)
            declared = getattr(origin, "__type_params__", None)
            params: tuple[Any, ...] = tuple(
                declared if declared else getattr(origin, "__parameters__", ())
            )
            for param, arg in zip(params, typing.get_args(orig), strict=False):
                bindings[param] = bindings.get(arg, arg)
    return {name: bindings.get(hint, hint) for name, hint in hints.items()}


#: Types the driver takes as they are. Nothing to convert, so nothing to check per row.
_VERBATIM = (str, int, float, bool, bytes, uuid.UUID, Decimal, date, time)


def _bare(hint: Any) -> Any:
    """`datetime | None` is a union, and the interesting half is the half that is not None."""
    if typing.get_origin(hint) in (typing.Union, types.UnionType):
        rest = [a for a in typing.get_args(hint) if a is not type(None)]
        return rest[0] if len(rest) == 1 else None
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
        if issubclass(origin, datetime):
            return lambda v: to_storage(v) if v is not None else None
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


class ModelRepository[M: Model[Any, Any], K](Repository[M, K]):
    """Rows of a :class:`~ekbatan.domain.Model` - state whose changes are events.

    Everything is inherited; what this adds is the declaration. Saying which of the two you
    meant lets the framework hold you to it, and a mismatch is caught where the repository is
    built rather than at the first write.
    """

    def __init__(self, model_type: type[M], *args: Any, **kwargs: Any) -> None:
        if not (isinstance(model_type, type) and issubclass(model_type, Model)):
            raise ValidationError(
                f"{model_type.__name__} is not a Model, so it has no events and no timestamps. "
                f"Use EntityRepository, or make it a Model if its changes are worth telling "
                f"the rest of the system about."
            )
        super().__init__(model_type, *args, **kwargs)


class EntityRepository[M: Entity[Any, Any], K](Repository[M, K]):
    """Rows of an :class:`~ekbatan.domain.Entity` - state whose changes are not events.

    The counterpart to :class:`ModelRepository`, and the same bargain: declare which one it is
    and be held to it.
    """

    def __init__(self, model_type: type[M], *args: Any, **kwargs: Any) -> None:
        if not (isinstance(model_type, type) and issubclass(model_type, Entity)):
            raise ValidationError(
                f"{model_type.__name__} is not an Entity. Use ModelRepository if its changes "
                f"belong in the event stream."
            )
        super().__init__(model_type, *args, **kwargs)
