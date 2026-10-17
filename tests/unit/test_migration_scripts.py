"""Everything the migrator decides about its files before it touches a database.

The files are read by Flyway's rules - the names, the checksum, the statements, the ``.conf``
files - because a Java service running Flyway 12 reads the same files and writes the same
history. Where a number below is Flyway's own (a checksum, a lock key), it was read off a run of
Java's migrator against the same file (`parity-probes/F-migrations/`). How the files compare with
a database's history is `test_migration_model.py`; a real database is
`tests/integration/test_migrations.py`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from ekbatan._flyway_sql import (
    SqlParseError,
    checksum,
    java_lines,
    java_string_hash,
    parser_for,
)
from ekbatan._migration_backend import (
    MigrationBackendError,
    MySQLBackend,
    PostgresBackend,
    connection_arguments,
    mysql_lock_name,
    postgres_lock_key,
)
from ekbatan.config import DataSourceConfig, ShardingConfig
from ekbatan.errors import ValidationError
from ekbatan.migrations import (
    FlywayConfiguration,
    FlywayMigrator,
    MigrationError,
    Target,
    _Failed,
    _failure,
    _Prepared,
    _scan,
    abbreviate_description,
    abbreviate_script,
    baseline,
    description_order,
    migrate,
    migrate_targets,
    parse_name,
    target,
    targets,
    validate_scripts,
    version_key,
)
from ekbatan.sharding import ShardIdentifier
from fixtures.config import group, member

#: Nothing listens here: a test that reaches a database by mistake fails to connect, loudly.
NOWHERE = "postgresql+psycopg://nowhere.invalid:1/db"
REPLICA = "postgresql+psycopg://replica.invalid:1/db"
MEXICO = "postgresql+psycopg://mexico.invalid:1/db"

NAMING_LOG = "org.flywaydb.core.internal.resource.ResourceNameValidator"


def write(directory: Path, name: str, sql: str = "SELECT 1;") -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sql)
    return path


def names(scripts) -> list[str]:
    return [script.script for script in scripts]


# -- names, read as Flyway reads them -----------------------------------------------------------


def test_two_files_with_the_same_version_are_refused(tmp_path):
    write(tmp_path, "V1__create_foo.sql")
    write(tmp_path, "V1__create_bar.sql")

    with pytest.raises(MigrationError) as raised:
        validate_scripts(tmp_path)

    message = str(raised.value)
    assert "two migrations are one migration" in message
    assert "V1__create_foo.sql" in message and "V1__create_bar.sql" in message, "both named"


@pytest.mark.parametrize(
    ("first", "second"),
    [("V1__a.sql", "V1.0__b.sql"), ("V1__a.sql", "V01__b.sql"), ("V1_1__a.sql", "V1.1__b.sql")],
)
def test_one_version_spelled_two_ways_is_still_a_duplicate(tmp_path, first, second):
    write(tmp_path, first)
    write(tmp_path, second)

    with pytest.raises(MigrationError, match="two migrations are one migration"):
        validate_scripts(tmp_path)


def test_a_duplicate_across_two_locations_is_refused(tmp_path):
    write(tmp_path / "a", "V1__one.sql")
    write(tmp_path / "b", "V1__other.sql")

    with pytest.raises(MigrationError, match="two migrations are one migration"):
        validate_scripts(tmp_path / "a", tmp_path / "b")


def test_versions_are_ordered_as_numbers(tmp_path):
    for name in ["V10__ten.sql", "V2__two.sql", "V1_10__x.sql", "V1.9__y.sql", "V1__one.sql"]:
        write(tmp_path, name)

    assert names(validate_scripts(tmp_path)) == [
        "V1__one.sql",
        "V1.9__y.sql",
        "V1_10__x.sql",
        "V2__two.sql",
        "V10__ten.sql",
    ]


@pytest.mark.parametrize(
    ("smaller", "larger"), [("2", "10"), ("1.9", "1.10"), ("1", "1.1"), ("1_5", "1.6")]
)
def test_version_keys_compare_as_numbers(smaller, larger):
    assert version_key(smaller) < version_key(larger)


def test_the_version_is_written_as_flyway_writes_it(tmp_path):
    """``V1_5`` is version ``1.5`` in Flyway's history - the underscore is a dot - and leading
    zeros stay as written. A Java service reads this history, so it must say what Flyway's
    says (row 2 of the scan: Python wrote ``1_5``)."""
    write(tmp_path, "V1_5__underscore_version.sql")
    write(tmp_path, "V0002__padded.sql")
    write(tmp_path, "V5__.sql")

    scripts = validate_scripts(tmp_path)

    assert [(s.version, s.description) for s in scripts] == [
        ("1.5", "underscore version"),
        ("0002", "padded"),
        ("5", ""),
    ]


@pytest.mark.parametrize(
    ("name", "valid"),
    [
        ("V1__x.sql", True),
        ("V1.SQL", True),  # no separator: version 1, no description - Flyway accepts it
        ("R__x.sql", True),
        ("afterMigrate.sql", True),
        ("beforeEachMigrate__grants.sql", True),
        ("v2__lower.sql", False),
        ("create_stuff.sql", False),
        ("R1__x.sql", False),
        ("U1__undo.sql", False),
        ("V__x.sql", False),
        ("Vx__y.sql", False),
        ("post-apply.sql", False),
    ],
)
def test_names_are_taken_apart_as_flyways_resource_name_parser_does(name, valid):
    assert parse_name(name).valid is valid


@pytest.mark.parametrize(
    "name", ["v2__lower.sql", "create_stuff.sql", "R1__x.sql", "U1__undo.sql", "post-apply.sql"]
)
def test_a_sql_file_flyway_cannot_read_is_skipped_with_flyways_log_line(tmp_path, caplog, name):
    """Flyway skips it and runs the rest; a Python service that refused the whole run would not
    start where a Java one on the same files does (row 7 of the scan)."""
    write(tmp_path, "V1__first.sql")
    write(tmp_path, name)

    with caplog.at_level(logging.INFO, logger=NAMING_LOG):
        scripts = validate_scripts(tmp_path)

    assert names(scripts) == ["V1__first.sql"]
    lines = [r.getMessage() for r in caplog.records if r.name == NAMING_LOG]
    assert lines == [
        "1 SQL migrations were detected but not run because they did not follow the filename "
        "convention.",
        "Set 'validateMigrationNaming' to true to fail fast and see a list of the invalid file "
        "names.",
    ]


def test_validate_migration_naming_makes_a_bad_name_fatal(tmp_path):
    write(tmp_path, "V1__first.sql")
    write(tmp_path, "R1__x.sql")

    with pytest.raises(MigrationError, match="Invalid SQL filenames found"):
        _scan([tmp_path], FlywayConfiguration().validate_migration_naming(True))


def test_a_script_migration_is_refused(tmp_path):
    """Flyway runs ``V2__x.py`` with ``python``. This migrator runs no programs, and skipping a
    file a Java service would apply would leave the two databases different, so it refuses."""
    write(tmp_path, "V1__one.sql")
    write(tmp_path, "V2__code.py", "print('x')")

    with pytest.raises(MigrationError, match="only SQL migrations are supported"):
        validate_scripts(tmp_path)


def test_a_baseline_migration_is_refused(tmp_path):
    write(tmp_path, "B5__all.sql")

    with pytest.raises(MigrationError, match="baseline migrations"):
        validate_scripts(tmp_path)


def test_other_files_are_left_alone(tmp_path):
    write(tmp_path, "V1__one.sql")
    write(tmp_path, "__init__.py", "")
    write(tmp_path, "README.md", "# notes")
    write(tmp_path, "V1__one.sql.bak", "old")

    assert names(validate_scripts(tmp_path)) == ["V1__one.sql"]


def test_subdirectories_are_read_at_any_depth_and_hidden_ones_are_not(tmp_path):
    write(tmp_path, "V1__top.sql")
    write(tmp_path / "sub" / "deeper", "V2__deep.sql")
    write(tmp_path / ".hidden", "V3__hidden.sql")

    assert names(validate_scripts(tmp_path)) == ["V1__top.sql", "sub/deeper/V2__deep.sql"]


def test_sql_in_capitals_is_read(tmp_path):
    write(tmp_path, "V1__capital.SQL")

    assert names(validate_scripts(tmp_path)) == ["V1__capital.SQL"]


def test_a_long_description_is_accepted_and_cut_to_fit_as_flyway_cuts_it(tmp_path):
    """Flyway applies it and stores 197 characters and ``...`` (row 13 of the scan)."""
    write(tmp_path, f"V2__{'d' * 220}.sql")

    (script,) = validate_scripts(tmp_path)

    assert script.description == "d" * 220
    assert abbreviate_description(script.description) == "d" * 197 + "..."
    assert abbreviate_script("s" * 1200) == "..." + "s" * 997


# -- repeatable migrations and callbacks --------------------------------------------------------


def test_repeatables_come_after_versioned_ones_in_javas_order(tmp_path):
    for name in ["R__b_view.sql", "R__Z_capital.sql", "R__a_view.sql", "V1__one.sql"]:
        write(tmp_path, name)

    scripts = validate_scripts(tmp_path)

    assert names(scripts) == ["V1__one.sql", "R__Z_capital.sql", "R__a_view.sql", "R__b_view.sql"]
    assert [s.version for s in scripts] == ["1", None, None, None]


def test_descriptions_compare_by_utf16_unit_as_java_compares_them():
    assert sorted(["ab", "a b", "a-b", "B"], key=description_order) == ["B", "a b", "a-b", "ab"]


def test_two_repeatables_with_one_description_are_refused(tmp_path):
    write(tmp_path, "R__a_b.sql")
    write(tmp_path, "R__a b.sql")

    with pytest.raises(MigrationError, match="repeatable 'a b'"):
        validate_scripts(tmp_path)


def test_callbacks_are_read_apart_from_migrations(tmp_path):
    write(tmp_path, "V1__one.sql")
    write(tmp_path, "afterMigrate.sql")
    write(tmp_path, "beforeEachMigrate__grants.sql")

    scripts = _scan([tmp_path], FlywayConfiguration())

    assert names(scripts.migrations) == ["V1__one.sql"]
    assert [(c.event, c.description) for c in scripts.callbacks] == [
        ("afterMigrate", ""),
        ("beforeEachMigrate", "grants"),
    ]


# -- .conf files --------------------------------------------------------------------------------


def test_a_conf_file_beside_a_migration_is_read(tmp_path):
    """Flyway's script configuration (row 8 of the scan): Python refused the whole run."""
    write(tmp_path, "V1__index.sql", "CREATE INDEX i ON t (c);")
    write(tmp_path, "V1__index.sql.conf", "# no transaction\nexecuteInTransaction=false\n")
    write(tmp_path, "V2__plain.sql")

    one, two = validate_scripts(tmp_path)

    assert one.config.execute_in_transaction is False
    assert two.config.execute_in_transaction is None


