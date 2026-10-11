"""The yoyo side of the migrator: one connection per database, and everything done on it.

Imported only once something migrates, because yoyo is the optional ``migrations`` extra and
``import ekbatan.migrations`` must work without it.

yoyo is used for what it is good at - reading a SQL file into statements, and running them in a
transaction, or without one for a file marked ``-- transactional: false`` - and for nothing
else. Its lock, its bookkeeping tables and its "apply one migration" path are not used, each for
a reason that was measured:

- **Its lock** is a row in a ``yoyo_lock`` table. A waiting process gives up after ten seconds,
  and a process killed while holding it leaves the row behind, so every later deploy fails until
  someone runs ``yoyo break-lock`` by hand.
- **Its apply path** (``apply_one``) runs each migration on a *second* connection it opens for
  the purpose. The lock is then on one connection and the migration on another: when the lock's
  connection dies - a network blip, an idle timeout, a proxy - the database releases the lock
  while the migration carries on, and a second process runs the same migration at the same time.
  Every design that split the two ran the migration twice when that happened.
- **Its bookkeeping** records a migration in a separate step after applying it, so a crash in
  between leaves a migration applied but not recorded, and it runs again next time.

So here the lock, the migration and the row that records it all go through the one connection
this backend opened: if that connection dies, all three die together.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

from yoyo.backends.core.mysql import MySQLBackend
from yoyo.backends.core.postgresql import PostgresqlPsycopgBackend
from yoyo.backends.core.sqlite3 import SQLiteBackend
from yoyo.connections import DatabaseURI
from yoyo.migrations import Migration

from ._redact import redact
from .locks.base import lock_key_hash

if TYPE_CHECKING:
    from sqlalchemy.engine import URL

LOG = logging.getLogger("ekbatan.migrations")

#: How long a process waiting for another's migration sleeps between attempts. Every attempt is
#: its own short statement, so a waiting session is idle between them rather than blocked inside
#: one - see `_SessionLock.acquire` for why that matters.
POLL = 0.5
#: How often a waiting process says it is still waiting.
REPORT_EVERY = 30.0

#: The advisory-lock key on PostgreSQL. Advisory locks are scoped to one database already, so
#: two shards that are two databases on the same server do not wait on each other.
POSTGRES_LOCK_KEY = lock_key_hash("ekbatan-migrate")
#: MySQL and MariaDB lock names are server-wide and at most 64 characters.
MYSQL_LOCK_PREFIX = "ekbatan-migrate:"
MYSQL_LOCK_NAME_LIMIT = 64


class MigrationBackendError(Exception):
    """Something about the database itself refuses a migration: a replica, a lock failure."""


def mysql_lock_name(database: str | None) -> str:
    """The server-wide lock name for one database's migrations.

    Server-wide is the trap: two shards that are two databases on one MySQL server would
    otherwise wait on each other's migrations, and so would every other service using the same
    name. A database name long enough to push the name past the 64-character limit is hashed.
    """
    name = MYSQL_LOCK_PREFIX + (database or "")
    if len(name) <= MYSQL_LOCK_NAME_LIMIT:
        return name
    digest = hashlib.sha256((database or "").encode()).hexdigest()
    return MYSQL_LOCK_PREFIX + digest[: MYSQL_LOCK_NAME_LIMIT - len(MYSQL_LOCK_PREFIX)]


class _SessionLock:
    """The lock, on the backend's own connection.

    Acquired by trying and sleeping rather than by one blocking call. A session blocked inside a
    lock call is in the middle of a statement, and PostgreSQL's ``CREATE INDEX CONCURRENTLY``,
    run by the process holding the lock, waits for every such session - which are themselves
    waiting for the lock. Neither side ever moves, and nothing reports a deadlock. Trying and
    sleeping keeps the waiters idle between attempts.
    """

    label: str

    def _try_lock(self) -> bool:
        raise NotImplementedError

    def _unlock(self) -> None:
        raise NotImplementedError

    def acquire(self) -> None:
        """Wait as long as it takes. Another process's migration finishes or its process dies,
        and either way the database lets go of the lock."""
        started = time.monotonic()
        last_report: float | None = None
        while not self._try_lock():
            now = time.monotonic()
            if last_report is None:
                LOG.info(
                    "%s: another process is migrating this database; waiting for it to finish",
                    self.label,
                )
                last_report = now
            elif now - last_report >= REPORT_EVERY:
                LOG.info("%s: still waiting (%.0fs)", self.label, now - started)
                last_report = now
            time.sleep(POLL)

    def release(self) -> None:
        """Best effort. Closing the connection releases the lock whatever happens here."""
        try:
            self._unlock()
        except Exception:  # pragma: no cover - the connection is already gone
            LOG.debug("%s: releasing the migration lock failed", self.label, exc_info=True)


class _Common(_SessionLock):
    """What every dialect's backend shares.

    ``connect`` ignores yoyo's ``DatabaseURI`` and uses the arguments SQLAlchemy derives from
    the same url the application's engine is built from - so the migration connects exactly as
    the application does, the login, query options and ``data_source_properties`` included.
    """

    #: Which database this is, in the framework's own names.
    dialect: ClassVar[str]
    #: Whether a schema change can be rolled back. PostgreSQL and SQLite: yes. MySQL and
    #: MariaDB commit every schema change on their own.
    transactional_ddl: ClassVar[bool]

    # What yoyo's backend provides, declared for the type checker only - yoyo ships no types.
    connection: Any
    DatabaseError: Any
    has_transactional_ddl: bool
    if TYPE_CHECKING:

        def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> Any: ...

        def transaction(self, rollback_on_exit: bool = False) -> Any: ...

    def __init__(
        self, label: str, args: tuple[Any, ...], kwargs: Mapping[str, Any], database: str | None
    ) -> None:
        self.label = label
        self._args = args
        self._kwargs = dict(kwargs)
        self.database = database
        uri = DatabaseURI(self.dialect, None, None, None, None, database or "", {})
        super().__init__(uri, "_yoyo_migration")  # type: ignore[call-arg]
        # Stated rather than probed. yoyo probes by creating and dropping a scratch table, which
        # needs rights a migration role may not have and leaves the table behind on a failure.
        self.has_transactional_ddl = self.transactional_ddl

    # -- nothing of yoyo's that would open a second connection or use its lock table ----------

    def init_database(self) -> None:
        """yoyo would create its ``yoyo_lock`` table and probe for transactional DDL here."""

    def create_lock_table(self) -> None:
        """Never created: the lock is a database session lock."""

    def copy(self) -> Any:
        raise AssertionError(
            "the migrator runs every migration on its one connection; nothing may open another"
        )

    def lock(self, timeout: int = 10) -> Any:
        raise AssertionError("the migrator locks with a database session lock, not yoyo's")

    # -- per dialect ---------------------------------------------------------------------------

    def refuse_if_read_only(self) -> None:
        """Refuse a database that cannot take the migration: a replica, or one set read-only."""

    def connection_lost(self) -> bool:
        """Whether the connection is gone, for saying so in the error rather than guessing."""
        return False

    def table_exists(self, name: str) -> bool:
        """Whether a table of this name is in the schema the connection works in."""
        raise NotImplementedError

    def columns(self, table: str) -> set[str]:
        """The table's column names, read from an empty result so every dialect answers alike."""
        cursor = self.execute(f"SELECT * FROM {table} WHERE 1 = 0")
        names = {str(column[0]).lower() for column in cursor.description}
        cursor.fetchall()
        return names

    def schema_objects(self) -> list[str]:
        """A few of the things already in the schema, as Flyway looks for them before it will
        create its history table: tables, views, sequences, types, functions, procedures and
        triggers - anything a migration could have made. Empty for an empty schema."""
        raise NotImplementedError

    def current_user(self) -> str:
        """Who is migrating, as the database knows them - Flyway's ``installed_by``."""
        raise NotImplementedError

    def close(self) -> None:
        try:
            self.connection.close()
        except Exception:  # pragma: no cover - already gone
            LOG.debug("%s: closing the migration connection failed", self.label, exc_info=True)


