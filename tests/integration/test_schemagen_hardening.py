"""schemagen against real databases, at the edges: every forced-type key alone and together,
every kind of target, and the shapes of schema the other files do not migrate.

Each test runs the real pipeline - migrations into a fresh database on the session's container,
``generate``, the module imported from the file it wrote - and, where the module claims to
describe the database, ``metadata.create_all`` on a second fresh database and a comparison.

    uv run pytest tests/integration/test_schemagen_hardening.py --dialects=all

The ledger is `docs/SCHEMAGEN-TEST-LEDGER.md`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import insert, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from backends import Backend
from ekbatan.errors import ValidationError
from ekbatan.schemagen import ForcedType, SchemagenConfig, fingerprint, generate
from schemagen_support import (
    SQLALCHEMY_NAME,
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

ROOT = Path(__file__).resolve().parents[2]
WHEN = datetime(2026, 1, 2, 3, 4, 5, 123456)


def _family(dialect: str) -> str:
    return "mysql" if dialect == "mariadb" else dialect


def _own_schema(dialect: str, url: str) -> str:
    """The connection's own schema, by its real name: what `schema.table.column` is said with."""
    return {"postgres": "public", "sqlite": "main"}.get(dialect) or str(make_url(url).database)


def _clean(text_: str) -> None:
    """A generated module imports exactly what it uses (ledger E-1)."""
    unused, undefined = unused_and_undefined(text_)
    assert (unused, undefined) == (set(), set())


def _rebuild(backend: Backend, module: Any, *before: str, prefix: str = "hard_b") -> str:
    """A second fresh database, built by `create_all` from the module, after `before` SQL."""
    url = fresh(backend, prefix)
    db = engine(url)
    try:
        if before:
            with db.begin() as connection:
                for sql in before:
                    connection.exec_driver_sql(sql)
        module.metadata.create_all(db)
    finally:
        db.dispose()
    return url


def _columns(url: str, table: str, schema: str | None = None) -> list[tuple[Any, ...]]:
    """Each column as a database describes it, in a form two databases can be compared by."""
    db = engine(url)
    try:
        return [
            (
                c["name"],
                c["nullable"],
                " ".join(str(c["default"]).lower().split()) if c.get("default") else None,
                c.get("comment"),
                tuple(
                    sorted((k, v) for k, v in (c.get("identity") or {}).items() if v is not None)
                ),
            )
            for c in inspect(db).get_columns(table, schema=schema)
        ]
    finally:
        db.dispose()


# -- the user's own code: converters and SQLAlchemy types ----------------------------------

USER_CODE = '''"""Converters and types an application might bring."""
from sqlalchemy import Numeric
from sqlalchemy.types import TypeDecorator


class Tag:
    def __init__(self, text, places):
        self.text, self.places = text, places

    def __eq__(self, other):
        return isinstance(other, Tag) and other.text == self.text


class TagConverter:
    def __init__(self, places=0):
        self.places = places

    def from_db(self, value: str) -> Tag:
        return Tag(value, self.places)

    def to_db(self, value):
        return value.text


def make(places=7):
    return TagConverter(places)


SHARED = TagConverter(9)


class Money(TypeDecorator):
    impl = Numeric
    cache_ok = True

    def __init__(self, scale=2):
        super().__init__(precision=19, scale=scale)


def cents():
    return Numeric(19, 2)


AMOUNT = Numeric(19, 4)
'''


@pytest.fixture
def user_code(tmp_path: Path) -> Iterator[str]:
    """The application's own module, importable for the run and the generated module."""
    package = f"hard_user_{uuid.uuid4().hex[:8]}"
    root = tmp_path / "user"
    (root / package).mkdir(parents=True)
    (root / package / "__init__.py").write_text("")
    (root / package / "code.py").write_text(USER_CODE)
    sys.path.insert(0, str(root))
    yield f"{package}.code"
    sys.path.remove(str(root))
    for name in [m for m in sys.modules if m.startswith(package)]:
        sys.modules.pop(name)


# -- the forced-type grid: which columns each key picks (ledger G-1..G-13, G-20) -------------

