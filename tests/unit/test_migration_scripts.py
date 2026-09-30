"""Everything the migrator decides before it touches a database.

The migration files are checked here, the way a CI job would check them, and the comparison
between the files and a database's history is checked as the pure function it is. What happens
on a real database is `tests/integration/test_migrations.py`.

Each group names the row of the migrator's shortcomings table it covers.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ekbatan._yoyo import (
    MYSQL_LOCK_NAME_LIMIT,
    MigrationBackendError,
    MySQLMigrationBackend,
    PostgresMigrationBackend,
    connection_arguments,
    mysql_lock_name,
)
from ekbatan.config import DataSourceConfig, ShardingConfig
from ekbatan.errors import ValidationError
from ekbatan.migrations import (
    MigrationError,
    Target,
    YoyoMigrator,
    _Applied,
    _failure,
    _plan,
    checksum,
    migrate,
    migrate_targets,
    target,
    targets,
    validate_scripts,
    version_key,
)
from ekbatan.sharding import ShardIdentifier
from fixtures.config import group, member

#: Nothing listens here: a test that reaches a database by mistake fails to connect, loudly.
NOWHERE = "postgresql+psycopg://u:p@nowhere.invalid:1/db"
REPLICA = "postgresql+psycopg://u:p@replica.invalid:1/db"
MEXICO = "postgresql+psycopg://u:p@mexico.invalid:1/db"


def write(directory: Path, name: str, sql: str = "SELECT 1;") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(sql)
    return path


def names(scripts) -> list[str]:
    return [script.script for script in scripts]


# -- #1: two files with the same version -----------------------------------------------------


def test_two_files_with_the_same_version_are_refused(tmp_path):
    """The case yoyo lets through: `0001.create_foo` and `0001.create_bar` both run there,
    because its name for a migration is the whole file name. Refused here, with no database."""
    write(tmp_path, "V1__create_foo.sql")
    write(tmp_path, "V1__create_bar.sql")

    with pytest.raises(MigrationError) as raised:
        validate_scripts(tmp_path)

    message = str(raised.value)
    assert "same version" in message
    assert "V1__create_foo.sql" in message and "V1__create_bar.sql" in message, "both named"
    assert "next free number" in message, "and what to do"


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("V1__a.sql", "V01__b.sql"),
        ("V1__a.sql", "V1.0__b.sql"),
        ("V1.1__a.sql", "V1_1__b.sql"),
        ("V0001__a.sql", "V1__b.sql"),
    ],
)
def test_one_version_spelled_two_ways_is_still_a_duplicate(tmp_path, first, second):
    """Flyway compares versions as numbers, trailing zeros dropped - so does this."""
    write(tmp_path, first)
    write(tmp_path, second)

    with pytest.raises(MigrationError, match="same version"):
        validate_scripts(tmp_path)


def test_a_duplicate_across_two_locations_is_refused(tmp_path):
    write(tmp_path / "a", "V3__one.sql")
    write(tmp_path / "b", "V3__two.sql")

    with pytest.raises(MigrationError, match="same version"):
        validate_scripts(tmp_path / "a", tmp_path / "b")


def test_distinct_versions_across_locations_merge_in_order(tmp_path):
    write(tmp_path / "a", "V1__one.sql")
    write(tmp_path / "b", "V2__two.sql")
    write(tmp_path / "a", "V3__three.sql")

    assert names(validate_scripts(tmp_path / "a", tmp_path / "b")) == [
        "V1__one.sql",
        "V2__two.sql",
        "V3__three.sql",
    ]


# -- #5: the order is the version number, not the file name ----------------------------------


def test_versions_are_ordered_as_numbers(tmp_path):
    """yoyo sorts file names as text, so `10` ran before `2` - and failed, because it altered a
    table `2` had not created yet."""
    for name in ["V10__ten.sql", "V2__two.sql", "V1.10__one_ten.sql", "V1.9__one_nine.sql"]:
        write(tmp_path, name)

    assert names(validate_scripts(tmp_path)) == [
        "V1.9__one_nine.sql",
        "V1.10__one_ten.sql",
        "V2__two.sql",
        "V10__ten.sql",
    ]


@pytest.mark.parametrize(
    ("smaller", "larger"),
    [("2", "10"), ("1.9", "1.10"), ("1", "1.1"), ("0001", "0002"), ("1_9", "1.10")],
)
def test_version_keys_compare_as_numbers(smaller, larger):
    assert version_key(smaller) < version_key(larger)


def test_the_version_and_description_are_read_as_flyway_reads_them(tmp_path):
    write(tmp_path, "V0003__add_wallet_index.sql")

    (script,) = validate_scripts(tmp_path)

    assert script.version == "0003", "as written"
    assert script.description == "add wallet index", "underscores as spaces"
    assert script.key == (3,)


# -- names that would otherwise be skipped without a word ------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "V1_create.sql",
        "v1__create.sql",
        "1__create.sql",
        "create_wallets.sql",
        "V1__.sql",
        "Vx__a.sql",
    ],
)
def test_a_sql_file_with_the_wrong_name_is_refused(tmp_path, name):
    """Flyway ignores a misnamed file by default, and it then never runs. A typo here should
    fail the build instead."""
    write(tmp_path, name)

    with pytest.raises(MigrationError, match=r"must be V<version>__<description>\.sql"):
        validate_scripts(tmp_path)


def test_a_rollback_file_is_refused(tmp_path):
    """yoyo would quietly use it as the undo steps of the migration beside it."""
    write(tmp_path, "V1__create.sql")
    write(tmp_path, "V1__create.rollback.sql")

    with pytest.raises(MigrationError, match="rollback files are not supported"):
        validate_scripts(tmp_path)


def test_a_migration_that_is_not_sql_is_refused(tmp_path):
    write(tmp_path, "V2__backfill.py", "steps = []")

    with pytest.raises(MigrationError, match="only SQL migrations"):
        validate_scripts(tmp_path)


def test_other_files_and_subdirectories_are_left_alone(tmp_path):
    """A migrations directory inside a package has an __init__.py; per-dialect directories sit
    side by side. Neither is a migration."""
    write(tmp_path, "V1__one.sql")
    write(tmp_path, "__init__.py", "")
    write(tmp_path, "README.md", "notes")
    write(tmp_path, ".V9__hidden.sql")
    write(tmp_path / "postgres", "V2__elsewhere.sql")

    assert names(validate_scripts(tmp_path)) == ["V1__one.sql"]


def test_depends_is_refused(tmp_path):
    """yoyo would resolve it against every migration it has ever loaded, from any directory."""
    write(tmp_path, "V2__two.sql", "-- depends: V1__one\nSELECT 1;")

    with pytest.raises(MigrationError, match="`-- depends:` is not supported"):
        validate_scripts(tmp_path)


def test_a_transactional_directive_that_is_not_true_or_false_is_refused(tmp_path):
    write(tmp_path, "V1__one.sql", "-- transactional: no\nSELECT 1;")

    with pytest.raises(MigrationError, match="must be `true` or `false`"):
        validate_scripts(tmp_path)


def test_transactional_false_is_read(tmp_path):
    write(tmp_path, "V1__index.sql", "-- transactional: false\nCREATE INDEX i ON t (c);")

    (script,) = validate_scripts(tmp_path)

    assert script.load().use_transactions is False


def test_a_file_that_is_not_utf8_is_refused(tmp_path):
    (tmp_path / "V1__latin1.sql").write_bytes("SELECT 'caf\xe9';".encode("latin-1"))

    with pytest.raises(MigrationError, match="is not UTF-8"):
        validate_scripts(tmp_path)


def test_locations_are_required_and_must_exist(tmp_path):
    with pytest.raises(ValidationError, match="at least one migrations location is required"):
        validate_scripts()
    with pytest.raises(ValidationError, match="no migrations directory"):
        validate_scripts(tmp_path / "nowhere")


def test_an_empty_directory_has_nothing_to_run(tmp_path):
    assert validate_scripts(tmp_path) == ()


# -- #2: what a checksum does and does not notice --------------------------------------------


def test_a_different_line_ending_is_not_an_edit(tmp_path):
    """A checkout that turns \\n into \\r\\n has not changed the migration."""
    unix = write(tmp_path, "a.sql", "CREATE TABLE t (id INT);\nSELECT 1;\n")
    windows = tmp_path / "b.sql"
    windows.write_bytes(b"CREATE TABLE t (id INT);\r\nSELECT 1;\r\n")
    with_bom = tmp_path / "c.sql"
    with_bom.write_bytes(b"\xef\xbb\xbfCREATE TABLE t (id INT);\nSELECT 1;\n")

    assert checksum(unix) == checksum(windows) == checksum(with_bom)


def test_a_different_statement_is_an_edit(tmp_path):
    before = write(tmp_path, "a.sql", "CREATE TABLE t (id INT);")
    after = write(tmp_path, "b.sql", "CREATE TABLE t (id INT, name TEXT);")

    assert checksum(before) != checksum(after)
    assert len(checksum(before)) == 64, "sha256, hex"


# -- #2 #3 #4: the files against a database's history ----------------------------------------


def scripts_in(tmp_path, *names_and_sql):
    for name in names_and_sql:
        write(tmp_path, name, f"-- {name}\nSELECT 1;")
    return validate_scripts(tmp_path)


def applied(script) -> _Applied:
    return _Applied(script.version, script.script, script.checksum)


def test_nothing_applied_means_everything_is_pending_in_order(tmp_path):
    found = scripts_in(tmp_path, "V2__b.sql", "V1__a.sql")

    pending, warnings = _plan("shard", found, [])

    assert names(pending) == ["V1__a.sql", "V2__b.sql"]
    assert warnings == []


def test_only_what_has_not_run_is_pending(tmp_path):
    one, two, three = scripts_in(tmp_path, "V1__a.sql", "V2__b.sql", "V3__c.sql")

    pending, _ = _plan("shard", (one, two, three), [applied(one), applied(two)])

    assert names(pending) == ["V3__c.sql"]


def test_an_edited_migration_is_refused(tmp_path):
    """#2 - yoyo stores a hash of the file *name*, so an edit is invisible to it."""
    (one,) = scripts_in(tmp_path, "V1__a.sql")
    history = [_Applied(one.version, one.script, "0" * 64)]

    with pytest.raises(MigrationError, match="has changed since it was applied"):
        _plan("shard", (one,), history)


