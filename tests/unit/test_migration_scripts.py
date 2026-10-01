"""Everything the migrator decides about its files before it touches a database.

The migration files are checked here, the way a CI job would check them. How the files compare
with a database's history is `test_migration_model.py`; what happens on a real database is
`tests/integration/test_migrations.py`. The Flyway behaviour each rule copies was run, not just
read - `docs/MIGRATOR-LEDGER.md` names the scenario.
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
    _failure,
    _scan,
    baseline,
    checksum,
    description_order,
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
    assert "two migrations are one migration" in message
    assert "V1__create_foo.sql" in message and "V1__create_bar.sql" in message, "both named"
    assert "next free version" in message, "and what to do"


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

    with pytest.raises(MigrationError, match="two migrations are one migration"):
        validate_scripts(tmp_path)


def test_a_duplicate_across_two_locations_is_refused(tmp_path):
    write(tmp_path / "a", "V3__one.sql")
    write(tmp_path / "b", "V3__two.sql")

    with pytest.raises(MigrationError, match="two migrations are one migration"):
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
        "Vx__a.sql",
        "r__lower.sql",
        "R_one_underscore.sql",
    ],
)
def test_a_sql_file_with_the_wrong_name_is_refused(tmp_path, name):
    """Flyway skips a misnamed file, with one line in its log, and it then never runs. A typo
    here should fail the build instead."""
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


def test_other_files_are_left_alone(tmp_path):
    """A migrations directory inside a package has an __init__.py, and maybe a README."""
    write(tmp_path, "V1__one.sql")
    write(tmp_path, "__init__.py", "")
    write(tmp_path, "README.md", "notes")

    assert names(validate_scripts(tmp_path)) == ["V1__one.sql"]


def test_subdirectories_are_read_at_any_depth_and_hidden_ones_are_not(tmp_path):
    """As Flyway reads them (parity F04): a file in a subdirectory it would run cannot be one
    this skips. What is recorded is the path inside the location, as Flyway records it."""
    write(tmp_path, "V1__one.sql")
    write(tmp_path / "2026", "V2__two.sql")
    write(tmp_path / "2026" / "q4", "V3__three.sql")
    write(tmp_path / "_under", "V4__four.sql")
    write(tmp_path / ".hidden", "V5__five.sql")
    write(tmp_path, ".V9__hidden.sql")

    assert names(validate_scripts(tmp_path)) == [
        "V1__one.sql",
        "2026/V2__two.sql",
        "2026/q4/V3__three.sql",
        "_under/V4__four.sql",
    ]


def test_sql_in_capitals_is_read(tmp_path):
    """Flyway runs `V2__x.SQL` (parity F02). Ignoring it would be a migration that silently never
    runs - and yoyo, left to decide by the name, would have tried to import it as Python."""
    write(tmp_path, "V2__caps.SQL", "SELECT 2;")
    write(tmp_path, "R__caps.Sql", "SELECT 3;")

    found = validate_scripts(tmp_path)

    assert names(found) == ["V2__caps.SQL", "R__caps.Sql"]
    assert all(len(script.load().steps) == 1 for script in found), "read as SQL"


def test_an_empty_description_is_accepted(tmp_path):
    """Flyway accepts `V2__.sql` and `R__.sql` (parity R20)."""
    write(tmp_path, "V2__.sql")
    write(tmp_path, "R__.sql")

    versioned, repeatable = validate_scripts(tmp_path)

    assert (versioned.version, versioned.description) == ("2", "")
    assert (repeatable.version, repeatable.description) == (None, "")


def test_a_description_longer_than_the_history_keeps_is_refused(tmp_path):
    """Flyway cuts it to 200 characters, and then cannot find a repeatable migration's row by it:
    its next migrate fails (parity H07). Refused here before it can be applied."""
    write(tmp_path, f"R__{'a' * 201}.sql")

    with pytest.raises(MigrationError, match="the description is 201 characters"):
        validate_scripts(tmp_path)


# -- repeatable migrations -------------------------------------------------------------------


def test_a_repeatable_migration_has_a_description_and_no_version(tmp_path):
    write(tmp_path, "R__wallet_totals_view.sql", "CREATE VIEW v AS SELECT 1;")

    (script,) = validate_scripts(tmp_path)

    assert script.version is None and script.repeatable
    assert script.description == "wallet totals view"
    assert script.category == "Repeatable"
    with pytest.raises(ValueError, match="has no version"):
        _ = script.key


def test_repeatables_come_after_versioned_ones_in_javas_order(tmp_path):
    """Flyway applies repeatables after every versioned migration, in the order Java's
    `String.compareTo` gives their descriptions: capitals first, and `a` before `a b` before `a-b`
    before `ab` (parity R01, on all three databases)."""
    for name in [
        "R__ab.sql",
        "R__a-b.sql",
        "R__a_b.sql",
        "R__a.sql",
        "R__Zeta.sql",
        "R__C.sql",
        "R__B.sql",
        "V2__two.sql",
        "V1__one.sql",
    ]:
        write(tmp_path, name)

    assert [s.description for s in validate_scripts(tmp_path)] == [
        "one",
        "two",
        "B",
        "C",
        "Zeta",
        "a",
        "a b",
        "a-b",
        "ab",
    ]


def test_descriptions_compare_by_utf16_unit_as_java_compares_them():
    """Python compares code points; Java compares UTF-16 units. They disagree exactly where a
    character outside the Basic Multilingual Plane meets one near its top."""
    emoji, last_bmp = "\U0001f600", "\uffff"

    assert emoji > last_bmp, "Python's own order"
    assert description_order(emoji) < description_order(last_bmp), "Java's order, and Flyway's"


def test_a_repeatable_with_a_version_is_refused(tmp_path):
    """Flyway skips `R1__x.sql` as misnamed (parity R19)."""
    write(tmp_path, "R1__x.sql")

    with pytest.raises(MigrationError, match="a repeatable migration has no version"):
        validate_scripts(tmp_path)


def test_two_repeatables_with_one_description_are_refused(tmp_path):
    """`R__a_b.sql` and `R__a b.sql` are one description, as Flyway reads them (parity R21)."""
    write(tmp_path, "R__a_b.sql")
    write(tmp_path, "R__a b.sql")

    with pytest.raises(MigrationError) as raised:
        validate_scripts(tmp_path)

    assert "repeatable 'a b'" in str(raised.value)
    assert "R__a_b.sql" in str(raised.value) and "R__a b.sql" in str(raised.value)


def test_one_repeatable_in_two_locations_is_refused(tmp_path):
    """Parity R22."""
    write(tmp_path / "one", "R__x.sql")
    write(tmp_path / "two", "R__x.sql")

    with pytest.raises(MigrationError, match="repeatable 'x'"):
        validate_scripts(tmp_path / "one", tmp_path / "two")


# -- post-apply hooks ------------------------------------------------------------------------


def test_post_apply_files_are_hooks_not_migrations(tmp_path):
    """yoyo's hook, Flyway's afterMigrate: not versioned, not recorded, run every time."""
    write(tmp_path / "a", "V1__one.sql")
    write(tmp_path / "a", "post-apply-grants.sql", "SELECT 'grants';")
    write(tmp_path / "b", "post-apply.sql", "SELECT 'views';")

    scripts = _scan(tmp_path / "a", tmp_path / "b")

    assert names(scripts.migrations) == ["V1__one.sql"]
    assert [h.script for h in scripts.hooks] == ["post-apply-grants.sql", "post-apply.sql"]
    assert names(validate_scripts(tmp_path / "a", tmp_path / "b")) == ["V1__one.sql"]


def test_a_hook_that_is_not_sql_is_refused(tmp_path):
    write(tmp_path, "post-apply.py", "")

    with pytest.raises(MigrationError, match="only SQL migrations"):
        validate_scripts(tmp_path)


def test_a_hook_is_checked_like_a_migration(tmp_path):
    (tmp_path / "post-apply.sql").write_bytes("SELECT 'caf\xe9';".encode("latin-1"))

    with pytest.raises(MigrationError, match="is not UTF-8"):
        validate_scripts(tmp_path)


@pytest.mark.parametrize(
    "name", ["afterMigrate.sql", "beforeMigrate.sql", "afterEachMigrate__log.sql"]
)
def test_a_flyway_callback_is_refused_with_what_to_use_instead(tmp_path, name):
    """It would otherwise be refused as misnamed, which is true but not helpful."""
    write(tmp_path, name)

    with pytest.raises(MigrationError, match="Flyway callbacks are not run here") as raised:
        validate_scripts(tmp_path)

    assert "post-apply.sql" in str(raised.value)


# -- what yoyo would not read the way Flyway does --------------------------------------------


def test_a_flyway_conf_file_is_refused(tmp_path):
    """Flyway reads `V1__x.sql.conf` beside a migration; ignoring it would run the migration
    in a transaction it asked not to be in (parity F10)."""
    write(tmp_path, "V1__index.sql")
    write(tmp_path, "V1__index.sql.conf", "executeInTransaction=false\n")

    with pytest.raises(MigrationError, match="`-- transactional: false`"):
        validate_scripts(tmp_path)


def test_delimiter_is_refused(tmp_path):
    """Flyway understands the mysql client's `DELIMITER`; yoyo glues it onto the next statement,
    which the server then rejects - after the statements before it have run."""
    write(
        tmp_path,
        "R__proc.sql",
        "DROP PROCEDURE IF EXISTS p;\nDELIMITER //\nCREATE PROCEDURE p() BEGIN SELECT 1; END //\n"
        "DELIMITER ;\n",
    )

    with pytest.raises(MigrationError, match="`DELIMITER` is a command of the mysql client"):
        validate_scripts(tmp_path)


@pytest.mark.parametrize(
    ("body", "statements"),
    [
        (
            "DROP PROCEDURE IF EXISTS p;\nCREATE PROCEDURE p() BEGIN\n  INSERT INTO t VALUES (1);"
            "\n  INSERT INTO t VALUES (2);\nEND;\nCALL p();\n",
            3,
        ),
        (
            "CREATE TRIGGER t BEFORE INSERT ON w FOR EACH ROW\nBEGIN\n  IF NEW.n = 'x' THEN\n"
            "    SET NEW.n = 'y';\n  END IF;\nEND;\n",
            1,
        ),
        (
            "CREATE OR REPLACE FUNCTION f() RETURNS void AS $$\nBEGIN\n  INSERT INTO t VALUES (1);"
            "\n  INSERT INTO t VALUES (2);\nEND;\n$$ LANGUAGE plpgsql;\nSELECT f();\n"
            "DO $body$ BEGIN INSERT INTO t VALUES (3); END $body$;\n",
            3,
        ),
        (
            "CREATE TRIGGER t AFTER INSERT ON log BEGIN\n  INSERT INTO t VALUES (1);\n"
            "  INSERT INTO t VALUES (2);\nEND;\nINSERT INTO log VALUES ('x');\n",
            2,
        ),
    ],
    ids=["mysql procedure", "mysql trigger", "postgres functions", "sqlite trigger"],
)
def test_a_body_with_semicolons_inside_stays_one_statement(tmp_path, body, statements):
    """What a repeatable migration usually holds. yoyo splits each of these as Flyway does
    (parity F17) - without `DELIMITER`."""
    write(tmp_path, "R__body.sql", body)

    (script,) = validate_scripts(tmp_path)

    assert len(script.load().steps) == statements


def test_an_editors_backup_of_a_migration_is_refused(tmp_path):
    write(tmp_path, "V1__one.sql")
    write(tmp_path, "V1__one.sql.bak")

    with pytest.raises(MigrationError, match="an editor's backup"):
        validate_scripts(tmp_path)


@pytest.mark.parametrize("version", ["", "v1", "1.x", "1..2", "-1"])
def test_a_baseline_version_is_digits(version):
    """Checked before any database is reached."""
    with pytest.raises(ValidationError, match="a baseline version is digits"):
        baseline(ShardingConfig.single(NOWHERE), version=version)


def test_a_baseline_needs_a_description_that_fits():
    with pytest.raises(ValidationError, match="a baseline needs a description"):
        baseline(ShardingConfig.single(NOWHERE), description="x" * 201)


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


def scripts_in(tmp_path, *names_and_sql):
    for name in names_and_sql:
        write(tmp_path, name, f"-- {name}\nSELECT 1;")
    return validate_scripts(tmp_path)


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

    with pytest.raises(MigrationError, match="two migrations are one migration"):
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
        False,
    )

    assert (
        "migrating global/eu (0:0) failed in V2__b.sql: RuntimeError: relation missing" in message
    )
    assert "Applied by this run before that: V1__a.sql." in message
    assert "Nothing of V2__b.sql remains" in message


def test_on_mysql_the_failure_says_the_schema_change_stays_and_is_recorded(tmp_path):
    """No tool can undo a committed schema change on MySQL; the message says so, and says the
    failure is recorded, so the next run will not apply it again over what it half did."""
    (one,) = scripts_in(tmp_path, "V1__a.sql")

    message = _failure(
        "s", failing_backend(transactional_ddl=False), one, [], RuntimeError("x"), True
    )

    assert "MySQL and MariaDB commit each one on its own" in message
    assert "recorded as failed, so the next run stops here" in message
    assert "repair()" in message


def test_a_failure_that_could_not_be_recorded_says_the_next_run_would_repeat_it(tmp_path):
    (one,) = scripts_in(tmp_path, "V1__a.sql")

    message = _failure(
        "s",
        failing_backend(transactional_ddl=False),
        one,
        [],
        RuntimeError("x"),
        RuntimeError("gone"),
    )

    assert "Recording it as failed did not work either (gone)" in message
    assert "the next run would apply it again" in message


def test_a_lost_connection_is_said_out_loud(tmp_path):
    (one,) = scripts_in(tmp_path, "V1__a.sql")

    message = _failure(
        "s",
        failing_backend(transactional_ddl=True, lost=True),
        one,
        [],
        RuntimeError("closed"),
        False,
    )

    assert "The connection to the database was lost." in message