#: (column type, include-types pattern): each pattern must pick its own column, which no other
#: rule names. Every spelling the docs promise - the database's own, SQLAlchemy's, with and
#: without arguments, short names, any case.
SPELLED = {
    "postgres": [
        ("VARCHAR(36)", r"character varying\(36\)"),
        ("VARCHAR(36)", r"VARCHAR\(36\)"),
        ("VARCHAR(36)", "varchar"),
        ("VARCHAR(36)", "CHARACTER VARYING"),
        ("CHAR(36)", r"CHAR\(36\)"),
        ("CHAR(36)", r"character\(36\)"),
        ("CHAR(36)", "CHAR"),
        ("NUMERIC(19, 4)", r"NUMERIC\(19,4\)"),
        ("NUMERIC(19, 4)", r"NUMERIC\(19, 4\)"),
        ("NUMERIC(19, 4)", "numeric"),
        ("TIMESTAMP(3)", r"TIMESTAMP\(3\)"),
        ("TIMESTAMP(3)", "timestamp without time zone"),
        ("TIMESTAMP(3)", r"timestamp\(3\) without time zone"),
        ("TIMESTAMPTZ", "TIMESTAMPTZ"),
        ("TIMESTAMPTZ", "timestamp with time zone"),
        ("TIMETZ", "TIMETZ"),
        ("DOUBLE PRECISION", "double precision"),
        ("INTEGER", "integer"),
        ("mood", "ENUM"),
        ("mood", "mood"),
        ("JSON", "json"),
        ("TEXT[]", r"TEXT\[\]"),
    ],
    "mysql": [
        ("INT UNSIGNED", "int unsigned"),
        ("INT UNSIGNED", "INTEGER UNSIGNED"),
        ("TINYINT(1)", r"tinyint\(1\)"),
        ("TINYINT(1)", "TINYINT"),
        ("BOOLEAN", r"TINYINT\(1\)"),
        ("VARCHAR(36)", r"varchar\(36\)"),
        ("VARCHAR(36)", "VARCHAR"),
        ("CHAR(36) CHARACTER SET ascii", r"CHAR\(36\)"),
        ("DECIMAL(19, 4)", r"DECIMAL\(19,4\)"),
        ("DECIMAL(19, 4)", r"decimal\(19, 4\)"),
        ("DATETIME(6)", r"DATETIME\(6\)"),
        ("DATETIME(6)", "datetime"),
        ("ENUM('a', 'b')", "ENUM"),
        ("ENUM('a', 'b')", r"enum\('a','b'\)"),
        ("JSON", "json"),
        ("LONGTEXT", "LONGTEXT"),
        ("BIGINT UNSIGNED", "BIGINT UNSIGNED"),
    ],
    "sqlite": [
        ("VARCHAR(36)", r"VARCHAR\(36\)"),
        ("VARCHAR(36)", "varchar"),
        ("CHAR(32)", r"CHAR\(32\)"),
        ("NUMERIC(19, 4)", r"NUMERIC\(19, ?4\)"),
        ("DATETIME", "datetime"),
        ("JSON", "JSON"),
        ("BIGINT", "bigint"),
    ],
}

STAMP = {"postgres": "TIMESTAMP", "mysql": "DATETIME(6)", "sqlite": "DATETIME"}


def _grid_sql(dialect: str) -> str:
    family = _family(dialect)
    stamp = STAMP[family]
    spelled = ",\n".join(f"    sp_{i} {sql}" for i, (sql, _) in enumerate(SPELLED[family]))
    preamble = "CREATE TYPE mood AS ENUM ('a', 'b');\n" if family == "postgres" else ""
    return f"""{preamble}CREATE TABLE grid (
    id INTEGER NOT NULL PRIMARY KEY,
    by_column VARCHAR(20),
    by_table VARCHAR(20),
    by_schema VARCHAR(20),
    by_case VARCHAR(20),
    ex_in VARCHAR(20),
    ex_out VARCHAR(20),
    ty_in VARCHAR(20),
    ty_out TEXT,
    nl_null VARCHAR(20),
    nl_notnull VARCHAR(20) NOT NULL,
    both_text TEXT,
    both_varchar VARCHAR(20),
    first_wins VARCHAR(20),
    stamp_text {stamp},
    stamp_default {stamp},
    conv_then_name VARCHAR(20),
    name_then_conv VARCHAR(20),
{spelled}
);
"""


def _grid_rules(dialect: str, own: str, code: str) -> tuple[ForcedType, ...]:
    def to(length: int, **match: Any) -> ForcedType:
        return ForcedType(name=f"String({length})", **match)

    return (
        to(101, include_expression="by_column"),
        to(102, include_expression=r"grid\.by_table"),
        to(103, include_expression=rf"{own}\.grid\.by_schema"),
        to(104, include_expression=r"GRID\.by_case|by_cas"),
        to(105, include_expression="ex_.*", exclude_expression="ex_out"),
        to(106, include_expression="ty_.*", exclude_types="TEXT"),
        to(107, include_expression="nl_.*", nullability="NULL"),
        to(108, include_expression="nl_.*", nullability="NOT_NULL"),
        to(109, include_expression="both_.*", include_types="TEXT"),
        to(110, include_expression="first_wins"),
        to(111, include_expression="first_wins"),
        ForcedType(name="Text", include_expression="stamp_text"),
        ForcedType(converter=f"{code}.TagConverter", include_expression="conv_then_name"),
        to(114, include_expression="conv_then_name|name_then_conv"),
        ForcedType(converter=f"{code}.TagConverter", include_expression="name_then_conv"),
        *(
            to(200 + i, include_expression=f"sp_{i}", include_types=pattern)
            for i, (_, pattern) in enumerate(SPELLED[_family(dialect)])
        ),
    )


