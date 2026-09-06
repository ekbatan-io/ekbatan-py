"""`JdbcCustomization` and the per-database subclasses.

Two things differ between dialects, and only two: whether a row can be claimed in the same
statement that selects it, and how timestamps are stored. Everything else in this library is the
same SQL everywhere.
"""

from __future__ import annotations

import logging

import pytest

from db_scheduler.jdbc.customization import (
    AutodetectJdbcCustomization,
    DefaultJdbcCustomization,
    JdbcCustomization,
    MariaDBJdbcCustomization,
    MssqlJdbcCustomization,
    MySQL8JdbcCustomization,
    MySQLJdbcCustomization,
    OracleJdbcCustomization,
    PostgreSqlJdbcCustomization,
    SqliteJdbcCustomization,
    autodetect,
)


class TestLockAndFetchSupport:
    def test_postgres_claims_in_one_statement(self) -> None:
        customization = PostgreSqlJdbcCustomization()

        assert customization.supports_single_statement_lock_and_fetch is True
        assert customization.supports_generic_lock_and_fetch is False
        assert customization.supports_lock_and_fetch is True

    def test_postgres_can_be_told_to_use_the_generic_form(self) -> None:
        customization = PostgreSqlJdbcCustomization(use_generic_lock_and_fetch=True)

        assert customization.supports_single_statement_lock_and_fetch is False
        assert customization.supports_generic_lock_and_fetch is True

    def test_mysql_8_uses_the_generic_form(self) -> None:
        assert MySQL8JdbcCustomization().supports_generic_lock_and_fetch is True

    def test_mysql_before_8_cannot_lock_and_fetch_at_all(self) -> None:
        """`SKIP LOCKED` arrived in 8.0.1. Before that there is nothing to build it on."""
        assert MySQLJdbcCustomization().supports_lock_and_fetch is False

    def test_mssql_can_but_is_not_recommended_to(self) -> None:
        assert MssqlJdbcCustomization().supports_generic_lock_and_fetch is True

    @pytest.mark.parametrize(
        "customization",
        [
            MariaDBJdbcCustomization(),
            OracleJdbcCustomization(),
            SqliteJdbcCustomization(),
            DefaultJdbcCustomization(),
        ],
    )
    def test_the_rest_fall_back_to_fetch_then_pick(
        self, customization: JdbcCustomization
    ) -> None:
        """Not a degraded mode: the claim is the version guard on the UPDATE, not the row lock.
        Lock-and-fetch saves a round trip; it does not add a guarantee."""
        assert customization.supports_lock_and_fetch is False


class TestNames:
    @pytest.mark.parametrize(
        ("customization", "expected"),
        [
            (DefaultJdbcCustomization(), "Default"),
            (PostgreSqlJdbcCustomization(), "PostgreSQL"),
            (MySQLJdbcCustomization(), "MySQL < v8"),
            (MySQL8JdbcCustomization(), "MySQL => v8"),
            (MariaDBJdbcCustomization(), "MariaDB"),
            (MssqlJdbcCustomization(), "MSSQL"),
            (OracleJdbcCustomization(), "Oracle"),
            (SqliteJdbcCustomization(), "SQLite"),
        ],
    )
    def test_the_names_are_the_java_ones(
        self, customization: JdbcCustomization, expected: str
    ) -> None:
        """They go in log lines and error messages that an operator may search for in either
        project's issues."""
        assert customization.name == expected
        assert expected in repr(customization)


class TestTimestampShape:
    def test_utc_is_the_default_here(self) -> None:
        assert DefaultJdbcCustomization().persist_timestamp_in_utc is True

    def test_it_can_be_turned_off(self) -> None:
        assert DefaultJdbcCustomization(False).persist_timestamp_in_utc is False

    def test_sqlite_is_always_utc(self) -> None:
        """It has no zoned type and no session zone to interpret one with."""
        assert SqliteJdbcCustomization().persist_timestamp_in_utc is True

    def test_mysql_warns_when_it_is_off(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """MySQL's TIMESTAMP is zone-less, so a value not transferred as UTC is interpreted in
        whatever zone the session happens to have - an hour's drift in one region."""
        with caplog.at_level(logging.WARNING, logger="db_scheduler.jdbc"):
            MySQL8JdbcCustomization(persist_timestamp_in_utc=False)

        assert "persist_timestamp_in_utc" in caplog.text

    def test_and_does_not_when_it_is_on(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="db_scheduler.jdbc"):
            MySQL8JdbcCustomization(persist_timestamp_in_utc=True)

        assert caplog.text == ""


class TestAutodetect:
    @pytest.mark.parametrize(
        ("dialect_name", "expected_name"),
        [
            ("postgresql", "PostgreSQL"),
            ("mariadb", "MariaDB"),
            ("sqlite", "SQLite"),
            ("mssql", "MSSQL"),
            ("oracle", "Oracle"),
        ],
    )
    def test_it_recognises_the_dialects(self, dialect_name: str, expected_name: str) -> None:
        assert autodetect(dialect_name).name == expected_name

    def test_mysql_is_split_on_the_major_version(self) -> None:
        assert autodetect("mysql", (5, 7, 40)).name == "MySQL < v8"
        assert autodetect("mysql", (8, 4, 0)).name == "MySQL => v8"

    def test_mysqls_calendar_versions_are_on_the_modern_side(self) -> None:
        """MySQL left semantic versioning after 9: the release after 9.4 is 26.7. A check
        written as `major == 8` rather than `major >= 8` would have silently dropped
        lock-and-fetch on every current release."""
        assert autodetect("mysql", (9, 4, 0)).name == "MySQL => v8"
        assert autodetect("mysql", (26, 7, 0)).name == "MySQL => v8"
        assert autodetect("mysql", (26, 7, 0)).supports_generic_lock_and_fetch is True

    def test_an_unknown_mysql_version_is_assumed_to_be_modern(self) -> None:
        """8 has been current since 2018; assuming 5 would silently disable lock-and-fetch."""
        assert autodetect("mysql", None).name == "MySQL => v8"

    def test_an_unknown_database_gets_the_default_rather_than_a_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="db_scheduler.jdbc.utc_warning"):
            detected = autodetect("some-new-database")

        assert detected.name == "Default"
        assert detected.supports_lock_and_fetch is False
        assert "No database-specific jdbc-overrides applied" in caplog.text

    def test_it_forwards_everything_to_what_it_detected(self) -> None:
        detected = AutodetectJdbcCustomization("postgresql")

        assert detected.supports_single_statement_lock_and_fetch is True
        assert detected.persist_timestamp_in_utc is True
        assert "PostgreSqlJdbcCustomization" in repr(detected)

    def test_the_utc_setting_reaches_the_detected_one(self) -> None:
        assert autodetect("postgresql", None, False).persist_timestamp_in_utc is False

    def test_mssql_is_always_utc_whatever_it_was_asked_for(self) -> None:
        """db-scheduler hard-codes it there, and this follows."""
        assert autodetect("mssql", None, False).persist_timestamp_in_utc is True