@pytest.mark.parametrize(
    ("conf", "message"),
    [
        ("executeInTransaction=no", "should be either true or false"),
        ("transactional=false", "Unknown configuration property: transactional"),
        ("shouldExecute=true", "paid editions"),
    ],
)
def test_a_conf_file_flyway_refuses_is_refused(tmp_path, conf, message):
    write(tmp_path, "V1__one.sql")
    write(tmp_path, "V1__one.sql.conf", conf)

    with pytest.raises(MigrationError, match=message):
        validate_scripts(tmp_path)


def test_a_conf_file_can_name_the_encoding(tmp_path):
    (tmp_path / "V1__latin1.sql").write_bytes("SELECT 'caf\xe9';".encode("latin-1"))
    write(tmp_path, "V1__latin1.sql.conf", "encoding=ISO-8859-1")

    (script,) = validate_scripts(tmp_path)

    assert "caf\xe9" in script.text


def test_a_file_that_is_not_utf8_is_refused(tmp_path):
    (tmp_path / "V1__latin1.sql").write_bytes("SELECT 'caf\xe9';".encode("latin-1"))

    with pytest.raises(MigrationError, match=r"Unable to calculate checksum of V1__latin1\.sql"):
        validate_scripts(tmp_path)


def test_locations_are_required_and_must_not_be_blank(tmp_path):
    with pytest.raises(ValidationError, match="at least one migrations location is required"):
        validate_scripts()
    with pytest.raises(ValidationError, match="locations cannot contain blank values"):
        validate_scripts(" ")


