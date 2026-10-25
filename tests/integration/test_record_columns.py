"""A record is the dict the database is written from and read into, whatever its columns are
called.

Since `EKB-0485` a table's record is a subclass of `dict` - `record["order date"]` and
`record.column_order_date()` both work - and these pin what that promises, on every database, in
both modes:

- a column may be called anything: `class` (a keyword), `keys` and `items` (`dict` methods),
  `order date` (a space), `2fa` (a leading digit). Each goes in by its keyword (`class_=`,
  `order_date=`, `_2fa=`), is stored under its real name, and comes back by key and by method,
  through `add`, `get_by_id`, `find_by_id`, `find_all_where`, `update` and `update_all`;
- a column the database fills (`filled`, `DEFAULT 7`) is a keyword defaulting to `UNSET`; left
  so, the insert does not send it and the database fills it;
- `from_record` is handed an instance of the record class on every read path - the finders,
  a write's read-back (`RETURNING`, or on MySQL the record `to_record` wrote), the fluent reads
  and `tx.rows` - on one database and on a shard of two;
- the record `to_record` returns is the very object the INSERT is given - no copy - and the
  object an UPDATE's values are read from;
- a mapper handed to `rows(...)` gets the record class its first parameter names, or a
  repository's own method's; anything else - a lambda - gets the row's mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, overload
from uuid import UUID

import pytest
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Uuid,
    event,
    select,
    text,
)
from sqlalchemy.engine import RowMapping

from ekbatan import Model, ModelRepository
from ekbatan.ids import ShardedId
from ekbatan.schema import UNSET, Unset
from ekbatan.sharding import EmbeddedBitsShardingStrategy, ShardIdentifier
from modes import rebased

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)
NOW = datetime(2026, 1, 1, 12, 0)

metadata = MetaData()
ODDS = Table(
    "record_oddities",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("state", String(24), nullable=False),
    Column("class", String(32), nullable=False),
    Column("keys", String(32), nullable=False),
    Column("items", Integer, nullable=True),
    Column("order date", DateTime, nullable=False),
    Column("2fa", String(16), nullable=True),
    Column("filled", Integer, nullable=False, server_default=text("7")),
    Column("created_date", DateTime, nullable=False),
    Column("updated_date", DateTime, nullable=False),
)


class OddsRecord(dict[str, Any]):
    """A row of `record_oddities`: every awkward name a column can have."""

    __slots__ = ()

    def __init__(
        self,
        *,
        id: UUID,
        version: int,
        state: str,
        class_: str,
        keys: str,
        items: int | None,
        order_date: datetime,
        _2fa: str | None,
        filled: int | Unset = UNSET,
        created_date: datetime,
        updated_date: datetime,
    ) -> None:
        super().__init__(
            {
                "id": id,
                "version": version,
                "state": state,
                "class": class_,
                "keys": keys,
                "items": items,
                "order date": order_date,
                "2fa": _2fa,
                "created_date": created_date,
                "updated_date": updated_date,
            }
        )
        if filled is not UNSET:
            self["filled"] = filled

    if TYPE_CHECKING:

        @overload  # type: ignore[override]
        def __getitem__(self, key: Literal["id"]) -> UUID: ...
        @overload
        def __getitem__(self, key: Literal["version"]) -> int: ...
        @overload
        def __getitem__(self, key: Literal["state"]) -> str: ...
        @overload
        def __getitem__(self, key: Literal["class"]) -> str: ...
        @overload
        def __getitem__(self, key: Literal["keys"]) -> str: ...
        @overload
        def __getitem__(self, key: Literal["items"]) -> int | None: ...
        @overload
        def __getitem__(self, key: Literal["order date"]) -> datetime: ...
        @overload
        def __getitem__(self, key: Literal["2fa"]) -> str | None: ...
        @overload
        def __getitem__(self, key: Literal["filled"]) -> int: ...
        @overload
        def __getitem__(self, key: Literal["created_date"]) -> datetime: ...
        @overload
        def __getitem__(self, key: Literal["updated_date"]) -> datetime: ...
        def __getitem__(self, key: str) -> Any: ...

    def column_id(self) -> UUID:
        return self["id"]

    def column_version(self) -> int:
        return self["version"]

    def column_state(self) -> str:
        return self["state"]

    def column_class(self) -> str:
        return self["class"]

    def column_keys(self) -> str:
        return self["keys"]

    def column_items(self) -> int | None:
        return self["items"]

    def column_order_date(self) -> datetime:
        return self["order date"]

    def column_2fa(self) -> str | None:
        return self["2fa"]

    def column_filled(self) -> int:
        return self["filled"]

    def column_created_date(self) -> datetime:
        return self["created_date"]

    def column_updated_date(self) -> datetime:
        return self["updated_date"]


class OddityState(Enum):
    ACTIVE = "ACTIVE"
    DELETED = "DELETED"


@dataclass(frozen=True, kw_only=True)
class Oddity(Model[ShardedId["Oddity"], OddityState]):
    klass: str
    keys: str
    items: int | None
    order_date: datetime
    two_fa: str | None
    #: `None` leaves it to the database: the record then has no `filled` key at all.
    filled: int | None

    def validate(self) -> None:
        pass  # no rules beyond its types


#: What `from_record` was handed, by type, and every record `to_record` returned - in order.
SEEN: list[type] = []
MADE: list[OddsRecord] = []
#: Set, `to_record` returns a record that notes each key read from it.
WATCH: list[bool] = []


@rebased
class OddityRepository(ModelRepository[Oddity, OddsRecord, ShardedId[Oddity]]):
    def to_record(self, oddity: Oddity) -> OddsRecord:
        record = OddsRecord(
            id=oddity.id.value,
            version=oddity.version,
            state=oddity.state.name,
            class_=oddity.klass,
            keys=oddity.keys,
            items=oddity.items,
            order_date=oddity.order_date,
            _2fa=oddity.two_fa,
            filled=UNSET if oddity.filled is None else oddity.filled,
            created_date=oddity.created_date,
            updated_date=oddity.updated_date or oddity.created_date,
        )
        if WATCH:
            watched = _Watched.__new__(_Watched)
            dict.update(watched, record)
            record = watched
        MADE.append(record)
        return record

    def from_record(self, record: OddsRecord) -> Oddity:
        SEEN.append(type(record))
        # Half by key, half by method: both are the record's.
        return Oddity(
            id=ShardedId[Oddity](record["id"]),
            version=record.column_version(),
            state=OddityState[record["state"]],
            klass=record.column_class(),
            keys=record["keys"],
            items=record.column_items(),
            order_date=record["order date"],
            two_fa=record.column_2fa(),
            filled=record.get("filled"),
            created_date=record.column_created_date(),
            updated_date=record["updated_date"],
        )

    def by_fetch(self, shard: ShardIdentifier = GLOBAL) -> list[Oddity]:
        with self.readonly_db(shard=shard) as conn:
            return conn.rows(select(ODDS).order_by(ODDS.c["keys"])).fetch(self.from_record)

    def by_each(self, shard: ShardIdentifier = GLOBAL) -> list[Oddity]:
        with self.readonly_db(shard=shard) as conn:
            return list(conn.rows(select(ODDS).order_by(ODDS.c["keys"])).each(self.from_record))

    def first(self, shard: ShardIdentifier = GLOBAL) -> Oddity | None:
        with self.readonly_db(shard=shard) as conn:
            return conn.rows(select(ODDS).order_by(ODDS.c["keys"])).fetch_any(self.from_record)

    def by_annotated_function(self, shard: ShardIdentifier = GLOBAL) -> list[str]:
        with self.readonly_db(shard=shard) as conn:
            return conn.rows(select(ODDS).order_by(ODDS.c["keys"])).fetch(class_of)

    def by_lambda(self, shard: ShardIdentifier = GLOBAL) -> list[Any]:
        with self.readonly_db(shard=shard) as conn:
            return conn.rows(select(ODDS).order_by(ODDS.c["keys"])).fetch(lambda row: row)

    def in_transaction(self, shard: ShardIdentifier = GLOBAL) -> list[Oddity]:
        with self.tx_db_else_db(shard=shard) as conn:
            return conn.rows(select(ODDS).order_by(ODDS.c["keys"])).fetch(self.from_record)


def class_of(record: OddsRecord) -> str:
    """A mapper of the user's own, typed by the record: handed the record class."""
    assert type(record) is OddsRecord, type(record)
    return record.column_class()


