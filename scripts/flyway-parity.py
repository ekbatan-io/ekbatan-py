#!/usr/bin/env python3
"""Run the migrator and Flyway through the same scenarios, side by side.

The evidence behind `docs/MIGRATOR-LEDGER.md`. A scenario is a series of steps - the files as
they stand, then a command - run on a fresh database for each tool. After every step the tool's
output is kept, and after the last one the history table and what the migrations left in the
database, so what each tool did is read rather than believed.

    uv run python scripts/flyway-parity.py                  # every scenario, both tools
    uv run python scripts/flyway-parity.py R04 X02           # some
    uv run python scripts/flyway-parity.py --tool flyway     # one tool

Needs docker. PostgreSQL, MariaDB and Flyway 12.0.0 - the version the Java port uses - run in
containers; a SQLite file is shared with the Flyway container through a mounted directory.
Results are also written to `flyway-parity.json` in the working directory.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FLYWAY = "flyway/flyway:12.0.0"
TOOLS = ("flyway", "ours")

# -- databases ---------------------------------------------------------------------------------


class Database:
    key = ""

    def fresh(self, name: str) -> str:
        raise NotImplementedError

    def url(self, handle: str) -> str:
        raise NotImplementedError

    def jdbc(self, handle: str) -> tuple[str, str, str, list[str]]:
        """The JDBC url, user, password, and any `docker run` arguments it needs."""
        raise NotImplementedError

    def query(self, handle: str, sql: str) -> list[tuple[Any, ...]]:
        raise NotImplementedError

    def execute(self, handle: str, sql: str) -> None:
        raise NotImplementedError

    def tables(self, handle: str) -> list[str]:
        raise NotImplementedError


class Postgres(Database):
    key = "pg"

    def __init__(self, port: int) -> None:
        self.port = port

    def _connect(self, db: str) -> Any:
        import psycopg

        return psycopg.connect(
            f"postgresql://test:test@localhost:{self.port}/{db}", autocommit=True
        )

    def fresh(self, name: str) -> str:
        with self._connect("test") as c:
            c.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
            c.execute(f"CREATE DATABASE {name}")
        return name

    def url(self, handle: str) -> str:
        return f"postgresql+psycopg://test:test@localhost:{self.port}/{handle}"

    def jdbc(self, handle: str) -> tuple[str, str, str, list[str]]:
        return f"jdbc:postgresql://host.docker.internal:{self.port}/{handle}", "test", "test", []

    def query(self, handle: str, sql: str) -> list[tuple[Any, ...]]:
        with self._connect(handle) as c:
            cur = c.execute(sql)
            return list(cur.fetchall()) if cur.description else []

    def execute(self, handle: str, sql: str) -> None:
        with self._connect(handle) as c:
            c.execute(sql)

    def tables(self, handle: str) -> list[str]:
        rows = self.query(
            handle,
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "UNION SELECT viewname FROM pg_views WHERE schemaname = 'public'",
        )
        return sorted(r[0] for r in rows)


class Maria(Database):
    key = "maria"

    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port

    def _connect(self, db: str | None) -> Any:
        import pymysql

        return pymysql.connect(
            host=self.host,
            port=self.port,
            user="root",
            password="test",
            database=db,
            autocommit=True,
        )

    def fresh(self, name: str) -> str:
        c = self._connect(None)
        try:
            c.cursor().execute(f"DROP DATABASE IF EXISTS {name}")
            c.cursor().execute(f"CREATE DATABASE {name}")
        finally:
            c.close()
        return name

    def url(self, handle: str) -> str:
        return f"mariadb+pymysql://root:test@{self.host}:{self.port}/{handle}"

    def jdbc(self, handle: str) -> tuple[str, str, str, list[str]]:
        return f"jdbc:mariadb://host.docker.internal:{self.port}/{handle}", "root", "test", []

    def query(self, handle: str, sql: str) -> list[tuple[Any, ...]]:
        c = self._connect(handle)
        try:
            cur = c.cursor()
            cur.execute(sql)
            return list(cur.fetchall()) if cur.description else []
        finally:
            c.close()

    def execute(self, handle: str, sql: str) -> None:
        self.query(handle, sql)

    def tables(self, handle: str) -> list[str]:
        return sorted(r[0] for r in self.query(handle, "SHOW TABLES"))


class SQLite(Database):
    """A file in a directory both this process and the Flyway container can write."""

    key = "sqlite"

    def fresh(self, name: str) -> str:
        directory = Path(tempfile.mkdtemp(prefix=f"{name}-", dir="/private/tmp"))
        directory.chmod(0o777)
        return str(directory)

    def url(self, handle: str) -> str:
        return f"sqlite+pysqlite:///{handle}/test.db"

    def jdbc(self, handle: str) -> tuple[str, str, str, list[str]]:
        return "jdbc:sqlite:/db/test.db", "", "", ["-v", f"{handle}:/db"]

    def query(self, handle: str, sql: str) -> list[tuple[Any, ...]]:
        import sqlite3

        with sqlite3.connect(f"{handle}/test.db") as c:
            return list(c.execute(sql).fetchall())

    def execute(self, handle: str, sql: str) -> None:
        import sqlite3

        c = sqlite3.connect(f"{handle}/test.db", isolation_level=None)
        try:
            c.executescript(sql)
        finally:
            c.close()

    def tables(self, handle: str) -> list[str]:
        rows = self.query(
            handle,
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
            "AND name NOT LIKE 'sqlite_%'",
        )
        return sorted(r[0] for r in rows)


# -- scenarios ---------------------------------------------------------------------------------

Files = dict[str, "str | bytes | None"]


@dataclass
class Step:
    #: Changes to the migrations directory: a path and its content, or `None` to delete it.
    files: Files = field(default_factory=dict)
    #: Where a tool needs something of its own. Since parity fix M the migrator reads Flyway's
    #: files as they are, so only Flyway's flags (`flyway_args`) still differ.
    flyway_files: Files = field(default_factory=dict)
    ours_files: Files = field(default_factory=dict)
    #: migrate | repair | baseline | info | validate | sql
    command: str = "migrate"
    #: For `sql`: run against the database directly, as a person would.
    sql: str = ""
    #: Subdirectories of the migrations directory to use as locations; the directory itself when
    #: empty.
    locations: tuple[str, ...] = ()
    flyway_args: tuple[str, ...] = ()
    #: Keyword arguments for ours, where Flyway takes a flag (`baseline(version="2")`).
    ours_kwargs: dict[str, Any] = field(default_factory=dict)
    #: Run this many processes of the tool at once, as replicas starting together would.
    concurrent: int = 0


@dataclass
class Scenario:
    id: str
    title: str
    steps: list[Step]
    dialects: tuple[str, ...] = ("pg",)
    #: What the migrations left in the database, read after the last step.
    check: Callable[[Database, str], object] | None = None


RUNS = {
    "pg": "CREATE TABLE runs (id SERIAL PRIMARY KEY, name VARCHAR(100));\n",
    "maria": "CREATE TABLE runs (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100));\n",
    "sqlite": "CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);\n",
}


def ran(name: str) -> str:
    return f"INSERT INTO runs (name) VALUES ('{name}');\n"


def runs(db: Database, handle: str) -> object:
    if "runs" not in db.tables(handle):
        return "no runs table"
    return [r[0] for r in db.query(handle, "SELECT name FROM runs ORDER BY id")]


def tables(db: Database, handle: str) -> object:
    return db.tables(handle)


def with_runs(dialect: str, files: Files) -> Files:
    return {"V1__runs.sql": RUNS[dialect], **files}


def per_dialect(
    id: str,
    title: str,
    dialects: tuple[str, ...],
    build: Callable[[str], list[Step]],
    check: Callable[[Database, str], object] | None = runs,
) -> list[Scenario]:
    return [
        Scenario(f"{id}-{d}" if len(dialects) > 1 else id, title, build(d), (d,), check)
        for d in dialects
    ]


FAIL_PG = "SELECT 1/0;\n"
#: A schema change that commits on its own, then a failure: half a migration.
FAIL_MARIA = "CREATE TABLE half (id INT);\nCREATE TABLE half (id INT);\n"


def scenarios() -> list[Scenario]:
    s: list[Scenario] = []
    all3 = ("pg", "maria", "sqlite")

    # -- repeatable migrations
    order = {
        # `C` rather than `A`: beside `R__a.sql`, `R__A.sql` is the same file on a macOS disk.
        "R__B.sql": ran("B"),
        "R__a.sql": ran("a"),
        "R__C.sql": ran("C"),
        "R__a_b.sql": ran("a b"),
        "R__a-b.sql": ran("a-b"),
        "R__ab.sql": ran("ab"),
        "R__Zeta.sql": ran("Zeta"),
        "V2__second.sql": ran("V2"),
    }
    s += per_dialect(
        "R01",
        "order: versioned first, then repeatables by description",
        all3,
        lambda d: [Step(with_runs(d, order))],
    )
    s += per_dialect(
        "R03",
        "unchanged repeatable is not applied again",
        ("pg",),
        lambda d: [Step(with_runs(d, {"R__x.sql": ran("x1")})), Step()],
    )
    s += per_dialect(
        "R04",
        "changed repeatable is applied again",
        all3,
        lambda d: [
            Step(with_runs(d, {"R__x.sql": ran("x1")})),
            Step({"R__x.sql": ran("x2")}),
            Step(command="info"),
        ],
    )
    s += per_dialect(
        "R05",
        "repeatable: only line endings changed",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {"R__x.sql": "-- a\n" + ran("x1")})),
            Step({"R__x.sql": ("-- a\r\n" + ran("x1")).encode()}),
        ],
    )
    s += per_dialect(
        "R06",
        "repeatable changed back: A, B, A",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {"R__x.sql": ran("A")})),
            Step({"R__x.sql": ran("B")}),
            Step({"R__x.sql": ran("A")}),
            Step(command="info"),
        ],
    )
    s += per_dialect(
        "R07",
        "applied repeatable's file deleted",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {"R__x.sql": ran("x")})),
            Step({"R__x.sql": None}),
            Step(command="info"),
        ],
    )
    s += per_dialect(
        "R08",
        "applied repeatable renamed, same content",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {"R__x.sql": ran("x")})),
            Step({"R__x.sql": None, "R__y.sql": ran("x")}),
            Step(command="info"),
        ],
    )
    s += per_dialect(
        "R09",
        "repeatable fails where DDL is transactional, then is fixed",
        ("pg", "sqlite"),
        lambda d: [
            Step(
                with_runs(
                    d,
                    {
                        "R__x.sql": ran("x")
                        + (FAIL_PG if d == "pg" else "INSERT INTO nowhere VALUES (1);\n")
                    },
                )
            ),
            Step({"R__x.sql": ran("x-fixed")}),
        ],
    )
    s += per_dialect(
        "R10",
        "repeatable fails on MariaDB after a schema change",
        ("maria",),
        lambda d: [
            Step(with_runs(d, {"R__x.sql": ran("x") + FAIL_MARIA})),
            Step(command="info"),
            Step({"R__x.sql": ran("x-fixed")}),
            Step(command="repair"),
            Step(command="info"),
            Step(),
        ],
    )
    s += per_dialect(
        "R11",
        "only repeatables, no versioned migration",
        ("pg",),
        lambda d: [Step({"R__v.sql": "CREATE OR REPLACE VIEW v AS SELECT 1 AS n;"})],
        check=tables,
    )
    s += per_dialect(
        "R12",
        "repeatable and a pending versioned migration in one run",
        ("pg",),
        lambda d: [Step(with_runs(d, {})), Step({"R__x.sql": ran("x"), "V2__two.sql": ran("V2")})],
    )
    s += per_dialect(
        "R13",
        "a versioned migration fails: repeatables do not run",
        ("pg",),
        lambda d: [Step(with_runs(d, {"V2__bad.sql": FAIL_PG, "R__x.sql": ran("x")}))],
    )
    s += per_dialect(
        "R14",
        "two repeatables, one changed",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {"R__a.sql": ran("a1"), "R__b.sql": ran("b1")})),
            Step({"R__b.sql": ran("b2")}),
        ],
    )
    s += per_dialect(
        "R15",
        "repeatable outside a transaction",
        ("pg",),
        lambda d: [
            Step(
                with_runs(d, {"R__x.sql": ran("x")}),
                flyway_files={"R__x.sql.conf": "executeInTransaction=false\n"},
                ours_files={"R__x.sql.conf": "executeInTransaction=false\n"},
                flyway_args=("-postgresql.transactional.lock=false",),
            )
        ],
    )
    s += per_dialect(
        "R16",
        "an older release starts after a newer one",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {"V2__two.sql": ran("V2"), "R__x.sql": ran("new")})),
            Step({"V2__two.sql": None, "R__x.sql": ran("old")}),
        ],
    )
    s += per_dialect(
        "R19",
        "a repeatable with a version: R1__x.sql",
        ("pg",),
        lambda d: [Step(with_runs(d, {"R1__x.sql": ran("x")}))],
    )
    s += per_dialect(
        "R20",
        "empty descriptions: V2__.sql, R__.sql",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {"V2__.sql": ran("V2"), "R__.sql": ran("r")})),
            Step(command="info"),
        ],
    )
    s += per_dialect(
        "R21",
        "two repeatables with one description: R__a_b and 'R__a b'",
        ("pg",),
        lambda d: [Step(with_runs(d, {"R__a_b.sql": ran("1"), "R__a b.sql": ran("2")}))],
    )
    s += per_dialect(
        "R22",
        "one repeatable in two locations",
        ("pg",),
        lambda d: [
            Step(
                {"one/V1__runs.sql": RUNS[d], "one/R__x.sql": ran("1"), "two/R__x.sql": ran("2")},
                locations=("one", "two"),
            )
        ],
    )

    # -- failure and recovery
    s += per_dialect(
        "X02",
        "versioned migration fails on MariaDB after a schema change",
        ("maria",),
        lambda d: [
            Step(with_runs(d, {"V2__half.sql": ran("V2") + FAIL_MARIA})),
            Step(command="info"),
            Step(),
            Step({"V2__half.sql": "CREATE TABLE IF NOT EXISTS half (id INT);\n"}),
            Step(command="repair"),
            Step(command="info"),
            Step(),
        ],
        check=lambda db, h: {"runs": runs(db, h), "tables": db.tables(h)},
    )
    s += per_dialect(
        "X02b",
        "non-transactional file fails on PostgreSQL",
        ("pg",),
        lambda d: [
            Step(
                with_runs(d, {"V2__half.sql": ran("V2") + FAIL_PG}),
                flyway_files={"V2__half.sql.conf": "executeInTransaction=false\n"},
                ours_files={"V2__half.sql.conf": "executeInTransaction=false\n"},
                flyway_args=("-postgresql.transactional.lock=false",),
            ),
            Step(command="info"),
            Step(flyway_args=("-postgresql.transactional.lock=false",)),
        ],
    )
    s += per_dialect(
        "X04a",
        "repair after an applied file was edited",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {"V2__two.sql": ran("V2")})),
            Step({"V2__two.sql": ran("V2") + "-- edited\n"}),
            Step(command="repair"),
            Step(command="info"),
            Step(),
        ],
    )
    s += per_dialect(
        "X04b",
        "repair after an applied file was deleted",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {"V2__two.sql": ran("V2"), "V3__three.sql": ran("V3")})),
            Step({"V2__two.sql": None}),
            Step(command="repair"),
            Step(command="info"),
            Step(),
            Step({"V2__two.sql": ran("V2")}),
        ],
    )
    s += per_dialect(
        "X04c",
        "repair after an applied file was renamed",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {"V2__two.sql": ran("V2")})),
            Step({"V2__two.sql": None, "V2__deux.sql": ran("V2")}),
            Step(command="repair"),
            Step(command="info"),
            Step(),
        ],
    )
    s += per_dialect(
        "X05",
        "baseline a database that already has tables",
        all3,
        lambda d: [
            Step(command="sql", sql="CREATE TABLE legacy (id INT);"),
            Step({"V1__legacy.sql": "CREATE TABLE legacy (id INT);\n", "V2__runs.sql": RUNS[d]}),
            Step(command="baseline"),
            Step(command="info"),
            Step(),
            Step(command="info"),
        ],
        check=tables,
    )
    s += per_dialect(
        "X05b",
        "baseline twice, and baseline after migrating",
        ("pg",),
        lambda d: [Step(with_runs(d, {})), Step(command="baseline")],
    )

    # -- one migrate, start to end
    s += per_dialect(
        "M03",
        "tables but no history table",
        all3,
        lambda d: [
            Step(command="sql", sql="CREATE TABLE legacy (id INT);"),
            Step(with_runs(d, {})),
        ],
        check=tables,
    )
    s += per_dialect(
        "M03b",
        "only a view, only a sequence, only a function",
        ("pg",),
        lambda d: [Step(command="sql", sql="CREATE SEQUENCE s;"), Step(with_runs(d, {}))],
        check=tables,
    )
    s += per_dialect(
        "M03c",
        "only a view",
        ("maria",),
        lambda d: [
            Step(command="sql", sql="CREATE VIEW v AS SELECT 1 AS n;"),
            Step(with_runs(d, {})),
        ],
        check=tables,
    )
    s += per_dialect(
        "M16",
        "no migration files at all",
        ("pg",),
        lambda d: [Step({"README": "notes\n"})],
        check=tables,
    )

    # -- files
    s += per_dialect(
        "F02",
        "`.SQL` in capitals",
        ("pg",),
        lambda d: [Step(with_runs(d, {"V2__caps.SQL": ran("V2"), "R__caps.SQL": ran("R")}))],
    )
    s += per_dialect(
        "F04",
        "subdirectories, hidden ones, deeper ones",
        ("pg",),
        lambda d: [
            Step(
                with_runs(
                    d,
                    {
                        "sub/V2__two.sql": ran("V2"),
                        "sub/deeper/V3__three.sql": ran("V3"),
                        ".hidden/V4__four.sql": ran("V4"),
                        "_under/V5__five.sql": ran("V5"),
                    },
                )
            )
        ],
    )
    s += per_dialect(
        "F10",
        "a `.conf` beside a migration",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {"V2__two.sql": ran("V2"), "V2__two.sql.conf": "nonsense=true\n"}))
        ],
    )
    s += per_dialect(
        "F11",
        "a file that is not UTF-8",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {"V2__latin1.sql": b"-- caf\xe9\n" + ran("V2").encode()})),
            Step(),
        ],
    )
    s += per_dialect(
        "F16",
        "MySQL DELIMITER around a procedure",
        ("maria",),
        lambda d: [
            Step(
                with_runs(
                    d,
                    {
                        "R__proc.sql": (
                            "DROP PROCEDURE IF EXISTS p;\nDELIMITER //\n"
                            "CREATE PROCEDURE p() BEGIN\n  INSERT INTO runs (name) VALUES ('p1');\n"
                            "  INSERT INTO runs (name) VALUES ('p2');\nEND //\nDELIMITER ;\n"
                            "CALL p();\n"
                        )
                    },
                )
            )
        ],
    )
    s += per_dialect(
        "F17m",
        "MySQL procedure without DELIMITER",
        ("maria",),
        lambda d: [
            Step(
                with_runs(
                    d,
                    {
                        "R__proc.sql": (
                            "DROP PROCEDURE IF EXISTS p;\n"
                            "CREATE PROCEDURE p() BEGIN\n  INSERT INTO runs (name) VALUES ('p1');\n"
                            "  INSERT INTO runs (name) VALUES ('p2');\nEND;\nCALL p();\n"
                        )
                    },
                )
            )
        ],
    )
    s += per_dialect(
        "F17p",
        "PostgreSQL function body in $$ with ; inside",
        ("pg",),
        lambda d: [
            Step(
                with_runs(
                    d,
                    {
                        "R__fn.sql": (
                            "CREATE OR REPLACE FUNCTION f() RETURNS void AS $$\nBEGIN\n"
                            "  INSERT INTO runs (name) VALUES ('f1');\n"
                            "  INSERT INTO runs (name) VALUES ('f2');\nEND;\n"
                            "$$ LANGUAGE plpgsql;\nSELECT f();\n"
                            "DO $body$ BEGIN INSERT INTO runs (name) VALUES ('do'); END $body$;\n"
                        )
                    },
                )
            )
        ],
    )
    s += per_dialect(
        "F17s",
        "SQLite trigger BEGIN ... END with ; inside",
        ("sqlite",),
        lambda d: [
            Step(
                with_runs(
                    d,
                    {
                        "V2__log.sql": "CREATE TABLE log (n TEXT);\n",
                        "R__trigger.sql": (
                            "DROP TRIGGER IF EXISTS t;\n"
                            "CREATE TRIGGER t AFTER INSERT ON log BEGIN\n"
                            "  INSERT INTO runs (name) VALUES ('t1');\n"
                            "  INSERT INTO runs (name) VALUES ('t2');\nEND;\n"
                            "INSERT INTO log VALUES ('x');\n"
                        ),
                    },
                )
            )
        ],
    )
    s += per_dialect(
        "F18",
        "afterMigrate: when it runs",
        ("pg",),
        lambda d: [
            Step(
                with_runs(d, {}),
                flyway_files={"afterMigrate.sql": ran("after")},
                ours_files={"afterMigrate.sql": ran("after")},
            ),
            Step(),
            Step({"V2__two.sql": ran("V2")}),
        ],
    )
    s += per_dialect(
        "F18b",
        "afterMigrate fails",
        ("pg",),
        lambda d: [
            Step(
                with_runs(d, {}),
                flyway_files={"afterMigrate.sql": ran("after") + FAIL_PG},
                ours_files={"afterMigrate.sql": ran("after") + FAIL_PG},
            ),
            Step(command="info"),
            Step(
                flyway_files={"afterMigrate.sql": ran("after-fixed")},
                ours_files={"afterMigrate.sql": ran("after-fixed")},
            ),
        ],
    )
    s += per_dialect(
        "R23",
        "a newer release added a repeatable; an older release starts",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {"V2__two.sql": ran("V2"), "R__new.sql": ran("new")})),
            Step({"V2__two.sql": None, "R__new.sql": None}),
        ],
    )
    s += per_dialect(
        "R24",
        "repair after a repeatable's file was deleted, then it comes back",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {"R__x.sql": ran("x")})),
            Step({"R__x.sql": None}),
            Step(command="repair"),
            Step(command="info"),
            Step(),
            Step({"R__x.sql": ran("x")}),
            Step(command="info"),
        ],
    )
    s += per_dialect(
        "X02c",
        "a newer release's migration failed; an older release starts",
        ("maria",),
        lambda d: [
            Step(with_runs(d, {"V2__half.sql": ran("V2") + FAIL_MARIA})),
            Step({"V2__half.sql": None}),
            Step(command="info"),
        ],
    )
    s += per_dialect(
        "X04d",
        "repair with nothing to repair",
        ("pg",),
        lambda d: [Step(with_runs(d, {})), Step(command="repair")],
    )
    s += per_dialect(
        "M12b",
        "an applied file's version respelled: V2 to V002",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {"V2__two.sql": ran("V2")})),
            Step({"V2__two.sql": None, "V002__two.sql": ran("V2")}),
            Step(command="info"),
        ],
    )
    s += per_dialect(
        "M12c",
        "an applied file moved into a subdirectory",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {"V2__two.sql": ran("V2")})),
            Step({"V2__two.sql": None, "sub/V2__two.sql": ran("V2")}),
            Step(command="info"),
        ],
    )
    s += per_dialect(
        "X05c",
        "baseline an empty database",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {"V2__two.sql": ran("V2")}), command="baseline"),
            Step(command="info"),
            Step(),
        ],
        check=tables,
    )
    s += per_dialect(
        "X05d",
        "baseline at version 2",
        ("pg",),
        lambda d: [
            Step(command="sql", sql=RUNS[d]),
            Step(
                with_runs(d, {"V2__two.sql": ran("V2"), "V3__three.sql": ran("V3")}),
                command="baseline",
                flyway_args=("-baselineVersion=2",),
                ours_kwargs={"version": "2"},
            ),
            Step(),
            Step(command="info"),
        ],
    )
    s += per_dialect(
        "C03",
        "four processes at once, a changed repeatable",
        ("pg", "maria"),
        lambda d: [
            Step(with_runs(d, {"R__x.sql": ran("x1")})),
            Step({"R__x.sql": ran("x2"), "V2__two.sql": ran("V2")}, concurrent=4),
        ],
    )
    s += per_dialect(
        "C04",
        "four processes at once, a migration that fails half-way",
        ("maria",),
        lambda d: [
            Step(with_runs(d, {"V2__half.sql": ran("V2") + FAIL_MARIA}), concurrent=4),
            Step(command="info"),
        ],
    )
    long = "a" * 210
    s += per_dialect(
        "H07",
        "a description longer than 200 characters",
        ("pg",),
        lambda d: [
            Step(with_runs(d, {f"V2__{long}.sql": ran("V2"), f"R__{long}.sql": ran("R")})),
            Step(),
        ],
    )
    return s


# -- the two tools -----------------------------------------------------------------------------

NOISE = re.compile(
    r"^(Flyway OSS Edition|See release notes|Database: |WARNING: No locations configured|"
    r"A more recent version|You are not signed in|Flyway (Community|Teams)|$)"
)


@dataclass
class Outcome:
    ok: bool
    lines: list[str]


def materialize(root: Path, step: Step, tool: str) -> None:
    changes = {**step.files, **(step.flyway_files if tool == "flyway" else step.ours_files)}
    for name, content in changes.items():
        path = root / name
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
    for path in [root, *root.rglob("*")]:
        path.chmod(0o777 if path.is_dir() else 0o666)


def run_flyway(db: Database, handle: str, root: Path, step: Step) -> Outcome:
    url, user, password, _ = db.jdbc(handle)
    name = f"fw-{uuid.uuid4().hex[:8]}"
    locations = (
        ",".join(f"filesystem:/flyway/sql/{loc}" for loc in step.locations)
        or "filesystem:/flyway/sql"
    )
    if db.key == "sqlite":
        # SQLite through a mounted directory fails inside docker on macOS ("the database file
        # has been moved since it was opened"), so the file is copied in and back out instead.
        url = "jdbc:sqlite:/tmp/db/test.db"
    arguments = [
        "-v",
        f"{root}:/flyway/sql",
        FLYWAY,
        f"-url={url}",
        f"-user={user}",
        f"-password={password}",
        "-connectRetries=0",
        f"-locations={locations}",
        *step.flyway_args,
        step.command,
    ]
    try:
        if db.key == "sqlite":
            subprocess.run(
                ["docker", "create", "--name", name, *arguments], check=True, capture_output=True
            )
            subprocess.run(
                ["docker", "cp", f"{handle}/.", f"{name}:/tmp/db"], check=True, capture_output=True
            )
            done = subprocess.run(
                ["docker", "start", "-a", name], capture_output=True, text=True, timeout=180
            )
            subprocess.run(
                ["docker", "cp", f"{name}:/tmp/db/.", handle], check=True, capture_output=True
            )
        else:
            done = subprocess.run(
                ["docker", "run", "--rm", "--name", name, *arguments],
                capture_output=True,
                text=True,
                timeout=180,
            )
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True)
        return Outcome(False, ["HUNG - still running after 180s, killed"])
    finally:
        if db.key == "sqlite":
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    lines = [line.rstrip() for line in (done.stdout + done.stderr).splitlines()]
    return Outcome(done.returncode == 0, [line for line in lines if not NOISE.match(line)])


def run_ours(db: Database, handle: str, root: Path, step: Step) -> Outcome:
    from ekbatan import migrations
    from ekbatan.config import ShardingConfig, split_login

    locations = [root / loc for loc in step.locations] or [root]
    plain, username, password = split_login(db.url(handle))
    config = ShardingConfig.single(plain, username=username, password=password)
    captured: list[str] = []
    handler = _Capture(captured)
    logger = logging.getLogger("io.ekbatan.flyway.FlywayMigrator")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        if step.command == "migrate":
            (result,) = migrations.migrate(config, *locations)
            r = result.migrate_result
            captured.append(
                f"result: {r.initial_schema_version} -> {r.target_schema_version}, "
                f"applied {list(r.migrations)}"
            )
            captured += [f"warning: {w}" for w in r.warnings]
        else:
            command = getattr(migrations, step.command, None)
            if command is None:
                return Outcome(False, [f"no `{step.command}` in ekbatan.migrations"])
            if step.command == "baseline":
                (outcome,) = command(config, **step.ours_kwargs)
                captured.append(
                    f"result: baselined at {outcome.version}"
                    + ("" if outcome.created else " (already)")
                )
            elif step.command == "info":
                (outcome,) = command(config, *locations, **step.ours_kwargs)
                captured.append(f"schema version: {outcome.schema_version}")
                captured += [
                    f"| {m.category:10} | {m.version or '':7} | {m.description[:30]:30} "
                    f"| {m.type:8} | {m.state.value:18} |"
                    for m in outcome.migrations
                ]
            elif step.command == "repair":
                (outcome,) = command(config, *locations, **step.ours_kwargs)
                captured.append(
                    f"result: removed {list(outcome.removed)}, deleted {list(outcome.deleted)}, "
                    f"aligned {list(outcome.aligned)}"
                )
            else:
                captured.append(f"result: {command(config, *locations)!r}")
        return Outcome(True, captured)
    except Exception as error:
        return Outcome(False, [*captured, f"{type(error).__name__}: {error}"])
    finally:
        logger.removeHandler(handler)


class _Capture(logging.Handler):
    def __init__(self, into: list[str]) -> None:
        super().__init__(logging.INFO)
        self.into = into

    def emit(self, record: logging.LogRecord) -> None:
        self.into.append(f"{record.levelname}: {record.getMessage()}")


def concurrently(
    runner: Callable[[Database, str, Path, Step], Outcome],
    db: Database,
    handle: str,
    root: Path,
    step: Step,
) -> list[Outcome]:
    """The tool run `step.concurrent` times at once, as replicas starting together would.

    Flyway runs in its own container each time. Ours runs in its own process each time - not a
    thread, because the lock under test is between processes.
    """
    if runner is run_flyway:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(step.concurrent) as pool:
            futures = [
                pool.submit(run_flyway, db, handle, root, step) for _ in range(step.concurrent)
            ]
            return [f.result() for f in futures]
    locations = [str(root / loc) for loc in step.locations] or [str(root)]
    code = (
        "import json, sys\n"
        "from ekbatan.migrations import migrate\n"
        "from ekbatan.config import ShardingConfig, split_login\n"
        "try:\n"
        f"    url, user, pw = split_login({db.url(handle)!r})\n"
        "    config = ShardingConfig.single(url, username=user, password=pw)\n"
        f"    (r,) = migrate(config, *{locations!r})\n"
        "    print(json.dumps([True, [f'applied {list(r.migrate_result.migrations)}']]))\n"
        "except Exception as e:\n"
        "    print(json.dumps([False, [f'{type(e).__name__}: {e}']]))\n"
    )
    processes = [
        subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        for _ in range(step.concurrent)
    ]
    outcomes = []
    for process in processes:
        out, _ = process.communicate(timeout=300)
        ok, lines = json.loads(out.strip().splitlines()[-1])
        outcomes.append(Outcome(ok, lines))
    return outcomes


def history(db: Database, handle: str, tool: str) -> list[tuple[Any, ...]] | str:
    table = "flyway_schema_history"  # both tools' since parity fix M
    if table not in db.tables(handle):
        return "no history table"
    for columns in (
        "installed_rank, version, description, type, script, checksum, success",
        "version, description, script, checksum",
    ):
        with suppress(Exception):
            order = "installed_rank" if "installed_rank" in columns else "version"
            return db.query(handle, f"SELECT {columns} FROM {table} ORDER BY {order}")
    return "unreadable"


# -- running -----------------------------------------------------------------------------------


@contextmanager
def databases(wanted: set[str]) -> Iterator[dict[str, Database]]:
    from contextlib import ExitStack

    with ExitStack() as stack:
        found: dict[str, Database] = {"sqlite": SQLite()}
        if "pg" in wanted:
            from testcontainers.postgres import PostgresContainer

            pgc = stack.enter_context(
                PostgresContainer(
                    "postgres:17-alpine", username="test", password="test", dbname="test"
                )
            )
            found["pg"] = Postgres(int(pgc.get_exposed_port(5432)))
        if "maria" in wanted:
            from testcontainers.mysql import MySqlContainer

            mc = stack.enter_context(
                MySqlContainer("mariadb:11.4", username="root", password="test", dbname="test")
            )
            found["maria"] = Maria(mc.get_container_host_ip(), int(mc.get_exposed_port(3306)))
        yield found


def run(scenario: Scenario, db: Database, tools: tuple[str, ...]) -> dict[str, Any]:
    report: dict[str, Any] = {"id": scenario.id, "title": scenario.title, "dialect": db.key}
    for tool in tools:
        handle = db.fresh(f"p_{scenario.id.lower().replace('-', '_')}_{tool[:2]}")
        root = Path(tempfile.mkdtemp(dir="/private/tmp")) / "sql"
        root.mkdir()
        steps = []
        for step in scenario.steps:
            materialize(root, step, tool)
            if step.command == "sql":
                db.execute(handle, step.sql)
                continue
            runner = run_flyway if tool == "flyway" else run_ours
            if step.concurrent:
                outcomes = concurrently(runner, db, handle, root, step)
                steps += [
                    {"command": f"{step.command}#{i}", "ok": o.ok, "lines": o.lines}
                    for i, o in enumerate(outcomes, 1)
                ]
                continue
            outcome = runner(db, handle, root, step)
            steps.append({"command": step.command, "ok": outcome.ok, "lines": outcome.lines})
        report[tool] = {
            "steps": steps,
            "history": history(db, handle, tool),
            "check": scenario.check(db, handle) if scenario.check else None,
        }
    return report


def show(report: dict[str, Any], tools: tuple[str, ...]) -> None:
    print(f"\n### {report['id']} ({report['dialect']}): {report['title']}")
    for tool in tools:
        result = report[tool]
        print(f"  [{tool}]")
        for i, step in enumerate(result["steps"], 1):
            mark = "ok  " if step["ok"] else "FAIL"
            print(f"    {i}. {step['command']:8} {mark}")
            for line in step["lines"][:30]:
                print(f"         {line[:220]}")
        print(f"    history: {result['history']}")
        if result["check"] is not None:
            print(f"    left   : {result['check']}")


def comparable(history: object) -> object:
    """A history's rows without what is meant to differ: the rank, the checksum (CRC32 against
    SHA-256) and the baseline's wording."""
    if not isinstance(history, list):
        return history
    rows = []
    for row in history:
        _, version, description, kind, script, _, success = row
        if kind == "BASELINE":
            description = script = "<< baseline >>"
        rows.append((version, description, kind, script, bool(success)))
    return rows


