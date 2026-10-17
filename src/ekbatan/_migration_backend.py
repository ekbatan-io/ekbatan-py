"""The migrator's side of each database: one connection, Flyway's lock, and the statements.

One connection per database, opened the way the application's engine opens its own - the same
url, login and ``data_source_properties``, through the framework's driver for the dialect. The
lock, every migration and the row that records it all go through it, so when the connection dies
all three die together, and a second process can never be running a migration whose lock the
database has already let go of.

**The lock is Flyway's.** A Java service migrating the same database has to wait for a Python
one and the other way round, so the lock is the one Flyway 12 takes, on the same key:

- PostgreSQL: ``pg_try_advisory_lock`` on ``0x466C79776179`` ("Flyway") plus the Java
  ``hashCode`` of the history table's quoted name, ``"public"."flyway_schema_history"``. Tried
  once a second, and given up after ``lock_retry_count`` retries (50, so about 50 seconds) with
  Flyway's own message. Flyway takes the transaction-scoped form of the same key; the two forms
  conflict with each other, so either side waits for the other. This side takes the session form
  and never holds a transaction open while it waits or while a migration runs outside one: that
  open transaction is what makes Flyway wait forever on its own ``CREATE INDEX CONCURRENTLY``.
- MySQL and MariaDB: ``GET_LOCK('Flyway-<hashCode of `db`.`flyway_schema_history`>')``, waited
  on for as long as it takes, as Flyway waits.
- SQLite has no session lock; an operating-system lock on a file beside the database stands in,
  which the system drops when the process dies.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

from ._flyway_sql import java_string_hash
from ._redact import redact

if TYPE_CHECKING:
    from sqlalchemy.engine import URL

LOG = logging.getLogger("io.ekbatan.flyway.FlywayMigrator")
#: Flyway's logger for what the database says beside a statement.
SCRIPT_LOG = logging.getLogger("org.flywaydb.core.internal.sqlscript.DefaultSqlScriptExecutor")

#: Flyway's PostgreSQL lock: "Flyway" in ASCII, plus a number per history table.
POSTGRES_LOCK_MAGIC = 0x466C79776179
#: How long Flyway waits between attempts at the PostgreSQL lock, in seconds.
LOCK_RETRY_INTERVAL = 1.0
#: Flyway's link for the setting, which it names in the error.
LOCK_RETRY_COUNT_LINK = "https://rd.gt/3A57jfk"
#: How long one attempt at the MySQL lock waits inside the server, in seconds.
MYSQL_LOCK_WAIT = 1
#: How often SQLite's file lock is tried.
POLL = 0.5
#: How often a waiting process says it is still waiting.
REPORT_EVERY = 30.0


class MigrationBackendError(Exception):
    """Something about the database itself refuses a migration: a replica, a lock failure."""


def postgres_lock_key(table: str) -> int:
    """Flyway's advisory-lock key for a history table, given its quoted, qualified name."""
    return POSTGRES_LOCK_MAGIC + java_string_hash(table)


def mysql_lock_name(table: str) -> str:
    """Flyway's ``GET_LOCK`` name for a history table, given its quoted, qualified name."""
    return f"Flyway-{java_string_hash(table)}"


