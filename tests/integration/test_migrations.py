"""The migrator against a real database - every dialect the run asks for.

Java runs Flyway here. The migrator is yoyo underneath, with a lock, checks and a history of its
own on top, because yoyo alone gets each of those wrong. The behaviour copied from Flyway was run
on Flyway 12 first - `docs/MIGRATOR-LEDGER.md` says what each run showed, and
`scripts/flyway-parity.py` runs it again. What several processes do at once is
`test_migration_concurrency.py`; what is decided before a database is reached is
`tests/unit/test_migration_scripts.py` and `tests/unit/test_migration_model.py`.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from ekbatan._yoyo import POSTGRES_LOCK_KEY, mysql_lock_name, open_backend
from ekbatan.config import ShardingConfig
from ekbatan.migrations import (
    HISTORY_TABLE,
    MigrationError,
    MigrationState,
    YoyoMigrator,
    baseline,
    info,
    migrate,
    repair,
    target,
)
from ekbatan.sharding import ShardIdentifier
from fixtures.config import group, member
from fixtures.logins import data_source
from fixtures.logins import single as single_with_login
from fixtures.migrating import fresh, query, tables

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)


# -- helpers ---------------------------------------------------------------------------------


def history(url: str) -> list[tuple[str | None, str]]:
    """The rows of migrations that succeeded, in the order they were recorded."""
    return [(version, script) for version, script, _, _, success in rows(url) if success]


def rows(url: str) -> list[tuple[str | None, str, str, str, bool]]:
    """Every row: version, script, description, type, success."""
    if HISTORY_TABLE not in tables(url):
        return []
    found = query(
        url,
        f"SELECT version, script, description, type, success FROM {HISTORY_TABLE} "
        "ORDER BY installed_rank",
    )
    return [(r[0], r[1], r[2], r[3], bool(r[4])) for r in found]


def failed(url: str) -> list[str]:
    return [script for _, script, _, _, success in rows(url) if not success]


RUNS = {
    "postgres": "CREATE TABLE runs (id SERIAL PRIMARY KEY, name VARCHAR(100));",
    "mysql": "CREATE TABLE runs (id INTEGER AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100));",
    "mariadb": "CREATE TABLE runs (id INTEGER AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100));",
    "sqlite": "CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);",
}


def ran(name: str) -> str:
    """A migration that says it ran, so the order and the number of runs can be read back."""
    return f"INSERT INTO runs (name) VALUES ('{name}');\n"


def runs(url: str) -> list[str]:
    return [r[0] for r in query(url, "SELECT name FROM runs ORDER BY id")]


def transactional_ddl(backend) -> bool:
    return backend.dialect in ("postgres", "sqlite")


#: A schema change that commits on its own on MySQL and MariaDB, then a failure: half a
#: migration, where one cannot be rolled back.
HALF = "CREATE TABLE half (id INTEGER);\nINSERT INTO nowhere VALUES (1);\n"


class Files:
    """A migrations directory, written file by file as a project would grow one."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True)

    def add(self, name: str, sql: str) -> Files:
        path = self.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sql)
        return self

    def drop(self, name: str) -> None:
        (self.path / name).unlink()

    def rename(self, old: str, new: str) -> None:
        (self.path / old).rename(self.path / new)


@pytest.fixture
def files(tmp_path) -> Files:
    return Files(tmp_path / "migrations")


def single(url: str) -> ShardingConfig:
    return single_with_login(url)


# -- applying, in version order (#5) ---------------------------------------------------------


def test_a_fresh_database_gets_every_migration_in_version_order(backend, request, files):
    """`V10` alters the table `V2` creates. Sorted as text, as yoyo sorts, it ran first and
    failed; sorted as numbers it runs last."""
    url = fresh(backend, request)
    files.add("V2__gadgets.sql", "CREATE TABLE gadgets (id INTEGER PRIMARY KEY);")
    files.add("V10__gadget_names.sql", "ALTER TABLE gadgets ADD COLUMN name VARCHAR(20);")
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")

    (result,) = migrate(single(url), files.path)

    assert result.migrate_result.migrations == (
        "V1__widgets.sql",
        "V2__gadgets.sql",
        "V10__gadget_names.sql",
    )
    assert result.migrate_result.initial_schema_version is None
    assert result.migrate_result.target_schema_version == "10"
    assert result.target.name == "default/member-0"
    assert {"widgets", "gadgets", HISTORY_TABLE} <= tables(url)
    assert history(url) == [
        ("1", "V1__widgets.sql"),
        ("2", "V2__gadgets.sql"),
        ("10", "V10__gadget_names.sql"),
    ]


