"""Where the dialects genuinely differ, and nowhere else.

A port of `JdbcCustomization` and its per-database subclasses. Two things differ:

**Whether a row can be claimed in the same statement that selects it.**

    single-statement   PostgreSQL: UPDATE ... WHERE (task_name, task_instance) IN
                       (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING * - one round trip
    generic            MySQL 8, MSSQL: the same idea across two statements in one transaction
    neither            MariaDB, Oracle, SQLite, anything unrecognised - fetch-then-pick, which
                       needs no locking primitives at all

db-scheduler refuses lock-and-fetch on a dialect that cannot do it rather than emulating it, and
so does this: an emulation would be a claim that does not hold, which is the one failure this
library exists to prevent. MariaDB and Oracle *do* have `SKIP LOCKED` in recent versions;
db-scheduler leaves both off pending the right syntax, and this follows rather than guessing.

**How timestamps go in and come out.** Which is what `persist_timestamp_in_utc` is for, and why
it lives here rather than on the repository: it is a per-database decision, and MySQL and
MariaDB have no zoned type at all, so they warn when it is off.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

__all__ = [
    "AutodetectJdbcCustomization",
    "DefaultJdbcCustomization",
    "JdbcCustomization",
    "MariaDBJdbcCustomization",
    "MssqlJdbcCustomization",
    "MySQL8JdbcCustomization",
    "MySQLBaseJdbcCustomization",
    "MySQLJdbcCustomization",
    "OracleJdbcCustomization",
    "PostgreSqlJdbcCustomization",
    "SqliteJdbcCustomization",
    "autodetect",
]

LOG = logging.getLogger("db_scheduler.jdbc")

#: db-scheduler puts the UTC warning on a logger of its own so it can be silenced separately -
#: it fires once per scheduler and is advice, not a fault.
UTC_WARNING_LOG = logging.getLogger("db_scheduler.jdbc.utc_warning")


class JdbcCustomization(ABC):
    """What one database can do that another cannot."""

    __slots__ = ()

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def persist_timestamp_in_utc(self) -> bool:
        """Zone-less columns holding UTC, rather than zoned columns.

        db-scheduler's default is False - zoned where the dialect has them - because that is
        the shape of a table an older Java instance created. It recommends True everywhere, and
        warns on MySQL and MariaDB, which have no zoned type to fall back on.
        """
        return True

    @property
    def supports_single_statement_lock_and_fetch(self) -> bool:
        """Claim and return the rows in one statement."""
        return False

    @property
    def supports_generic_lock_and_fetch(self) -> bool:
        """Claim with `SELECT ... FOR UPDATE SKIP LOCKED` inside a transaction, then update."""
        return False

    @property
    def supports_lock_and_fetch(self) -> bool:
        return self.supports_single_statement_lock_and_fetch or self.supports_generic_lock_and_fetch

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r})"


def warn_if_not_persisting_in_utc(
    persist_timestamp_in_utc: bool, customization: type[JdbcCustomization]
) -> None:
    if not persist_timestamp_in_utc:
        LOG.warning(
            "Always use persist_timestamp_in_utc=True with %s to ensure correct "
            "serialization/deserialization of timestamps.",
            customization.__name__,
        )


class DefaultJdbcCustomization(JdbcCustomization):
    """Fetch-then-pick only. What an unrecognised database gets.

    Not a degraded mode: fetch-then-pick is correct everywhere, because the claim is the version
    guard on the UPDATE rather than a row lock. Lock-and-fetch saves a round trip; it does not
    add a guarantee.
    """

    __slots__ = ("_persist_timestamp_in_utc",)

    def __init__(self, persist_timestamp_in_utc: bool = True) -> None:
        self._persist_timestamp_in_utc = persist_timestamp_in_utc

    @property
    def name(self) -> str:
        return "Default"

    @property
    def persist_timestamp_in_utc(self) -> bool:
        return self._persist_timestamp_in_utc


class PostgreSqlJdbcCustomization(DefaultJdbcCustomization):
    """The one dialect that claims and returns in a single statement."""

    __slots__ = ("_use_generic_lock_and_fetch",)

    def __init__(
        self,
        use_generic_lock_and_fetch: bool = False,
        persist_timestamp_in_utc: bool = True,
    ) -> None:
        super().__init__(persist_timestamp_in_utc)
        self._use_generic_lock_and_fetch = use_generic_lock_and_fetch

    @property
    def name(self) -> str:
        return "PostgreSQL"

    @property
    def supports_single_statement_lock_and_fetch(self) -> bool:
        return not self._use_generic_lock_and_fetch

    @property
    def supports_generic_lock_and_fetch(self) -> bool:
        return self._use_generic_lock_and_fetch


class MySQLBaseJdbcCustomization(DefaultJdbcCustomization):
    """What MySQL and MariaDB share: a zone-less TIMESTAMP, and the warning that goes with it."""

    __slots__ = ()

    def __init__(self, persist_timestamp_in_utc: bool = True) -> None:
        super().__init__(persist_timestamp_in_utc)
        warn_if_not_persisting_in_utc(persist_timestamp_in_utc, type(self))

    @property
    def name(self) -> str:
        return "MySQL"


class MySQLJdbcCustomization(MySQLBaseJdbcCustomization):
    """MySQL before 8. No `SKIP LOCKED` - it arrived in 8.0.1 - so no lock-and-fetch."""

    __slots__ = ()

    @property
    def name(self) -> str:
        return "MySQL < v8"


class MySQL8JdbcCustomization(MySQLBaseJdbcCustomization):
    """MySQL 8 and later. `SELECT ... FOR UPDATE SKIP LOCKED` inside a transaction."""

    __slots__ = ()

    @property
    def name(self) -> str:
        return "MySQL => v8"

    @property
    def supports_generic_lock_and_fetch(self) -> bool:
        return True


class MariaDBJdbcCustomization(MySQLBaseJdbcCustomization):
    """MariaDB has `SKIP LOCKED` from 10.6, and db-scheduler still leaves it off pending the
    right syntax. Followed rather than second-guessed: fetch-then-pick is correct here."""

    __slots__ = ()

    @property
    def name(self) -> str:
        return "MariaDB"

    @property
    def supports_generic_lock_and_fetch(self) -> bool:
        return False


class MssqlJdbcCustomization(DefaultJdbcCustomization):
    """SQL Server. Generic lock-and-fetch works, but db-scheduler does not recommend it - it
    has seen deadlock warnings - and says so rather than turning it off."""

    __slots__ = ()

    def __init__(self, persist_timestamp_in_utc: bool = True) -> None:
        super().__init__(persist_timestamp_in_utc)
        warn_if_not_persisting_in_utc(persist_timestamp_in_utc, type(self))

    @property
    def name(self) -> str:
        return "MSSQL"

    @property
    def supports_generic_lock_and_fetch(self) -> bool:
        return True


class OracleJdbcCustomization(DefaultJdbcCustomization):
    """Oracle. Lock-and-fetch left off, as db-scheduler leaves it."""

    __slots__ = ()

    @property
    def name(self) -> str:
        return "Oracle"

    @property
    def supports_generic_lock_and_fetch(self) -> bool:
        return False


class SqliteJdbcCustomization(DefaultJdbcCustomization):
    """One writer at a time, so there is nothing for `SKIP LOCKED` to do."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(persist_timestamp_in_utc=True)

    @property
    def name(self) -> str:
        return "SQLite"