@dataclass(frozen=True, slots=True)
class ResultSet:
    """What one statement returned, for Flyway's ``outputQueryResults``."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


class Backend:
    """What every dialect shares: the connection, transactions, the statements, the lock."""

    #: The framework's name for the dialect - also the parser Flyway picks.
    dialect: ClassVar[str]
    #: Whether a schema change can be rolled back: PostgreSQL and SQLite yes, MySQL no.
    transactional_ddl: ClassVar[bool]
    #: How the driver marks a parameter.
    paramstyle: ClassVar[str] = "%s"

    def __init__(
        self, label: str, args: tuple[Any, ...], kwargs: Mapping[str, Any], database: str | None
    ) -> None:
        self.label = label
        self._args = args
        self._kwargs = dict(kwargs)
        self.database = database
        self.in_transaction = False
        self._locked: str | None = None
        self._notices: list[tuple[str, str, int]] | None = None
        self.connection = self.connect()

    # -- the connection --------------------------------------------------------------------

    def connect(self) -> Any:
        raise NotImplementedError

    def close(self) -> None:
        try:
            self.connection.close()
        except Exception:  # pragma: no cover - already gone
            LOG.debug("%s: closing the migration connection failed", self.label, exc_info=True)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        """One of the migrator's own statements, with ``%s`` for each parameter."""
        cursor = self.connection.cursor()
        if params:
            cursor.execute(sql.replace("%s", self.paramstyle), tuple(params))
        else:
            cursor.execute(sql)
        return cursor

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.execute(sql, params).fetchone()
        return None if row is None else row[0]

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """``BEGIN`` ... ``COMMIT``, or ``ROLLBACK`` on the way out of a failure. The
        connection is otherwise in auto-commit, so nothing is ever left open by accident."""
        self.execute("BEGIN")
        self.in_transaction = True
        try:
            yield
        except BaseException:
            self.in_transaction = False
            try:
                self.execute("ROLLBACK")
            except Exception:
                LOG.debug("%s: rolling back failed", self.label, exc_info=True)
            raise
        self.in_transaction = False
        self.execute("COMMIT")

    def run(self, sql: str) -> list[ResultSet]:
        """One statement from a migration or a callback, sent as written - no parameter
        markers are read in it - and everything it returned. What the database said beside
        it goes to the log as Flyway logs it."""
        self._notices = []
        try:
            cursor = self.connection.cursor()
            cursor.execute(sql)
            results = self._results(cursor)
            self._collect_warnings(cursor)
            return results
        finally:
            notices, self._notices = self._notices, None
            for message, state, code in notices or ():
                if state == "00000":
                    SCRIPT_LOG.info("DB: %s", message)
                else:
                    SCRIPT_LOG.warning(
                        "DB: %s (SQL State: %s - Error Code: %s)", message, state, code
                    )

    def _results(self, cursor: Any) -> list[ResultSet]:
        results = []
        while True:
            if cursor.description:
                columns = tuple(str(column[0]) for column in cursor.description)
                results.append(ResultSet(columns, tuple(tuple(r) for r in cursor.fetchall())))
            nextset = getattr(cursor, "nextset", None)
            if nextset is None or not nextset():
                return results

    def _collect_warnings(self, cursor: Any) -> None:
        pass

    def copy(self, sql: str, data: str) -> None:
        raise MigrationBackendError(f"{self.label}: COPY ... FROM STDIN is PostgreSQL's alone")

    # -- what Flyway asks of the database ----------------------------------------------------

    def quote(self, *names: str) -> str:
        return ".".join('"' + name.replace('"', '""') + '"' for name in names)

    def server_version(self) -> tuple[int, ...] | None:
        return None

    def parser_dialect(self) -> str:
        return self.dialect

    def current_schema(self) -> str:
        raise NotImplementedError

    def schema_exists(self, schema: str) -> bool:
        return True

    def create_schema(self, schema: str) -> None:
        raise NotImplementedError

    def table_exists(self, schema: str, table: str) -> bool:
        raise NotImplementedError

    def columns(self, qualified: str) -> set[str]:
        """The table's column names, read from an empty result so every dialect answers alike."""
        cursor = self.execute(f"SELECT * FROM {qualified} WHERE 1 = 0")
        names = {str(column[0]).lower() for column in cursor.description}
        cursor.fetchall()
        return names

    def schema_objects(self, schema: str) -> list[str]:
        """A few of the things already in the schema, as Flyway looks for them before it will
        create its history table. Empty for an empty schema."""
        raise NotImplementedError

    def current_user(self) -> str:
        raise NotImplementedError

    def database_name(self) -> str | None:
        return self.database

    def create_history_script(self, qualified: str, schema: str, table: str) -> list[str]:
        raise NotImplementedError

    def refuse_if_read_only(self) -> None:
        """Refuse a database that cannot take the migration: a replica, or one set read-only."""

    def connection_lost(self) -> bool:
        return False

    # -- the lock ----------------------------------------------------------------------------

    def _try_lock(self, table: str) -> bool:
        raise NotImplementedError

    def _unlock(self, table: str) -> None:
        raise NotImplementedError

    def acquire(self, table: str, retries: int) -> None:
        """Flyway's lock for this history table. ``retries`` is Flyway's ``lockRetryCount``,
        which only PostgreSQL's lock reads: MySQL's waits for as long as it takes."""
        started = time.monotonic()
        reported: float | None = None
        while not self._try_lock(table):
            now = time.monotonic()
            if reported is None:
                LOG.info(
                    "%s: another process is migrating this database; waiting for it to finish",
                    self.label,
                )
                reported = now
            elif now - reported >= REPORT_EVERY:
                LOG.info("%s: still waiting (%.0fs)", self.label, now - started)
                reported = now
            self._wait(retries)
        self._locked = table

    def _wait(self, retries: int) -> None:
        time.sleep(POLL)

    def release(self) -> None:
        """Best effort. Closing the connection lets go of the lock whatever happens here."""
        table = self._locked
        if table is None:
            return
        self._locked = None
        try:
            self._unlock(table)
        except Exception:  # pragma: no cover - the connection is already gone
            LOG.debug("%s: releasing the migration lock failed", self.label, exc_info=True)


