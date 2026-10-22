"""schemagen's completeness, proven by a round trip on each database.

For each case: the migration runs into a fresh database A; ``generate`` reads A and writes the
module; the module is imported; ``metadata.create_all`` builds a second fresh database B from it;
then A and B describe themselves from their own catalogs (``schemagen_support.describe_database``)
and the two descriptions must be the same, line for line. A line that differs is something the
module lost or wrote wrong - unless the case names it as a difference schemagen means to make,
and then a note must say so at generation time.

    uv run pytest tests/integration/test_schemagen_round_trip.py --dialects=all

The ledger is `docs/SCHEMAGEN-COMPLETENESS-LEDGER.md`; each case names its rows.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text

from backends import Backend
from ekbatan.schemagen import SchemagenConfig, generate
from schemagen_support import describe_database, engine, fresh, load, migrations, notes


@dataclass(frozen=True)
class Trip:
    id: str
    sql: str
    #: SQL run on B before `create_all`: what a module cannot create (an extension).
    before: tuple[str, ...] = ()
    #: Other schemas to read (PostgreSQL), created empty on B first.
    schemas: tuple[str, ...] = ()
    #: Lines allowed to differ, by a piece of their text - each a difference schemagen makes on
    #: purpose and says so in a note.
    differs: tuple[str, ...] = ()
    #: Pieces of text the notes must hold.
    notes: tuple[str, ...] = ()
    #: Pieces of text the generated module must hold.
    writes: tuple[str, ...] = ()
    #: Pieces of text the generated module must not hold.
    never: tuple[str, ...] = field(default_factory=tuple)


POSTGRES = (
    # K11-K14: every way PostgreSQL can say an index's elements.
    Trip(
        "index_elements",
        """
        CREATE TABLE t (id INT PRIMARY KEY, a INT, b INT, name TEXT, created TIMESTAMP,
                        doc JSONB, r INT4RANGE);
        CREATE INDEX t_lower ON t (lower(name));
        CREATE INDEX t_partial ON t (a) WHERE b > 0;
        CREATE INDEX t_incl ON t (a) INCLUDE (b, name);
        CREATE INDEX t_desc ON t (a DESC NULLS LAST, b ASC NULLS FIRST);
        CREATE INDEX t_ops ON t (name text_pattern_ops);
        CREATE INDEX t_gin ON t USING gin (doc jsonb_path_ops);
        CREATE INDEX t_brin ON t USING brin (created) WITH (pages_per_range = 32);
        CREATE INDEX t_hash ON t USING hash (name);
        CREATE INDEX t_gist ON t USING gist (r);
        CREATE UNIQUE INDEX t_uq_expr ON t (lower(name), a) WHERE a IS NOT NULL;
        CREATE UNIQUE INDEX t_uq_nnd ON t (a, b) NULLS NOT DISTINCT;
        CREATE INDEX t_mixed ON t (a, (b + 1) DESC, lower(name) COLLATE "C");
        CREATE INDEX t_coll ON t (name COLLATE "C" DESC);
        CREATE INDEX t_ops_desc ON t (name text_pattern_ops DESC);
        """,
        writes=(
            'sqlalchemy.text("a DESC NULLS LAST")',
            "postgresql_ops={'name': 'text_pattern_ops'}",
        ),
    ),
)


#: C3: MySQL's most usual timestamps, forced to `timestamp_column()` (DATETIME(6)): their defaults
#: of the current time must say six digits too, or MySQL refuses the column.
CURRENT_TIME_DEFAULTS = Trip(
    "current_time_defaults",
    """
    CREATE TABLE ts (
      id INT PRIMARY KEY,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      d3 DATETIME(3) DEFAULT NOW(3),
      d6 DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
    );
    """,
    # The documented difference: timestamp_column() builds DATETIME(6).
    differs=("`created_at`", "`updated_at`", "`d3`"),
    notes=(
        "ts.created_at: its default of the current time is written at six digits",
        "ts.updated_at: its default of the current time is written at six digits",
        "ts.d3: its default of the current time is written at six digits",
    ),
)

MYSQL = (
    CURRENT_TIME_DEFAULTS,
    # K15-K17: functional key parts, DESC, prefixes, FULLTEXT and SPATIAL.
    Trip(
        "index_elements",
        """
        CREATE TABLE t (
          id INT PRIMARY KEY, a INT, b DECIMAL(10, 2), name VARCHAR(100), body TEXT,
          pt POINT NOT NULL SRID 0,
          KEY t_desc (b DESC, a),
          KEY t_func ((lower(name))),
          KEY t_func_desc (a, (b + 1) DESC),
          KEY t_prefix (body(20)),
          KEY t_prefix_desc (name(10) DESC),
          UNIQUE KEY t_uq (name(50), a),
          FULLTEXT KEY t_ft (body),
          SPATIAL KEY t_sp (pt)
        );
        """,
        writes=('sqlalchemy.text("(lower(`name`))")', "mysql_length={'body': 20}"),
    ),
)

MARIADB = (
    CURRENT_TIME_DEFAULTS,
    # K16-K17: MariaDB has no functional key parts; DESC since 10.8.
    Trip(
        "index_elements",
        """
        CREATE TABLE t (
          id INT PRIMARY KEY, a INT, b DECIMAL(10, 2), name VARCHAR(100), body TEXT,
          KEY t_desc (b DESC, a),
          KEY t_prefix (body(20)),
          KEY t_prefix_desc (name(10) DESC),
          UNIQUE KEY t_uq (name(50), a),
          FULLTEXT KEY t_ft (body)
        );
        """,
        writes=('sqlalchemy.text("b DESC")',),
    ),
)

SQLITE = (
    # T24, T25, C10, C11, O10: what only SQLite's own text of a table says.
    Trip(
        "table_text",
        """
        CREATE TABLE auto (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT);
        CREATE TABLE alias (id INTEGER PRIMARY KEY, v TEXT);
        CREATE TABLE coll (
          id INTEGER PRIMARY KEY, a VARCHAR(20) COLLATE NOCASE, b TEXT COLLATE RTRIM,
          c CHAR(3) NOT NULL COLLATE BINARY, d TEXT DEFAULT 'x COLLATE y'
        );
        CREATE TABLE untyped (id INTEGER PRIMARY KEY, anything, other NOT NULL);
        CREATE TABLE wr (k TEXT PRIMARY KEY, v INT) WITHOUT ROWID;
        CREATE TABLE st (id INTEGER PRIMARY KEY, v TEXT, n INT, a ANY) STRICT;
        """,
        writes=(
            "sqlite_autoincrement=True",
            "sqlite_with_rowid=False",
            "sqlite_strict=True",
            'sqlalchemy.VARCHAR(length=20, collation="NOCASE")',
            'sqlalchemy.Column("anything", sql_type(""))',
            "    id: NotRequired[int]",
        ),
        never=("autoincrement=False",),
    ),
    # K19-K21: SQLite's reflection skips an index on an expression and drops DESC and COLLATE.
    Trip(
        "index_elements",
        """
        CREATE TABLE t (id INTEGER PRIMARY KEY, a INT, b REAL, name TEXT);
        CREATE INDEX t_lower ON t (lower(name));
        CREATE INDEX t_partial ON t (a) WHERE a > 0;
        CREATE INDEX t_desc ON t (a DESC, b);
        CREATE INDEX t_coll ON t (name COLLATE NOCASE);
        CREATE UNIQUE INDEX t_uq_expr ON t (lower(name), a) WHERE a IS NOT NULL;
        CREATE INDEX "t odd" ON t ("a" ASC, [b]);
        """,
        writes=('sqlalchemy.Index("t_lower", sqlalchemy.text("lower(name)"))',),
    ),
)

CASES_BY_DIALECT: dict[str, tuple[Trip, ...]] = {
    "postgres": POSTGRES,
    "mysql": MYSQL,
    "mariadb": MARIADB,
    "sqlite": SQLITE,
}


def _case(dialect: str, case_id: str) -> Trip:
    return next(c for c in CASES_BY_DIALECT[dialect] if c.id == case_id)


def round_trip(backend: Backend, tmp_path: Path, case: Trip) -> tuple[str, list[str], Any]:
    """Migrate A, generate, import, `create_all` into B; assert the two describe the same."""
    a = fresh(backend, "rt_a")
    out = tmp_path / "schema.py"
    with notes() as said:
        generate(
            migrations(tmp_path / "migrations", case.sql),
            out,
            url=a,
            config=SchemagenConfig(schemas=case.schemas),
        )
    written = out.read_text()
    module = load(out)
    b = fresh(backend, "rt_b")
    db = engine(b)
    try:
        with db.begin() as connection:
            for schema in case.schemas:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            for sql in case.before:
                connection.exec_driver_sql(sql)
        module.metadata.create_all(db)
    finally:
        db.dispose()

    def kept(lines: list[str]) -> list[str]:
        return [line for line in lines if not any(d in line for d in case.differs)]

    migrated = kept(describe_database(a, case.schemas))
    rebuilt = kept(describe_database(b, case.schemas))
    assert migrated == rebuilt, "\n".join(
        difflib.unified_diff(migrated, rebuilt, "migrated", "create_all", lineterm="", n=0)
    )
    for piece in case.writes:
        assert piece in written, f"{piece!r} not in the module:\n{written}"
    for piece in case.never:
        assert piece not in written, f"{piece!r} in the module:\n{written}"
    for piece in case.notes:
        assert any(piece in n for n in said), f"no note says {piece!r}: {said}"
    return written, said, module


def test_round_trip(backend: Backend, dialect: str, case_id: str, tmp_path: Path) -> None:
    round_trip(backend, tmp_path, _case(dialect, case_id))