class AutodetectJdbcCustomization(JdbcCustomization):
    """Picks the right customization from the dialect, and forwards everything to it.

    Java reads `DatabaseMetaData.getDatabaseProductName()` and the major version. SQLAlchemy has
    already done that work by the time an `Engine` exists, so the dialect name and
    `server_version_info` are what this reads - which is also why it needs no connection and
    cannot fail the way Java's can.
    """

    __slots__ = ("_delegate",)

    #: The names SQLAlchemy uses. Java matches on the JDBC product name; the mapping is the same
    #: set of databases either way.
    MSSQL = "mssql"
    POSTGRESQL = "postgresql"
    ORACLE = "oracle"
    MYSQL = "mysql"
    MARIADB = "mariadb"
    SQLITE = "sqlite"

    def __init__(
        self,
        dialect_name: str,
        server_version_info: tuple[int, ...] | None = None,
        persist_timestamp_in_utc: bool = True,
    ) -> None:
        self._delegate = self._detect(
            dialect_name, server_version_info, persist_timestamp_in_utc
        )

    @staticmethod
    def _detect(
        dialect_name: str,
        server_version_info: tuple[int, ...] | None,
        persist_timestamp_in_utc: bool,
    ) -> JdbcCustomization:
        LOG.debug("Detecting database...")
        LOG.info("Detected database %s.", dialect_name)
        match dialect_name:
            case AutodetectJdbcCustomization.MSSQL:
                LOG.info("Using MSSQL jdbc-overrides.")
                return MssqlJdbcCustomization(True)
            case AutodetectJdbcCustomization.POSTGRESQL:
                LOG.info("Using PostgreSQL jdbc-overrides.")
                return PostgreSqlJdbcCustomization(False, persist_timestamp_in_utc)
            case AutodetectJdbcCustomization.ORACLE:
                LOG.info("Using Oracle jdbc-overrides.")
                return OracleJdbcCustomization(persist_timestamp_in_utc)
            case AutodetectJdbcCustomization.MARIADB:
                LOG.info("Using MariaDB jdbc-overrides.")
                _warn_zone_less("MariaDB", persist_timestamp_in_utc)
                return MariaDBJdbcCustomization(persist_timestamp_in_utc)
            case AutodetectJdbcCustomization.MYSQL:
                _warn_zone_less("MySQL", persist_timestamp_in_utc)
                major = server_version_info[0] if server_version_info else 8
                if major >= 8:
                    LOG.info("Using MySQL jdbc-overrides version 8 and later. (v %s)", major)
                    return MySQL8JdbcCustomization(persist_timestamp_in_utc)
                LOG.info("Using MySQL jdbc-overrides for version older than 8. (v %s)", major)
                return MySQLJdbcCustomization(persist_timestamp_in_utc)
            case AutodetectJdbcCustomization.SQLITE:
                LOG.info("Using SQLite jdbc-overrides.")
                return SqliteJdbcCustomization()
            case _:
                UTC_WARNING_LOG.warning(
                    "No database-specific jdbc-overrides applied. See "
                    "'DefaultJdbcCustomization' for default behavior. If using zone-less types "
                    "for time-columns, consider persist_timestamp_in_utc=True."
                )
                return DefaultJdbcCustomization(persist_timestamp_in_utc)

    @property
    def name(self) -> str:
        return self._delegate.name

    @property
    def persist_timestamp_in_utc(self) -> bool:
        return self._delegate.persist_timestamp_in_utc

    @property
    def supports_single_statement_lock_and_fetch(self) -> bool:
        return self._delegate.supports_single_statement_lock_and_fetch

    @property
    def supports_generic_lock_and_fetch(self) -> bool:
        return self._delegate.supports_generic_lock_and_fetch

    def __repr__(self) -> str:
        return f"AutodetectJdbcCustomization({self._delegate!r})"


def _warn_zone_less(database: str, persist_timestamp_in_utc: bool) -> None:
    if not persist_timestamp_in_utc:
        UTC_WARNING_LOG.warning(
            "%s uses a zone-less TIMESTAMP and needs read and write timestamps transferred in "
            "UTC to avoid quirks. Use persist_timestamp_in_utc=True to avoid this.",
            database,
        )


def autodetect(
    dialect_name: str,
    server_version_info: tuple[int, ...] | None = None,
    persist_timestamp_in_utc: bool = True,
) -> JdbcCustomization:
    """The customization for a SQLAlchemy dialect name.

    An unknown database gets the default rather than a failure: fetch-then-pick works
    everywhere, so a database this port has not been tested against still coordinates
    correctly - it just does not get the round-trip saving.
    """
    return AutodetectJdbcCustomization(
        dialect_name, server_version_info, persist_timestamp_in_utc
    )