def test_running_again_applies_nothing(backend, request, files):
    url = fresh(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    migrate(single(url), files.path)

    (again,) = migrate(single(url), files.path)

    assert again.migrate_result.migrations == ()
    assert again.migrate_result.initial_schema_version == "1"
    assert again.migrate_result.target_schema_version == "1"


def test_a_new_migration_is_picked_up(backend, request, files):
    url = fresh(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    migrate(single(url), files.path)

    files.add("V2__gadgets.sql", "CREATE TABLE gadgets (id INTEGER PRIMARY KEY);")
    (result,) = migrate(single(url), files.path)

    assert result.migrate_result.migrations == ("V2__gadgets.sql",)
    assert result.migrate_result.initial_schema_version == "1"
    assert "gadgets" in tables(url)


def test_the_history_records_what_each_file_contained(backend, request, files):
    url = fresh(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")

    migrate(single(url), files.path)

    ((rank, version, description, kind, script, checksum, by, on, took, success),) = query(
        url,
        "SELECT installed_rank, version, description, type, script, checksum, installed_by, "
        f"installed_on, execution_time, success FROM {HISTORY_TABLE}",
    )
    assert (rank, version, description, kind, script) == (
        1,
        "1",
        "widgets",
        "SQL",
        "V1__widgets.sql",
    )
    assert len(checksum) == 64, "sha256 of what the file contained"
    assert on.endswith("Z") and took >= 0 and bool(success) is True
    assert by == _user(url, backend.dialect), "the database user, as Flyway records it"


def _user(url: str, dialect: str) -> str:
    if dialect == "postgres":
        return str(query(url, "SELECT current_user")[0][0])
    if dialect in ("mysql", "mariadb"):
        return str(query(url, "SELECT SUBSTRING_INDEX(USER(), '@', 1)")[0][0])
    return ""  # SQLite has no users; Flyway records an empty string too


# -- the files against the history (#1 #2 #3 #4) --------------------------------------------


def test_duplicate_versions_are_refused_and_the_database_is_not_touched(backend, request, files):
    """#1 - checked before any connection is made."""
    url = fresh(backend, request)
    files.add("V1__create_foo.sql", "CREATE TABLE foo (id INTEGER PRIMARY KEY);")
    files.add("V1__create_bar.sql", "CREATE TABLE bar (id INTEGER PRIMARY KEY);")

    with pytest.raises(MigrationError, match="two migrations are one migration"):
        migrate(single(url), files.path)

    assert tables(url) == set(), "not even the history table"


def test_an_edited_migration_is_refused_and_nothing_new_runs(backend, request, files):
    """#2 - measured with yoyo alone: it accepted the edit, and the database kept the old table
    while the file described a new one."""
    url = fresh(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    migrate(single(url), files.path)

    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY, n INTEGER);")
    files.add("V2__gadgets.sql", "CREATE TABLE gadgets (id INTEGER PRIMARY KEY);")
    with pytest.raises(MigrationError, match="has changed since it was applied"):
        migrate(single(url), files.path)

    assert "gadgets" not in tables(url), "refused before anything ran"


def test_a_different_line_ending_is_not_an_edit(backend, request, files):
    url = fresh(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);\n")
    migrate(single(url), files.path)

    (files.path / "V1__widgets.sql").write_bytes(
        b"CREATE TABLE widgets (id INTEGER PRIMARY KEY);\r\n"
    )

    (result,) = migrate(single(url), files.path)
    assert result.migrate_result.migrations == ()


def test_a_renamed_migration_is_refused(backend, request, files):
    url = fresh(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    migrate(single(url), files.path)

    files.rename("V1__widgets.sql", "V1__things.sql")

    with pytest.raises(MigrationError, match=r"was applied as 'widgets'.*now says 'things'"):
        migrate(single(url), files.path)


def test_a_migration_older_than_one_applied_is_refused_and_nothing_runs(backend, request, files):
    """#3 - yoyo runs it, out of order, without a word."""
    url = fresh(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    files.add("V3__sprockets.sql", "CREATE TABLE sprockets (id INTEGER PRIMARY KEY);")
    migrate(single(url), files.path)

    files.add("V2__gadgets.sql", "CREATE TABLE gadgets (id INTEGER PRIMARY KEY);")
    with pytest.raises(MigrationError, match=r"V2__gadgets\.sql has not been applied"):
        migrate(single(url), files.path)

    assert "gadgets" not in tables(url)


def test_an_applied_migration_whose_file_is_gone_is_refused(backend, request, files):
    """#4 - yoyo ignores it."""
    url = fresh(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    files.add("V2__gadgets.sql", "CREATE TABLE gadgets (id INTEGER PRIMARY KEY);")
    migrate(single(url), files.path)

    files.drop("V1__widgets.sql")

    with pytest.raises(MigrationError, match="its file is gone"):
        migrate(single(url), files.path)


def test_a_database_a_newer_release_migrated_is_left_alone(backend, request, files, caplog):
    """An older release starting against a newer schema - a rollback of the application. Flyway
    lets it start, warning; so does this."""
    url = fresh(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    files.add("V2__gadgets.sql", "CREATE TABLE gadgets (id INTEGER PRIMARY KEY);")
    migrate(single(url), files.path)

    files.drop("V2__gadgets.sql")
    caplog.set_level(logging.WARNING, logger="io.ekbatan.flyway.FlywayMigrator")
    (result,) = migrate(single(url), files.path)

    assert result.migrate_result.migrations == ()
    assert result.migrate_result.target_schema_version == "2"
    assert "newer than every migration here" in caplog.text
    assert result.migrate_result.warnings


# -- what a SELECT in a migration returns (#11) ----------------------------------------------


def test_query_output_goes_to_the_log_not_the_screen(backend, request, files, caplog, capsys):
    url = fresh(backend, request)
    files.add("V1__hello.sql", "SELECT 'hello from the migration' AS greeting;")
    caplog.set_level(logging.INFO, logger="io.ekbatan.flyway.FlywayMigrator")

    migrate(single(url), files.path)

    assert "hello from the migration" in caplog.text, "logged, as Flyway logs it"
    assert "greeting" in caplog.text
    assert "hello from the migration" not in capsys.readouterr().out, "not printed"


def test_query_output_can_be_switched_off(backend, request, files, caplog, capsys):
    """Flyway's `outputQueryResults=false`."""
    url = fresh(backend, request)
    files.add("V1__hello.sql", "SELECT 'hello from the migration' AS greeting;")
    caplog.set_level(logging.INFO, logger="io.ekbatan.flyway.FlywayMigrator")

    migrate(single(url), files.path, output_query_results=False)

    assert "hello from the migration" not in caplog.text
    assert "hello from the migration" not in capsys.readouterr().out


# -- applied and recorded together (#14, #13) ------------------------------------------------


def _fail_to_record(*args, **kwargs):
    raise RuntimeError("the history row could not be written")


def test_a_migration_and_its_history_row_commit_together(backend, request, files, monkeypatch):
    """#14 - yoyo records a migration in a separate step after applying it, so a failure between
    the two leaves it applied and unrecorded, and it runs again. Here the recording fails on
    purpose. Where a schema change can be rolled back, nothing of the migration remains; on
    MySQL and MariaDB the table stays, because no tool can undo a committed schema change there
    (#13) - and the message says so."""
    url = fresh(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    monkeypatch.setattr("ekbatan.migrations._record", _fail_to_record)

    with pytest.raises(MigrationError) as raised:
        migrate(single(url), files.path)

    message = str(raised.value)
    assert "failed in V1__widgets.sql" in message
    assert history(url) == [], "not recorded"
    if backend.dialect in ("postgres", "sqlite"):
        assert "widgets" not in tables(url), "rolled back with the row"
        assert "Nothing of V1__widgets.sql remains" in message
    else:
        assert "widgets" in tables(url), "MySQL committed the CREATE TABLE on its own"
        assert "MySQL and MariaDB commit each one on its own" in message


def test_a_data_migration_is_recorded_atomically_on_every_dialect(
    backend, request, files, monkeypatch
):
    """No schema change inside, so nothing commits on its own - not even on MySQL."""
    url = fresh(backend, request)
    files.add("V1__table.sql", "CREATE TABLE things (id INTEGER PRIMARY KEY);")
    migrate(single(url), files.path)

    files.add("V2__rows.sql", "INSERT INTO things VALUES (1);\nINSERT INTO things VALUES (2);")
    monkeypatch.setattr("ekbatan.migrations._record", _fail_to_record)
    with pytest.raises(MigrationError):
        migrate(single(url), files.path)

    assert query(url, "SELECT count(*) FROM things")[0][0] == 0


def test_a_failure_names_the_migration_and_what_ran_before_it(backend, request, files):
    url = fresh(backend, request)
    files.add("V1__ok.sql", "CREATE TABLE ok_table (id INTEGER PRIMARY KEY);")
    files.add("V2__broken.sql", "CREATE TABLE ( THIS IS NOT SQL;")

    with pytest.raises(MigrationError) as raised:
        migrate(single(url), files.path)

    message = str(raised.value)
    assert "migrating default/member-0 (0:0) failed in V2__broken.sql" in message
    assert "Applied by this run before that: V1__ok.sql." in message
    assert "Already migrated: none" in message
    assert history(url) == [("1", "V1__ok.sql")], "what succeeded stays recorded"
    if transactional_ddl(backend):
        assert failed(url) == [], "rolled back as a whole, so there is nothing to record"
    else:
        assert failed(url) == ["V2__broken.sql"], "MySQL: recorded as failed, as Flyway records it"


# -- running outside a transaction (#10) -----------------------------------------------------


def test_a_postgres_index_can_be_built_concurrently(backend, request, files, caplog):
    if backend.dialect != "postgres":
        pytest.skip("CREATE INDEX CONCURRENTLY is PostgreSQL's")
    url = fresh(backend, request)
    files.add("V1__table.sql", "CREATE TABLE people (id INTEGER PRIMARY KEY, owner TEXT);")
    files.add(
        "V2__index.sql",
        "-- transactional: false\nCREATE INDEX CONCURRENTLY ix_people_owner ON people (owner);",
    )
    caplog.set_level(logging.WARNING, logger="io.ekbatan.flyway.FlywayMigrator")

    migrate(single(url), files.path)

    indexes = {r[0] for r in query(url, "SELECT indexname FROM pg_indexes")}
    assert "ix_people_owner" in indexes
    assert "no transaction in progress" not in caplog.text, "yoyo's stray ROLLBACK is not sent"


def test_without_the_directive_postgres_refuses_it_and_says_why(backend, request, files):
    if backend.dialect != "postgres":
        pytest.skip("CREATE INDEX CONCURRENTLY is PostgreSQL's")
    url = fresh(backend, request)
    files.add("V1__table.sql", "CREATE TABLE people (id INTEGER PRIMARY KEY, owner TEXT);")
    files.add("V2__index.sql", "CREATE INDEX CONCURRENTLY ix_people_owner ON people (owner);")

    with pytest.raises(MigrationError, match="cannot run inside a transaction block"):
        migrate(single(url), files.path)


def test_a_migration_outside_a_transaction_keeps_its_rows(backend, request, files):
    """#10 - yoyo on MySQL and MariaDB never commits these: the rows vanished when the connection
    closed, and the migration was recorded as applied anyway. Measured."""
    url = fresh(backend, request)
    files.add("V1__table.sql", "CREATE TABLE things (id INTEGER PRIMARY KEY);")
    files.add(
        "V2__rows.sql",
        "-- transactional: false\nINSERT INTO things VALUES (1);\nINSERT INTO things VALUES (2);",
    )

    migrate(single(url), files.path)

    assert [r[0] for r in query(url, "SELECT id FROM things ORDER BY id")] == [1, 2]
    assert history(url)[-1] == ("2", "V2__rows.sql")


def test_on_mysql_the_real_error_is_reported_not_a_savepoint_error(backend, request, files):
    """#10 - a schema change commits implicitly on MySQL and MariaDB and drops yoyo's savepoint;
    rolling back to it failed with `SAVEPOINT sp_N does not exist`, which replaced the error
    that actually happened."""
    if backend.dialect not in ("mysql", "mariadb"):
        pytest.skip("only MySQL and MariaDB commit a schema change on their own")
    url = fresh(backend, request)
    files.add("V1__half.sql", "CREATE TABLE half (id INTEGER);\nINSERT INTO nope VALUES (1);")

    with pytest.raises(MigrationError) as raised:
        migrate(single(url), files.path)

    assert "nope" in str(raised.value), "the statement that failed"
    assert "SAVEPOINT" not in str(raised.value)
    assert "half" in tables(url), "#13: the schema change stays - a database limit, documented"
    assert history(url) == [] and failed(url) == ["V1__half.sql"]


# -- one connection, closed when done (#12, #15) ---------------------------------------------


def test_the_migrator_never_opens_a_second_connection_or_uses_yoyos_lock(backend, request):
    """#15 - yoyo applies each migration on a connection it opens with `copy()`, apart from
    the one holding the lock. Neither path is reachable here."""
    migrator = open_backend("s", fresh(backend, request), {})
    try:
        with pytest.raises(AssertionError, match="nothing may open another"):
            migrator.copy()
        with pytest.raises(AssertionError, match="not yoyo's"):
            migrator.lock()
    finally:
        migrator.close()


def test_no_yoyo_lock_table_is_created(backend, request, files):
    url = fresh(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")

    migrate(single(url), files.path)

    assert "yoyo_lock" not in tables(url)
    assert not {t for t in tables(url) if t.startswith("_yoyo")}, "nor yoyo's bookkeeping"


def _alter_history(url: str, dialect: str) -> None:
    """A schema change on the history table, with a short wait limit where there is one."""
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            if dialect == "postgres":
                conn.execute(text("SET lock_timeout = '5s'"))
            elif dialect in ("mysql", "mariadb"):
                conn.execute(text("SET SESSION lock_wait_timeout = 5"))
            conn.execute(text(f"ALTER TABLE {HISTORY_TABLE} ADD COLUMN note VARCHAR(10)"))
    finally:
        engine.dispose()


def _sessions(url: str, dialect: str) -> int:
    if dialect == "postgres":
        sql = "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
    else:
        sql = "SELECT count(*) FROM information_schema.processlist WHERE db = DATABASE()"
    return int(query(url, sql)[0][0])


def test_a_run_with_nothing_to_apply_leaves_nothing_open(backend, request, files):
    """#12 - a transaction left open by a read with nothing after it held a lock on the history
    table, and a later schema change waited on it forever: 2h39m, the one time it happened."""
    url = fresh(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    migrate(single(url), files.path)
    before = _sessions(url, backend.dialect) if backend.dialect != "sqlite" else 0

    migrate(single(url), files.path)  # nothing to apply

    started = time.monotonic()
    _alter_history(url, backend.dialect)
    assert time.monotonic() - started < 5, "not blocked"
    if backend.dialect != "sqlite":
        assert _sessions(url, backend.dialect) == before, "the migrator's connection is closed"


# -- the database it reaches (follow-ups a and b) --------------------------------------------


def test_a_read_only_mysql_is_refused(backend, request, files):
    """A replica, or a server set aside. Root could write past `read_only`, which is why this
    refuses rather than trying."""
    if backend.dialect not in ("mysql", "mariadb"):
        pytest.skip("read_only is MySQL's and MariaDB's; PostgreSQL's replica check is a unit test")
    url = fresh(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")

    query(url, "SET GLOBAL read_only = ON")
    try:
        with pytest.raises(MigrationError, match="is read-only"):
            migrate(single(url), files.path)
    finally:
        query(url, "SET GLOBAL read_only = OFF")

    assert tables(url) == set(), "refused before anything was created"


class Held:
    """The migration lock of one database, held from outside - as another process would."""

    def __init__(self, url: str, dialect: str) -> None:
        self._handle = None
        if dialect == "sqlite":
            import fcntl

            path = url.removeprefix("sqlite:///") + "-ekbatan-migrate.lock"
            self._handle = open(path, "a+b")  # noqa: SIM115
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
            return
        self._engine = create_engine(url, isolation_level="AUTOCOMMIT")
        self._conn = self._engine.connect()
        if dialect == "postgres":
            self._conn.execute(text(f"SELECT pg_advisory_lock({POSTGRES_LOCK_KEY})"))
        else:
            database = self._conn.execute(text("SELECT DATABASE()")).scalar()
            name = mysql_lock_name(database)
            assert self._conn.execute(text(f"SELECT GET_LOCK('{name}', 0)")).scalar() == 1

    def release(self) -> None:
        if self._handle is not None:
            self._handle.close()
            return
        self._conn.close()
        self._engine.dispose()


def test_a_database_whose_lock_is_held_waits_and_another_database_does_not(
    backend, request, files, caplog
):
    """Follow-up b - two shards that are two databases on one server must not wait on each
    other: MySQL and MariaDB lock names are server-wide, so the name carries the database."""
    busy, free = fresh(backend, request, "_a"), fresh(backend, request, "_b")
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    caplog.set_level(logging.INFO, logger="io.ekbatan.flyway.FlywayMigrator")

    held = Held(busy, backend.dialect)
    outcome: list[object] = []
    waiter = threading.Thread(target=lambda: outcome.append(migrate(single(busy), files.path)))
    try:
        started = time.monotonic()
        migrate(single(free), files.path)
        assert time.monotonic() - started < 5, "the other database is not held up"

        waiter.start()
        time.sleep(2)
        assert waiter.is_alive(), "the held database waits"
        assert "waiting for it to finish" in caplog.text
    finally:
        held.release()
    waiter.join(timeout=15)

    assert not waiter.is_alive() and outcome, "and runs once the lock is let go"
    assert "widgets" in tables(busy)


# -- SQLite specifics ------------------------------------------------------------------------


def test_sqlite_locks_with_a_file_beside_the_database(backend, request, files):
    if backend.dialect != "sqlite":
        pytest.skip("SQLite only")
    url = fresh(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")

    migrate(single(url), files.path)

    assert Path(url.removeprefix("sqlite:///") + "-ekbatan-migrate.lock").exists()


def test_an_in_memory_sqlite_database_migrates_with_nothing_to_lock(files):
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")

    (result,) = migrate(single("sqlite://"), files.path)

    assert result.migrate_result.migrations == ("V1__widgets.sql",)


# -- several databases, one at a time --------------------------------------------------------


def two_shards(backend, request) -> ShardingConfig:
    return ShardingConfig(
        default_shard=GLOBAL,
        groups=(
            group(0, "global", member(0, data_source(fresh(backend, request, "_a")))),
            group(1, "mexico", member(0, data_source(fresh(backend, request, "_b")))),
        ),
    )


def test_every_shard_is_migrated_in_layout_order(backend, request, files):
    config = two_shards(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")

    results = migrate(config, files.path)

    assert [r.target.name for r in results] == ["global/member-0", "mexico/member-0"]
    assert all(r.migrate_result.migrations == ("V1__widgets.sql",) for r in results)


def test_a_subset_migrates_only_those_shards_and_the_rest_later(backend, request, files):
    """Java does this by passing some of `targets(config)`: a rollout one group at a time."""
    config = two_shards(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")

    (only,) = migrate(config, files.path, shards=[MEXICO])
    assert only.target.shard == MEXICO
    assert "widgets" not in tables(config.member(GLOBAL).primary_config().sqlalchemy_url())

    later = migrate(config, files.path)
    assert [r.migrate_result.migrations_executed for r in later] == [1, 0]


def test_shards_go_in_layout_order_not_argument_order(backend, request, files):
    config = two_shards(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")

    results = migrate(config, files.path, shards=[MEXICO, GLOBAL])

    assert [r.target.shard for r in results] == [GLOBAL, MEXICO]


def test_a_failing_shard_stops_the_run_and_names_what_was_already_migrated(backend, request, files):
    """Mexico already has a table and no history, which is refused as Flyway refuses it."""
    config = two_shards(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    query(
        config.member(MEXICO).primary_config().sqlalchemy_url(), "CREATE TABLE widgets (id INTEGER)"
    )

    with pytest.raises(MigrationError) as raised:
        migrate(config, files.path)

    message = str(raised.value)
    assert "mexico/member-0 (1:0)" in message, "the shard that failed"
    assert "Already migrated: global/member-0" in message
    assert "what was applied is skipped" in message, "and what to do about it"


def _errors(caplog) -> list[tuple[str, str | None, str | None]]:
    """The migrator's ERROR lines, each with the shard and target its log context carried."""
    return [
        (r.getMessage(), getattr(r, "ekbatanShard", None), getattr(r, "ekbatanTarget", None))
        for r in caplog.records
        if r.name == "io.ekbatan.flyway.FlywayMigrator" and r.levelno == logging.ERROR
    ]


def test_a_failed_migration_is_logged_as_java_logs_it(backend, request, files, caplog):
    """Java's `inLogContext` and builder `migrate()`: `Migrating <target> failed` inside the
    target's log context, then `Stopped at <target>; already migrated: [...]` outside it."""
    caplog.set_level(logging.INFO, logger="io.ekbatan.flyway.FlywayMigrator")
    files.add("V1__broken.sql", "CREATE TABLE ( THIS IS NOT SQL;")

    with pytest.raises(MigrationError):
        migrate(single(fresh(backend, request)), files.path)

    assert _errors(caplog) == [
        ("Migrating default/member-0 (0:0) failed", "0:0", "default/member-0"),
        ("Stopped at default/member-0 (0:0); already migrated: []", None, None),
    ]


def test_a_failing_shard_is_logged_with_the_shards_migrated_before_it(
    backend, request, files, caplog
):
    caplog.set_level(logging.INFO, logger="io.ekbatan.flyway.FlywayMigrator")
    config = two_shards(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    query(
        config.member(MEXICO).primary_config().sqlalchemy_url(), "CREATE TABLE widgets (id INTEGER)"
    )

    with pytest.raises(MigrationError):
        migrate(config, files.path)

    assert _errors(caplog) == [
        ("Migrating mexico/member-0 (1:0) failed", "1:0", "mexico/member-0"),
        ("Stopped at mexico/member-0 (1:0); already migrated: [global/member-0]", None, None),
    ]


def test_one_database_outside_a_layout_logs_no_stop_line(backend, request, files, caplog):
    """Java's single-database `migrate(DataSourceConfig, ...)` runs one target, not a run of
    them, so it writes only the first line."""
    caplog.set_level(logging.INFO, logger="io.ekbatan.flyway.FlywayMigrator")
    files.add("V1__broken.sql", "CREATE TABLE ( THIS IS NOT SQL;")

    with pytest.raises(MigrationError):
        YoyoMigrator.migrate_data_source(data_source(fresh(backend, request)), files.path)

    assert [message for message, _, _ in _errors(caplog)] == ["Migrating default (0:0) failed"]


# -- Java's other entry points ---------------------------------------------------------------


def test_one_database_outside_a_layout(backend, request, files):
    """Java's `FlywayMigrator.migrate(DataSourceConfig, locations...)`."""
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")

    result = YoyoMigrator.migrate_data_source(data_source(fresh(backend, request)), files.path)

    assert result.migrations == ("V1__widgets.sql",)


def test_the_builder_migrates_its_targets_under_their_own_names(backend, request, files):
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    orders = target(data_source(fresh(backend, request, "_o")), name="orders")
    ledger = target(data_source(fresh(backend, request, "_l")), shard=ShardIdentifier(3, 1))

    results = (
        YoyoMigrator.builder()
        .with_target(orders)
        .with_targets([ledger])
        .locations(files.path)
        .output_query_results(False)
        .migrate()
    )

    assert [r.target.name for r in results] == ["orders", "shard-3-1"]
    assert [r.migrate_result.migrations_executed for r in results] == [1, 1]


# -- repeatable migrations (ledger section 3) ------------------------------------------------


def test_repeatables_run_after_every_versioned_migration_in_description_order(
    backend, request, files
):
    """Parity R01, on every dialect: capitals first, `a` before `a b` before `a-b` before `ab`."""
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect])
    for name in ["ab", "a-b", "a b", "a", "Zeta", "C", "B"]:
        files.add(f"R__{name.replace(' ', '_')}.sql", ran(name))
    files.add("V2__second.sql", ran("V2"))

    (result,) = migrate(single(url), files.path)

    assert runs(url) == ["V2", "B", "C", "Zeta", "a", "a b", "a-b", "ab"]
    assert result.migrate_result.migrations[:2] == ("V1__runs.sql", "V2__second.sql")
    assert result.migrate_result.target_schema_version == "2", "a repeatable is not a version"
    assert [version for version, *_ in rows(url)] == ["1", "2"] + [None] * 7


def test_a_repeatable_runs_again_only_when_its_file_changes(backend, request, files):
    """Parity R03, R04, R14: a new row each time it runs; the old rows stay."""
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect])
    files.add("R__a.sql", ran("a1")).add("R__b.sql", ran("b1"))
    migrate(single(url), files.path)

    (unchanged,) = migrate(single(url), files.path)
    files.add("R__b.sql", ran("b2"))
    (changed,) = migrate(single(url), files.path)

    assert unchanged.migrate_result.migrations == ()
    assert changed.migrate_result.migrations == ("R__b.sql",)
    assert runs(url) == ["a1", "b1", "b2"]
    assert history(url) == [
        ("1", "V1__runs.sql"),
        (None, "R__a.sql"),
        (None, "R__b.sql"),
        (None, "R__b.sql"),
    ]


def test_a_repeatable_changed_back_runs_again(backend, request, files):
    """Parity R06: only the newest row counts."""
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect])
    for content in ("A", "B", "A"):
        files.add("R__x.sql", ran(content))
        migrate(single(url), files.path)

    assert runs(url) == ["A", "B", "A"]


def test_a_line_ending_change_does_not_run_a_repeatable_again(backend, request, files):
    """Parity R05."""
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect]).add("R__x.sql", ran("x"))
    migrate(single(url), files.path)

    (files.path / "R__x.sql").write_bytes(ran("x").replace("\n", "\r\n").encode())
    (result,) = migrate(single(url), files.path)

    assert result.migrate_result.migrations == () and runs(url) == ["x"]


def test_a_view_is_replaced_whole_each_time_its_file_changes(backend, request, files):
    """What repeatable migrations are for."""
    url = fresh(backend, request)
    files.add("V1__wallets.sql", "CREATE TABLE wallets (id INTEGER PRIMARY KEY, balance INTEGER);")
    files.add(
        "V2__rows.sql", "INSERT INTO wallets VALUES (1, 10);\nINSERT INTO wallets VALUES (2, 30);"
    )
    drop = "DROP VIEW IF EXISTS totals;\n" if backend.dialect == "sqlite" else ""
    create = "CREATE VIEW" if backend.dialect == "sqlite" else "CREATE OR REPLACE VIEW"
    view = f"{drop}{create} totals AS SELECT SUM(balance) AS total FROM wallets"

    files.add("R__totals.sql", f"{view};")
    migrate(single(url), files.path)
    assert query(url, "SELECT total FROM totals")[0][0] == 40

    files.add("R__totals.sql", f"{view} WHERE id = 2;")
    migrate(single(url), files.path)
    assert query(url, "SELECT total FROM totals")[0][0] == 30


def test_a_repeatable_whose_file_is_gone_is_refused_until_repair_marks_it_deleted(
    backend, request, files
):
    """Parity R07 and R24: refused as Flyway refuses it; repair records the deletion; a file of
    that description coming back later runs again."""
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect]).add("R__x.sql", ran("x"))
    migrate(single(url), files.path)
    files.drop("R__x.sql")

    with pytest.raises(MigrationError, match=r"repeatable migration 'x' \(R__x\.sql\).*is gone"):
        migrate(single(url), files.path)

    (repaired,) = repair(single(url), files.path)
    assert repaired.deleted == ("repeatable migration 'x' (R__x.sql)",)
    assert migrate(single(url), files.path)[0].migrate_result.migrations == ()

    files.add("R__x.sql", ran("x"))
    (back,) = migrate(single(url), files.path)
    assert back.migrate_result.migrations == ("R__x.sql",)
    assert runs(url) == ["x", "x"]


def test_a_failing_repeatable(backend, request, files):
    """Parity R09 and R10: rolled back and simply tried again where DDL is transactional;
    recorded as failed on MySQL and MariaDB, and refused - even fixed - until repaired."""
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect]).add("R__x.sql", ran("x") + HALF)

    with pytest.raises(MigrationError, match=r"failed in R__x\.sql"):
        migrate(single(url), files.path)
    files.add("R__x.sql", ran("x-fixed"))

    if transactional_ddl(backend):
        assert failed(url) == [] and runs(url) == []
        (result,) = migrate(single(url), files.path)
        assert result.migrate_result.migrations == ("R__x.sql",)
        assert runs(url) == ["x-fixed"]
        return

    assert failed(url) == ["R__x.sql"]
    assert runs(url) == ["x"], "the insert before the schema change was committed with it"
    with pytest.raises(MigrationError, match=r"'x' \(R__x\.sql\) failed on an earlier run"):
        migrate(single(url), files.path)
    (repaired,) = repair(single(url), files.path)
    assert repaired.removed == ("repeatable migration 'x' (R__x.sql)",)
    query(url, "DROP TABLE half")  # the person putting the database right
    (result,) = migrate(single(url), files.path)
    assert result.migrate_result.migrations == ("R__x.sql",)
    assert runs(url) == ["x", "x-fixed"] and failed(url) == []


def test_a_repeatable_can_run_outside_a_transaction(backend, request, files):
    """Parity R15."""
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect])
    files.add("R__x.sql", "-- transactional: false\n" + ran("x"))

    migrate(single(url), files.path)

    assert runs(url) == ["x"] and history(url)[-1] == (None, "R__x.sql")


def test_repeatables_do_not_run_when_a_versioned_migration_fails(backend, request, files):
    """Parity R13."""
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect]).add("R__x.sql", ran("x"))
    files.add("V2__bad.sql", "INSERT INTO nowhere VALUES (1);")

    with pytest.raises(MigrationError, match=r"failed in V2__bad\.sql"):
        migrate(single(url), files.path)

    assert runs(url) == []


def test_an_older_release_applies_its_own_repeatable_again(backend, request, files, caplog):
    """Parity R16 - Flyway's behaviour, and the docs say so: whichever release migrates last
    decides what a repeatable migration's object looks like."""
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect]).add("V2__two.sql", ran("V2"))
    files.add("R__x.sql", ran("new"))
    migrate(single(url), files.path)

    files.drop("V2__two.sql")
    files.add("R__x.sql", ran("old"))
    caplog.set_level(logging.WARNING, logger="io.ekbatan.flyway.FlywayMigrator")
    (result,) = migrate(single(url), files.path)

    assert result.migrate_result.migrations == ("R__x.sql",)
    assert runs(url) == ["V2", "new", "old"]
    assert "newer than every migration here" in caplog.text