def left(check: object) -> object:
    """What a scenario left in the database, with each tool's history table under one name."""
    text = json.dumps(check, default=str)
    for name in ("flyway_schema_history", "ekbatan_schema_history"):
        text = text.replace(name, "<history>")
    return json.loads(text)


def summarize(reports: list[dict[str, Any]]) -> list[tuple[str, str, str, str, str]]:
    """One line per scenario: each tool's steps (ok or FAIL), and whether the two left the
    database the same and wrote the same history."""
    lines = []
    for report in reports:
        if "flyway" not in report or "ours" not in report:
            continue
        flyway, ours = report["flyway"], report["ours"]
        steps = [
            " ".join("ok" if step["ok"] else "FAIL" for step in tool["steps"])
            for tool in (flyway, ours)
        ]
        same_data = "same" if left(flyway["check"]) == left(ours["check"]) else "DIFFERENT"
        same_history = (
            "same" if comparable(flyway["history"]) == comparable(ours["history"]) else "DIFFERENT"
        )
        lines.append((f"{report['id']}", steps[0], steps[1], same_data, same_history))
    return lines


def show_summary(reports: list[dict[str, Any]]) -> None:
    lines = summarize(reports)
    widths = [max(len(line[i]) for line in [HEAD, *lines]) for i in range(5)]
    for line in [HEAD, *lines]:
        print("  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True)))


HEAD = ("scenario", "flyway", "ours", "data", "history")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("ids", nargs="*", help="scenario ids or prefixes (R04, R04-pg, X)")
    parser.add_argument("--tool", choices=(*TOOLS, "both"), default="both")
    parser.add_argument(
        "--summary", metavar="JSON", help="summarize an earlier run's flyway-parity.json and stop"
    )
    args = parser.parse_args()
    if args.summary:
        show_summary(json.loads(Path(args.summary).read_text()))
        return
    tools = TOOLS if args.tool == "both" else (args.tool,)
    chosen = [
        sc
        for sc in scenarios()
        if not args.ids or any(sc.id == i or sc.id.startswith(i) for i in args.ids)
    ]
    logging.getLogger("testcontainers").setLevel(logging.WARNING)
    reports = []
    with databases({d for sc in chosen for d in sc.dialects}) as dbs:
        for sc in chosen:
            (dialect,) = sc.dialects
            report = run(sc, dbs[dialect], tools)
            show(report, tools)
            reports.append(report)
    Path("flyway-parity.json").write_text(json.dumps(reports, indent=1, default=str))
    if len(tools) == 2:
        print()
        show_summary(reports)


if __name__ == "__main__":
    main()
