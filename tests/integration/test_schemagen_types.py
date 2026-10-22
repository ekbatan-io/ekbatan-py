"""The type table, proven on each database: every common SQL type, through the whole pipeline.

For each database, one migration declares a column of every type below. Then, for real:

1. ``generate`` migrates a fresh database and writes ``schema.py``;
2. the module is imported - round 2 found common types (MySQL's BOOLEAN) that stopped it
   importing at all;
3. ``metadata.create_all`` builds a second fresh database from it;
4. each case checks the type it was written as, that ``create_all`` built the column the
   migration built (or the documented one, where a forced helper builds its own), and that a
   value goes in and comes back - through the migrated database and the rebuilt one, and as a
   ``WHERE`` that finds it;
5. the table's record, ``TTypesRecord``, names each column with the type its value really comes
   back as - read through the framework's own sync engine and its async one, so through every
   driver either mode uses - and mypy accepts the whole module.

    uv run pytest tests/integration/test_schemagen_types.py --dialects=all
"""

from __future__ import annotations

import asyncio
import sys
import types
import typing
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import insert, select

from backends import Backend
from ekbatan.config import DataSourceConfig, split_login
from ekbatan.schemagen import generate
from ekbatan.time import Instant, LocalDateTime, OffsetDateTime
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
from schemagen_type_cases import (
    CASES_BY_DIALECT,
    NAIVE,
    PREAMBLE,
    SAME,
    Case,
)


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
    assert matrix.module.__all__ == ["T_TYPES", "TTypesRecord", "metadata"]
    names = {c.name for c in CASES_BY_DIALECT[dialect]}
    assert set(matrix.module.T_TYPES.c.keys()) == {"pk", *names}


def test_the_module_imports_exactly_what_it_uses(matrix: Matrix) -> None:
    """Every type in one module: no import left unused, no name used and not imported."""
    assert unused_and_undefined(matrix.text) == (set(), set())


def test_timestamps_stay_zone_less_and_hold_what_was_written(matrix: Matrix, dialect: str) -> None:
    """The UTC recommendation, end to end: the column built has no zone (TIMESTAMP, DATETIME(6)),
    and a naive UTC value goes in, is stored and comes back exactly as written - no zone added,
    to the microsecond."""
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
            connection.execute(insert(table).values({"pk": 7_000_001, name: NAIVE}))
            back = connection.execute(
                select(table.c[name]).where(table.c.pk == 7_000_001)
            ).scalar_one()
            stored = connection.execute(
                text(f"SELECT {name} FROM t_types WHERE pk = 7000001")
            ).scalar_one()
    finally:
        db.dispose()
    assert back == NAIVE and back.tzinfo is None
    if isinstance(stored, str):  # SQLite keeps text
        stored = datetime.fromisoformat(stored)
    assert stored == NAIVE, "the value as written, with no zone"


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
    if dialect == "postgres":
        said = 't_types.c_pg_char is "char": it reads as str on psycopg and as bytes on asyncpg'
        assert any(n.startswith(said) for n in matrix.notes), matrix.notes
    assert all("always-utc" in n for n in matrix.notes if "TIMESTAMP" in n or "DATETIME" in n)


# -- the record ---------------------------------------------------------------------------------


def _holds(value: Any, hint: Any) -> bool:
    """Whether a value read back is what the record's hint says - a runtime reading of the
    hint, as far as each kind of hint goes."""
    if hint is Any:
        return True
    if hint in (Instant, LocalDateTime):
        # A label, not a class: a datetime with no zone attached.
        return isinstance(value, datetime) and value.tzinfo is None
    if hint is OffsetDateTime:
        return isinstance(value, datetime) and value.utcoffset() is not None
    if isinstance(hint, typing.TypeAliasType):
        return _holds(value, hint.__value__)
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if origin is typing.Union or origin is types.UnionType:
        return any(_holds(value, a) for a in args)
    if hint is None or hint is types.NoneType:
        return value is None
    if origin is typing.Literal:
        return value in args
    if origin in (list, set, Sequence):
        if not isinstance(value, origin) or isinstance(value, (str, bytes)):
            return False
        return all(_holds(v, args[0]) for v in value) if args else True
    if origin in (dict, Mapping):
        return isinstance(value, origin) and all(
            _holds(k, args[0]) and _holds(v, args[1]) for k, v in value.items()
        )
    if origin is not None:  # Range[int], MultiRange[datetime]
        if not isinstance(value, origin):
            return False
        bounds = [b for b in (getattr(value, "lower", None), getattr(value, "upper", None))]
        return all(b is None or _holds(b, args[0]) for b in bounds)
    return isinstance(value, hint)