def test_bodies_with_semicolons_inside_run_whole(backend, request, files):
    """Parity F17: a function, a procedure, a trigger - what repeatable migrations hold - each
    without `DELIMITER`."""
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect])
    both = "  INSERT INTO runs (name) VALUES ('one');\n  INSERT INTO runs (name) VALUES ('two');\n"
    if backend.dialect == "postgres":
        body = (
            f"CREATE OR REPLACE FUNCTION two() RETURNS void AS $$\nBEGIN\n{both}END;\n"
            "$$ LANGUAGE plpgsql;\nSELECT two();\n"
        )
    elif backend.dialect == "sqlite":
        files.add("V2__log.sql", "CREATE TABLE log (n TEXT);")
        body = (
            f"DROP TRIGGER IF EXISTS two;\nCREATE TRIGGER two AFTER INSERT ON log BEGIN\n{both}"
            "END;\nINSERT INTO log VALUES ('x');\n"
        )
    else:
        body = (
            f"DROP PROCEDURE IF EXISTS two;\nCREATE PROCEDURE two() BEGIN\n{both}END;\n"
            "CALL two();\n"
        )
    files.add("R__body.sql", body)

    migrate(single(url), files.path, output_query_results=False)

    assert runs(url) == ["one", "two"]


# -- a failure that cannot be rolled back (ledger section 4) ---------------------------------