def test_an_empty_directory_has_nothing_to_run(tmp_path):
    assert validate_scripts(tmp_path) == ()


# -- the checksum: Flyway's CRC32 ---------------------------------------------------------------


def test_the_checksum_is_flyways_number_for_the_same_file():
    """Read off Java's run against these very bytes (f1 accept): Flyway's CRC32 as a signed int.
    A different number would make every file look edited to the other port (row 27)."""
    assert checksum("CREATE TABLE t_v1 (id INT);\n") == 77939629
    assert checksum("CREATE TABLE t_v2 (id INT);\n") == -1656281684


def test_line_breaks_and_a_bom_are_not_an_edit():
    unix = "CREATE TABLE t (id INT);\nSELECT 1;\n"
    assert checksum(unix) == checksum(unix.replace("\n", "\r\n"))
    assert checksum(unix) == checksum("﻿" + unix)
    assert checksum(unix) == checksum(unix.replace("\n", "\r"))


def test_two_lines_joined_is_not_an_edit_to_flyway_either():
    """The CRC runs over the lines without their breaks, so this is the same file to Flyway
    (row 35). The cost of sharing a history with Java; accepted."""
    assert checksum("SELECT 1;\nSELECT 2;\n") == checksum("SELECT 1;SELECT 2;\n")


