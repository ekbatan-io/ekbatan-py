"""The parts of yoyo the migrator stands on - checked, so an upgrade cannot move them quietly.

The migrator uses yoyo as a library and subclasses its backends, so it depends on a handful of
names and behaviours that yoyo does not promise to keep. The dependency is pinned to one major
version for that reason, and this file is what a new major version has to get past. If yoyo is
ever abandoned, this is also the list of what to vendor.
"""

from __future__ import annotations

import inspect
import io
import sys
from contextlib import redirect_stdout
from importlib.metadata import version

from yoyo.backends.base import DatabaseBackend
from yoyo.backends.core.mysql import MySQLBackend
from yoyo.backends.core.postgresql import PostgresqlPsycopgBackend
from yoyo.backends.core.sqlite3 import SQLiteBackend
from yoyo.migrations import Migration, MigrationStep, read_sql_migration

from ekbatan import _yoyo


def test_the_pinned_major_version_is_the_one_installed():
    assert version("yoyo-migrations").split(".")[0] == "9"


def test_a_migration_is_built_from_an_id_a_path_and_a_directory(tmp_path):
    path = tmp_path / "V1__two.sql"
    path.write_text("-- transactional: false\nCREATE TABLE a (id INT);\nCREATE TABLE b (id INT);")

    migration = Migration("V1__two", str(path), str(tmp_path))
    migration.load()

    assert migration.use_transactions is False, "the directive the migrator documents"
    assert len(migration.steps) == 2, "one step per statement"
    assert callable(migration.process_steps)


def test_directives_are_read_from_the_leading_comments(tmp_path):
    path = tmp_path / "V1__x.sql"
    path.write_text("-- depends: V0__y\n-- transactional: true\nSELECT 1;")

    directives, _, statements = read_sql_migration(str(path))

    assert directives == {"depends": "V0__y", "transactional": "true"}
    assert statements == ["SELECT 1;"]


class Cursor:
    def __init__(self):
        self.description = [("greeting",)]

    def execute(self, statement):
        self.statement = statement

    def fetchall(self):
        return [("hello",)]


def test_query_output_goes_to_whatever_stdout_is_when_it_runs():
    """The migrator captures it with redirect_stdout, which only works while yoyo looks
    `sys.stdout` up at the moment it prints rather than holding on to it."""
    captured = io.StringIO()
    with redirect_stdout(captured):
        MigrationStep(0, "SELECT 'hello' AS greeting", None)._execute(Cursor(), "SELECT 1")

    assert "greeting" in captured.getvalue() and "hello" in captured.getvalue()
    assert sys.stdout is not captured


def test_the_backend_methods_the_migrator_uses_or_replaces_exist():
    for backend in (PostgresqlPsycopgBackend, MySQLBackend, SQLiteBackend):
        for name in (
            "connect",
            "execute",
            "transaction",
            "disable_transactions",
            "savepoint_rollback",
            "copy",
            "lock",
            "init_database",
            "create_lock_table",
        ):
            assert callable(getattr(backend, name)), f"{backend.__name__}.{name}"
    assert list(inspect.signature(DatabaseBackend.__init__).parameters) == [
        "self",
        "dburi",
        "migration_table",
    ]


def test_yoyo_still_applies_on_a_second_connection_which_is_why_it_is_not_used():
    """If yoyo ever stops opening a second connection per migration, the reason the migrator
    runs migrations itself has gone - worth knowing, so it can be revisited."""
    assert "self.copy()" in inspect.getsource(DatabaseBackend.apply_one)


def test_every_backend_the_migrator_speaks_is_one_of_yoyos():
    assert issubclass(_yoyo.PostgresMigrationBackend, PostgresqlPsycopgBackend)
    assert issubclass(_yoyo.MySQLMigrationBackend, MySQLBackend)
    assert issubclass(_yoyo.SQLiteMigrationBackend, SQLiteBackend)