def test_a_half_applied_migration_is_recorded_and_blocks_until_repaired(backend, request, files):
    """Parity X02, X03: on MySQL and MariaDB a failure after a schema change is recorded as
    failed, and refused - even once the file is fixed - until repaired. Running it again over what
    it half did is what the failed row prevents. Where DDL is transactional it rolls back and is
    simply tried again."""
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect]).add("V2__half.sql", ran("V2") + HALF)

    with pytest.raises(MigrationError) as first:
        migrate(single(url), files.path)
    files.add("V2__half.sql", "CREATE TABLE IF NOT EXISTS half (id INTEGER);\n")

    if transactional_ddl(backend):
        assert "rolled back as a whole" in str(first.value)
        assert failed(url) == [] and "half" not in tables(url)
        (result,) = migrate(single(url), files.path)
        assert result.migrate_result.migrations == ("V2__half.sql",)
        return

    assert "It is recorded as failed" in str(first.value)
    assert failed(url) == ["V2__half.sql"] and "half" in tables(url) and runs(url) == ["V2"]
    with pytest.raises(MigrationError, match=r"version 2 \(V2__half\.sql\) failed on an ea") as no:
        migrate(single(url), files.path)
    assert "repair()" in str(no.value)
    (status,) = info(single(url), files.path)
    assert status.migrations[-1].state is MigrationState.FAILED

    (repaired,) = repair(single(url), files.path)
    assert repaired.removed == ("version 2 (V2__half.sql)",)
    (result,) = migrate(single(url), files.path)
    assert result.migrate_result.migrations == ("V2__half.sql",)
    assert history(url)[-1] == ("2", "V2__half.sql") and failed(url) == []