def test_a_different_statement_is_an_edit():
    assert checksum("CREATE TABLE t (id INT);") != checksum("CREATE TABLE t (id INT, n TEXT);")


def test_lines_are_split_as_javas_readline_splits_them():
    assert java_lines("a\nb\r\nc\rd\n") == ["a", "b", "c", "d"]
    assert java_lines("a\n\n") == ["a", ""]
    assert java_lines("") == []
    assert java_lines("a\x0bb\x0cc\u2028d") == ["a\x0bb\x0cc\u2028d"], "not str.splitlines"


# -- the lock: Flyway's key and name ------------------------------------------------------------


def test_java_string_hash_is_javas():
    assert java_string_hash("hello") == 99162322
    assert java_string_hash("") == 0
    assert java_string_hash("polygenelubricants") == -2147483648


def test_the_postgres_lock_key_is_flyways():
    """What Java's migrator held, as pg_locks showed it: 18028:4163491309 (f3)."""
    key = postgres_lock_key('"public"."flyway_schema_history"')

    assert (key >> 32, key & 0xFFFFFFFF) == (18028, 4163491309)


def test_the_mysql_lock_name_is_flyways():
    """What Java's migrator held for database f3_lock (f3); MySQL lower-cases lock names when it
    shows them."""
    assert mysql_lock_name("`f3_lock`.`flyway_schema_history`") == "Flyway--196644676"
    assert mysql_lock_name("`wallet_eu`.`flyway_schema_history`") != mysql_lock_name(
        "`wallet_mx`.`flyway_schema_history`"
    ), "two databases on one server do not wait on each other"


# -- the statements: Flyway's parser ------------------------------------------------------------


def sqls(database: str, text: str, version: tuple[int, ...] | None = (17, 0)) -> list[str]:
    return [s.sql for s in parser_for(database, version).parse(text)]


def test_mysql_delimiter_is_read_as_flyway_reads_it():
    """Flyway applies a file written for the mysql client (row 16); Python refused it."""
    text = (
        "DROP PROCEDURE IF EXISTS p;\nDELIMITER //\nCREATE PROCEDURE p() BEGIN SELECT 1; END //\n"
        "DELIMITER ;\nCALL p();\n"
    )

    assert sqls("mysql", text) == [
        "DROP PROCEDURE IF EXISTS p",
        "CREATE PROCEDURE p() BEGIN SELECT 1; END",
        "CALL p()",
    ]