def _prepare(registry: Any) -> None:
    for shard in registry.shards:
        engine = registry.manager(shard).primary.sqlalchemy_engine
        with engine.begin() as conn:
            if hasattr(conn, "run_sync"):
                conn.run_sync(metadata.create_all)
            else:
                metadata.create_all(conn)
            conn.execute(ODDS.delete())


@pytest.fixture(autouse=True)
def _forget() -> None:
    SEEN.clear()
    MADE.clear()
    WATCH.clear()


@pytest.fixture
def odds(registry, clock):
    _prepare(registry)
    return OddityRepository(Oddity, ODDS, registry, clock=clock)


@pytest.fixture
def sharded_odds(sharded_registry, clock):
    _prepare(sharded_registry)
    return OddityRepository(
        Oddity, ODDS, sharded_registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
    )


def an_oddity(n: int, shard: ShardIdentifier = GLOBAL, filled: int | None = 1) -> Oddity:
    return Oddity(
        id=ShardedId[Oddity].generate(shard),
        state=OddityState.ACTIVE,
        version=1,
        klass=f"class-{n}",
        keys=f"keys-{n:02d}",
        items=n,
        order_date=datetime(2026, 2, 1 + n % 27, 9, 30),
        two_fa=None if n % 2 else f"2fa-{n}",
        filled=filled,
        created_date=NOW,
    )


