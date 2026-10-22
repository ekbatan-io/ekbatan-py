"""`Db.rows` and what comes back from it.

In-memory SQLite, no fixtures: the behaviour under test is a mapper applied to rows, and the
only thing a real database would add here is a second of startup.

The distinctions worth pinning are the ones a reader will assume they already know.
`fetch_first` and `fetch_one_or_none` differ only in what a *second* row means, and picking the
wrong one is invisible until the day the data has two.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any, Literal, overload

import pytest
from sqlalchemy import (
    Column,
    Connection,
    Integer,
    MetaData,
    Row,
    RowMapping,
    String,
    Table,
    create_engine,
    select,
)
from sqlalchemy.exc import MultipleResultsFound, NoResultFound

from ekbatan.fetching import Db, FetchResult

METADATA = MetaData()

ANIMALS = Table(
    "animals",
    METADATA,
    Column("id", Integer, primary_key=True),
    Column("name", String(32), nullable=False),
    Column("legs", Integer, nullable=False),
)


@pytest.fixture
def db() -> Iterator[Db]:
    engine = create_engine("sqlite://")
    METADATA.create_all(engine)
    with Db(engine) as conn:
        conn.execute(
            ANIMALS.insert(),
            [
                {"id": 1, "name": "ant", "legs": 6},
                {"id": 2, "name": "bee", "legs": 6},
                {"id": 3, "name": "cat", "legs": 4},
            ],
        )
        conn.commit()
        yield conn
    engine.dispose()


def named(row: RowMapping) -> str:
    return str(row["name"])


class AnimalsRecord(dict[str, Any]):
    """A row of `animals`."""

    __slots__ = ()

    def __init__(
        self,
        *,
        id: int,
        name: str,
        legs: int,
    ) -> None:
        super().__init__(
            id=id,
            name=name,
            legs=legs,
        )

    if TYPE_CHECKING:

        @overload  # type: ignore[override]
        def __getitem__(self, key: Literal["id"]) -> int: ...
        @overload
        def __getitem__(self, key: Literal["name"]) -> str: ...
        @overload
        def __getitem__(self, key: Literal["legs"]) -> int: ...
        def __getitem__(self, key: str) -> Any: ...

    def column_id(self) -> int:
        return self["id"]

    def column_name(self) -> str:
        return self["name"]

    def column_legs(self) -> int:
        return self["legs"]


ALL = select(ANIMALS).order_by(ANIMALS.c.id)
NONE = ALL.where(ANIMALS.c.legs == 100)
ONE = ALL.where(ANIMALS.c.name == "cat")


class TestDb:
    def test_it_is_a_connection(self, db: Db) -> None:
        """A subclass, not a wrapper - so nothing SQLAlchemy documents is missing."""
        assert isinstance(db, Connection)

    def test_execute_is_untouched(self, db: Db) -> None:
        """The reason `rows` is an addition rather than a replacement: a query with no rows to
        map still answers through `execute`, exactly as it did before."""
        assert db.execute(select(ANIMALS.c.name).where(ANIMALS.c.id == 3)).scalar_one() == "cat"
        assert db.execute(ANIMALS.delete().where(ANIMALS.c.id == 3)).rowcount == 1

    def test_rows_hands_back_a_fetch_result(self, db: Db) -> None:
        assert isinstance(db.rows(ALL), FetchResult)

    def test_execution_options_still_returns_this_connection(self, db: Db) -> None:
        """Every accessor chains `.execution_options(isolation_level=...)` onto a fresh `Db`.
        SQLAlchemy returns `self` there; this pins that the subclass survives it."""
        assert db.execution_options(stream_results=False) is db


class TestFetch:
    def test_it_maps_every_row(self, db: Db) -> None:
        assert db.rows(ALL).fetch(named) == ["ant", "bee", "cat"]

    def test_it_returns_a_list_that_is_yours(self, db: Db) -> None:
        """A `list`, not the `Sequence` SQLAlchemy's `all()` promises - so a caller can sort it
        in place without copying it first."""
        got = db.rows(ALL).fetch(named)
        got.append("dog")
        assert got == ["ant", "bee", "cat", "dog"]

    def test_empty_is_empty(self, db: Db) -> None:
        assert db.rows(NONE).fetch(named) == []

    def test_the_mapper_decides_the_type(self, db: Db) -> None:
        """Nothing about `FetchResult` is bound to a model. The same result can be read as
        whatever the caller asks for."""
        assert db.rows(ALL).fetch(lambda row: row["legs"]) == [6, 6, 4]

    def test_the_mapper_is_handed_the_row_as_a_record(self, db: Db) -> None:
        """Each row arrives read by column name - its mapping - which is what a repository's
        `from_record` takes - not the `Row`."""
        seen: list[object] = []
        db.rows(ALL).fetch(seen.append)
        assert all(isinstance(r, Mapping) and not isinstance(r, Row) for r in seen)

        def from_record(record: AnimalsRecord) -> str:
            return f"{record['name']}:{record['legs']}"

        assert db.rows(ALL).fetch(from_record) == ["ant:6", "bee:6", "cat:4"]


class TestWhatTheMapperIsHanded:
    """A mapper whose first parameter names a record class is handed that class, built from
    the row - so `record.column_name()` works as `record["name"]` does. Anything else is
    handed the row's mapping."""

    def test_a_mapper_typed_by_a_record_gets_the_record(self, db: Db) -> None:
        def named(record: AnimalsRecord) -> str:
            assert type(record) is AnimalsRecord, type(record)
            return record.column_name() + record["name"]

        assert db.rows(ALL).fetch(named) == ["antant", "beebee", "catcat"]
        assert db.rows(ONE).fetch_one(named) == "catcat"
        assert db.rows(ONE).fetch_first(named) == "catcat"
        assert db.rows(ONE).fetch_one_or_none(named) == "catcat"
        assert list(db.rows(ALL).each(named)) == ["antant", "beebee", "catcat"]
        assert list(db.rows(ALL).partitions(named, 2)) == [["antant", "beebee"], ["catcat"]]

    def test_a_lambda_gets_the_mapping(self, db: Db) -> None:
        seen: list[object] = []
        db.rows(ALL).fetch(lambda row: seen.append(row))
        assert all(isinstance(r, RowMapping) for r in seen), seen

    def test_a_decorated_mapper_is_read_through_what_it_wraps(self, db: Db) -> None:
        def named(record: AnimalsRecord) -> str:
            return record.column_name()

        @functools.wraps(named)
        def logged(*args: Any) -> str:
            return named(*args)

        assert db.rows(ALL).fetch(logged) == ["ant", "bee", "cat"]

    def test_a_method_typed_by_a_record_gets_the_record(self, db: Db) -> None:
        class Reader:
            def legs(self, record: AnimalsRecord) -> int:
                return record.column_legs()

        assert db.rows(ALL).fetch(Reader().legs) == [6, 6, 4]

    def test_an_annotation_nobody_can_read_gets_the_mapping(self, db: Db) -> None:
        def named(record: NotImportedAnywhere) -> str:  # type: ignore[name-defined]  # noqa: F821
            return str(record["name"])

        assert db.rows(ALL).fetch(named) == ["ant", "bee", "cat"]


