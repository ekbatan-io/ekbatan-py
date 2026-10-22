"""An update writes the record `to_record` returns - every column in it - as an insert does.

Until `EKB-0480` the columns an UPDATE set were the model's *field names* that happened to be
columns too. A column mapped from a field of another name - `nick` into `nickname`, `klass` into
`class` - was written by `add` and then never by `update`: the call succeeded, the version moved
on, and the old value stayed in the row. Java's update is built from `toRecord(model)`, every
column the record carries, and so is this one now.

Each test reads the row back with plain SQL, not through the repository: the repository's own
read would rebuild the model from the row and could not tell a written value from a stale one
any better than the caller could.

Every shape an update takes is here: one row (`UPDATE … WHERE id = ? AND version = ?`), and a
batch, which is a `VALUES` join on PostgreSQL and a `UNION ALL` join everywhere else - 2 rows
and 50, with and without reading back, on one database and on a shard of two. Both modes, every
database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, overload
from uuid import UUID

import pytest
from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table, Uuid, select, text

from ekbatan import AbstractRepository, Model
from ekbatan.errors import ValidationError
from ekbatan.ids import ShardedId
from ekbatan.schema import UNSET, Unset
from ekbatan.sharding import EmbeddedBitsShardingStrategy, ShardIdentifier
from modes import rebased

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)

metadata = MetaData()
PEOPLE = Table(
    "renamed_people",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("state", String(24), nullable=False),
    # Mapped from the field `nick`.
    Column("nickname", String(64), nullable=False),
    # A keyword, mapped from the field `klass`. Filled by the database when the record leaves
    # it out.
    Column("class", String(32), nullable=True, server_default=text("'filled'")),
    Column("created_date", DateTime, nullable=False),
    Column("updated_date", DateTime, nullable=False),
)


class PeopleRecord(dict[str, Any]):
    """A row of `renamed_people`. `class` is a keyword, so it is passed as `class_=`; the
    database fills it when the record leaves it out (`UNSET`, the default)."""

    __slots__ = ()

    def __init__(
        self,
        *,
        id: UUID,
        version: int,
        state: str,
        nickname: str,
        class_: str | Unset | None = UNSET,
        created_date: datetime,
        updated_date: datetime,
    ) -> None:
        super().__init__(
            id=id,
            version=version,
            state=state,
            nickname=nickname,
            created_date=created_date,
            updated_date=updated_date,
        )
        if class_ is not UNSET:
            self["class"] = class_

    if TYPE_CHECKING:

        @overload  # type: ignore[override]
        def __getitem__(self, key: Literal["id"]) -> UUID: ...
        @overload
        def __getitem__(self, key: Literal["version"]) -> int: ...
        @overload
        def __getitem__(self, key: Literal["state"]) -> str: ...
        @overload
        def __getitem__(self, key: Literal["nickname"]) -> str: ...
        @overload
        def __getitem__(self, key: Literal["class"]) -> str | None: ...
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

    def column_nickname(self) -> str:
        return self["nickname"]

    def column_class(self) -> str | None:
        return self["class"]

    def column_created_date(self) -> datetime:
        return self["created_date"]

    def column_updated_date(self) -> datetime:
        return self["updated_date"]


NOW = datetime(2026, 1, 1, 12, 0)


class PersonState(Enum):
    ACTIVE = "ACTIVE"
    DELETED = "DELETED"


@dataclass(frozen=True, kw_only=True)
class Person(Model[ShardedId["Person"], PersonState]):
    nick: str
    #: `None` means "leave it to the database": the record then has no `class` key at all.
    klass: str | None


@rebased
class PersonRepository(AbstractRepository[Person, PeopleRecord, ShardedId[Person]]):
    """No field shares its column's name but the base ones: `nick` is `nickname`, `klass` is
    `class`."""

    def to_record(self, person: Person) -> PeopleRecord:
        return PeopleRecord(
            id=person.id.value,
            version=person.version,
            state=person.state.name,
            nickname=person.nick,
            class_=UNSET if person.klass is None else person.klass,
            created_date=person.created_date,
            updated_date=person.updated_date or person.created_date,
        )

    def from_record(self, record: PeopleRecord) -> Person:
        return Person(
            id=ShardedId[Person](record.column_id()),
            version=record.column_version(),
            state=PersonState[record.column_state()],
            nick=record.column_nickname(),
            klass=record.get("class"),
            created_date=record.column_created_date(),
            updated_date=record.column_updated_date(),
        )


def _prepare(registry: Any) -> None:
    """This module brings its own table, so it creates and empties it on every shard itself.
    `run_sync` on an async connection, as SQLAlchemy's asyncio API says: DDL helpers are sync."""
    for shard in registry.shards:
        engine = registry.manager(shard).primary
        with engine.begin() as conn:
            if hasattr(conn, "run_sync"):
                conn.run_sync(metadata.create_all)
            else:
                metadata.create_all(conn)
            conn.execute(PEOPLE.delete())


@pytest.fixture
def people(registry, clock):
    _prepare(registry)
    return PersonRepository(Person, PEOPLE, registry, clock=clock)


@pytest.fixture
def sharded_people(sharded_registry, clock):
    _prepare(sharded_registry)
    return PersonRepository(
        Person, PEOPLE, sharded_registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
    )


def a_person(n: int, shard: ShardIdentifier = GLOBAL, klass: str | None = "first") -> Person:
    return Person(
        id=ShardedId[Person].generate(shard),
        state=PersonState.ACTIVE,
        version=1,
        nick=f"nick-{n}",
        klass=klass,
        created_date=NOW,
    )