def stored(repo: Any, shard: ShardIdentifier = GLOBAL) -> dict[UUID, dict[str, Any]]:
    """What the table holds, by id, read with plain SQL by the columns' real names."""
    with repo.db(shard=shard) as conn:
        return {
            row["id"]: {k: v for k, v in row.items() if k not in ("id", "created_date")}
            for row in (r._mapping for r in conn.execute(select(ODDS)))
        }


def _sent(multiparams: Any, params: Any) -> list[Any]:
    """The rows an INSERT is given, as `before_execute` sees them: a batch of one arrives as
    `params`, a bigger one as `multiparams`."""
    return list(multiparams) if multiparams else [params]


def _engine(repo: Any, shard: ShardIdentifier = GLOBAL) -> Any:
    engine = repo.registry.manager(shard).primary.sqlalchemy_engine
    return getattr(engine, "sync_engine", engine)


# ------------------------------------------------------------------ odd names, round trip


def test_columns_named_anything_go_in_by_keyword_and_come_back_by_key_and_method(odds):
    one = odds.add(an_oddity(1))
    assert (one.klass, one.keys, one.items, one.two_fa) == ("class-1", "keys-01", 1, None)
    row = stored(odds)[one.id.value]
    assert row["class"] == "class-1" and row["keys"] == "keys-01" and row["items"] == 1
    assert row["order date"] == datetime(2026, 2, 2, 9, 30) and row["2fa"] is None

    assert odds.get_by_id(one.id.value) == odds.find_by_id(one.id.value)
    assert odds.get_by_id(one.id.value).order_date == datetime(2026, 2, 2, 9, 30)
    assert [o.klass for o in odds.find_all_where(ODDS.c["class"] == "class-1")] == ["class-1"]
    assert odds.find_one_where(ODDS.c["2fa"].is_(None)).id == one.id
    assert [o.id for o in odds.find_all_by_ids([one.id.value])] == [one.id]

    updated = odds.update(one.evolve(klass="class-x", keys="keys-x", two_fa="2fa-x", items=None))
    assert (updated.klass, updated.keys, updated.two_fa, updated.items) == (
        "class-x",
        "keys-x",
        "2fa-x",
        None,
    )
    row = stored(odds)[one.id.value]
    assert (row["class"], row["keys"], row["2fa"], row["items"], row["version"]) == (
        "class-x",
        "keys-x",
        "2fa-x",
        None,
        2,
    )


@pytest.mark.parametrize("size", [2, 30])
@pytest.mark.parametrize("read_back", [True, False], ids=["update_all", "update_all_no_result"])
def test_a_batch_round_trips_every_awkward_column(odds, size, read_back):
    """`add_all` and `update_all` - the `VALUES` join on PostgreSQL, the `UNION ALL` elsewhere -
    bind each column by its real name."""
    added = odds.add_all([an_oddity(n) for n in range(size)])
    changed = [
        o.evolve(klass=f"new-{n}", two_fa=f"new-2fa-{n}", order_date=NOW)
        for n, o in enumerate(added)
    ]
    if read_back:
        returned = odds.update_all(changed)
        assert [(o.klass, o.two_fa) for o in returned] == [
            (f"new-{n}", f"new-2fa-{n}") for n in range(size)
        ]
    else:
        odds.update_all_no_result(changed)
    rows = stored(odds)
    assert [(rows[o.id.value]["class"], rows[o.id.value]["2fa"]) for o in added] == [
        (f"new-{n}", f"new-2fa-{n}") for n in range(size)
    ]
    assert {rows[o.id.value]["order date"] for o in added} == {NOW}
    assert sorted(o.keys for o in odds.find_all()) == [f"keys-{n:02d}" for n in range(size)]


# ------------------------------------------------------------------ a column the database fills