class TestFetchFirst:
    def test_it_takes_the_first(self, db: Db) -> None:
        assert db.rows(ALL).fetch_first(named) == "ant"

    def test_absence_is_none_not_an_error(self, db: Db) -> None:
        assert db.rows(NONE).fetch_first(named) is None

    def test_a_second_row_is_ignored(self, db: Db) -> None:
        """The whole difference from `fetch_one_or_none`, and the reason both exist."""
        assert db.rows(ALL.where(ANIMALS.c.legs == 6)).fetch_first(named) == "ant"


class TestFetchOne:
    def test_exactly_one(self, db: Db) -> None:
        assert db.rows(ONE).fetch_one(named) == "cat"

    def test_none_raises(self, db: Db) -> None:
        with pytest.raises(NoResultFound):
            db.rows(NONE).fetch_one(named)

    def test_two_raises(self, db: Db) -> None:
        """`fetch_one` means one. jOOQ's `fetchOne` means *at most* one, which is why these
        names are not copied from it."""
        with pytest.raises(MultipleResultsFound):
            db.rows(ALL.where(ANIMALS.c.legs == 6)).fetch_one(named)


class TestFetchOneOrNone:
    def test_one(self, db: Db) -> None:
        assert db.rows(ONE).fetch_one_or_none(named) == "cat"

    def test_none_is_none(self, db: Db) -> None:
        assert db.rows(NONE).fetch_one_or_none(named) is None

    def test_two_raises(self, db: Db) -> None:
        """Two rows here means an invariant is broken, and saying so is the point of choosing
        this over `fetch_first`."""
        with pytest.raises(MultipleResultsFound):
            db.rows(ALL.where(ANIMALS.c.legs == 6)).fetch_one_or_none(named)


class TestEach:
    def test_it_maps_lazily(self, db: Db) -> None:
        """A caller that stops early has not paid to map the rest."""
        mapped: list[str] = []

        def counting(row: RowMapping) -> str:
            mapped.append(named(row))
            return named(row)

        first = next(iter(db.rows(ALL).each(counting)))
        assert first == "ant"
        assert mapped == ["ant"]

    def test_it_flattens_in_a_comprehension(self, db: Db) -> None:
        """What a scatter-gather across shards is made of."""
        assert [name for name in db.rows(ALL).each(named)] == ["ant", "bee", "cat"]


class TestShaping:
    def test_partitions_batches(self, db: Db) -> None:
        """Lazy, so it is a generator: the next batch is not read until it is asked for."""
        got = db.rows(ALL).yield_per(2).partitions(named)
        assert list(got) == [["ant", "bee"], ["cat"]]

    def test_partitions_takes_its_own_size(self, db: Db) -> None:
        assert list(db.rows(ALL).partitions(named, 1)) == [["ant"], ["bee"], ["cat"]]

    def test_yield_per_chains(self, db: Db) -> None:
        """It returns the result rather than a new one, so it sits ahead of the terminal."""
        result = db.rows(ALL)
        assert result.yield_per(2) is result

    def test_unique_drops_duplicates(self, db: Db) -> None:
        assert db.rows(select(ANIMALS.c.legs).order_by(ANIMALS.c.legs)).unique().fetch(
            lambda row: row["legs"]
        ) == [4, 6]

    def test_unique_takes_a_strategy(self, db: Db) -> None:
        """One per leg count, keeping whichever came first."""
        got = db.rows(ALL).unique(lambda row: row.legs).fetch(named)
        assert got == ["ant", "cat"]
