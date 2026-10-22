"""The type table's cases: a column of every common SQL type, per database, with the type
schemagen writes for it.

Shared by `test_schemagen_types.py`, which proves each one end to end, and
`test_schemagen_table_names.py`, which proves each is still written right when a table takes
the name of what it is written with.
"""

from __future__ import annotations

import re
import struct
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.dialects.postgresql import Range

SAME = object()
WHEN = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
#: What a zone-less column holds: the value as given - here UTC, as SystemClock gives it.
NAIVE = WHEN.replace(tzinfo=None)
TEHRAN = timezone(timedelta(hours=3, minutes=30))
#: POINT(1 2) as MySQL stores and returns it: a 4-byte SRID, then the WKB.
POINT = bytes(4) + b"\x01\x01\x00\x00\x00" + struct.pack("<dd", 1.0, 2.0)
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
    Case("smallint", "SMALLINT", "sqlalchemy.SMALLINT()", -7),
    Case("integer", "INTEGER", "sqlalchemy.INTEGER()", 2147483647),
    Case("bigint", "BIGINT", "sqlalchemy.BIGINT()", 9223372036854775807),
    Case(
        "numeric_19_4",
        "NUMERIC(19, 4)",
        "sqlalchemy.NUMERIC(precision=19, scale=4)",
        Decimal("12345.6789"),
    ),
    Case("numeric", "NUMERIC", "sqlalchemy.NUMERIC()", Decimal("1.000000000000000000001")),
    Case(
        "decimal_10_2",
        "DECIMAL(10, 2)",
        "sqlalchemy.NUMERIC(precision=10, scale=2)",
        Decimal("99.95"),
    ),
    Case("real", "REAL", "sqlalchemy.REAL()", 1.5),
    Case("double", "DOUBLE PRECISION", "sqlalchemy.DOUBLE_PRECISION()", 1.2345678901234),
    Case("boolean", "BOOLEAN", "sqlalchemy.BOOLEAN()", True),
    Case("char_3", "CHAR(3)", "sqlalchemy.CHAR(length=3)", "EUR"),
    Case("varchar_80", "VARCHAR(80)", "sqlalchemy.VARCHAR(length=80)", "a name"),
    # Round 2, N2 #2: named like an id and 36 long, and still text - PostgreSQL has a uuid type.
    Case(
        "varchar_36_id",
        "VARCHAR(36)",
        "sqlalchemy.VARCHAR(length=36)",
        "not-a-uuid",
        column="ext_id",
    ),
    Case("varchar", "VARCHAR", "sqlalchemy.VARCHAR()", "any length"),
    Case("text", "TEXT", "sqlalchemy.TEXT()", "x" * 10_000),
    Case("citext", "CITEXT", "postgresql.CITEXT()", "MiXeD"),
    Case("bytea", "BYTEA", "sqlalchemy.LargeBinary()", b"\x00\x01\xfe\xff", where=False),
    Case("uuid", "UUID", "uuid_column()", ID),
    Case("json", "JSON", "sqlalchemy.JSON(none_as_null=True)", {"a": [1, "b"]}, where=False),
    Case("jsonb", "JSONB", "json_column()", {"k": "v", "n": 2}, where=False),
    Case("timestamp", "TIMESTAMP", "timestamp_column()", NAIVE),
    Case(
        "timestamp_3",
        "TIMESTAMP(3)",
        "timestamp_column()",
        NAIVE.replace(microsecond=123000),
        rebuilt="timestamp without time zone",
    ),
    Case(
        "timestamptz",
        "TIMESTAMPTZ",
        "sqlalchemy.TIMESTAMP(timezone=True)",
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
    Case("date", "DATE", "sqlalchemy.DATE()", date(2026, 1, 2)),
    Case("time", "TIME", "sqlalchemy.TIME()", time(12, 34, 56, 789000)),
    Case("time_3", "TIME(3)", "postgresql.TIME(precision=3)", time(1, 2, 3, 456000)),
    Case("timetz", "TIMETZ", "sqlalchemy.TIME(timezone=True)", time(1, 2, 3, tzinfo=UTC)),
    Case("interval", "INTERVAL", "postgresql.INTERVAL()", timedelta(days=1, seconds=5)),
    Case(
        "interval_day_second_3",
        "INTERVAL DAY TO SECOND(3)",
        'postgresql.INTERVAL(precision=3, fields="day to second")',
        timedelta(hours=2, microseconds=123000),
    ),
    Case("text_array", "TEXT[]", "postgresql.ARRAY(sqlalchemy.TEXT())", ["a", "b"]),
    Case("integer_array", "INTEGER[]", "postgresql.ARRAY(sqlalchemy.INTEGER())", [1, 2, 3]),
    Case("uuid_array", "UUID[]", "postgresql.ARRAY(sqlalchemy.UUID())", [ID]),
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
    Case("enum", "mood", 'sqlalchemy.Enum("happy", "sad", name="mood")', "sad"),
    # Types SQLAlchemy has no class for: written as PostgreSQL spells them, so create_all builds
    # the same column, and the value is what the driver gives - text. No `=` for either.
    Case("xml", "XML", 'sql_type("xml")', "<a>1</a>", where=False),
    Case("point", "POINT", 'sql_type("point")', "(1,2)", where=False),
    # An array of a type SQLAlchemy cannot read: the whole array as PostgreSQL spells it. The
    # driver reads it back as a list of the items' text.
    Case("xml_array", "XML[]", 'sql_type("xml[]")', "{<a/>}", expected=["<a/>"], where=False),
    # Spelled types both drivers read as text: the record says str (EKB-0428).
    Case("tsquery", "TSQUERY", 'sql_type("tsquery")', "'a' & 'b'", where=False),
    Case("jsonpath", "JSONPATH", 'sql_type("jsonpath")', "$.a", expected='$."a"', where=False),
    # Declared with two dimensions, read back as a list of lists (EKB-0429).
    Case(
        "integer_array_2d",
        "INTEGER[][]",
        "postgresql.ARRAY(sqlalchemy.INTEGER(), dimensions=2)",
        [[1, 2], [3, 4]],
        where=False,
    ),
    # The review (EKB-0458): PostgreSQL's one-byte "char" and its `name` reflect as a plain
    # String and were rebuilt VARCHAR; "char" reads as str on psycopg and as bytes on asyncpg.
    Case("pg_char", '"char"', 'sql_type("\\"char\\"")', "x", where=False),
    Case("pg_name", "NAME", 'sql_type("name")', "a_name"),
    # A domain, whole: create_all builds it with its check (EKB-0429).
    Case(
        "domain",
        "positive_int",
        'postgresql.DOMAIN("positive_int", sqlalchemy.INTEGER(), '
        'constraint_name="positive_int_check", check=sqlalchemy.text("((VALUE > 0))"))',
        5,
    ),
)