def test_a_column_left_unset_is_not_sent_and_the_database_fills_it(odds):
    sent: list[Any] = []

    def capture(conn, clauseelement, multiparams, params, execution_options):
        if getattr(clauseelement, "is_insert", False):
            sent.append(_sent(multiparams, params))

    event.listen(_engine(odds), "before_execute", capture)
    try:
        one = odds.add(an_oddity(1, filled=None))
        two = odds.add(an_oddity(2, filled=42))
    finally:
        event.remove(_engine(odds), "before_execute", capture)

    (first,), (second,) = sent
    assert "filled" not in first, "UNSET leaves the key out, so the INSERT leaves the column out"
    assert second["filled"] == 42
    assert stored(odds)[one.id.value]["filled"] == 7, "the database filled it"
    assert stored(odds)[two.id.value]["filled"] == 42
    assert odds.get_by_id(one.id.value).filled == 7


def test_an_update_leaves_a_column_left_unset_as_it_is(odds):
    one = odds.add(an_oddity(1, filled=5))
    odds.update(one.evolve(filled=None, klass="again"))
    assert stored(odds)[one.id.value]["filled"] == 5
    assert stored(odds)[one.id.value]["class"] == "again"


# ------------------------------------------------------------------ what from_record is handed


@pytest.mark.parametrize("where", ["one database", "a shard of two"])
def test_from_record_is_handed_the_record_class_on_every_read_path(odds, sharded_odds, where):
    repo = odds if where == "one database" else sharded_odds
    shard = GLOBAL if where == "one database" else MEXICO
    paths: dict[str, Any] = {}

    def through(name: str, call: Any) -> Any:
        SEEN.clear()
        result = call()
        paths[name] = set(SEEN)
        return result

    one = through("add", lambda: repo.add(an_oddity(1, shard)))
    through("add_all", lambda: repo.add_all([an_oddity(2, shard), an_oddity(3, shard)]))
    through("get_by_id", lambda: repo.get_by_id(one.id.value))
    through("find_by_id", lambda: repo.find_by_id(one.id.value))
    through("find_all_by_ids", lambda: repo.find_all_by_ids([one.id.value]))
    through("find_all_where", lambda: repo.find_all_where(ODDS.c["keys"] == "keys-01"))
    through("find_one_where", lambda: repo.find_one_where(ODDS.c["keys"] == "keys-01"))
    through("find_all", lambda: repo.find_all())
    one = through("update", lambda: repo.update(one.evolve(klass="x")))
    through("update_all", lambda: repo.update_all(repo.find_all()))
    through("fetch", lambda: repo.by_fetch(shard))
    through("each", lambda: repo.by_each(shard))
    through("fetch_any", lambda: repo.first(shard))
    through("tx.rows", lambda: repo.in_transaction(shard))

    assert paths == {name: {OddsRecord} for name in paths}, paths
    assert len(repo.by_fetch(shard)) == 3
    assert stored(repo, shard), "on the shard the ids name"


def test_a_mapper_gets_the_record_its_parameter_names_and_a_lambda_the_mapping(odds):
    odds.add(an_oddity(1))
    assert odds.by_annotated_function() == ["class-1"]
    (row,) = odds.by_lambda()
    assert isinstance(row, RowMapping), "a lambda says nothing, so it reads the row's mapping"
    assert row["order date"] == datetime(2026, 2, 2, 9, 30)


# ------------------------------------------------------------------ the very object


def test_the_insert_is_given_the_very_records_to_record_returned(odds):
    sent: list[Any] = []

    def capture(conn, clauseelement, multiparams, params, execution_options):
        if getattr(clauseelement, "is_insert", False):
            sent.extend(_sent(multiparams, params))

    event.listen(_engine(odds), "before_execute", capture)
    try:
        odds.add_all([an_oddity(n) for n in range(3)])
        odds.add_all_no_result([an_oddity(n) for n in range(3, 5)])
    finally:
        event.remove(_engine(odds), "before_execute", capture)

    assert len(sent) == 5
    assert all(a is b for a, b in zip(sent, MADE, strict=True)), "no copy on the way in"
    assert all(type(record) is OddsRecord for record in sent)


READS: list[tuple[int, str]] = []


class _Watched(OddsRecord):
    """A record that notes each key read from it, and from which object."""

    __slots__ = ()

    def __getitem__(self, key: str) -> Any:  # type: ignore[override]
        READS.append((id(self), key))
        return dict.__getitem__(self, key)


@pytest.mark.parametrize("size", [1, 3])
def test_an_update_reads_its_values_from_the_very_records_to_record_returned(odds, size):
    added = odds.add_all([an_oddity(n) for n in range(size)])
    MADE.clear()
    READS.clear()
    WATCH.append(True)
    try:
        odds.update_all_no_result([o.evolve(klass="u") for o in added])
    finally:
        WATCH.clear()

    read = set(READS)
    for record in MADE:
        written = {key for obj, key in read if obj == id(record)}
        assert set(ODDS.c.keys()) - {"filled"} <= written | {"id"}, written
    assert {o["class"] for o in stored(odds).values()} == {"u"}