class PostgresMigrationBackend(_Common, PostgresqlPsycopgBackend):  # type: ignore[misc]
    dialect = "postgres"
    transactional_ddl = True

    def connect(self, dburi: Any) -> Any:
        import psycopg

        # Auto-commit, as yoyo's own PostgreSQL backend connects: yoyo opens every transaction
        # with an explicit BEGIN, and a lock taken outside one belongs to the session.
        connection = psycopg.connect(*self._args, **self._kwargs, autocommit=True)
        # A session only notices its client has gone when it next talks to it. A migrator killed
        # half-way through a long statement therefore left that statement running - and holding
        # the lock - until it finished, and the next deploy waited for it: thirty seconds, for
        # the thirty-second statement it was measured with. Nothing ran twice, but nothing should
        # wait on an orphan either. With this set, the server checks during a statement and
        # cancels it, rolling it back and letting go of the lock. PostgreSQL 14 and later; on an
        # older server the setting does not exist and the wait is what it was.
        try:
            connection.execute("SET client_connection_check_interval = '1s'")
        except psycopg.Error:
            LOG.debug("%s: client_connection_check_interval is not available", self.label)
        connection.add_notice_handler(self._notice)
        return connection

    def _notice(self, diagnostic: Any) -> None:
        """What the server says beside a statement - "does not exist, skipping" - to the log, as
        Flyway logs it rather than dropping it."""
        LOG.warning(
            "%s: the database said: %s (SQL state %s)",
            self.label,
            diagnostic.message_primary,
            diagnostic.sqlstate,
        )

    @contextmanager
    def disable_transactions(self) -> Iterator[None]:
        """yoyo's, less its opening ``ROLLBACK`` when no transaction is open.

        yoyo rolls back before every file marked ``-- transactional: false``, open transaction or
        not, and PostgreSQL answers a ``ROLLBACK`` with nothing to roll back with a warning -
        which, now that the server's warnings reach the log, read as a problem with every such
        migration. The connection is in autocommit throughout, as yoyo sets it.
        """
        import psycopg

        if self.connection.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            self.rollback()
        self._in_transaction = False
        saved = self.connection.autocommit
        self.connection.autocommit = True
        try:
            yield
        finally:
            self.connection.autocommit = saved

    def _try_lock(self) -> bool:
        cursor = self.execute("SELECT pg_try_advisory_lock(:key)", {"key": POSTGRES_LOCK_KEY})
        return bool(cursor.fetchone()[0])

    def _unlock(self) -> None:
        self.execute("SELECT pg_advisory_unlock(:key)", {"key": POSTGRES_LOCK_KEY})

    def refuse_if_read_only(self) -> None:
        if self.execute("SELECT pg_is_in_recovery()").fetchone()[0]:
            raise MigrationBackendError(
                f"{self.label} is a read-only replica (the server is in recovery). Migrations "
                f"must run against the primary: point this shard's primaryConfig at it directly, "
                f"not at a replica or a proxy that might route to one."
            )

    def connection_lost(self) -> bool:
        return bool(getattr(self.connection, "closed", False) or self.connection.broken)

    def table_exists(self, name: str) -> bool:
        cursor = self.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = :name",
            {"name": name},
        )
        return cursor.fetchone() is not None

    def schema_objects(self) -> list[str]:
        # Flyway's query, with materialized views, partitioned and foreign tables added: a schema
        # holding only one of those is not empty either. Objects an extension owns are left out,
        # as Flyway leaves them out - `CREATE EXTENSION` in a fresh database is not a migration.
        owned = (
            "LEFT JOIN pg_catalog.pg_depend d ON d.objid = {oid} AND d.deptype = 'e' "
            "WHERE n.nspname = current_schema() AND d.objid IS NULL"
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
            + " LIMIT 5"
        )
        return [str(row[0]) for row in cursor.fetchall()]

    def current_user(self) -> str:
        return str(self.execute("SELECT current_user").fetchone()[0])