def test_each_forced_type_key_picks_exactly_the_columns_it_says(
    tmp_path: Path, backend: Backend, dialect: str, user_code: str
) -> None:
    """One rule per behaviour, each scoped to its own column: a rule that picks wrongly shows as
    a column with the wrong type, named. Column names as `column`, `table.column` and the
    connection's own `schema.table.column`; case-sensitive whole names; excludes; nullability;
    both includes at once; the first rule winning; the user's rules before Ekbatan's; a
    converter wrapping the name after it and not the one before; and every spelling of a type."""
    url = fresh(backend, "grid")
    source = migrations(tmp_path / "migrations", _grid_sql(dialect))
    out = tmp_path / "app" / "schema.py"
    rules = _grid_rules(dialect, _own_schema(dialect, url), user_code)

    written = generate(source, out, url=url, config=SchemagenConfig(forced_types=rules))

    unforced = "VARCHAR(length=20)"
    expected = {
        "by_column": "String(101)",
        "by_table": "String(102)",
        "by_schema": "String(103)",
        "by_case": unforced,
        "ex_in": "String(105)",
        "ex_out": unforced,
        "ty_in": "String(106)",
        "ty_out": "TEXT()",
        "nl_null": "String(107)",
        "nl_notnull": "String(108)",
        "both_text": "String(109)",
        "both_varchar": unforced,
        "first_wins": "String(110)",
        "stamp_text": "Text()",
        "stamp_default": "timestamp_column()",
        "conv_then_name": "converted(String(114), TagConverter())",
        "name_then_conv": "String(114)",
        **{f"sp_{i}": f"String({200 + i})" for i in range(len(SPELLED[_family(dialect)]))},
    }
    sources = column_sources(written)
    assert {c: sources[("GRID", c)] for c in expected} == {
        c: normal(s) for c, s in expected.items()
    }
    _clean(written)
    module = load(out)
    assert module.__all__ == ["GRID", "GridRecord", "metadata"]
    _rebuild(backend, module)