def test_a_renamed_migration_is_refused(tmp_path):
    (one,) = scripts_in(tmp_path, "V1__renamed.sql")
    history = [_Applied("1", "V1__original.sql", one.checksum)]

    with pytest.raises(MigrationError, match=r"is now named V1__renamed\.sql"):
        _plan("shard", (one,), history)


def test_an_older_applied_migration_whose_file_is_gone_is_refused(tmp_path):
    """#4 - yoyo does not notice at all."""
    one, two = scripts_in(tmp_path, "V1__a.sql", "V2__b.sql")
    (tmp_path / "V1__a.sql").unlink()
    remaining = validate_scripts(tmp_path)

    with pytest.raises(MigrationError, match="its file is gone"):
        _plan("shard", remaining, [applied(one), applied(two)])


def test_an_applied_migration_newer_than_every_file_is_left_alone_with_a_warning(tmp_path):
    """Flyway's default: an older release starting against a database a newer one migrated.
    Refusing would stop a rollback of the application from starting at all."""
    one, two = scripts_in(tmp_path, "V1__a.sql", "V2__b.sql")
    (tmp_path / "V2__b.sql").unlink()
    remaining = validate_scripts(tmp_path)

    pending, warnings = _plan("shard", remaining, [applied(one), applied(two)])

    assert pending == []
    assert len(warnings) == 1 and "newer than every migration here" in warnings[0]