class _WarningCursor:
    """A MySQL cursor that hands the server's warnings to the log after each statement.

    MySQL and MariaDB answer some statements with warnings rather than errors - a table that did
    not exist to be dropped, a value that did not fit where the server is not strict. Flyway logs
    them; the driver only counts them. Every other attribute is the cursor's own.
    """

    def __init__(self, cursor: Any, backend: MySQLMigrationBackend) -> None:
        self._cursor = cursor
        self._backend = backend

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        result = self._cursor.execute(*args, **kwargs)
        if getattr(self._cursor, "warning_count", 0):
            for level, code, message in self._backend.connection.show_warnings() or ():
                LOG.warning(
                    "%s: the database said: %s (%s %s)", self._backend.label, message, level, code
                )
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class MySQLMigrationBackend(_Common, MySQLBackend):  # type: ignore[misc]
    dialect = "mysql"
    transactional_ddl = False

    def connect(self, dburi: Any) -> Any:
        import pymysql  # type: ignore[import-untyped]

        connection = pymysql.connect(*self._args, **self._kwargs)
        # Auto-commit on, as yoyo's PostgreSQL backend has it. yoyo opens every transaction with
        # an explicit BEGIN, so a normal migration is unaffected. Without it, a migration marked
        # `-- transactional: false` never has its statements committed - its rows are discarded
        # when the connection closes, and the migration is recorded as applied anyway. Measured,
        # on MySQL and MariaDB both.
        connection.autocommit(True)
        return connection

    def cursor(self) -> Any:
        return _WarningCursor(super().cursor(), self)

    def savepoint_rollback(self, id: str) -> None:
        """Let the real error through when MySQL has already dropped the savepoint.

        A schema change commits implicitly on MySQL and MariaDB, which ends the transaction and
        drops its savepoints. Rolling back to one then fails with error 1305, and without this
        that error replaces the one that actually happened.
        """
        try:
            super().savepoint_rollback(id)
        except self.DatabaseError as error:
            if error.args and error.args[0] == 1305:
                return
            raise

    @property
    def lock_name(self) -> str:
        return mysql_lock_name(self.database)

    def _try_lock(self) -> bool:
        # A zero wait, and the answer checked. MariaDB answers a negative wait with NULL at once
        # and takes no lock, where MySQL waits forever - measured - so neither is used.
        result = self.execute("SELECT GET_LOCK(:name, 0)", {"name": self.lock_name}).fetchone()[0]
        if result is None:
            raise MigrationBackendError(
                f"{self.label}: GET_LOCK({self.lock_name!r}) returned NULL, which is a "
                f"server-side failure rather than a lock someone else holds"
            )
        return bool(result == 1)

    def _unlock(self) -> None:
        self.execute("SELECT RELEASE_LOCK(:name)", {"name": self.lock_name})

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

    def table_exists(self, name: str) -> bool:
        cursor = self.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = :name",
            {"name": name},
        )
        return cursor.fetchone() is not None

    def schema_objects(self) -> list[str]:
        # Flyway's query: tables and views, routines, triggers - and events where the server
        # lets them be read, which MariaDB does not when its scheduler is off.
        cursor = self.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() "
            "UNION ALL SELECT routine_name FROM information_schema.routines "
            "WHERE routine_schema = DATABASE() "
            "UNION ALL SELECT trigger_name FROM information_schema.triggers "
            "WHERE trigger_schema = DATABASE() LIMIT 5"
        )
        found = [str(row[0]) for row in cursor.fetchall()]
        if not found:
            try:
                events = self.execute(
                    "SELECT event_name FROM information_schema.events "
                    "WHERE event_schema = DATABASE() LIMIT 5"
                )
                found = [str(row[0]) for row in events.fetchall()]
            except self.DatabaseError:
                LOG.debug("%s: events cannot be read here", self.label)
        return found

    def current_user(self) -> str:
        return str(self.execute("SELECT SUBSTRING_INDEX(USER(), '@', 1)").fetchone()[0])