def _config(url: str) -> DataSourceConfig:
    plain, username, password = split_login(url)
    return DataSourceConfig(url=plain, username=username, password=password)


def _read_back(url: str, table: Any, pk: int, row: dict[str, Any]) -> tuple[Any, Any]:
    """Write the row through the framework's sync engine, and read it back through that and
    through its async one."""
    from ekbatan.aio.persistence import async_engine_for
    from ekbatan.persistence import engine_for

    config = _config(url)
    sync = engine_for(config)
    try:
        with sync.begin() as connection:
            connection.execute(insert(table).values({"pk": pk, **row}))
            through_sync = connection.execute(select(table).where(table.c.pk == pk)).one()
    finally:
        sync.dispose()

    async def read() -> Any:
        engine = async_engine_for(config)
        try:
            async with engine.connect() as connection:
                found = await connection.execute(select(table).where(table.c.pk == pk))
                return found.one()
        finally:
            await engine.dispose()

    return through_sync._mapping, asyncio.run(read())._mapping


def test_the_record_names_every_column_with_the_type_it_reads_as(
    matrix: Matrix, dialect: str
) -> None:
    """Every case's value, read back through the sync driver and the async one, is what
    ``TTypesRecord`` says that column holds - the record is not a guess."""
    # A TypedDict resolves its hints in its module as `sys.modules` has it; `load` took it out.
    sys.modules[matrix.module.__name__] = matrix.module
    try:
        hints = typing.get_type_hints(matrix.module.TTypesRecord)
    finally:
        sys.modules.pop(matrix.module.__name__, None)
    cases = CASES_BY_DIALECT[dialect]
    assert set(hints) == {"pk", *(c.name for c in cases)}
    row = {c.name: c.value for c in cases}
    sync, async_ = _read_back(matrix.migrated, matrix.module.T_TYPES, 9_000_001, row)
    wrong = {
        (mode, c.name): (type(read[c.name]).__name__, hints[c.name])
        for mode, read in (("sync", sync), ("async", async_))
        for c in cases
        if not _holds(read[c.name], hints[c.name])
    }
    assert wrong == {}, "read back as something the record does not say"
    # And a column left NULL is allowed to be: every case column is nullable here.
    assert all(_holds(None, hints[c.name]) for c in cases)


def test_mypy_accepts_the_generated_module(matrix: Matrix) -> None:
    """The module, records and all, type-checks under ``--strict``."""
    from mypy import api

    module = Path(matrix.module.__file__ or "")
    stdout, stderr, status = api.run(
        ["--strict", "--no-incremental", "--follow-imports=silent", str(module)]
    )
    assert status == 0, stdout + stderr


def test_an_array_of_a_domain_is_written_whole_and_its_record_says_what_both_drivers_agree_on(
    backend: Backend, dialect: str, tmp_path: Path
) -> None:
    """psycopg does not know a domain's array type: it hands back the text, ``{5,6}``, which
    SQLAlchemy's ARRAY splits into characters. asyncpg reads ``[5, 6]``. No one type says both,
    so the record says ``Any`` and a note says why - and how to read it as a list on both."""
    if dialect != "postgres":
        pytest.skip("domains are PostgreSQL's")
    from sqlalchemy import cast
    from sqlalchemy.dialects import postgresql

    out = tmp_path / "schema.py"
    url = fresh(backend, "domain_array")
    sql = (
        "CREATE DOMAIN posint AS INTEGER CHECK (VALUE > 0);\n"
        "CREATE TABLE t_arr (pk INTEGER PRIMARY KEY, dd posint[]);\n"
    )
    with notes() as said:
        text = generate(migrations(tmp_path / "migrations", sql), out, url=url)
    module = load(out)
    assert (
        'postgresql.ARRAY(postgresql.DOMAIN("posint", sqlalchemy.INTEGER(), '
        'constraint_name="posint_check", check=sqlalchemy.text("((VALUE > 0))")))'
    ) in text
    assert "    dd: Any\n" in text
    assert any("t_arr.dd is an array of the domain posint" in n for n in said), said

    sync, async_ = _read_back(url, module.T_ARR, 1, {"dd": [5, 6]})
    assert async_["dd"] == [5, 6]
    assert sync["dd"] == list("{5,6}"), "psycopg learned the type: the record can say list[int]"
    # The note's way out: cast to the base type's array, and psycopg reads a list too.
    db = engine(url)
    try:
        with db.connect() as connection:
            column = module.T_ARR.c.dd
            read = connection.execute(
                select(cast(column, postgresql.ARRAY(column.type.item_type.data_type)))
            ).scalar_one()
    finally:
        db.dispose()
    assert read == [5, 6]