def test_a_new_migration_older_than_one_already_applied_is_refused(tmp_path):
    """#3 - yoyo runs it out of order without a word."""
    one, three = scripts_in(tmp_path, "V1__a.sql", "V3__c.sql")
    write(tmp_path, "V2__b.sql", "SELECT 2;")
    now = validate_scripts(tmp_path)

    with pytest.raises(MigrationError) as raised:
        _plan("shard", now, [applied(one), applied(three)])

    assert "V2__b.sql has not been applied, but version 3 already has" in str(raised.value)
    assert "nothing was run" in str(raised.value)


def test_every_problem_is_reported_at_once(tmp_path):
    one, two, three = scripts_in(tmp_path, "V1__a.sql", "V2__b.sql", "V3__c.sql")
    history = [_Applied("1", "V1__a.sql", "0" * 64), applied(three)]

    with pytest.raises(MigrationError) as raised:
        _plan("wallet (0:0)", (one, two, three), history)

    message = str(raised.value)
    assert message.startswith("wallet (0:0):"), "the database it is about"
    assert "V1__a.sql has changed" in message
    assert "V2__b.sql has not been applied" in message


# -- targets, named as Java names them -------------------------------------------------------


def a_source(url: str = NOWHERE) -> DataSourceConfig:
    return DataSourceConfig(url=url)