class SQLiteMigrationBackend(_Common, SQLiteBackend):  # type: ignore[misc]
    dialect = "sqlite"
    transactional_ddl = True

    _lock_file: Any = None

    def connect(self, dburi: Any) -> Any:
        import sqlite3

        connect: Any = sqlite3.connect
        # A plain connection: not yoyo's shared-cache one, which the SQLite documentation
        # discourages, and without its date converters, which change process-wide state.
        # `isolation_level=None` because yoyo issues its own BEGIN.
        return connect(*self._args, **self._kwargs, isolation_level=None)

    # yoyo's SQLite backend registers process-wide date adapters around a `with` block; the
    # migrator never enters one, and never needs them.
    def __enter__(self) -> Any:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    @property
    def lock_path(self) -> str | None:
        """A file beside the database, locked by the operating system.

        SQLite has no session lock to take, but an OS file lock behaves like one: the operating
        system drops it when the process dies. An in-memory database has one connection and
        nobody to wait for.
        """
        path = str(self._args[0]) if self._args else ""
        if not path or path == ":memory:" or self._kwargs.get("uri"):
            return None
        return path + "-ekbatan-migrate.lock"

    def _try_lock(self) -> bool:
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

    def _unlock(self) -> None:
        handle, self._lock_file = self._lock_file, None
        if handle is not None:
            handle.close()  # closing the file releases the lock

    def table_exists(self, name: str) -> bool:
        cursor = self.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :name", {"name": name}
        )
        return cursor.fetchone() is not None

    def schema_objects(self) -> list[str]:
        # Flyway counts tables only; a view or a trigger is something a migration made too.
        cursor = self.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view', 'trigger') "
            "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' AND name <> 'android_metadata' LIMIT 5"
        )
        return [str(row[0]) for row in cursor.fetchall()]

    def current_user(self) -> str:
        # SQLite has no users. Flyway records an empty string here too.
        return ""


class SqlMigration(Migration):  # type: ignore[misc]
    """A yoyo migration that is SQL whatever its file is called.

    yoyo tells SQL from Python by a case-sensitive ``.sql`` at the end of the name, so it would
    hand ``V2__x.SQL`` - which Flyway runs as SQL - to Python's import machinery. Only SQL files
    are ever loaded here; nothing else about yoyo's reading changes.
    """

    def is_raw_sql(self) -> bool:
        return True


def load_sql(path: str) -> Any:
    """One SQL file as a yoyo migration: its statements split and its directives read."""
    import os

    migration = SqlMigration(
        os.path.splitext(os.path.basename(path))[0], path, os.path.dirname(path)
    )
    migration.load()
    return migration


BACKENDS: dict[str, type[_Common]] = {
    "postgresql": PostgresMigrationBackend,
    "mysql": MySQLMigrationBackend,
    "mariadb": MySQLMigrationBackend,
    "sqlite": SQLiteMigrationBackend,
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


def open_backend(label: str, url: str | URL, connect_args: Mapping[str, Any]) -> _Common:
    """Connect to one database the way the application's engine would, and nothing more."""
    arguments = connection_arguments(label, url, connect_args)
    return BACKENDS[arguments.family](label, arguments.args, arguments.kwargs, arguments.database)