@pytest.mark.parametrize(
    ("database", "body", "statements"),
    [
        (
            "mysql",
            "DROP PROCEDURE IF EXISTS p;\nCREATE PROCEDURE p() BEGIN\n  INSERT INTO t VALUES (1);"
            "\n  INSERT INTO t VALUES (2);\nEND;\nCALL p();\n",
            3,
        ),
        (
            "mysql",
            "CREATE TRIGGER t BEFORE INSERT ON w FOR EACH ROW\nBEGIN\n  IF NEW.n = 'x' THEN\n"
            "    SET NEW.n = 'y';\n  END IF;\nEND;\n",
            1,
        ),
        (
            "postgresql",
            "CREATE OR REPLACE FUNCTION f() RETURNS void AS $$\nBEGIN\n  INSERT INTO t VALUES (1);"
            "\n  INSERT INTO t VALUES (2);\nEND;\n$$ LANGUAGE plpgsql;\nSELECT f();\n"
            "DO $body$ BEGIN INSERT INTO t VALUES (3); END $body$;\n",
            3,
        ),
        (
            "sqlite",
            "CREATE TRIGGER t AFTER INSERT ON log BEGIN\n  INSERT INTO t VALUES (1);\n"
            "  INSERT INTO t VALUES (2);\nEND;\nINSERT INTO log VALUES ('x');\n",
            2,
        ),
        ("mysql", "# a comment; with a semicolon\nSELECT 'a;b', \"c;d\", `e;f`;\n", 1),
        ("mysql", "/*!40101 SET NAMES utf8 */;\nSELECT 1;\n", 2),
        ("postgresql", "-- only a comment;\n/* and; another */\n", 0),
    ],
    ids=[
        "mysql procedure",
        "mysql trigger",
        "postgres functions",
        "sqlite trigger",
        "mysql quotes",
        "mysql directive",
        "comments only",
    ],
)
def test_a_body_with_semicolons_inside_stays_one_statement(database, body, statements):
    assert len(sqls(database, body)) == statements


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE INDEX CONCURRENTLY i ON t (c)",
        "CREATE UNIQUE INDEX CONCURRENTLY i ON t (c)",
        "DROP INDEX CONCURRENTLY i",
        "VACUUM t",
        "CREATE DATABASE other",
        "ALTER SYSTEM SET work_mem = '1MB'",
        "REINDEX SCHEMA public",
    ],
)
def test_postgres_statements_that_cannot_run_in_a_transaction_are_found(statement):
    """Flyway's list (row 23). It runs such a migration outside a transaction; so does this."""
    (found,) = parser_for("postgresql", (17, 0)).parse(statement + ";")

    assert found.can_execute_in_transaction is False


def test_alter_type_add_value_needs_no_transaction_only_before_postgres_12():
    text = "ALTER TYPE mood ADD VALUE 'meh';"
    assert parser_for("postgresql", (11, 0)).parse(text)[0].can_execute_in_transaction is False
    assert parser_for("postgresql", (17, 0)).parse(text)[0].can_execute_in_transaction is True


def test_ordinary_statements_run_in_a_transaction():
    for statement in parser_for("postgresql", (17, 0)).parse(
        "CREATE TABLE t (id INT);\nCREATE INDEX i ON t (id);\nINSERT INTO t VALUES (1);"
    ):
        assert statement.can_execute_in_transaction


def test_postgres_copy_from_stdin_carries_its_rows():
    (copy, after) = parser_for("postgresql", (17, 0)).parse(
        "COPY t (id, name) FROM STDIN;\n1\tone\n2\ttwo\n\\.\nSELECT 1;\n"
    )

    assert copy.sql == "COPY t (id, name) FROM STDIN"
    assert copy.copy_data == "1\tone\n2\ttwo\n"
    assert after.sql == "SELECT 1"


def test_an_unfinished_block_is_refused_as_flyway_refuses_it():
    with pytest.raises(SqlParseError, match="Incomplete statement"):
        parser_for("mysql").parse("CREATE PROCEDURE p() BEGIN SELECT 1;")


def test_a_delimiter_change_inside_a_statement_is_refused():
    with pytest.raises(SqlParseError, match="Delimiter changed inside statement"):
        parser_for("mysql").parse("SELECT 1\nDELIMITER //\n")


# -- baseline arguments, targets, and the url ---------------------------------------------------


@pytest.mark.parametrize("version", ["", "v1", "1.x", "1..2", "-1"])
def test_a_baseline_version_is_digits(version):
    """Checked before any database is reached."""
    with pytest.raises(ValidationError, match="a baseline version is digits"):
        baseline(ShardingConfig.single(NOWHERE), version=version)


