"""The migrator against a real database - every dialect the run asks for.

Java runs Flyway here. The migrator is yoyo underneath, with a lock, checks and a history of its
own on top, because yoyo alone gets each of those wrong. Each group below names the row of the
migrator's shortcomings table it covers; what several processes do at once is
`test_migration_concurrency.py`, and everything decided before a database is reached is
`tests/unit/test_migration_scripts.py`.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from ekbatan._yoyo import POSTGRES_LOCK_KEY, mysql_lock_name, open_backend
from ekbatan.config import DataSourceConfig, ShardingConfig
from ekbatan.migrations import (
    HISTORY_TABLE,
    MigrationError,
    YoyoMigrator,
    migrate,
    target,
)
from ekbatan.sharding import ShardIdentifier
from fixtures.config import group, member
from fixtures.migrating import fresh, query, tables

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)


# -- helpers ---------------------------------------------------------------------------------


def history(url: str) -> list[tuple[str, str]]:
    if HISTORY_TABLE not in tables(url):
        return []
    rows = query(url, f"SELECT version, script FROM {HISTORY_TABLE}")
    return sorted(((r[0], r[1]) for r in rows), key=lambda r: [int(p) for p in r[0].split(".")])


class Files:
    """A migrations directory, written file by file as a project would grow one."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True)

    def add(self, name: str, sql: str) -> Files:
        (self.path / name).write_text(sql)
        return self

    def drop(self, name: str) -> None:
        (self.path / name).unlink()

    def rename(self, old: str, new: str) -> None:
        (self.path / old).rename(self.path / new)


@pytest.fixture
def files(tmp_path) -> Files:
    return Files(tmp_path / "migrations")


def single(url: str) -> ShardingConfig:
    return ShardingConfig.single(url)


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

    ((version, description, script, checksum, installed_on, execution_time),) = query(
        url,
        "SELECT version, description, script, checksum, installed_on, execution_time "
        f"FROM {HISTORY_TABLE}",
    )
    assert (version, description, script) == ("1", "widgets", "V1__widgets.sql")
    assert len(checksum) == 64, "sha256 of what the file contained"
    assert installed_on.endswith("Z") and execution_time >= 0


# -- the files against the history (#1 #2 #3 #4) --------------------------------------------


def test_duplicate_versions_are_refused_and_the_database_is_not_touched(backend, request, files):
    """#1 - checked before any connection is made."""
    url = fresh(backend, request)
    files.add("V1__create_foo.sql", "CREATE TABLE foo (id INTEGER PRIMARY KEY);")
    files.add("V1__create_bar.sql", "CREATE TABLE bar (id INTEGER PRIMARY KEY);")

    with pytest.raises(MigrationError, match="same version"):
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

    with pytest.raises(MigrationError, match=r"is now named V1__things\.sql"):
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
    caplog.set_level(logging.WARNING, logger="ekbatan.migrations")
    (result,) = migrate(single(url), files.path)

    assert result.migrate_result.migrations == ()
    assert result.migrate_result.target_schema_version == "2"
    assert "newer than every migration here" in caplog.text
    assert result.migrate_result.warnings


# -- what a SELECT in a migration returns (#11) ----------------------------------------------


def test_query_output_goes_to_the_log_not_the_screen(backend, request, files, caplog, capsys):
    url = fresh(backend, request)
    files.add("V1__hello.sql", "SELECT 'hello from the migration' AS greeting;")
    caplog.set_level(logging.INFO, logger="ekbatan.migrations")

    migrate(single(url), files.path)

    assert "hello from the migration" in caplog.text, "logged, as Flyway logs it"
    assert "greeting" in caplog.text
    assert "hello from the migration" not in capsys.readouterr().out, "not printed"


def test_query_output_can_be_switched_off(backend, request, files, caplog, capsys):
    """Flyway's `outputQueryResults=false`."""
    url = fresh(backend, request)
    files.add("V1__hello.sql", "SELECT 'hello from the migration' AS greeting;")
    caplog.set_level(logging.INFO, logger="ekbatan.migrations")

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


# -- running outside a transaction (#10) -----------------------------------------------------


def test_a_postgres_index_can_be_built_concurrently(backend, request, files):
    if backend.dialect != "postgres":
        pytest.skip("CREATE INDEX CONCURRENTLY is PostgreSQL's")
    url = fresh(backend, request)
    files.add("V1__table.sql", "CREATE TABLE people (id INTEGER PRIMARY KEY, owner TEXT);")
    files.add(
        "V2__index.sql",
        "-- transactional: false\nCREATE INDEX CONCURRENTLY ix_people_owner ON people (owner);",
    )

    migrate(single(url), files.path)

    indexes = {r[0] for r in query(url, "SELECT indexname FROM pg_indexes")}
    assert "ix_people_owner" in indexes


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
    assert history(url) == []


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
    caplog.set_level(logging.INFO, logger="ekbatan.migrations")

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
            group(0, "global", member(0, DataSourceConfig(url=fresh(backend, request, "_a")))),
            group(1, "mexico", member(0, DataSourceConfig(url=fresh(backend, request, "_b")))),
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
    assert "widgets" not in tables(config.member(GLOBAL).primary_config().url)

    later = migrate(config, files.path)
    assert [r.migrate_result.migrations_executed for r in later] == [1, 0]


def test_shards_go_in_layout_order_not_argument_order(backend, request, files):
    config = two_shards(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")

    results = migrate(config, files.path, shards=[MEXICO, GLOBAL])

    assert [r.target.shard for r in results] == [GLOBAL, MEXICO]


def test_a_failing_shard_stops_the_run_and_names_what_was_already_migrated(backend, request, files):
    config = two_shards(backend, request)
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    query(config.member(MEXICO).primary_config().url, "CREATE TABLE widgets (id INTEGER)")

    with pytest.raises(MigrationError) as raised:
        migrate(config, files.path)

    message = str(raised.value)
    assert "mexico/member-0 (1:0)" in message, "the shard that failed"
    assert "Already migrated: global/member-0" in message
    assert "what was applied is skipped" in message, "and what to do about it"


# -- Java's other entry points ---------------------------------------------------------------


def test_one_database_outside_a_layout(backend, request, files):
    """Java's `FlywayMigrator.migrate(DataSourceConfig, locations...)`."""
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")

    result = YoyoMigrator.migrate_data_source(
        DataSourceConfig(url=fresh(backend, request)), files.path
    )

    assert result.migrations == ("V1__widgets.sql",)


def test_the_builder_migrates_its_targets_under_their_own_names(backend, request, files):
    files.add("V1__widgets.sql", "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    orders = target(DataSourceConfig(url=fresh(backend, request, "_o")), name="orders")
    ledger = target(
        DataSourceConfig(url=fresh(backend, request, "_l")), shard=ShardIdentifier(3, 1)
    )

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
