"""`Db.rows` and what comes back from it.

In-memory SQLite, no fixtures: the behaviour under test is a mapper applied to rows, and the
only thing a real database would add here is a second of startup.

The distinctions worth pinning are the ones a reader will assume they already know.
`fetch_first` and `fetch_one_or_none` differ only in what a *second* row means, and picking the
wrong one is invisible until the day the data has two.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import (
    Column,
    Connection,
    Integer,
    MetaData,
    Row,
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


def named(row: Row[Any]) -> str:
    return str(row.name)


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
        assert db.rows(ALL).fetch(lambda row: row.legs) == [6, 6, 4]


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

        def counting(row: Row[Any]) -> str:
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
            lambda row: row.legs
        ) == [4, 6]

    def test_unique_takes_a_strategy(self, db: Db) -> None:
        """One per leg count, keeping whichever came first."""
        got = db.rows(ALL).unique(lambda row: row.legs).fetch(named)
        assert got == ["ant", "cat"]