def test_a_failure_outside_a_transaction_is_recorded_on_every_dialect(backend, request, files):
    """Parity X02b: `-- transactional: false` cannot be rolled back anywhere, so its failure is
    recorded on PostgreSQL and SQLite too."""
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect])
    files.add(
        "V2__loose.sql", "-- transactional: false\n" + ran("V2") + "INSERT INTO nowhere VALUES (1);"
    )

    with pytest.raises(MigrationError, match="It is recorded as failed"):
        migrate(single(url), files.path)

    assert runs(url) == ["V2"], "the statement before the failure stays"
    assert failed(url) == ["V2__loose.sql"]
    with pytest.raises(MigrationError, match="failed on an earlier run"):
        migrate(single(url), files.path)


def test_a_failure_a_newer_release_recorded_does_not_stop_an_older_one(
    backend, request, files, caplog
):
    """Parity X02c: Flyway warns "contains a failed future migration" and carries on - the
    rollback of an application whose new migration failed has to be able to start."""
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect])
    files.add("V2__loose.sql", "-- transactional: false\nINSERT INTO nowhere VALUES (1);")
    with pytest.raises(MigrationError):
        migrate(single(url), files.path)

    files.drop("V2__loose.sql")
    caplog.set_level(logging.WARNING, logger="io.ekbatan.flyway.FlywayMigrator")
    (result,) = migrate(single(url), files.path)

    assert result.migrate_result.migrations == ()
    assert "failed when a newer release applied it" in caplog.text


