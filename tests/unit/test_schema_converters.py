"""`converted()` and `sql_type()`: what a converter sees, how SQLAlchemy caches it, how it reads.

The same against every real database, through schemagen, is
`tests/integration/test_schemagen_converters.py`. These use SQLite in memory, where a statement
cache can be watched directly.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import (
    INTEGER,
    NUMERIC,
    Column,
    MetaData,
    Table,
    create_engine,
    insert,
    literal,
    select,
)
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql.cache_key import NO_CACHE

from ekbatan.schema import Converter, converted, sql_type


@dataclass(frozen=True)
class Money:
    amount: Decimal


class MoneyConverter(Converter[Decimal, Money]):
    def __init__(self) -> None:
        self.seen: list[object] = []

    def from_db(self, value: Decimal) -> Money:
        self.seen.append(value)
        return Money(value)

    def to_db(self, value: Money) -> Decimal:
        self.seen.append(value)
        return value.amount


class Plus:
    """Adds `k` on the way in, nothing on the way out: two of them differ only in state."""

    def __init__(self, k: int) -> None:
        self.k = k

    def from_db(self, value: int) -> int:
        return value

    def to_db(self, value: int) -> int:
        return value + self.k


@dataclass
class PlusData:
    """A plain dataclass: `==` by value, and so no hash - SQLAlchemy cannot key on it."""

    k: int

    def from_db(self, value: int) -> int:
        return value

    def to_db(self, value: int) -> int:
        return value + self.k


def test_values_go_through_the_converter_and_none_never_reaches_it() -> None:
    converter = MoneyConverter()
    table = Table(
        "t",
        MetaData(),
        Column("id", INTEGER, primary_key=True),
        Column("amount", converted(NUMERIC(19, 4), converter)),
    )
    db = create_engine("sqlite://")
    table.metadata.create_all(db)
    with db.begin() as connection:
        connection.execute(insert(table), [{"id": 1, "amount": Money(Decimal("2.5"))}])
        connection.execute(insert(table), [{"id": 2, "amount": None}])
        rows = connection.execute(select(table.c.amount).order_by(table.c.id)).scalars().all()
        found = connection.execute(
            select(table.c.id).where(table.c.amount == Money(Decimal("2.5")))
        ).scalar_one()

    assert rows == [Money(Decimal("2.5000")), None]
    assert found == 1
    assert None not in converter.seen


def test_any_object_with_from_db_and_to_db_is_a_converter() -> None:
    plain = converted(INTEGER(), Plus(1))  # no base class

    assert plain.converter.k == 1


def test_an_object_without_them_is_refused_by_name() -> None:
    class Half:
        def from_db(self, value: object) -> object:
            return value

    with pytest.raises(TypeError, match=r"has no to_db\(\) method. A converter is any object"):
        converted(INTEGER(), Half())  # type: ignore[arg-type]


def test_it_reads_as_what_it_is() -> None:
    @dataclass
    class Scaled:
        places: int

        def from_db(self, value: Decimal) -> Decimal:
            return value

        def to_db(self, value: Decimal) -> Decimal:
            return value

    assert repr(converted(NUMERIC(19, 4), Scaled(2))) == (
        "converted(NUMERIC(precision=19, scale=4), "
        "test_it_reads_as_what_it_is.<locals>.Scaled(places=2))"
    )
    assert repr(sql_type("vector(3)")) == "sql_type('vector(3)')"


def test_it_is_cacheable_and_says_so() -> None:
    """`cache_ok` is set, so SQLAlchemy neither warns nor gives up caching the statement."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        key = converted(INTEGER(), Plus(1))._static_cache_key
        spelled = sql_type("xml")._static_cache_key

    assert key is not NO_CACHE
    assert spelled is not NO_CACHE


def test_the_cache_key_keeps_two_converters_apart() -> None:
    """Two converters of one class with different state must not share a cached statement:
    the second would be bound by the first. Keyed on the class alone, `Plus(10)` here read 102
    (measured, with the key replaced); keyed on the converter, each gets its own."""
    db = create_engine("sqlite://")
    with db.connect() as connection:
        two = connection.execute(select(literal(100, converted(INTEGER(), Plus(2))))).scalar()
        ten = connection.execute(select(literal(100, converted(INTEGER(), Plus(10))))).scalar()

    assert (two, ten) == (102, 110)


def test_the_same_converter_is_one_cache_entry() -> None:
    same = Plus(1)

    first = converted(INTEGER(), same)._static_cache_key
    again = converted(INTEGER(), same)._static_cache_key
    other = converted(INTEGER(), Plus(1))._static_cache_key

    assert first == again
    assert first != other, "a plain class is equal only to itself"


def test_a_converter_that_cannot_be_hashed_still_caches_by_identity() -> None:
    """A plain `@dataclass` has no hash. It is keyed by identity instead of failing."""
    same = PlusData(1)

    first = converted(INTEGER(), same)._static_cache_key
    assert first == converted(INTEGER(), same)._static_cache_key
    assert first != converted(INTEGER(), PlusData(1))._static_cache_key
    db = create_engine("sqlite://")
    with db.connect() as connection:
        for _ in range(2):  # the second run is served from the cache
            assert connection.execute(select(literal(1, converted(INTEGER(), same)))).scalar() == 2


@pytest.mark.parametrize(
    ("compiler", "spelling"),
    [
        (postgresql.dialect(), "vector(3)"),
        (postgresql.dialect(), "xml"),
        (mysql.dialect(), "inet6"),
        (sqlite.dialect(), "vector(3)"),
    ],
)
def test_sql_type_builds_the_column_as_spelled(compiler: object, spelling: str) -> None:
    table = Table(
        "t",
        MetaData(),
        Column("plain", sql_type(spelling)),
        Column("wrapped", converted(sql_type(spelling), Plus(0))),
    )

    ddl = str(CreateTable(table).compile(dialect=compiler))  # type: ignore[arg-type]

    assert f"plain {spelling}" in ddl
    assert f"wrapped {spelling}" in ddl