def test_the_baseline_description_is_flyways_by_default():
    assert FlywayConfiguration()._baseline_description == "<< Flyway Baseline >>"


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
    assert FlywayMigrator.targets(config) == found


def test_a_target_needs_a_name():
    with pytest.raises(ValidationError, match="name is required"):
        Target(ShardIdentifier(0, 0), "  ", a_source())


def test_an_unknown_shard_is_refused_before_any_database_is_reached(tmp_path):
    write(tmp_path, "V1__a.sql")

    with pytest.raises(ValidationError, match="not in this layout"):
        migrate(ShardingConfig.single(NOWHERE), tmp_path, shards=[ShardIdentifier(9, 9)])


def test_an_empty_subset_migrates_nothing(tmp_path):
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


def test_the_builder_refuses_a_blank_location():
    with pytest.raises(ValidationError, match="locations cannot contain blank values"):
        FlywayMigrator.builder().locations(" ")


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


# -- a replica, or a server set read-only -------------------------------------------------------


class Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return self._rows


def stub(rows):
    return SimpleNamespace(
        label="wallet (0:0)",
        execute=lambda sql, params=(): Rows(rows),
        scalar=lambda sql, params=(): rows[0][0],
    )


def test_a_postgres_replica_is_refused():
    with pytest.raises(MigrationBackendError, match="read-only replica"):
        PostgresBackend.refuse_if_read_only(stub([(True,)]))


def test_a_postgres_primary_is_accepted():
    PostgresBackend.refuse_if_read_only(stub([(False,)]))


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
        MySQLBackend.refuse_if_read_only(stub(rows))


def test_a_writable_mysql_is_accepted():
    MySQLBackend.refuse_if_read_only(stub([("read_only", "OFF"), ("super_read_only", "0")]))


# -- saying what happened -----------------------------------------------------------------------


def session_on(*, transactional_ddl: bool, lost: bool = False):
    backend = SimpleNamespace(transactional_ddl=transactional_ddl, connection_lost=lambda: lost)
    return SimpleNamespace(backend=backend)


def prepared(tmp_path, *files: str):
    for name in files:
        write(tmp_path, name, f"-- {name}\nSELECT 1;")
    return [_Prepared(s, [], True) for s in validate_scripts(tmp_path)]


def test_a_failure_names_the_target_the_migration_and_what_was_applied(tmp_path):
    one, two = prepared(tmp_path, "V1__a.sql", "V2__b.sql")

    message = _failure(
        "global/eu (0:0)",
        session_on(transactional_ddl=True),
        two,
        [one.script],
        _Failed(RuntimeError("relation missing"), False, True),
    )

    assert (
        "migrating global/eu (0:0) failed in V2__b.sql: RuntimeError: relation missing" in message
    )
    assert "Applied by this run before that: V1__a.sql." in message
    assert "Nothing of V2__b.sql remains" in message


def test_on_mysql_the_failure_says_the_schema_change_stays_and_is_recorded(tmp_path):
    (one,) = prepared(tmp_path, "V1__a.sql")

    message = _failure(
        "s", session_on(transactional_ddl=False), one, [], _Failed(RuntimeError("x"), True, False)
    )

    assert "MySQL and MariaDB commit each one on its own" in message
    assert "recorded as failed, so the next run stops here" in message
    assert "repair()" in message


def test_a_failure_that_could_not_be_recorded_says_the_next_run_would_repeat_it(tmp_path):
    (one,) = prepared(tmp_path, "V1__a.sql")

    message = _failure(
        "s",
        session_on(transactional_ddl=False),
        one,
        [],
        _Failed(RuntimeError("x"), RuntimeError("gone"), False),
    )

    assert "Recording it as failed did not work either (gone)" in message
    assert "the next run would apply it again" in message


def test_a_lost_connection_is_said_out_loud(tmp_path):
    (one,) = prepared(tmp_path, "V1__a.sql")

    message = _failure(
        "s",
        session_on(transactional_ddl=True, lost=True),
        one,
        [],
        _Failed(RuntimeError("closed"), False, True),
    )

    assert "The connection to the database was lost." in message