# -- repair (ledger X05) ---------------------------------------------------------------------


def test_repair_records_what_an_edited_or_renamed_file_now_says(backend, request, files):
    """Parity X04a, X04c: for a change a person has decided is not a change."""
    url = fresh(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    files.add("V2__two.sql", "SELECT 2;")
    migrate(single(url), files.path)
    files.add("V1__widgets.sql", "-- a comment\nCREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    files.rename("V2__two.sql", "V2__deux.sql")

    with pytest.raises(MigrationError):
        migrate(single(url), files.path)
    (repaired,) = repair(single(url), files.path)

    assert repaired.aligned == ("version 1 (V1__widgets.sql)", "version 2 (V2__deux.sql)")
    assert migrate(single(url), files.path)[0].migrate_result.migrations == ()
    assert [description for _, _, description, _, _ in rows(url)] == ["widgets", "deux"]
    assert history(url)[1] == ("2", "V2__two.sql"), (
        "the path is left as it was, as Flyway leaves it"
    )


def test_repair_marks_a_gone_migration_deleted_and_it_cannot_come_back(backend, request, files):
    """Parity X04b."""
    url = fresh(backend, request)
    files.add("V1__a.sql", "SELECT 1;").add("V2__b.sql", "SELECT 2;").add("V3__c.sql", "SELECT 3;")
    migrate(single(url), files.path)
    files.drop("V2__b.sql")

    (repaired,) = repair(single(url), files.path)

    assert repaired.deleted == ("version 2 (V2__b.sql)",)
    assert rows(url)[-1][:4] == ("2", "V2__b.sql", "b", "DELETE")
    (status,) = info(single(url), files.path)
    assert ("2", MigrationState.DELETED) in [(m.version, m.state) for m in status.migrations]
    assert migrate(single(url), files.path)[0].migrate_result.migrations == ()

    files.add("V2__b.sql", "SELECT 2;")
    with pytest.raises(MigrationError, match=r"V2__b\.sql has not been applied, but version 3"):
        migrate(single(url), files.path)


def test_repair_with_nothing_to_repair_changes_nothing(backend, request, files):
    """Parity X04d."""
    url = fresh(backend, request)
    files.add("V1__a.sql", "SELECT 1;")
    migrate(single(url), files.path)
    before = rows(url)

    (repaired,) = repair(single(url), files.path)

    assert (repaired.removed, repaired.deleted, repaired.aligned) == ((), (), ())
    assert rows(url) == before


def test_repair_leaves_what_a_newer_release_applied_alone(backend, request, files):
    """Flyway's repair marks deleted what is missing, not what is newer than every file."""
    url = fresh(backend, request)
    files.add("V1__a.sql", "SELECT 1;").add("V2__b.sql", "SELECT 2;")
    migrate(single(url), files.path)
    files.drop("V2__b.sql")

    (repaired,) = repair(single(url), files.path)

    assert repaired.deleted == ()


def test_repair_on_a_database_with_no_history_does_nothing(backend, request, files):
    url = fresh(backend, request)
    files.add("V1__a.sql", "SELECT 1;")

    (repaired,) = repair(single(url), files.path)

    assert (repaired.removed, repaired.deleted, repaired.aligned) == ((), (), ())
    assert HISTORY_TABLE not in tables(url)


# -- a database these migrations did not build (ledger M03, X06, X07) ------------------------


def test_a_database_with_tables_but_no_history_is_refused(backend, request, files):
    """Parity M03, on every dialect: Flyway refuses "non-empty schema(s) ... but no schema
    history table" and points at baseline."""
    url = fresh(backend, request)
    query(url, "CREATE TABLE legacy (id INTEGER)")
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")

    with pytest.raises(MigrationError, match=f"already has legacy but no {HISTORY_TABLE}") as no:
        migrate(single(url), files.path)

    assert "baseline(version=...)" in str(no.value)
    assert tables(url) == {"legacy"}, "nothing created, not even the history table"


def test_a_database_with_only_a_view_or_a_sequence_is_not_empty_either(backend, request, files):
    """Parity M03b, M03c."""
    url = fresh(backend, request)
    if backend.dialect == "postgres":
        query(url, "CREATE SEQUENCE numbers")
    else:
        query(url, "CREATE VIEW answer AS SELECT 42 AS n")
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")

    with pytest.raises(MigrationError, match=f"but no {HISTORY_TABLE}"):
        migrate(single(url), files.path)


def test_baseline_records_what_is_there_and_migrate_carries_on_from_it(backend, request, files):
    """Parity X05 and X05d: files at or below the baseline never run; the ones after do."""
    url = fresh(backend, request)
    query(url, RUNS[backend.dialect])
    files.add("V1__runs.sql", RUNS[backend.dialect]).add("V2__two.sql", ran("V2"))
    files.add("V3__three.sql", ran("V3"))

    (done,) = baseline(single(url), version="2")
    (result,) = migrate(single(url), files.path)

    assert (done.version, done.created) == ("2", True)
    assert result.migrate_result.migrations == ("V3__three.sql",)
    assert result.migrate_result.initial_schema_version == "2"
    assert runs(url) == ["V3"]
    (status,) = info(single(url), files.path)
    assert [(m.version, m.state) for m in status.migrations] == [
        ("1", MigrationState.BELOW_BASELINE),
        ("2", MigrationState.BASELINE_IGNORED),
        ("2", MigrationState.BASELINE),
        ("3", MigrationState.SUCCESS),
    ]


def test_baseline_is_refused_where_it_would_skip_or_rewrite_history(backend, request, files):
    """Flyway refuses a baseline once anything is recorded (parity X05b), and a different one;
    the same one again does nothing. An empty database is refused too, where Flyway would
    baseline it and then never run its first migration (parity X05c)."""
    empty, legacy = fresh(backend, request, "_e"), fresh(backend, request, "_l")
    with pytest.raises(MigrationError, match="is empty, so there is nothing to baseline"):
        baseline(single(empty))
    assert HISTORY_TABLE not in tables(empty)

    query(legacy, "CREATE TABLE legacy (id INTEGER)")
    baseline(single(legacy), version="3")
    (again,) = baseline(single(legacy), version="3")
    assert again.created is False
    with pytest.raises(MigrationError, match="already been baselined at version 3"):
        baseline(single(legacy), version="4")

    files.add("V1__a.sql", "SELECT 1;")
    migrated = fresh(backend, request, "_m")
    migrate(single(migrated), files.path)
    with pytest.raises(MigrationError, match="already has migrations in its history"):
        baseline(single(migrated))


# -- info (ledger X08) -----------------------------------------------------------------------


def test_info_says_where_each_migration_stands_and_changes_nothing(backend, request, files):
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect]).add("R__x.sql", ran("x1"))

    (before,) = info(single(url), files.path)
    assert HISTORY_TABLE not in tables(url), "info creates nothing"
    assert [m.script for m in before.pending] == ["V1__runs.sql", "R__x.sql"]

    migrate(single(url), files.path)
    files.add("R__x.sql", ran("x2")).add("V2__two.sql", ran("V2"))
    (now,) = info(single(url), files.path)

    assert now.schema_version == "1"
    assert [(m.category, m.version, m.description, m.state) for m in now.migrations] == [
        ("Versioned", "1", "runs", MigrationState.SUCCESS),
        ("Repeatable", None, "x", MigrationState.OUTDATED),
        ("Versioned", "2", "two", MigrationState.PENDING),
        ("Repeatable", None, "x", MigrationState.PENDING),
    ]
    assert now.problems == ()