class PostgresBackend(Backend):
    dialect = "postgresql"
    transactional_ddl = True

    def connect(self) -> Any:
        import psycopg

        connection = psycopg.connect(*self._args, **self._kwargs, autocommit=True)
        # A session only notices its client has gone when it next talks to it, so a migrator
        # killed half-way through a long statement left it running - and holding the lock -
        # until it finished. With this set the server checks during a statement and cancels it.
        # PostgreSQL 14 and later; on an older server the setting does not exist.
        try:
            connection.execute("SET client_connection_check_interval = '1s'")
        except psycopg.Error:
            LOG.debug("%s: client_connection_check_interval is not available", self.label)
        connection.add_notice_handler(self._notice)
        return connection

    def _notice(self, diagnostic: Any) -> None:
        if self._notices is not None:
            self._notices.append((diagnostic.message_primary, diagnostic.sqlstate or "", 0))

    def copy(self, sql: str, data: str) -> None:
        with self.connection.cursor() as cursor, cursor.copy(sql) as copy:
            copy.write(data)

    def server_version(self) -> tuple[int, ...] | None:
        number = int(self.connection.info.server_version)
        return (number // 10000, number % 10000)

    def current_schema(self) -> str:
        # Flyway's: current_schema, or the search path as written when that names nothing
        # that exists yet - which is how a schema that is not there gets created.
        schema = self.scalar("SELECT current_schema()")
        if schema:
            return str(schema)
        path = str(self.scalar("SHOW search_path") or "").strip()
        if not path:
            raise MigrationBackendError(
                f"{self.label}: unable to determine the current schema, as search_path is empty. "
                f"Set it in the url - options=-csearch_path=<schema>."
            )
        return path

    def schema_exists(self, schema: str) -> bool:
        return (
            self.scalar("SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = %s", (schema,))
            is not None
        )

    def create_schema(self, schema: str) -> None:
        self.execute(f"CREATE SCHEMA {self.quote(schema)}")

    def table_exists(self, schema: str, table: str) -> bool:
        return (
            self.scalar(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = %s "
                "AND table_name = %s",
                (schema, table),
            )
            is not None
        )

    def schema_objects(self, schema: str) -> list[str]:
        # Flyway's query, with materialized views, partitioned and foreign tables added: a schema
        # holding only one of those is not empty either. Objects an extension owns are left out,
        # as Flyway leaves them out - `CREATE EXTENSION` in a fresh database is not a migration.
        owned = (
            "LEFT JOIN pg_catalog.pg_depend d ON d.objid = {oid} AND d.deptype = 'e' "
            "WHERE n.nspname = %s AND d.objid IS NULL"
        )
        cursor = self.execute(
            "SELECT c.relname FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            + owned.format(oid="c.oid")
            + " AND c.relkind IN ('r', 'v', 'S', 'm', 'p', 'f') "
            "UNION ALL SELECT t.typname FROM pg_catalog.pg_type t "
            "JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace "
            + owned.format(oid="t.oid")
            + " AND t.typcategory NOT IN ('A', 'C') "
            "UNION ALL SELECT p.proname FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
            + owned.format(oid="p.oid")
            + " LIMIT 5",
            (schema, schema, schema),
        )
        return [str(row[0]) for row in cursor.fetchall()]

    def current_user(self) -> str:
        return str(self.scalar("SELECT current_user"))

    def database_name(self) -> str | None:
        return str(self.scalar("SELECT current_database()"))

    def create_history_script(self, qualified: str, schema: str, table: str) -> list[str]:
        # Flyway's PostgreSQLDatabase.getRawCreateScript, statement for statement.
        return [
            f"CREATE TABLE {qualified} (\n"
            '    "installed_rank" INT NOT NULL,\n'
            '    "version" VARCHAR(50),\n'
            '    "description" VARCHAR(200) NOT NULL,\n'
            '    "type" VARCHAR(20) NOT NULL,\n'
            '    "script" VARCHAR(1000) NOT NULL,\n'
            '    "checksum" INTEGER,\n'
            '    "installed_by" VARCHAR(100) NOT NULL,\n'
            '    "installed_on" TIMESTAMP NOT NULL DEFAULT now(),\n'
            '    "execution_time" INTEGER NOT NULL,\n'
            '    "success" BOOLEAN NOT NULL\n'
            ")",
            f'ALTER TABLE {qualified} ADD CONSTRAINT "{table}_pk" PRIMARY KEY ("installed_rank")',
            f'CREATE INDEX "{table}_s_idx" ON {qualified} ("success")',
        ]

    def refuse_if_read_only(self) -> None:
        if self.scalar("SELECT pg_is_in_recovery()"):
            raise MigrationBackendError(
                f"{self.label} is a read-only replica (the server is in recovery). Migrations "
                f"must run against the primary: point this shard's primaryConfig at it directly, "
                f"not at a replica or a proxy that might route to one."
            )

    def connection_lost(self) -> bool:
        return bool(getattr(self.connection, "closed", False) or self.connection.broken)

    def _try_lock(self, table: str) -> bool:
        return bool(self.scalar("SELECT pg_try_advisory_lock(%s)", (postgres_lock_key(table),)))

    def _wait(self, retries: int) -> None:
        # Flyway's RetryStrategy: a second between attempts, and a limit on the attempts.
        time.sleep(LOCK_RETRY_INTERVAL)
        remaining = getattr(self, "_retries_left", retries)
        if retries >= 0 and remaining <= 0:
            self._retries_left = None
            raise MigrationBackendError(
                "Number of retries exceeded while attempting to acquire PostgreSQL advisory "
                "lock. Configure the number of retries with the 'lockRetryCount' configuration "
                f"option: {LOCK_RETRY_COUNT_LINK}"
            )
        self._retries_left = remaining - 1

    def acquire(self, table: str, retries: int) -> None:
        self._retries_left = retries
        try:
            super().acquire(table, retries)
        finally:
            self._retries_left = None

    def _unlock(self, table: str) -> None:
        self.execute("SELECT pg_advisory_unlock(%s)", (postgres_lock_key(table),))


class MySQLBackend(Backend):
    dialect = "mysql"
    transactional_ddl = False

    def connect(self) -> Any:
        import pymysql  # type: ignore[import-untyped]

        connection = pymysql.connect(*self._args, **self._kwargs)
        # Auto-commit, as a JDBC connection is: a migration Flyway runs outside a transaction
        # has its statements committed one by one, and so does this one's.
        connection.autocommit(True)
        return connection

    def quote(self, *names: str) -> str:
        return ".".join("`" + name.replace("`", "``") + "`" for name in names)

    def server_version(self) -> tuple[int, ...] | None:
        text = str(self.scalar("SELECT VERSION()"))
        parts = text.split("-", 1)[0].split(".")
        return tuple(int(p) for p in parts if p.isdigit()) or None

    def parser_dialect(self) -> str:
        # By what the server says it is, as Flyway decides: a mysql:// url can reach MariaDB.
        return "mariadb" if "mariadb" in str(self.scalar("SELECT VERSION()")).lower() else "mysql"

    def _collect_warnings(self, cursor: Any) -> None:
        if getattr(cursor, "warning_count", 0) and self._notices is not None:
            # The driver gives no SQL state for a warning; MySQL's general one stands in. Every
            # level is a warning to Flyway here, notes included, as Connector/J reports them.
            for _level, code, message in self.connection.show_warnings() or ():
                self._notices.append((str(message), "HY000", int(code)))

    def current_schema(self) -> str:
        schema = self.scalar("SELECT DATABASE()")
        if not schema:
            raise MigrationBackendError(
                f"{self.label}: unable to determine the schema for the schema history table - "
                f"the url names no database."
            )
        return str(schema)

    def table_exists(self, schema: str, table: str) -> bool:
        return (
            self.scalar(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = %s "
                "AND table_name = %s",
                (schema, table),
            )
            is not None
        )

    def schema_objects(self, schema: str) -> list[str]:
        # Flyway's query: tables and views, routines, triggers - and events where the server
        # lets them be read, which MariaDB does not when its scheduler is off.
        cursor = self.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s "
            "UNION ALL SELECT routine_name FROM information_schema.routines "
            "WHERE routine_schema = %s "
            "UNION ALL SELECT trigger_name FROM information_schema.triggers "
            "WHERE trigger_schema = %s LIMIT 5",
            (schema, schema, schema),
        )
        found = [str(row[0]) for row in cursor.fetchall()]
        if not found:
            try:
                events = self.execute(
                    "SELECT event_name FROM information_schema.events "
                    "WHERE event_schema = %s LIMIT 5",
                    (schema,),
                )
                found = [str(row[0]) for row in events.fetchall()]
            except Exception:
                LOG.debug("%s: events cannot be read here", self.label)
        return found

    def current_user(self) -> str:
        return str(self.scalar("SELECT SUBSTRING_INDEX(USER(), '@', 1)"))

    def database_name(self) -> str | None:
        name = self.scalar("SELECT DATABASE()")
        return None if name is None else str(name)

    def create_history_script(self, qualified: str, schema: str, table: str) -> list[str]:
        # Flyway's MySQLDatabase.getRawCreateScript.
        return [
            f"CREATE TABLE {qualified} (\n"
            "    `installed_rank` INT NOT NULL,\n"
            "    `version` VARCHAR(50),\n"
            "    `description` VARCHAR(200) NOT NULL,\n"
            "    `type` VARCHAR(20) NOT NULL,\n"
            "    `script` VARCHAR(1000) NOT NULL,\n"
            "    `checksum` INT,\n"
            "    `installed_by` VARCHAR(100) NOT NULL,\n"
            "    `installed_on` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n"
            "    `execution_time` INT NOT NULL,\n"
            "    `success` BOOL NOT NULL,\n"
            f"    CONSTRAINT `{table}_pk` PRIMARY KEY (`installed_rank`)\n"
            ")",
            f"CREATE INDEX `{table}_s_idx` ON {qualified} (`success`)",
        ]

    def refuse_if_read_only(self) -> None:
        cursor = self.execute(
            "SHOW GLOBAL VARIABLES WHERE Variable_name IN ('read_only', 'super_read_only')"
        )
        on = [name for name, value in cursor.fetchall() if str(value).upper() not in ("OFF", "0")]
        if on:
            raise MigrationBackendError(
                f"{self.label} is read-only ({', '.join(sorted(on))} is on), so it is a replica "
                f"or has been set aside. Migrations must run against the primary: point this "
                f"shard's primaryConfig at it directly. A user with SUPER could write past "
                f"read_only, which is why this refuses rather than trying."
            )

    def connection_lost(self) -> bool:
        try:
            self.connection.ping(reconnect=False)
        except Exception:
            return True
        return False

    def _try_lock(self, table: str) -> bool:
        # A positive wait, and the answer checked. MariaDB answers a negative wait with NULL at
        # once and takes no lock, where MySQL waits forever - measured - so neither is used.
        name = mysql_lock_name(table)
        result = self.scalar("SELECT GET_LOCK(%s, %s)", (name, MYSQL_LOCK_WAIT))
        if result is None:
            raise MigrationBackendError(
                f"{self.label}: GET_LOCK({name!r}) returned NULL, which is a server-side failure "
                f"rather than a lock someone else holds"
            )
        return bool(result == 1)

    def _wait(self, retries: int) -> None:
        # GET_LOCK already waited inside the server.
        pass

    def _unlock(self, table: str) -> None:
        self.execute("SELECT RELEASE_LOCK(%s)", (mysql_lock_name(table),))