def test_target_without_a_shard_is_javas_default():
    assert target(a_source()) == Target(ShardIdentifier(0, 0), "default", a_source())


def test_target_with_a_shard_is_named_after_it():
    assert target(a_source(), shard=ShardIdentifier(2, 5)).name == "shard-2-5"


def test_target_with_a_name_keeps_it():
    assert target(a_source(), shard=ShardIdentifier(2, 5), name="mexico").name == "mexico"


def test_targets_are_one_per_member_on_its_primary():
    config = ShardingConfig(
        default_shard=ShardIdentifier(0, 0),
        groups=(
            group(0, "global", member(0, a_source(), a_source(REPLICA), name="eu")),
            group(1, "mexico", member(0, a_source(MEXICO))),
        ),
    )

    found = targets(config)

    assert [(t.shard, t.name, t.data_source.url) for t in found] == [
        (ShardIdentifier(0, 0), "global/eu", NOWHERE),
        (ShardIdentifier(1, 0), "mexico/member-0", MEXICO),
    ], "the primary, never the replica"
    assert YoyoMigrator.targets(config) == found


def test_a_target_needs_a_name():
    with pytest.raises(ValidationError, match="name is required"):
        Target(ShardIdentifier(0, 0), "  ", a_source())


def test_an_unknown_shard_is_refused_before_any_database_is_reached(tmp_path):
    write(tmp_path, "V1__a.sql")

    with pytest.raises(ValidationError, match="not in this layout"):
        migrate(ShardingConfig.single(NOWHERE), tmp_path, shards=[ShardIdentifier(9, 9)])


def test_an_empty_subset_migrates_nothing(tmp_path):
    """Distinct from `shards=None`, which is every shard: a caller that computed a subset and
    found it empty asked for nothing, and doing the whole layout would be the opposite."""
    write(tmp_path, "V1__a.sql")

    assert migrate(ShardingConfig.single(NOWHERE), tmp_path, shards=[]) == []


def test_bad_files_are_refused_before_any_database_is_reached(tmp_path):
    write(tmp_path, "V1__a.sql")
    write(tmp_path, "V1__b.sql")

    with pytest.raises(MigrationError, match="same version"):
        migrate(ShardingConfig.single(NOWHERE), tmp_path)


def test_migrate_targets_needs_one():
    with pytest.raises(ValidationError, match="at least one migration target is required"):
        migrate_targets([], "anywhere")


# -- #8: the url, as the driver receives it --------------------------------------------------


def test_postgres_arguments_are_what_sqlalchemy_would_hand_psycopg():
    found = connection_arguments(
        "shard",
        "postgresql+psycopg://us%40er:p%3Ass@db.example:6432/wallet?sslmode=require",
        {"connect_timeout": 3},
    )

    assert found.family == "postgresql"
    assert found.kwargs == {
        "host": "db.example",
        "dbname": "wallet",
        "user": "us@er",
        "password": "p:ss",
        "port": 6432,
        "sslmode": "require",
        "connect_timeout": 3,
    }, "decoded, typed, and connect_args laid over the top"
    assert found.database == "wallet"


def test_the_driver_is_always_the_frameworks():
    """yoyo refuses `mysql+pymysql://` and has no `mariadb` at all; the url here is the one the
    application already has, whatever driver it names."""
    assert connection_arguments("s", "postgresql+psycopg2://u:p@h/db", {}).kwargs["dbname"] == "db"
    mysql = connection_arguments("s", "mysql+pymysql://root:pw@h:3307/w?connect_timeout=5", {})
    assert mysql.kwargs["database"] == "w" and mysql.kwargs["connect_timeout"] == 5
    assert connection_arguments("s", "mariadb+pymysql://root:pw@h/w", {}).family == "mariadb"


