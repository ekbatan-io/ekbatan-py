"""The type table, proven on each database: every common SQL type, through the whole pipeline.

For each database, one migration declares a column of every type below. Then, for real:

1. ``generate`` migrates a fresh database and writes ``schema.py``;
2. the module is imported - round 2 found common types (MySQL's BOOLEAN) that stopped it
   importing at all;
3. ``metadata.create_all`` builds a second fresh database from it;
4. each case checks the type it was written as, that ``create_all`` built the column the
   migration built (or the documented one, where a forced helper builds its own), and that a
   value goes in and comes back - through the migrated database and the rebuilt one, and as a
   ``WHERE`` that finds it.

    uv run pytest tests/integration/test_schemagen_types.py --dialects=all
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import Range

from backends import Backend
from ekbatan.schemagen import generate
from schemagen_support import (
    column_sources,
    engine,
    fresh,
    load,
    migrations,
    normal,
    notes,
    raw_types,
    unused_and_undefined,
)

SAME = object()
WHEN = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
TEHRAN = timezone(timedelta(hours=3, minutes=30))
ID = uuid.UUID("0190a8a1-1c2b-7c3d-8e4f-5a6b7c8d9e0f")


@dataclass(frozen=True)
class Case:
    id: str
    sql: str
    source: str
    value: Any
    expected: Any = SAME
    column: str = ""
    #: The type `create_all` builds, where a forced helper builds its own on purpose.
    rebuilt: str | None = None
    #: Applied to what comes back before comparing.
    seen: Callable[[Any], Any] | None = None
    #: Whether `WHERE column = value` is meaningful for the type.
    where: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def name(self) -> str:
        return self.column or "c_" + re.sub(r"\W", "_", self.id)


def _str(value: Any) -> str:
    return str(value)


POSTGRES = (
    Case("smallint", "SMALLINT", "SMALLINT()", -7),
    Case("integer", "INTEGER", "INTEGER()", 2147483647),
    Case("bigint", "BIGINT", "BIGINT()", 9223372036854775807),
    Case("numeric_19_4", "NUMERIC(19, 4)", "NUMERIC(precision=19, scale=4)", Decimal("12345.6789")),
    Case("numeric", "NUMERIC", "NUMERIC()", Decimal("1.000000000000000000001")),
    Case("decimal_10_2", "DECIMAL(10, 2)", "NUMERIC(precision=10, scale=2)", Decimal("99.95")),
    Case("real", "REAL", "REAL()", 1.5),
    Case("double", "DOUBLE PRECISION", "DOUBLE_PRECISION()", 1.2345678901234),
    Case("boolean", "BOOLEAN", "BOOLEAN()", True),
    Case("char_3", "CHAR(3)", "CHAR(length=3)", "EUR"),
    Case("varchar_80", "VARCHAR(80)", "VARCHAR(length=80)", "a name"),
    # Round 2, N2 #2: named like an id and 36 long, and still text - PostgreSQL has a uuid type.
    Case("varchar_36_id", "VARCHAR(36)", "VARCHAR(length=36)", "not-a-uuid", column="ext_id"),
    Case("varchar", "VARCHAR", "VARCHAR()", "any length"),
    Case("text", "TEXT", "TEXT()", "x" * 10_000),
    Case("citext", "CITEXT", "postgresql.CITEXT()", "MiXeD"),
    Case("bytea", "BYTEA", "LargeBinary()", b"\x00\x01\xfe\xff", where=False),
    Case("uuid", "UUID", "uuid_column()", ID),
    Case("json", "JSON", "JSON(none_as_null=True)", {"a": [1, "b"]}, where=False),
    Case("jsonb", "JSONB", "json_column()", {"k": "v", "n": 2}, where=False),
    Case("timestamp", "TIMESTAMP", "timestamp_column()", WHEN),
    Case(
        "timestamp_3",
        "TIMESTAMP(3)",
        "timestamp_column()",
        WHEN.replace(microsecond=123000),
        rebuilt="timestamp without time zone",
    ),
    Case(
        "timestamptz",
        "TIMESTAMPTZ",
        "TIMESTAMP(timezone=True)",
        WHEN.astimezone(TEHRAN),
        expected=WHEN,
    ),
    # Zoned, so no forced type takes it: PostgreSQL's own TIMESTAMP, to keep the precision.
    Case(
        "timestamptz_3",
        "TIMESTAMPTZ(3)",
        "postgresql.TIMESTAMP(timezone=True, precision=3)",
        WHEN.replace(microsecond=123000).astimezone(TEHRAN),
        expected=WHEN.replace(microsecond=123000),
    ),
    Case("date", "DATE", "DATE()", date(2026, 1, 2)),
    Case("time", "TIME", "TIME()", time(12, 34, 56, 789000)),
    Case("time_3", "TIME(3)", "postgresql.TIME(precision=3)", time(1, 2, 3, 456000)),
    Case("timetz", "TIMETZ", "TIME(timezone=True)", time(1, 2, 3, tzinfo=UTC)),
    Case("interval", "INTERVAL", "postgresql.INTERVAL()", timedelta(days=1, seconds=5)),
    Case(
        "interval_day_second_3",
        "INTERVAL DAY TO SECOND(3)",
        'postgresql.INTERVAL(precision=3, fields="day to second")',
        timedelta(hours=2, microseconds=123000),
    ),
    Case("text_array", "TEXT[]", "postgresql.ARRAY(TEXT())", ["a", "b"]),
    Case("integer_array", "INTEGER[]", "postgresql.ARRAY(INTEGER())", [1, 2, 3]),
    Case("uuid_array", "UUID[]", "postgresql.ARRAY(UUID())", [ID]),
    Case("inet", "INET", "postgresql.INET()", "10.0.0.1", seen=_str),
    Case("cidr", "CIDR", "postgresql.CIDR()", "10.0.0.0/8", seen=_str),
    Case("macaddr", "MACADDR", "postgresql.MACADDR()", "08:00:2b:01:02:03"),
    Case("macaddr8", "MACADDR8", "postgresql.MACADDR8()", "08:00:2b:01:02:03:04:05"),
    # `WHERE` off for these three: SQLAlchemy binds a str compared with them as VARCHAR, which
    # PostgreSQL will not compare - true of a hand-written table too, so not the generator's.
    Case("money", "MONEY", "postgresql.MONEY()", "1.50", expected="$1.50", where=False),
    Case("bit_3", "BIT(3)", "postgresql.BIT(length=3)", "101", where=False),
    Case("varbit_8", "VARBIT(8)", "postgresql.BIT(length=8, varying=True)", "1011", where=False),
    Case("tsvector", "TSVECTOR", "postgresql.TSVECTOR()", "'a' 'b'", where=False),
    Case("int4range", "INT4RANGE", "postgresql.INT4RANGE()", Range(1, 5)),
    Case(
        "tsrange",
        "TSRANGE",
        "postgresql.TSRANGE()",
        Range(datetime(2026, 1, 1), datetime(2026, 2, 1)),
    ),
    Case("oid", "OID", "postgresql.OID()", 42),
    Case("enum", "mood", 'Enum("happy", "sad", name="mood")', "sad"),
    # Types SQLAlchemy has no class for: written as PostgreSQL spells them, so create_all builds
    # the same column, and the value is what the driver gives - text. No `=` for either.
    Case("xml", "XML", 'sql_type("xml")', "<a>1</a>", where=False),
    Case("point", "POINT", 'sql_type("point")', "(1,2)", where=False),
    # An array of a type SQLAlchemy cannot read: the whole array as PostgreSQL spells it. The
    # driver reads it back as a list of the items' text.
    Case("xml_array", "XML[]", 'sql_type("xml[]")', "{<a/>}", expected=["<a/>"], where=False),
)

MYSQL = (
    Case("tinyint", "TINYINT", "mysql.TINYINT()", -5),
    Case("tinyint_unsigned", "TINYINT UNSIGNED", "mysql.TINYINT(unsigned=True)", 250),
    # Round 2, N2 #4: MySQL's BOOLEAN is TINYINT(1), and it made the module fail to import.
    Case("boolean", "BOOLEAN", "BOOLEAN()", True),
    Case("tinyint_1", "TINYINT(1)", "BOOLEAN()", False),
    Case("smallint", "SMALLINT", "SMALLINT()", -300),
    Case("smallint_unsigned", "SMALLINT UNSIGNED", "mysql.SMALLINT(unsigned=True)", 65000),
    Case("mediumint", "MEDIUMINT", "mysql.MEDIUMINT()", -8000000),
    Case("mediumint_unsigned", "MEDIUMINT UNSIGNED", "mysql.MEDIUMINT(unsigned=True)", 16000000),
    Case("int", "INT", "INTEGER()", -2147483648),
    Case("int_unsigned", "INT UNSIGNED", "mysql.INTEGER(unsigned=True)", 4294967295),
    Case("bigint", "BIGINT", "BIGINT()", -9223372036854775808),
    Case("bigint_unsigned", "BIGINT UNSIGNED", "mysql.BIGINT(unsigned=True)", 18446744073709551615),
    Case(
        "decimal_19_4", "DECIMAL(19, 4)", "DECIMAL(precision=19, scale=4)", Decimal("-12345.6789")
    ),
    Case("numeric_10_2", "NUMERIC(10, 2)", "DECIMAL(precision=10, scale=2)", Decimal("99.95")),
    # UNSIGNED on a DECIMAL or FLOAT, and FLOAT(M,D): deprecated by MySQL, still accepted, and
    # only MySQL's own types can say them.
    Case(
        "decimal_unsigned",
        "DECIMAL(10, 2) UNSIGNED",
        "mysql.DECIMAL(precision=10, scale=2, unsigned=True)",
        Decimal("99.95"),
    ),
    Case(
        "float_7_3_unsigned",
        "FLOAT(7, 3) UNSIGNED",
        "mysql.FLOAT(precision=7, scale=3, unsigned=True)",
        1.5,
        where=False,
    ),
    Case("float", "FLOAT", "FLOAT()", 1.5),
    # A float, as Java reads a Double - MySQL's own DOUBLE type would hand back a Decimal.
    Case("double", "DOUBLE", "DOUBLE()", 1.25),
    Case("real", "REAL", "DOUBLE()", 2.5),
    Case("bit_1", "BIT(1)", "mysql.BIT(length=1)", 1),
    Case("bit_8", "BIT(8)", "mysql.BIT(length=8)", 200),
    Case("char_3", "CHAR(3)", "CHAR(length=3)", "EUR"),
    Case("varchar_80", "VARCHAR(80)", "VARCHAR(length=80)", "a name"),
    # Round 2, N2 #2: Java forces only CHAR(36); a VARCHAR(36) id stays text.
    Case("varchar_36_id", "VARCHAR(36)", "VARCHAR(length=36)", "not-a-uuid", column="ext_id"),
    # No uuid guess from the name: a CHAR(36) id is text unless the project forces it (see
    # test_schemagen's test_a_char_36_id_is_text_until_the_project_says_it_holds_a_uuid).
    Case("char_36_id", "CHAR(36)", "CHAR(length=36)", str(ID), column="legacy_id"),
    Case("char_36_not_id", "CHAR(36)", "CHAR(length=36)", "a" * 36, column="country_name"),
    Case(
        "varchar_latin1",
        "VARCHAR(10) CHARACTER SET latin1",
        'mysql.VARCHAR(length=10, charset="latin1", collation="latin1_swedish_ci")',
        "café",
    ),
    Case(
        "varchar_bin",
        "VARCHAR(10) COLLATE utf8mb4_bin",
        'mysql.VARCHAR(length=10, charset="utf8mb4", collation="utf8mb4_bin")',
        "Abc",
    ),
    Case("tinytext", "TINYTEXT", "mysql.TINYTEXT()", "short", where=False),
    Case("text", "TEXT", "TEXT()", "x" * 10_000, where=False),
    Case("mediumtext", "MEDIUMTEXT", "mysql.MEDIUMTEXT()", "y" * 70_000, where=False),
    Case("longtext", "LONGTEXT", "mysql.LONGTEXT()", "z" * 70_000, where=False),
    Case("binary_16", "BINARY(16)", "BINARY(length=16)", ID.bytes),
    Case("varbinary_64", "VARBINARY(64)", "VARBINARY(length=64)", b"\x00\x01\xff"),
    Case("tinyblob", "TINYBLOB", "mysql.TINYBLOB()", b"\x01", where=False),
    Case("blob", "BLOB", "BLOB()", b"\x00" * 300, where=False),
    Case("mediumblob", "MEDIUMBLOB", "mysql.MEDIUMBLOB()", b"\x02" * 70_000, where=False),
    Case("longblob", "LONGBLOB", "mysql.LONGBLOB()", b"\x03" * 70_000, where=False),
    Case("json", "JSON", "json_column()", {"a": [1, "b"]}, where=False),
    Case("datetime_6", "DATETIME(6)", "timestamp_column()", WHEN),
    Case(
        "datetime",
        "DATETIME",
        "timestamp_column()",
        WHEN.replace(microsecond=0),
        rebuilt="datetime(6)",
    ),
    Case(
        "timestamp_6",
        "TIMESTAMP(6) NULL",
        "timestamp_column()",
        WHEN,
        rebuilt="datetime(6)",
    ),
    Case("date", "DATE", "DATE()", date(2026, 1, 2)),
    Case("time", "TIME", "TIME()", time(12, 34, 56)),
    Case("time_6", "TIME(6)", "mysql.TIME(fsp=6)", time(1, 2, 3, 456789)),
    Case("year", "YEAR", "mysql.YEAR()", 2026),
    Case("enum", "ENUM('NEW', 'DONE')", 'Enum("NEW", "DONE")', "DONE"),
    Case("set", "SET('a', 'b')", 'mysql.SET("a", "b")', {"a", "b"}, where=False),
)

MARIADB = (
    *MYSQL,
    # MariaDB's own UUID type: no forced type, as in Java's MariaDB build.
    Case("uuid", "UUID", "UUID()", ID),
    # A type SQLAlchemy has no class for: written as MariaDB spells it, read back as text.
    Case("inet6", "INET6", 'sql_type("inet6")', "2001:db8::1"),
)

SQLITE = (
    Case("integer", "INTEGER", "INTEGER()", 7),
    Case("bigint", "BIGINT", "BIGINT()", 9223372036854775807),
    Case("smallint", "SMALLINT", "SMALLINT()", -7),
    Case("boolean", "BOOLEAN", "BOOLEAN()", True),
    Case("numeric_19_4", "NUMERIC(19, 4)", "NUMERIC(precision=19, scale=4)", Decimal("12345.6789")),
    Case("real", "REAL", "REAL()", 1.5),
    Case("float", "FLOAT", "FLOAT()", 2.5),
    Case("double", "DOUBLE", "DOUBLE()", 3.25),
    Case("char_3", "CHAR(3)", "CHAR(length=3)", "EUR"),
    Case("varchar_36_id", "VARCHAR(36)", "VARCHAR(length=36)", "not-a-uuid", column="ext_id"),
    Case(
        "char_32_id",
        "CHAR(32)",
        "CHAR(length=32)",
        "0190a8a11c2b7c3d8e4f5a6b7c8d9e0f",
        column="owner_id",
    ),
    Case("text", "TEXT", "TEXT()", "x" * 10_000),
    Case("blob", "BLOB", "BLOB()", b"\x00\x01\xff"),
    Case("json", "JSON", "json_column()", {"a": [1, "b"]}, where=False),
    Case("datetime", "DATETIME", "timestamp_column()", WHEN),
    Case("timestamp", "TIMESTAMP", "timestamp_column()", WHEN, rebuilt="datetime"),
    Case("date", "DATE", "DATE()", date(2026, 1, 2)),
    Case("time", "TIME", "TIME()", time(12, 34, 56, 789000)),
)

CASES_BY_DIALECT: dict[str, tuple[Case, ...]] = {
    "postgres": POSTGRES,
    "mysql": MYSQL,
    "mariadb": MARIADB,
    "sqlite": SQLITE,
}

#: What each migration needs before its table.
PREAMBLE = {
    "postgres": (
        "CREATE EXTENSION IF NOT EXISTS citext;\nCREATE TYPE mood AS ENUM ('happy', 'sad');\n"
    ),
}


@dataclass
class Matrix:
    text: str
    module: ModuleType
    migrated: str
    rebuilt: str
    notes: list[str]
    sources: dict[tuple[str, str], str]
    raw_migrated: dict[tuple[str, str], str]
    raw_rebuilt: dict[tuple[str, str], str]


#: By backend, not by dialect: the full suite stops a dialect's container and starts a new one
#: between modules, and a cached matrix would then point at databases that are gone.
_BUILT: dict[int, tuple[Backend, Matrix]] = {}


def _build(backend: Backend, dialect: str, root: Path) -> Matrix:
    cases = CASES_BY_DIALECT[dialect]
    columns = ",\n".join(f"    {c.name} {c.sql}" for c in cases)
    table = f"CREATE TABLE t_types (\n    pk INTEGER NOT NULL PRIMARY KEY,\n{columns}\n);\n"
    sql = PREAMBLE.get(dialect, "") + table
    source = migrations(root / "migrations", sql)
    migrated = fresh(backend, "types_a")
    out = root / "app" / "schema.py"
    with notes() as found:
        text = generate(source, out, url=migrated)
    module = load(out)

    rebuilt = fresh(backend, "types_b")
    built = engine(rebuilt)
    try:
        # An extension is the database's, not the schema's: created first, as a deploy would.
        if dialect == "postgres":
            with built.begin() as connection:
                connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS citext")
        module.metadata.create_all(built)
    finally:
        built.dispose()
    return Matrix(
        text=text,
        module=module,
        migrated=migrated,
        rebuilt=rebuilt,
        notes=found,
        sources=column_sources(text),
        raw_migrated=raw_types(migrated),
        raw_rebuilt=raw_types(rebuilt),
    )


@pytest.fixture
def case(dialect: str, case_id: str) -> Case:
    return next(c for c in CASES_BY_DIALECT[dialect] if c.id == case_id)


@pytest.fixture
def matrix(backend: Backend, dialect: str, tmp_path_factory: pytest.TempPathFactory) -> Matrix:
    cached = _BUILT.get(id(backend))
    if cached is None or cached[0] is not backend:
        built = _build(backend, dialect, tmp_path_factory.mktemp(f"types-{dialect}"))
        cached = _BUILT[id(backend)] = (backend, built)
    return cached[1]


def test_the_type_is_written_as(matrix: Matrix, case: Case) -> None:
    assert matrix.sources[("T_TYPES", case.name)] == normal(case.source)


def test_create_all_builds_the_column_the_migration_built(matrix: Matrix, case: Case) -> None:
    """The module does not misdescribe the database: built from it, the column is the same type
    - or, where a forced helper builds its own, the one the docs and the note say."""
    migrated = matrix.raw_migrated[("t_types", case.name)]
    rebuilt = matrix.raw_rebuilt[("t_types", case.name)]
    assert rebuilt == (case.rebuilt or migrated), f"migrated {migrated!r}, rebuilt {rebuilt!r}"


@pytest.mark.parametrize("where", ["migrated", "rebuilt"])
def test_a_value_goes_in_and_comes_back(matrix: Matrix, case: Case, where: str) -> None:
    table = matrix.module.T_TYPES
    column = table.c[case.name]
    expected = case.value if case.expected is SAME else case.expected
    pk = (abs(hash((case.id, where))) % 2_000_000_000) + 1
    url = matrix.migrated if where == "migrated" else matrix.rebuilt
    db = engine(url)
    try:
        with db.begin() as connection:
            connection.execute(insert(table).values({"pk": pk, case.name: case.value}))
            back = connection.execute(select(column).where(table.c.pk == pk)).scalar_one()
            if case.where:
                found = connection.execute(
                    select(table.c.pk).where(table.c.pk == pk, column == case.value)
                ).scalar_one_or_none()
                assert found == pk, f"WHERE {case.name} = {case.value!r} did not find the row"
    finally:
        db.dispose()
    if case.seen is not None:
        back = case.seen(back)
    assert back == expected, f"wrote {case.value!r}, read {back!r}"
    # Equal is not enough: Decimal("1.25") == 1.25, and MySQL's own DOUBLE reads a Decimal.
    assert type(back) is type(expected), (
        f"read a {type(back).__name__}, not {type(expected).__name__}"
    )


def test_the_whole_module_imports_and_names_its_table(matrix: Matrix, dialect: str) -> None:
    assert matrix.module.__all__ == ["T_TYPES", "metadata"]
    names = {c.name for c in CASES_BY_DIALECT[dialect]}
    assert set(matrix.module.T_TYPES.c.keys()) == {"pk", *names}


def test_the_module_imports_exactly_what_it_uses(matrix: Matrix) -> None:
    """Every type in one module: no import left unused, no name used and not imported."""
    assert unused_and_undefined(matrix.text) == (set(), set())


def test_timestamps_stay_zone_less_and_read_back_aware_utc(matrix: Matrix, dialect: str) -> None:
    """The UTC recommendation, end to end: the column built has no zone (TIMESTAMP, DATETIME(6)),
    the value stored is the UTC wall-clock time, and it reads back aware, in UTC."""
    from sqlalchemy import text

    name = {"postgres": "c_timestamp", "sqlite": "c_datetime"}.get(dialect, "c_datetime_6")
    zone_less = {
        "postgres": "timestamp without time zone",
        "mysql": "datetime(6)",
        "mariadb": "datetime(6)",
        "sqlite": "datetime",
    }[dialect]
    assert matrix.raw_rebuilt[("t_types", name)] == zone_less
    table = matrix.module.T_TYPES
    db = engine(matrix.rebuilt)
    try:
        with db.begin() as connection:
            connection.execute(
                insert(table).values({"pk": 7_000_001, name: WHEN.astimezone(TEHRAN)})
            )
            back = connection.execute(
                select(table.c[name]).where(table.c.pk == 7_000_001)
            ).scalar_one()
            stored = connection.execute(
                text(f"SELECT {name} FROM t_types WHERE pk = 7000001")
            ).scalar_one()
    finally:
        db.dispose()
    assert back == WHEN and back.tzinfo == UTC
    if isinstance(stored, str):  # SQLite keeps text
        stored = datetime.fromisoformat(stored)
    assert stored == WHEN.replace(tzinfo=None), "the UTC wall-clock time, with no zone"


def test_what_is_outside_the_recommendation_is_said(matrix: Matrix, dialect: str) -> None:
    """A zoned timestamp, MySQL's TIMESTAMP, a column that keeps fewer digits than the helper:
    generated, and noted, pointing at the recommendation."""
    expected = {
        "postgres": [
            "t_types.c_timestamptz is TIMESTAMPTZ",
            "t_types.c_timestamp_3 is TIMESTAMP(3)",
        ],
        "mysql": [
            "t_types.c_datetime is DATETIME:",
            "t_types.c_timestamp_6 is TIMESTAMP(6): the server converts",
        ],
        "mariadb": [
            "t_types.c_datetime is DATETIME:",
            "t_types.c_timestamp_6 is TIMESTAMP(6): the server converts",
        ],
        "sqlite": ["t_types.c_timestamp is TIMESTAMP"],
    }[dialect]
    for start in expected:
        assert any(n.startswith(start) for n in matrix.notes), (start, matrix.notes)
    assert all("always-utc" in n for n in matrix.notes if "TIMESTAMP" in n or "DATETIME" in n)