class SQLiteBackend(Backend):
    dialect = "sqlite"
    transactional_ddl = True
    paramstyle = "?"

    _lock_file: Any = None

    def connect(self) -> Any:
        import sqlite3

        connect: Any = sqlite3.connect
        # `isolation_level=None`: auto-commit, with every transaction opened by an explicit BEGIN.
        return connect(*self._args, **self._kwargs, isolation_level=None)

    def current_schema(self) -> str:
        return "main"

    def table_exists(self, schema: str, table: str) -> bool:
        return (
            self.scalar(
                f"SELECT 1 FROM {self.quote(schema)}.sqlite_master WHERE type = 'table' "
                "AND name = %s",
                (table,),
            )
            is not None
        )

    def schema_objects(self, schema: str) -> list[str]:
        # Flyway counts tables only; a view or a trigger is something a migration made too.
        cursor = self.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view', 'trigger') "
            "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' AND name <> 'android_metadata' LIMIT 5"
        )
        return [str(row[0]) for row in cursor.fetchall()]

    def current_user(self) -> str:
        # SQLite has no users; Flyway records an empty string too.
        return ""

    def database_name(self) -> str | None:
        return "main"

    def create_history_script(self, qualified: str, schema: str, table: str) -> list[str]:
        # Flyway's SQLiteDatabase.getRawCreateScript.
        return [
            f"CREATE TABLE {qualified} (\n"
            '    "installed_rank" INT NOT NULL PRIMARY KEY,\n'
            '    "version" VARCHAR(50),\n'
            '    "description" VARCHAR(200) NOT NULL,\n'
            '    "type" VARCHAR(20) NOT NULL,\n'
            '    "script" VARCHAR(1000) NOT NULL,\n'
            '    "checksum" INT,\n'
            '    "installed_by" VARCHAR(100) NOT NULL,\n'
            "    \"installed_on\" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')),\n"
            '    "execution_time" INT NOT NULL,\n'
            '    "success" BOOLEAN NOT NULL\n'
            ")",
            f'CREATE INDEX "{schema}"."{table}_s_idx" ON "{table}" ("success")',
        ]

    @property
    def lock_path(self) -> str | None:
        """A file beside the database, locked by the operating system. An in-memory database
        has one connection and nobody to wait for."""
        path = str(self._args[0]) if self._args else ""
        if not path or path == ":memory:" or self._kwargs.get("uri"):
            return None
        return path + "-ekbatan-migrate.lock"

    def _try_lock(self, table: str) -> bool:
        path = self.lock_path
        if path is None:
            return True
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows: documented as unprotected
            LOG.debug("%s: no file locking on this platform", self.label)
            return True
        handle = open(path, "a+b")  # noqa: SIM115 - held for as long as the lock is
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        self._lock_file = handle
        return True

    def _unlock(self, table: str) -> None:
        handle, self._lock_file = self._lock_file, None
        if handle is not None:
            handle.close()  # closing the file releases the lock