# -- post-apply hooks (ledger F18) -----------------------------------------------------------


def test_a_post_apply_hook_runs_at_the_end_of_every_migrate(backend, request, files):
    """Parity F18: Flyway's afterMigrate runs at the end of every migrate, even with nothing
    pending, after the repeatables. yoyo's own hook runs only when something was applied."""
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect]).add("R__x.sql", ran("x"))
    files.add("post-apply.sql", ran("after"))

    migrate(single(url), files.path)
    migrate(single(url), files.path)

    assert runs(url) == ["x", "after", "after"]
    assert history(url) == [("1", "V1__runs.sql"), (None, "R__x.sql")], "a hook is not recorded"


def test_a_failing_hook_fails_the_migrate_and_runs_again_next_time(backend, request, files):
    """Parity F18b: what was applied stays recorded, and the fixed hook runs on the next start -
    the retry yoyo's own timing would never give it."""
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect])
    files.add("post-apply.sql", ran("after") + "INSERT INTO nowhere VALUES (1);\n")

    with pytest.raises(MigrationError, match=r"post-apply hook post-apply\.sql failed") as raised:
        migrate(single(url), files.path)
    assert "(V1__runs.sql)" in str(raised.value) and "runs again" in str(raised.value)
    assert history(url) == [("1", "V1__runs.sql")]

    files.add("post-apply.sql", ran("after-fixed"))
    (result,) = migrate(single(url), files.path)

    assert result.migrate_result.migrations == ()
    assert runs(url)[-1] == "after-fixed"