MYSQL = (
    Case("tinyint", "TINYINT", "mysql.TINYINT()", -5),
    Case("tinyint_unsigned", "TINYINT UNSIGNED", "mysql.TINYINT(unsigned=True)", 250),
    # Round 2, N2 #4: MySQL's BOOLEAN is TINYINT(1), and it made the module fail to import.
    Case("boolean", "BOOLEAN", "sqlalchemy.BOOLEAN()", True),
    Case("tinyint_1", "TINYINT(1)", "sqlalchemy.BOOLEAN()", False),
    Case("smallint", "SMALLINT", "sqlalchemy.SMALLINT()", -300),
    Case("smallint_unsigned", "SMALLINT UNSIGNED", "mysql.SMALLINT(unsigned=True)", 65000),
    Case("mediumint", "MEDIUMINT", "mysql.MEDIUMINT()", -8000000),
    Case("mediumint_unsigned", "MEDIUMINT UNSIGNED", "mysql.MEDIUMINT(unsigned=True)", 16000000),
    Case("int", "INT", "sqlalchemy.INTEGER()", -2147483648),
    Case("int_unsigned", "INT UNSIGNED", "mysql.INTEGER(unsigned=True)", 4294967295),
    Case("bigint", "BIGINT", "sqlalchemy.BIGINT()", -9223372036854775808),
    Case("bigint_unsigned", "BIGINT UNSIGNED", "mysql.BIGINT(unsigned=True)", 18446744073709551615),
    Case(
        "decimal_19_4",
        "DECIMAL(19, 4)",
        "sqlalchemy.DECIMAL(precision=19, scale=4)",
        Decimal("-12345.6789"),
    ),
    Case(
        "numeric_10_2",
        "NUMERIC(10, 2)",
        "sqlalchemy.DECIMAL(precision=10, scale=2)",
        Decimal("99.95"),
    ),
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
    Case("float", "FLOAT", "sqlalchemy.FLOAT()", 1.5),
    # A float, as Java reads a Double - MySQL's own DOUBLE type would hand back a Decimal.
    Case("double", "DOUBLE", "sqlalchemy.DOUBLE()", 1.25),
    Case("real", "REAL", "sqlalchemy.DOUBLE()", 2.5),
    Case("bit_1", "BIT(1)", "mysql.BIT(length=1)", 1),
    Case("bit_8", "BIT(8)", "mysql.BIT(length=8)", 200),
    Case("char_3", "CHAR(3)", "sqlalchemy.CHAR(length=3)", "EUR"),
    Case("varchar_80", "VARCHAR(80)", "sqlalchemy.VARCHAR(length=80)", "a name"),
    # Round 2, N2 #2: Java forces only CHAR(36); a VARCHAR(36) id stays text.
    Case(
        "varchar_36_id",
        "VARCHAR(36)",
        "sqlalchemy.VARCHAR(length=36)",
        "not-a-uuid",
        column="ext_id",
    ),
    # No uuid guess from the name: a CHAR(36) id is text unless the project forces it (see
    # test_schemagen's test_a_char_36_id_is_text_until_the_project_says_it_holds_a_uuid).
    Case("char_36_id", "CHAR(36)", "sqlalchemy.CHAR(length=36)", str(ID), column="legacy_id"),
    Case(
        "char_36_not_id", "CHAR(36)", "sqlalchemy.CHAR(length=36)", "a" * 36, column="country_name"
    ),
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
    Case("text", "TEXT", "sqlalchemy.TEXT()", "x" * 10_000, where=False),
    Case("mediumtext", "MEDIUMTEXT", "mysql.MEDIUMTEXT()", "y" * 70_000, where=False),
    Case("longtext", "LONGTEXT", "mysql.LONGTEXT()", "z" * 70_000, where=False),
    Case("binary_16", "BINARY(16)", "sqlalchemy.BINARY(length=16)", ID.bytes),
    Case("varbinary_64", "VARBINARY(64)", "sqlalchemy.VARBINARY(length=64)", b"\x00\x01\xff"),
    Case("tinyblob", "TINYBLOB", "mysql.TINYBLOB()", b"\x01", where=False),
    Case("blob", "BLOB", "sqlalchemy.BLOB()", b"\x00" * 300, where=False),
    Case("mediumblob", "MEDIUMBLOB", "mysql.MEDIUMBLOB()", b"\x02" * 70_000, where=False),
    Case("longblob", "LONGBLOB", "mysql.LONGBLOB()", b"\x03" * 70_000, where=False),
    Case("json", "JSON", "json_column()", {"a": [1, "b"]}, where=False),
    Case("datetime_6", "DATETIME(6)", "timestamp_column()", NAIVE),
    Case(
        "datetime",
        "DATETIME",
        "timestamp_column()",
        NAIVE.replace(microsecond=0),
        rebuilt="datetime(6)",
    ),
    Case(
        "timestamp_6",
        "TIMESTAMP(6) NULL",
        "timestamp_column()",
        NAIVE,
        rebuilt="datetime(6)",
    ),
    Case("date", "DATE", "sqlalchemy.DATE()", date(2026, 1, 2)),
    Case("time", "TIME", "sqlalchemy.TIME()", time(12, 34, 56)),
    Case("time_6", "TIME(6)", "mysql.TIME(fsp=6)", time(1, 2, 3, 456789)),
    Case("year", "YEAR", "mysql.YEAR()", 2026),
    Case("enum", "ENUM('NEW', 'DONE')", 'sqlalchemy.Enum("NEW", "DONE")', "DONE"),
    Case("set", "SET('a', 'b')", 'mysql.SET("a", "b")', {"a", "b"}, where=False),
    # The review (EKB-0457): a value with a backslash was written escaped twice and read back
    # as no value of the enum; a blank SET member stopped the run.
    Case(
        "enum_backslash",
        r"ENUM('plain', 'x\\y')",
        r"""sql_type("enum('plain','x\\\\y')")""",
        "x\\y",
    ),
    Case(
        "set_blank",
        "SET('', 'a', 'b')",
        'mysql.SET("", "a", "b", retrieve_as_bitwise=True)',
        {"a", "b"},
        where=False,
    ),
    # Spatial, read as MySQL's own bytes (SRID, then WKB) by both drivers (EKB-0428).
    Case("point", "POINT", 'sql_type("point")', POINT, where=False),
)