BACKENDS: dict[str, type[Backend]] = {
    "postgresql": PostgresBackend,
    "mysql": MySQLBackend,
    "mariadb": MySQLBackend,
    "sqlite": SQLiteBackend,
}

#: The driver the migrator speaks to each database with - the framework's own, whatever driver
#: the application's url names.
DRIVERS = {
    "postgresql": "postgresql+psycopg",
    "mysql": "mysql+pymysql",
    "mariadb": "mariadb+pymysql",
    "sqlite": "sqlite+pysqlite",
}


class ConnectionArguments(NamedTuple):
    family: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    database: str | None

    def __repr__(self) -> str:
        # The driver's keyword arguments hold the password; the printed form never does.
        return (
            f"ConnectionArguments(family={self.family!r}, args={redact(self.args)!r}, "
            f"kwargs={redact(self.kwargs)!r}, database={self.database!r})"
        )


def connection_arguments(
    label: str, url: str | URL, connect_args: Mapping[str, Any]
) -> ConnectionArguments:
    """What the driver is handed for this url - exactly what SQLAlchemy would hand it.

    SQLAlchemy is asked, rather than the url being taken apart here, so credentials are decoded,
    query options are given their types (``connect_timeout=5`` arrives as a number) and relative
    SQLite paths resolve, all as they do for the application's own engine. The driver is always
    the framework's - psycopg, PyMySQL, sqlite3 - whatever the url names, and ``connect_args``
    are laid over the top as the engine lays them.
    """
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    family = parsed.get_backend_name()
    if family not in BACKENDS:
        raise MigrationBackendError(
            f"{label}: cannot migrate a {family!r} database; the migrator speaks "
            f"{', '.join(sorted(BACKENDS))}"
        )
    translated = parsed.set(drivername=DRIVERS[family])
    args, kwargs = translated.get_dialect()().create_connect_args(translated)
    merged = {**kwargs, **dict(connect_args)}
    database = merged.get("dbname") or merged.get("database") or (args[0] if args else None)
    return ConnectionArguments(family, tuple(args), merged, database)


def open_backend(label: str, url: str | URL, connect_args: Mapping[str, Any]) -> Backend:
    """Connect to one database the way the application's engine would, and nothing more."""
    arguments = connection_arguments(label, url, connect_args)
    return BACKENDS[arguments.family](label, arguments.args, arguments.kwargs, arguments.database)