# -- the history table (ledger section 6) ----------------------------------------------------


def test_every_application_is_ranked_in_the_order_it_happened(backend, request, files):
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect]).add("R__x.sql", ran("x1"))
    migrate(single(url), files.path)
    files.add("R__x.sql", ran("x2")).add("V2__two.sql", ran("V2"))
    migrate(single(url), files.path)

    ranked = query(
        url, f"SELECT installed_rank, version, script FROM {HISTORY_TABLE} ORDER BY installed_rank"
    )

    assert [tuple(r) for r in ranked] == [
        (1, "1", "V1__runs.sql"),
        (2, None, "R__x.sql"),
        (3, "2", "V2__two.sql"),
        (4, None, "R__x.sql"),
    ]


def test_a_history_table_from_an_earlier_build_is_refused(backend, request, files):
    url = fresh(backend, request)
    query(url, f"CREATE TABLE {HISTORY_TABLE} (version VARCHAR(50) PRIMARY KEY, script TEXT)")
    files.add("V1__a.sql", "SELECT 1;")

    for command in (migrate, info, repair):
        with pytest.raises(MigrationError, match="made by a development build of this migrator"):
            command(single(url), files.path)
    with pytest.raises(MigrationError, match="made by a development build of this migrator"):
        baseline(single(url))


# -- files as Flyway finds them (ledger F02, F04, M12) ---------------------------------------


def test_migrations_in_subdirectories_and_in_capitals_run(backend, request, files):
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect])
    files.add("2026/V2__two.SQL", ran("V2"))
    files.add("2026/q4/R__view.sql", ran("R"))

    (result,) = migrate(single(url), files.path)

    assert result.migrate_result.migrations == (
        "V1__runs.sql",
        "2026/V2__two.SQL",
        "2026/q4/R__view.sql",
    )
    assert history(url)[1] == ("2", "2026/V2__two.SQL"), "the path inside the location"


def test_moving_or_respelling_an_applied_file_is_not_a_change(backend, request, files):
    """Parity M12b, M12c."""
    url = fresh(backend, request)
    files.add("V1__a.sql", "SELECT 1;").add("V2__b.sql", "SELECT 2;")
    migrate(single(url), files.path)

    files.rename("V1__a.sql", "V0001__a.sql")
    (files.path / "old").mkdir()
    files.rename("V2__b.sql", "old/V2__b.sql")
    (result,) = migrate(single(url), files.path)

    assert result.migrate_result.migrations == ()


def test_the_builder_runs_info_repair_and_baseline_too(backend, request, files):
    files.add("V1__a.sql", "SELECT 1;")
    url = fresh(backend, request)
    builder = YoyoMigrator.builder().with_data_source(data_source(url)).locations(files.path)

    (status,) = builder.info()
    assert [m.state for m in status.migrations] == [MigrationState.PENDING]
    builder.migrate()
    (repaired,) = builder.repair()
    assert repaired.removed == ()

    legacy = fresh(backend, request, "_l")
    query(legacy, "CREATE TABLE legacy (id INTEGER)")
    (done,) = YoyoMigrator.builder().with_data_source(data_source(legacy)).baseline("5")
    assert done.version == "5"


def test_a_directory_of_only_repeatable_migrations(backend, request, files):
    """Parity R11: applied, and there is no version to report."""
    url = fresh(backend, request)
    files.add("R__answer.sql", "CREATE VIEW answer AS SELECT 42 AS n;")

    (result,) = migrate(single(url), files.path)

    assert result.migrate_result.migrations == ("R__answer.sql",)
    assert result.migrate_result.target_schema_version is None
    assert query(url, "SELECT n FROM answer")[0][0] == 42


def test_no_migration_files_at_all_is_a_warning(backend, request, files):
    """Parity M16: Flyway warns "No migrations found" and carries on."""
    url = fresh(backend, request)
    files.add("README.md", "notes")

    (result,) = migrate(single(url), files.path)

    assert result.migrate_result.migrations == ()
    assert result.migrate_result.warnings == ("no migrations found - are the locations right?",)


def test_the_log_says_what_it_did(backend, request, files, caplog):
    """Parity M15: what Flyway logs - the version it starts from, each migration, and a summary
    - in this migrator's words."""
    url = fresh(backend, request)
    files.add("V1__runs.sql", RUNS[backend.dialect]).add("R__x.sql", ran("x"))
    caplog.set_level(logging.INFO, logger="io.ekbatan.flyway.FlywayMigrator")

    migrate(single(url), files.path)
    migrate(single(url), files.path)

    for line in (
        "current version << empty >>",
        "migrating to version 1 - runs",
        "applying repeatable migration 'x'",
        "applied 2 migrations, now at version 1",
        "current version 1",
        "up to date, nothing to apply",
    ):
        assert line in caplog.text, line


def test_the_databases_own_warnings_are_logged(backend, request, files, caplog):
    """Flyway logs what the server says beside a statement (parity F16 showed MySQL's); the
    driver alone would only count it. SQLite says nothing of the kind."""
    if backend.dialect == "sqlite":
        pytest.skip("SQLite has no warnings to report")
    url = fresh(backend, request)
    files.add("V1__tidy.sql", "DROP TABLE IF EXISTS nothing_here;")
    caplog.set_level(logging.WARNING, logger="io.ekbatan.flyway.FlywayMigrator")

    migrate(single(url), files.path)

    assert "the database said:" in caplog.text
    assert "nothing_here" in caplog.text