def stored_rows(repo: Any, shard: ShardIdentifier = GLOBAL) -> dict[UUID, tuple[Any, ...]]:
    """What the table holds, by id: (version, nickname, class) - read with plain SQL."""
    stmt = select(PEOPLE.c.id, PEOPLE.c.version, PEOPLE.c.nickname, PEOPLE.c["class"])
    with repo.db(shard=shard) as conn:
        return {row[0]: tuple(row[1:]) for row in conn.execute(stmt)}


def changed(person: Person, n: int) -> Person:
    return person.evolve(nick=f"renamed-{n}", klass=f"second-{n}")


# --------------------------------------------------------------------------- one row


@pytest.mark.parametrize("read_back", [True, False], ids=["update", "update_no_result"])
def test_one_update_writes_a_column_mapped_from_a_field_of_another_name(people, read_back):
    stored = people.add(a_person(1))
    assert stored_rows(people)[stored.id.value] == (1, "nick-1", "first"), "add writes them"

    if read_back:
        returned = people.update(changed(stored, 1))
        assert (returned.nick, returned.klass, returned.version) == ("renamed-1", "second-1", 2)
    else:
        people.update_no_result(changed(stored, 1))

    assert stored_rows(people)[stored.id.value] == (2, "renamed-1", "second-1")
    found = people.get(stored.id)
    assert (found.nick, found.klass) == ("renamed-1", "second-1")


# --------------------------------------------------------------------------- a batch


@pytest.mark.parametrize("size", [2, 50])
@pytest.mark.parametrize("read_back", [True, False], ids=["update_all", "update_all_no_result"])
def test_a_batch_update_writes_columns_mapped_from_fields_of_other_names(people, size, read_back):
    """Two rows and fifty: the `VALUES` join on PostgreSQL, the `UNION ALL` join elsewhere."""
    stored = people.add_all([a_person(n) for n in range(size)])

    updated = [changed(p, n) for n, p in enumerate(stored)]
    if read_back:
        returned = people.update_all(updated)
        assert [(p.nick, p.klass) for p in returned] == [
            (f"renamed-{n}", f"second-{n}") for n in range(size)
        ]
    else:
        people.update_all_no_result(updated)

    rows = stored_rows(people)
    assert [rows[p.id.value] for p in stored] == [
        (2, f"renamed-{n}", f"second-{n}") for n in range(size)
    ]


def test_the_same_batch_shape_twice_writes_both_times(people):
    """A batch statement is composed once per size and re-bound after; the second, re-bound
    run has to write the renamed columns as the first did."""
    stored = people.add_all([a_person(n) for n in range(3)])
    once = people.update_all([changed(p, n) for n, p in enumerate(stored)])
    people.update_all([p.evolve(nick=f"again-{n}", klass=None) for n, p in enumerate(once)])

    # `klass=None` leaves `class` out of the record, so the database keeps what it had.
    rows = stored_rows(people)
    assert [rows[p.id.value] for p in stored] == [
        (3, f"again-{n}", f"second-{n}") for n in range(3)
    ]


# --------------------------------------------------------------------------- sharded


@pytest.mark.parametrize("size", [1, 2, 50])
def test_an_update_on_another_shard_writes_the_renamed_columns(sharded_people, size):
    stored = sharded_people.add_all([a_person(n, MEXICO) for n in range(size)])

    if size == 1:
        sharded_people.update(changed(stored[0], 0))
    else:
        sharded_people.update_all([changed(p, n) for n, p in enumerate(stored)])

    rows = stored_rows(sharded_people, MEXICO)
    assert [rows[p.id.value] for p in stored] == [
        (2, f"renamed-{n}", f"second-{n}") for n in range(size)
    ]
    assert stored_rows(sharded_people, GLOBAL) == {}, "nothing landed on the other shard"


# --------------------------------------------------------------- what the record leaves out


def test_a_column_the_record_leaves_out_is_not_written(people):
    """`class` is left to the database when `klass` is None: the insert does not send it, so the
    default fills it, and an update does not send it, so whatever is there stays."""
    stored = people.add(a_person(1, klass=None))
    assert stored_rows(people)[stored.id.value] == (1, "nick-1", "filled")

    with people.db() as conn:
        conn.execute(
            PEOPLE.update().where(PEOPLE.c.id == stored.id.value).values({"class": "by hand"})
        )
    people.update(stored.evolve(nick="renamed", klass=None))

    assert stored_rows(people)[stored.id.value] == (2, "renamed", "by hand")


def test_a_batch_update_whose_records_carry_different_columns_is_refused(people):
    """One statement writes one set of columns. Binding the first record's and dropping what
    the others carry beyond it is how a value goes missing without a word - so it is refused,
    before anything is sent."""
    first, second = people.add_all([a_person(1), a_person(2)])

    with pytest.raises(ValidationError, match=r"different columns.*row 1 lacks \['class'\]"):
        people.update_all([changed(first, 1), second.evolve(nick="renamed-2", klass=None)])

    rows = stored_rows(people)
    assert rows[first.id.value] == (1, "nick-1", "first"), "nothing was written"
    assert rows[second.id.value] == (1, "nick-2", "first")


def test_a_batch_insert_whose_records_carry_different_columns_is_refused(people):
    """The same for an insert: one INSERT for a batch is compiled from the first row's keys, so
    a later row's extra key would be dropped."""
    with pytest.raises(ValidationError, match=r"different columns.*row 1 has \['class'\] besides"):
        people.add_all([a_person(1, klass=None), a_person(2, klass="mine")])

    assert stored_rows(people) == {}, "nothing was written"