def test_sqlite_paths_resolve_as_the_engine_resolves_them(tmp_path):
    found = connection_arguments("s", f"sqlite:///{tmp_path}/x.db", {})
    assert found.args == (f"{tmp_path}/x.db",)
    assert connection_arguments("s", "sqlite://", {}).args == (":memory:",)


def test_an_unsupported_database_is_refused():
    with pytest.raises(MigrationBackendError, match="cannot migrate a 'oracle' database"):
        connection_arguments("s", "oracle://u:p@h/db", {})


# -- follow-up b: MySQL and MariaDB lock names are server-wide -------------------------------


def test_the_mysql_lock_name_carries_the_database():
    """Two shards that are two databases on one server must not wait on each other."""
    assert mysql_lock_name("wallet_eu") == "ekbatan-migrate:wallet_eu"
    assert mysql_lock_name("wallet_eu") != mysql_lock_name("wallet_mx")


def test_a_long_database_name_is_hashed_to_fit_the_limit():
    long_a, long_b = "a" * 64, "a" * 63 + "b"

    assert len(mysql_lock_name(long_a)) <= MYSQL_LOCK_NAME_LIMIT
    assert mysql_lock_name(long_a) != mysql_lock_name(long_b), "no collision on the prefix"


# -- follow-up a: a replica, or a server set read-only ---------------------------------------


class Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return self._rows


def stub(rows):
    return SimpleNamespace(label="wallet (0:0)", execute=lambda sql, params=None: Rows(rows))


def test_a_postgres_replica_is_refused():
    """No standby here to point at: the query is the whole of the check, so it is driven with
    the answer a standby gives."""
    with pytest.raises(MigrationBackendError, match="read-only replica"):
        PostgresMigrationBackend.refuse_if_read_only(stub([(True,)]))


def test_a_postgres_primary_is_accepted():
    PostgresMigrationBackend.refuse_if_read_only(stub([(False,)]))


@pytest.mark.parametrize(
    "rows",
    [
        [("read_only", "ON")],
        [("read_only", "OFF"), ("super_read_only", "ON")],
        [("read_only", "NO_LOCK")],  # MariaDB 11's stronger levels
    ],
)
def test_a_read_only_mysql_is_refused(rows):
    with pytest.raises(MigrationBackendError, match="is read-only"):
        MySQLMigrationBackend.refuse_if_read_only(stub(rows))


def test_a_writable_mysql_is_accepted():
    MySQLMigrationBackend.refuse_if_read_only(
        stub([("read_only", "OFF"), ("super_read_only", "0")])
    )


# -- follow-up c: saying what happened -------------------------------------------------------


def failing_backend(*, transactional_ddl: bool, lost: bool = False):
    return SimpleNamespace(has_transactional_ddl=transactional_ddl, connection_lost=lambda: lost)


def test_a_failure_names_the_target_the_migration_and_what_was_applied(tmp_path):
    one, two = scripts_in(tmp_path, "V1__a.sql", "V2__b.sql")

    message = _failure(
        "global/eu (0:0)",
        failing_backend(transactional_ddl=True),
        two,
        [one],
        RuntimeError("relation missing"),
    )

    assert (
        "migrating global/eu (0:0) failed in V2__b.sql: RuntimeError: relation missing" in message
    )
    assert "Applied by this run before that: V1__a.sql." in message
    assert "Nothing of V2__b.sql remains" in message


def test_on_mysql_the_failure_says_the_schema_change_stays(tmp_path):
    """#13 - no tool can undo a committed schema change on MySQL; the message says so."""
    (one,) = scripts_in(tmp_path, "V1__a.sql")

    message = _failure("s", failing_backend(transactional_ddl=False), one, [], RuntimeError("x"))

    assert "MySQL and MariaDB commit each one on its own" in message
    assert "check the database before running it again" in message


def test_a_lost_connection_is_said_out_loud(tmp_path):
    (one,) = scripts_in(tmp_path, "V1__a.sql")

    message = _failure(
        "s", failing_backend(transactional_ddl=True, lost=True), one, [], RuntimeError("closed")
    )

    assert "The connection to the database was lost." in message