MARIADB = (
    *MYSQL,
    # MariaDB's own UUID type: no forced type, as in Java's MariaDB build.
    Case("uuid", "UUID", "sqlalchemy.UUID()", ID),
    # A type SQLAlchemy has no class for: written as MariaDB spells it, read back as text.
    Case("inet6", "INET6", 'sql_type("inet6")', "2001:db8::1"),
    Case("inet4", "INET4", 'sql_type("inet4")', "10.0.0.1"),
)

SQLITE = (
    Case("integer", "INTEGER", "sqlalchemy.INTEGER()", 7),
    Case("bigint", "BIGINT", "sqlalchemy.BIGINT()", 9223372036854775807),
    Case("smallint", "SMALLINT", "sqlalchemy.SMALLINT()", -7),
    Case("boolean", "BOOLEAN", "sqlalchemy.BOOLEAN()", True),
    Case(
        "numeric_19_4",
        "NUMERIC(19, 4)",
        "sqlalchemy.NUMERIC(precision=19, scale=4)",
        Decimal("12345.6789"),
    ),
    Case("real", "REAL", "sqlalchemy.REAL()", 1.5),
    Case("float", "FLOAT", "sqlalchemy.FLOAT()", 2.5),
    Case("double", "DOUBLE", "sqlalchemy.DOUBLE()", 3.25),
    Case("char_3", "CHAR(3)", "sqlalchemy.CHAR(length=3)", "EUR"),
    Case(
        "varchar_36_id",
        "VARCHAR(36)",
        "sqlalchemy.VARCHAR(length=36)",
        "not-a-uuid",
        column="ext_id",
    ),
    Case(
        "char_32_id",
        "CHAR(32)",
        "sqlalchemy.CHAR(length=32)",
        "0190a8a11c2b7c3d8e4f5a6b7c8d9e0f",
        column="owner_id",
    ),
    Case("text", "TEXT", "sqlalchemy.TEXT()", "x" * 10_000),
    Case("blob", "BLOB", "sqlalchemy.BLOB()", b"\x00\x01\xff"),
    Case("json", "JSON", "json_column()", {"a": [1, "b"]}, where=False),
    Case("datetime", "DATETIME", "timestamp_column()", NAIVE),
    Case("timestamp", "TIMESTAMP", "timestamp_column()", NAIVE, rebuilt="datetime"),
    Case("date", "DATE", "sqlalchemy.DATE()", date(2026, 1, 2)),
    Case("time", "TIME", "sqlalchemy.TIME()", time(12, 34, 56, 789000)),
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
        "CREATE DOMAIN positive_int AS INTEGER CHECK (VALUE > 0);\n"
    ),
}