def test_a_listed_schema_s_columns_are_named_schema_table_column(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """`schema.table.column` picks a column in a schema listed in `schemas`, and not the
    same-named column in the connection's own schema. Naming the own schema, or one schema
    twice, in `schemas` changes nothing."""
    if dialect == "sqlite":
        pytest.skip("SQLite has no schemas")
    url = fresh(backend, "listed")
    other = f"aud_{uuid.uuid4().hex[:8]}"
    create = "SCHEMA" if dialect == "postgres" else "DATABASE"
    source = migrations(
        tmp_path / "migrations",
        f"CREATE TABLE entries (id INTEGER PRIMARY KEY, note VARCHAR(20));\n"
        f"CREATE {create} {other};\n"
        f"CREATE TABLE {other}.entries (id INTEGER PRIMARY KEY, note VARCHAR(20));\n",
    )
    own = _own_schema(dialect, url)

    written = generate(
        source,
        tmp_path / "app" / "schema.py",
        url=url,
        config=SchemagenConfig(
            schemas=(own, other, other),
            forced_types=(ForcedType(name="Text", include_expression=rf"{other}\.entries\.note"),),
        ),
    )

    sources = column_sources(written)
    assert sources[(f"{other.upper()}_ENTRIES", "note")] == "Text()"
    assert sources[("ENTRIES", "note")] == normal("VARCHAR(length=20)")
    assert written.count(f"{other.upper()}_ENTRIES = Table(") == 1, "read once"
    assert f'schema="{own}"' not in written, "the own schema stays unqualified"


# -- every kind of target, built and used (ledger G-14..G-19) --------------------------------

TARGET_TYPES = {
    "postgres": {
        "uuid": "UUID",
        "json": "JSONB",
        "at": "TIMESTAMP",
        "num": "NUMERIC(19, 4)",
        "dialect": "INET",
    },
    "mysql": {
        "uuid": "CHAR(36)",
        "json": "JSON",
        "at": "DATETIME(6)",
        "num": "DECIMAL(19, 4)",
        "dialect": "SMALLINT UNSIGNED",
    },
    "sqlite": {
        "uuid": "CHAR(32)",
        "json": "JSON",
        "at": "DATETIME",
        "num": "NUMERIC(19, 4)",
        "dialect": "JSON",
    },
}

#: The dialect type forced on `tg_dialect`: as written, as generated, a value, the type
#: `create_all` builds, and how the value reads back.
DIALECT_TARGET = {
    "postgres": ("postgresql.INET", "postgresql.INET()", "10.0.0.1", "inet", str),
    "mysql": ("mysql.TINYINT(unsigned=True)", "mysql.TINYINT(unsigned=True)", 250, None, None),
    "sqlite": ("sqlite.JSON", "sqlite.JSON()", {"a": [1]}, "json", None),
}


def test_every_kind_of_target_is_written_imported_built_and_used(
    tmp_path: Path, backend: Backend, dialect: str, user_code: str
) -> None:
    """With Ekbatan's forced types off, so each column is what its own rule says: the three
    helpers; SQLAlchemy types with positional and keyword arguments; a dialect's own type; a
    converter as a class, with arguments, with keywords, `module:Class`, an instance, a
    factory with and without arguments; a SQLAlchemy type as a function, `module:Class` with
    arguments, an instance and a bare class. A value goes through each one."""
    family = _family(dialect)
    types = TARGET_TYPES[family]
    dialect_name, dialect_source, dialect_value, dialect_built, dialect_seen = DIALECT_TARGET[
        family
    ]
    numeric = types["num"]
    converters = {
        "tg_c1": (f"{user_code}.TagConverter", "TagConverter()", 0),
        "tg_c2": (f"{user_code}.TagConverter(2)", "TagConverter(2)", 2),
        "tg_c3": (f"{user_code}.TagConverter(places=3)", "TagConverter(places=3)", 3),
        "tg_c4": (f"{user_code}:TagConverter", "TagConverter()", 0),
        "tg_c5": (f"{user_code}.SHARED", "SHARED", 9),
        "tg_c6": (f"{user_code}.make", "make()", 7),
        "tg_c7": (f"{user_code}:make(8)", "make(8)", 8),
    }
    sqlalchemy_types = {
        "tg_s1": (f"{user_code}.cents", "cents()"),
        "tg_s2": (f"{user_code}:Money(4)", "Money(4)"),
        "tg_s3": (f"{user_code}.AMOUNT", "AMOUNT"),
        "tg_s4": (f"{user_code}.Money", "Money()"),
    }
    columns = [
        f"tg_uuid {types['uuid']}",
        f"tg_json {types['json']}",
        f"tg_at {types['at']}",
        "tg_str VARCHAR(36)",
        f"tg_num {numeric}",
        f"tg_dialect {types['dialect']}",
        *(f"{c} VARCHAR(20)" for c in converters),
        *(f"{c} {numeric}" for c in sqlalchemy_types),
    ]
    source = migrations(
        tmp_path / "migrations",
        "CREATE TABLE targets (\n    id INTEGER NOT NULL PRIMARY KEY,\n    "
        + ",\n    ".join(columns)
        + "\n);\n",
    )
    rules = (
        ForcedType(name="uuid_column", include_expression="tg_uuid"),
        ForcedType(name="json_column", include_expression="tg_json"),
        ForcedType(name="timestamp_column", include_expression="tg_at"),
        ForcedType(name="String(36)", include_expression="tg_str"),
        ForcedType(name="Numeric(precision=19, scale=4)", include_expression="tg_num"),
        ForcedType(name=dialect_name, include_expression="tg_dialect"),
        *(ForcedType(converter=c, include_expression=n) for n, (c, _, _) in converters.items()),
        *(
            ForcedType(sqlalchemy_type=t, include_expression=n)
            for n, (t, _) in sqlalchemy_types.items()
        ),
    )
    url = fresh(backend, "targets")
    out = tmp_path / "app" / "schema.py"

    written = generate(
        source, out, url=url, config=SchemagenConfig(forced_types=rules, default_forced_types=False)
    )

    plain = "VARCHAR(length=20)"
    expected = {
        "tg_uuid": "uuid_column()",
        "tg_json": "json_column()",
        "tg_at": "timestamp_column()",
        "tg_str": "String(36)",
        "tg_num": "Numeric(precision=19, scale=4)",
        "tg_dialect": dialect_source,
        **{n: f"converted({plain}, {s})" for n, (_, s, _) in converters.items()},
        **{n: s for n, (_, s) in sqlalchemy_types.items()},
    }
    sources = column_sources(written)
    assert {c: sources[("TARGETS", c)] for c in expected} == {
        c: normal(s) for c, s in expected.items()
    }
    _clean(written)
    module = load(out)
    rebuilt = _rebuild(backend, module)
    migrated_raw, rebuilt_raw = raw_types(url), raw_types(rebuilt)
    for column in ("tg_str", "tg_num", "tg_s3", *converters):
        assert rebuilt_raw[("targets", column)] == migrated_raw[("targets", column)], column
    built = (
        dialect_built or {"mysql": "tinyint unsigned", "mariadb": "tinyint(3) unsigned"}[dialect]
    )
    assert rebuilt_raw[("targets", "tg_dialect")] == built, "the forced type builds its own"

    tag = sys.modules[user_code].Tag
    one = uuid.uuid4()
    row = {
        "id": 1,
        "tg_uuid": one,
        "tg_json": {"k": [1, 2]},
        "tg_at": WHEN,
        "tg_str": "x" * 36,
        "tg_num": Decimal("12.3456"),
        "tg_dialect": dialect_value,
        **{c: tag(f"v-{c}", 0) for c in converters},
        **dict.fromkeys(sqlalchemy_types, Decimal("1.23")),
    }
    for where in (url, rebuilt):
        db = engine(where)
        try:
            with db.begin() as connection:
                connection.execute(insert(module.TARGETS).values(row))
                back = connection.execute(select(module.TARGETS)).one()._mapping
        finally:
            db.dispose()
        assert back["tg_uuid"] == one
        assert back["tg_json"] == {"k": [1, 2]}
        assert back["tg_at"] == WHEN and back["tg_at"].tzinfo is None
        assert back["tg_num"] == Decimal("12.3456")
        seen = back["tg_dialect"] if dialect_seen is None else dialect_seen(back["tg_dialect"])
        assert seen == dialect_value
        for column, (_, _, places) in converters.items():
            assert back[column] == tag(f"v-{column}", 0)
            assert back[column].places == places, column
        for column in sqlalchemy_types:
            assert back[column] == Decimal("1.23") and isinstance(back[column], Decimal)


def test_with_ekbatan_s_forced_types_off_each_column_is_the_type_table_s(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """JSON is SQLAlchemy's JSON, MariaDB's included (its LONGTEXT + `json_valid`); JSONB is
    PostgreSQL's own; a timestamp keeps its precision. A JSON value still round-trips."""
    family = _family(dialect)
    columns, expected = {
        "postgres": (
            "doc JSONB, plain JSON, at TIMESTAMP(3), whole TIMESTAMP",
            {
                "doc": "postgresql.JSONB(none_as_null=True)",
                "plain": "JSON(none_as_null=True)",
                "at": "postgresql.TIMESTAMP(precision=3)",
                "whole": "TIMESTAMP()",
            },
        ),
        "mysql": (
            "doc JSON, at DATETIME(6), whole DATETIME",
            {
                "doc": "JSON(none_as_null=True)",
                "at": "mysql.DATETIME(fsp=6)",
                "whole": "DATETIME()",
            },
        ),
        "sqlite": (
            "doc JSON, at DATETIME",
            {"doc": "JSON(none_as_null=True)", "at": "DATETIME()"},
        ),
    }[family]
    url = fresh(backend, "nodefaults")
    out = tmp_path / "app" / "schema.py"
    source = migrations(
        tmp_path / "migrations", f"CREATE TABLE plain (id INTEGER PRIMARY KEY, {columns});\n"
    )

    written = generate(source, out, url=url, config=SchemagenConfig(default_forced_types=False))

    sources = column_sources(written)
    assert {c: sources[("PLAIN", c)] for c in expected} == {
        c: normal(s) for c, s in expected.items()
    }
    _clean(written)
    module = load(out)
    db = engine(url)
    try:
        with db.begin() as connection:
            connection.execute(insert(module.PLAIN).values(id=1, doc={"a": [1, None]}))
            assert connection.execute(select(module.PLAIN.c.doc)).scalar_one() == {"a": [1, None]}
    finally:
        db.dispose()


# -- shapes of schema, and whether create_all builds them again (ledger E-3, F-*) -------------


def test_a_single_column_key_with_its_own_name_is_not_made_auto_increment(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """`CONSTRAINT pk_keyed PRIMARY KEY (id)` on an INTEGER: the key is written apart, by its
    name, and SQLAlchemy - left to itself - would make a lone integer key SERIAL. It once did:
    `create_all` gave `id` a `nextval(...)` default the migration never had."""
    source = migrations(
        tmp_path / "migrations",
        "CREATE TABLE keyed (id INTEGER NOT NULL, note VARCHAR(10), "
        "CONSTRAINT pk_keyed PRIMARY KEY (id));\n",
    )
    url = fresh(backend, "keyed")
    out = tmp_path / "app" / "schema.py"

    written = generate(source, out, url=url)

    _clean(written)
    rebuilt = _rebuild(backend, load(out))
    assert _columns(rebuilt, "keyed") == _columns(url, "keyed")
    db = engine(rebuilt)
    try:
        assert inspect(db).get_pk_constraint("keyed")["constrained_columns"] == ["id"]
    finally:
        db.dispose()


def test_identity_columns_keep_their_start_step_and_cycle(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    if dialect != "postgres":
        pytest.skip("GENERATED ... AS IDENTITY is PostgreSQL's here")
    source = migrations(
        tmp_path / "migrations",
        "CREATE TABLE numbered (id INTEGER PRIMARY KEY, "
        "a BIGINT GENERATED BY DEFAULT AS IDENTITY (START WITH 10 INCREMENT BY 5 CYCLE), "
        "b INTEGER GENERATED ALWAYS AS IDENTITY (START WITH 3), "
        "c SMALLINT GENERATED ALWAYS AS IDENTITY);\n",
    )
    url = fresh(backend, "ident")
    out = tmp_path / "app" / "schema.py"

    written = generate(source, out, url=url)

    assert (
        'Column("a", BIGINT(), Identity(start=10, increment=5, cycle=True), nullable=False)'
        in written
    )
    assert 'Column("b", INTEGER(), Identity(always=True, start=3), nullable=False)' in written
    assert 'Column("c", SMALLINT(), Identity(always=True), nullable=False)' in written
    rebuilt = _rebuild(backend, load(out))
    assert _columns(rebuilt, "numbered") == _columns(url, "numbered")


def test_a_serial_that_is_not_the_key_brings_its_sequence(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """Declared, so `create_all` creates the sequence before the table - in the connection's
    own schema and in a listed one, whose sequence lives there too."""
    if dialect != "postgres":
        pytest.skip("SERIAL and nextval() are PostgreSQL's")
    source = migrations(
        tmp_path / "migrations",
        "CREATE TABLE counters (id INTEGER PRIMARY KEY, seq BIGSERIAL, n INTEGER);\n"
        "CREATE SCHEMA audit;\n"
        "CREATE TABLE audit.tallies (id INTEGER PRIMARY KEY, seq SERIAL);\n",
    )
    url = fresh(backend, "serial")
    out = tmp_path / "app" / "schema.py"

    written = generate(source, out, url=url, config=SchemagenConfig(schemas=("audit",)))

    assert 'Sequence("counters_seq_seq")' in written
    _clean(written)
    rebuilt = _rebuild(backend, load(out), "CREATE SCHEMA audit")
    assert _columns(rebuilt, "counters") == _columns(url, "counters")
    assert _columns(rebuilt, "tallies", "audit") == _columns(url, "tallies", "audit")
    db = engine(rebuilt)
    try:
        with db.begin() as connection:
            connection.execute(text("INSERT INTO counters (id) VALUES (1)"))
            connection.execute(text("INSERT INTO audit.tallies (id) VALUES (1)"))
            assert connection.execute(text("SELECT seq FROM counters")).scalar_one() == 1
            assert connection.execute(text("SELECT seq FROM audit.tallies")).scalar_one() == 1
    finally:
        db.dispose()


def test_auto_increment_outside_the_key_is_said(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    if _family(dialect) != "mysql":
        pytest.skip("AUTO_INCREMENT is MySQL's and MariaDB's")
    source = migrations(
        tmp_path / "migrations",
        "CREATE TABLE codes (code VARCHAR(10) NOT NULL PRIMARY KEY, "
        "n BIGINT NOT NULL AUTO_INCREMENT, UNIQUE KEY uq_codes_n (n));\n",
    )

    with notes() as said:
        generate(source, tmp_path / "app" / "schema.py", url=fresh(backend, "autoinc"))

    assert (
        "codes.n is AUTO_INCREMENT outside the primary key, which SQLAlchemy cannot declare: "
        "create_all builds it without"
    ) in said


def test_a_table_named_with_a_leading_digit_is_exported_and_built(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    quote = "`" if _family(dialect) == "mysql" else '"'
    source = migrations(
        tmp_path / "migrations",
        f"CREATE TABLE {quote}2024_sales{quote} (id INTEGER PRIMARY KEY, total INTEGER);\n",
    )
    out = tmp_path / "app" / "schema.py"

    generate(source, out, url=fresh(backend, "digit"))

    module = load(out)
    assert module.__all__ == ["T_2024_SALES", "T2024SalesRecord", "metadata"]
    assert module.T_2024_SALES.name == "2024_sales"
    _rebuild(backend, module)


def test_text_with_returns_quotes_and_backslashes_survives_from_the_database(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """A comment holding `\\r\\n` - as a migration written on Windows can leave one - once made
    the generated module a syntax error. Quotes and backslashes in comments and defaults too."""
    family = _family(dialect)
    comment = 'one\r\ntwo "q" \\ end'
    if family == "postgres":
        sql = (
            "CREATE TABLE odd (id INTEGER NOT NULL PRIMARY KEY, c TEXT, "
            "d VARCHAR(20) DEFAULT 'it''s \"q\"');\n"
            "COMMENT ON COLUMN odd.c IS E'one\\r\\ntwo \"q\" \\\\ end';\n"
        )
    elif family == "mysql":
        sql = (
            "CREATE TABLE odd (id INTEGER NOT NULL PRIMARY KEY, "
            "c TEXT COMMENT 'one\\r\\ntwo \"q\" \\\\ end', "
            "d VARCHAR(20) DEFAULT 'it''s \"q\"');\n"
        )
    else:
        sql = (
            "CREATE TABLE odd (id INTEGER NOT NULL PRIMARY KEY, c TEXT, "
            "d VARCHAR(20) DEFAULT 'it''s \"q\"');\n"
        )
    url = fresh(backend, "odd")
    out = tmp_path / "app" / "schema.py"

    written = generate(migrations(tmp_path / "migrations", sql), out, url=url)

    module = load(out)
    if family != "sqlite":
        assert module.ODD.c.c.comment == comment
    rebuilt = _rebuild(backend, module)
    assert _columns(rebuilt, "odd") == _columns(url, "odd")
    _clean(written)


def test_a_foreign_key_to_a_table_left_out_is_left_out_and_said(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    source = migrations(
        tmp_path / "migrations",
        "CREATE TABLE parents (id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE children (id INTEGER PRIMARY KEY, parent_id INTEGER, "
        "FOREIGN KEY (parent_id) REFERENCES parents (id));\n",
    )
    out = tmp_path / "app" / "schema.py"

    with notes() as said:
        written = generate(
            source, out, url=fresh(backend, "fkout"), config=SchemagenConfig(excludes="parents")
        )

    assert any(
        n.startswith("children: foreign key ")
        and n.endswith("refers to parents, which is not generated; left out")
        for n in said
    ), said
    assert "ForeignKeyConstraint" not in written
    _clean(written)
    _rebuild(backend, load(out))


def test_a_foreign_key_from_a_listed_schema_into_the_own_schema(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """PostgreSQL reports the key unqualified (the own schema is on the search path), so it is
    kept, and `create_all` builds it. MySQL and MariaDB report the own database by name; the
    key is left out with a note, because unqualified it would mean the *listed* database's
    `parents`, and qualified it would write the database's name into the module. Pinned as it
    is: an open question in DECISIONS-TO-REVIEW, schemagen tests (EKB-0355-)."""
    if dialect == "sqlite":
        pytest.skip("SQLite has no schemas")
    url = fresh(backend, "fkown")
    own = _own_schema(dialect, url)
    other = f"aud_{uuid.uuid4().hex[:8]}"
    create = "SCHEMA" if dialect == "postgres" else "DATABASE"
    source = migrations(
        tmp_path / "migrations",
        f"CREATE TABLE parents (id INTEGER PRIMARY KEY);\n"
        f"CREATE {create} {other};\n"
        f"CREATE TABLE {other}.entries (id INTEGER PRIMARY KEY, parent_id INTEGER, "
        f"CONSTRAINT fk_entries_parent FOREIGN KEY (parent_id) REFERENCES {own}.parents (id));\n"
        f"CREATE TABLE kids (id INTEGER PRIMARY KEY, entry_id INTEGER, "
        f"CONSTRAINT fk_kids_entry FOREIGN KEY (entry_id) REFERENCES {other}.entries (id));\n",
    )
    out = tmp_path / "app" / "schema.py"

    with notes() as said:
        written = generate(source, out, url=url, config=SchemagenConfig(schemas=(other,)))

    into_other = f'ForeignKeyConstraint(["entry_id"], ["{other}.entries.id"], name="fk_kids_entry")'
    assert into_other in written, "a key into a listed schema is kept, qualified"
    assert f"{own}." not in written, "the own schema's name is never written into the module"
    into_own = 'ForeignKeyConstraint(["parent_id"], ["parents.id"], name="fk_entries_parent")'
    if dialect == "postgres":
        assert into_own in written
        assert not [n for n in said if "foreign key" in n], said
    else:
        assert 'ForeignKeyConstraint(["parent_id"]' not in written
        assert (
            f"{other}.entries: foreign key fk_entries_parent refers to {own}.parents, "
            "which is not generated; left out"
        ) in said
    module = load(out)
    before = (
        [f"CREATE SCHEMA {other}"]
        if dialect == "postgres"
        else [
            # The first database's `kids` still points into it.
            "SET FOREIGN_KEY_CHECKS = 0",
            f"DROP DATABASE {other}",
            f"CREATE DATABASE {other}",
            "SET FOREIGN_KEY_CHECKS = 1",
        ]
    )
    rebuilt = _rebuild(backend, module, *before)
    db = engine(rebuilt)
    try:
        keys = inspect(db).get_foreign_keys("entries", schema=other)
    finally:
        db.dispose()
    assert [(k["constrained_columns"], k["referred_table"]) for k in keys] == (
        [(["parent_id"], "parents")] if dialect == "postgres" else []
    )


def test_index_options_are_kept(tmp_path: Path, backend: Backend, dialect: str) -> None:
    """`USING hash` and `INCLUDE (...)` on PostgreSQL, a prefix length on MySQL and MariaDB:
    written as the dialect's index options, and built again by `create_all`."""
    family = _family(dialect)
    if family == "sqlite":
        pytest.skip("SQLite indexes have no options but WHERE, covered by the shape test")
    if family == "postgres":
        sql = (
            "CREATE TABLE idx (id INTEGER PRIMARY KEY, code VARCHAR(10), note VARCHAR(200));\n"
            "CREATE INDEX ix_idx_hash ON idx USING hash (code);\n"
            "CREATE INDEX ix_idx_incl ON idx (code) INCLUDE (note);\n"
        )
        lines = [
            'Index("ix_idx_hash", "code", postgresql_using="hash")',
            'Index("ix_idx_incl", "code", postgresql_include=[\'note\'])',
        ]
    else:
        sql = (
            "CREATE TABLE idx (id INTEGER PRIMARY KEY, code VARCHAR(10), note VARCHAR(200));\n"
            "CREATE INDEX ix_idx_prefix ON idx (note(10));\n"
        )
        lines = [f'Index("ix_idx_prefix", "note", {dialect}_length={{\'note\': 10}})']
    url = fresh(backend, "idxopt")
    out = tmp_path / "app" / "schema.py"

    written = generate(migrations(tmp_path / "migrations", sql), out, url=url)

    for line in lines:
        assert line in written, written
    rebuilt = _rebuild(backend, load(out))

    def indexes(where: str) -> list[Any]:
        db = engine(where)
        try:
            return sorted(
                (i["name"], i["column_names"], sorted(i.get("dialect_options", {}).items()))
                for i in inspect(db).get_indexes("idx")
            )
        finally:
            db.dispose()

    assert indexes(rebuilt) == indexes(url)


def test_mariadb_s_column_checks_are_kept_and_its_json_check_is_not_a_check(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """Reflection does not list MariaDB's column-level checks; schemagen reads them. A
    `json_valid` check on a LONGTEXT is JSON; the same check on a TEXT, and any other column
    check, stays a check - and `create_all` builds it, so a bad value is refused. A `DEFAULT
    NULL` is no default."""
    if dialect != "mariadb":
        pytest.skip("MariaDB's column checks")
    source = migrations(
        tmp_path / "migrations",
        "CREATE TABLE stock (id INT PRIMARY KEY, qty INT CHECK (qty >= 0), "
        "doc TEXT CHECK (json_valid(doc)), data JSON, note TEXT DEFAULT NULL);\n",
    )
    out = tmp_path / "app" / "schema.py"

    written = generate(source, out, url=fresh(backend, "mchk"))

    assert 'CheckConstraint("`qty` >= 0")' in written
    assert 'CheckConstraint("json_valid(`doc`)")' in written
    assert 'Column("data", json_column())' in written
    assert 'Column("doc", TEXT())' in written
    assert 'Column("note", TEXT())' in written
    rebuilt = _rebuild(backend, load(out))
    db = engine(rebuilt)
    try:
        for values in ("(1, -1, NULL)", "(2, 1, 'not json')"):
            with pytest.raises(DBAPIError, match="CONSTRAINT"), db.begin() as connection:
                connection.execute(text(f"INSERT INTO stock (id, qty, doc) VALUES {values}"))
    finally:
        db.dispose()


# -- the framework's tables, when the migrations do not match their helper -----------------


def _framework_ddl(dialect: str) -> str:
    from sqlalchemy import MetaData

    from ekbatan.migrations import emit_ddl
    from ekbatan.schema import outbox_table

    framework = MetaData()
    outbox_table(framework)
    return emit_ddl(framework, SQLALCHEMY_NAME[dialect])


def test_a_framework_table_unlike_its_helper_is_said(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    source = migrations(
        tmp_path / "migrations",
        "CREATE TABLE apps (id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE events (id CHAR(32) NOT NULL PRIMARY KEY, extra INTEGER);\n",
    )
    out = tmp_path / "app" / "schema.py"

    with notes() as said:
        written = generate(source, out, url=fresh(backend, "fwdiff"))

    differs = [n for n in said if n.startswith("events differs from outbox_table(): ")]
    assert len(differs) == 1, said
    assert "the migrations lack ['action_id', 'action_name'," in differs[0]
    assert (
        "; they add ['extra']. The module uses outbox_table(), the framework's contract"
        in (differs[0])
    )
    assert "EVENTS = outbox_table(metadata)" in written


def test_a_framework_table_s_own_indexes_are_kept_and_an_expression_one_is_said(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """The helpers declare no indexes; the migrations choose them, and `create_all` builds the
    ones on the helper's columns. One on an expression cannot be tied to the helper's table."""
    family = _family(dialect)
    # PostgreSQL only: SQLite's reflection skips an expression index with a warning of its own,
    # and MariaDB has none (ledger, docs gaps).
    expression = (
        "CREATE INDEX ix_events_lower ON events (lower(action_name));\n"
        if dialect == "postgres"
        else ""
    )
    source = migrations(
        tmp_path / "migrations",
        "CREATE TABLE apps (id INTEGER PRIMARY KEY);\n",
        _framework_ddl(dialect)
        + "\nCREATE INDEX ix_events_action ON events (action_name, event_date);\n"
        + expression,
    )
    out = tmp_path / "app" / "schema.py"

    with notes() as said:
        written = generate(source, out, url=fresh(backend, "fwidx"))

    assert (
        'Index("ix_events_action", EVENTS.c["action_name"], EVENTS.c["event_date"])' in written
    ), written
    if expression:
        assert "events: index ix_events_lower is on an expression; left out" in said
    _clean(written)
    rebuilt = _rebuild(backend, load(out))
    db = engine(rebuilt)
    try:
        names = {i["name"] for i in inspect(db).get_indexes("events")}
    finally:
        db.dispose()
    assert "ix_events_action" in names
    assert family != "postgres" or "ix_events_lower" not in names


# -- regenerating: the same input, the same bytes; a stale module, current again (E-4, E-5) ---


def _cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), env.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "-m", "ekbatan.schemagen", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_the_same_migrations_give_the_same_bytes_every_time(
    tmp_path: Path, backend: Backend, dialect: str, user_code: str
) -> None:
    """Twice in one process with every kind of forced type, and once more from the command in
    a process of its own: a Makefile that regenerates must not see a change that is not one."""
    source = migrations(tmp_path / "migrations", _grid_sql(dialect))
    out = tmp_path / "app" / "schema.py"
    url = fresh(backend, "same_a")
    config = SchemagenConfig(
        forced_types=_grid_rules(dialect, _own_schema(dialect, url), user_code)
    )

    first = generate(source, out, url=url, config=config)
    url_b = fresh(backend, "same_b")
    second = generate(
        source,
        out,
        url=url_b,
        config=SchemagenConfig(
            forced_types=_grid_rules(dialect, _own_schema(dialect, url_b), user_code)
        ),
    )
    if _family(dialect) == "mysql":  # the own schema's name is in one rule; nothing else differs
        second = second.replace(_own_schema(dialect, url_b), _own_schema(dialect, url))

    assert second == first
    assert out.read_text() == second, "what is written is what is returned"

    plain = tmp_path / "plain" / "schema.py"
    in_process = generate(source, plain, url=fresh(backend, "same_c"))
    plain.unlink()
    ran = _cli(
        tmp_path,
        "--migrations",
        str(source),
        "--out",
        str(plain),
        "--url",
        fresh(backend, "same_d"),
    )
    assert ran.returncode == 0, ran.stderr
    assert plain.read_text() == in_process


def test_a_stale_module_is_current_again_once_regenerated(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    source = migrations(tmp_path / "migrations", "CREATE TABLE a (id INTEGER PRIMARY KEY);\n")
    out = tmp_path / "app" / "schema.py"
    generate(source, out, url=fresh(backend, "stale_a"))
    before = fingerprint(source)
    (source / "V0002__more.sql").write_text("CREATE TABLE b (id INTEGER PRIMARY KEY);\n")

    with pytest.raises(ValidationError, match=f"fingerprinted {before}"):
        load(out)
    written = generate(source, out, url=fresh(backend, "stale_b"))

    assert f'MIGRATIONS_FINGERPRINT = "{fingerprint(source)}"' in written
    assert load(out).__all__ == ["A", "B", "ARecord", "BRecord", "metadata"]


def test_the_module_imports_in_a_fresh_interpreter(
    tmp_path: Path, backend: Backend, dialect: str, user_code: str
) -> None:
    """Not only under the test's own imports: a new Python, the module's directory, and the
    application's code on the path, as `python -m app` would have it."""
    url = fresh(backend, "fresh")
    source = migrations(tmp_path / "migrations", _grid_sql(dialect))
    out = tmp_path / "app" / "schema.py"
    generate(
        source,
        out,
        url=url,
        config=SchemagenConfig(
            forced_types=_grid_rules(dialect, _own_schema(dialect, url), user_code)
        ),
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src"), str(tmp_path / "user"), str(tmp_path / "app")]
    )

    ran = subprocess.run(
        [sys.executable, "-c", "import schema; print(sorted(schema.metadata.tables))"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "['grid']"
