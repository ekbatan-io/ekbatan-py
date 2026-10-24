"""Somewhere to put a database, for each dialect the framework supports.

One container per dialect for the whole session, with a logical database created inside it per
fixture that needs one. Starting a container per test would be correct and unusable; starting
one per dialect and truncating between tests is correct and takes seconds.

SQLite is here too, and is the default, because a suite that needs a daemon to run at all is a
suite people stop running. It is not a supported production dialect - what it buys is that the
125 behavioural tests stay a second away while you are writing them, and then run against the
real three before anything is believed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy import MetaData, create_engine, text

ALL_DIALECTS = ("sqlite", "postgres", "mysql", "mariadb")
DEFAULT_DIALECTS = ("sqlite",)

IMAGES = {
    "postgres": os.environ.get("EKBATAN_POSTGRES_IMAGE", "postgres:17-alpine"),
    "mysql": os.environ.get("EKBATAN_MYSQL_IMAGE", "mysql:8.4"),
    "mariadb": os.environ.get("EKBATAN_MARIADB_IMAGE", "mariadb:11.4"),
}


def requested_dialects(config: Any) -> tuple[str, ...]:
    raw = config.getoption("--dialects") or os.environ.get("EKBATAN_DIALECTS") or ""
    if not raw:
        return DEFAULT_DIALECTS
    if raw.strip() == "all":
        return ALL_DIALECTS
    chosen = tuple(d.strip() for d in raw.split(",") if d.strip())
    unknown = [d for d in chosen if d not in ALL_DIALECTS]
    if unknown:
        raise pytest.UsageError(f"unknown dialect(s) {unknown}; known: {list(ALL_DIALECTS)}")
    return chosen


def docker_is_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class Backend(ABC):
    """Hands out a connection URL per logical database, and cleans up between tests."""

    dialect: str

    @abstractmethod
    def url(self, name: str) -> str:
        """A URL for a database of this name, created on first use."""

    #: The suite's own migrations, a directory per dialect, applied per logical database.
    MIGRATIONS = Path(__file__).resolve().parent / "migrations"
    #: Which directory each dialect migrates from - MariaDB shares MySQL's.
    MIGRATION_DIRECTORIES: ClassVar[dict[str, str]] = {
        "sqlite": "sqlite",
        "postgres": "postgres",
        "mysql": "mysql",
        "mariadb": "mysql",
    }

    def prepare(self, name: str, metadata: MetaData) -> None:
        """Build the schema by migrating, not by `create_all`.

        Java's integration tests run Flyway over the same SQL a deployment runs; this is that.
        It is not fidelity for its own sake: the indexes are **not** in `ekbatan.schema` - a
        single declaration cannot be `WHERE delivered = FALSE` on PostgreSQL and a plain
        composite on MySQL - so `create_all` would build the tables with no indexes at all, and
        the suite would stop covering the shape a deployment actually has.
        """
        from ekbatan.migrations import migrate
        from fixtures.logins import single

        migrate(
            single(self.url(name)),
            self.MIGRATIONS / self.MIGRATION_DIRECTORIES[self.dialect],
        )

    def data_source(self, name: str, **settings: Any) -> Any:
        """A sync `DataSource` on one logical database, built from its config as an application
        builds one. Its owner closes it. A test that runs in both modes builds its own with
        `DataSource.from_config(data_source(url))` instead, so the harness swaps in the async
        class - this method is not rebound."""
        from ekbatan.persistence import DataSource
        from fixtures.logins import data_source

        return DataSource.from_config(data_source(self.url(name), **settings))

    def engine(self, name: str, **kwargs: Any) -> Any:
        """SQLAlchemy's own engine on one logical database, for a test's own statements. It maps
        `eventlog` onto that database where it is neither a schema nor a database (SQLite), as a
        data source's engine does (ekbatan.schema.eventlog_translation) - so a test reads the
        event tables where the framework writes them."""
        from ekbatan.schema import eventlog_translation

        translation = eventlog_translation(self.dialect)
        options = {"schema_translate_map": translation} if translation else {}
        return create_engine(self.url(name), execution_options=options, **kwargs)

    def truncate(self, name: str, metadata: MetaData) -> None:
        """Empty every table, leaving the schema alone.

        Between tests rather than recreating: DDL is the slow part on a real server, and a
        suite that pays it 125 times per dialect is a suite nobody waits for.
        """
        engine = self.engine(name)
        try:
            with engine.begin() as conn:
                self._disable_constraints(conn)
                for table in reversed(metadata.sorted_tables):
                    conn.execute(table.delete())
                self._enable_constraints(conn)
        finally:
            engine.dispose()

    # Optional hooks, deliberately not abstract: most backends need none of them.
    def _disable_constraints(self, conn: Any) -> None:  # noqa: B027
        """MySQL needs its foreign-key checks off to truncate in any order."""

    def _enable_constraints(self, conn: Any) -> None:  # noqa: B027
        """And back on afterwards."""

    def apart(self, name: str) -> None:  # noqa: B027
        """Put the database ``name`` on a server of its own. Only MySQL and MariaDB need it: their
        ``eventlog`` is the server's, so two databases that each need event tables of their own -
        two shards, a primary and its replica - need two servers, as Java's examples give each
        shard one. Elsewhere the event tables belong to the database."""

    def drop_eventlog(self, name: str) -> None:  # noqa: B027
        """Throw away the event tables the database ``name`` sees, where they are not its own -
        MySQL's and MariaDB's ``eventlog`` - for a test whose own migrations create them."""

    def stop(self) -> None:  # noqa: B027
        """A container has something to shut down; a directory does not."""


class SqliteBackend(Backend):
    dialect = "sqlite"

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def url(self, name: str) -> str:
        return f"sqlite:///{self._directory / (name + '.db')}"


class ServerBackend(Backend):
    """A real server in a container, with logical databases created on demand."""

    def __init__(self, dialect: str, container: Any, driver: str) -> None:
        self.dialect = dialect
        self._container = container
        self._driver = driver
        self._created: set[str] = set()
        self._base = self._scheme(container.get_connection_url())

    def _scheme(self, url: str) -> str:
        """The URL the container hands out, said in SQLAlchemy's terms.

        ``MySqlContainer`` builds its own scheme as ``mysql+<driver>`` and there is no MariaDB
        container class to ask instead, so a MariaDB container announces itself as MySQL. That
        is not cosmetic: SQLAlchemy picks the server's version-reporting, its DDL, and its
        `SKIP LOCKED` support off the scheme, and the framework picks its lock provider off
        ``engine.dialect.name``. Left alone, "the MariaDB suite" tests the MySQL paths.
        """
        _, _, rest = url.partition("://")
        return f"{self.dialect if self.dialect != 'postgres' else 'postgresql'}+{self._driver}://{rest}"

    def url(self, name: str) -> str:
        if name not in self._created:
            self._create(name)
            self._created.add(name)
        return self._with_database(name)

    def _create(self, name: str) -> None:
        engine = create_engine(self._base, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                conn.execute(text(f"DROP DATABASE IF EXISTS {name}"))
                conn.execute(text(f"CREATE DATABASE {name}"))
        finally:
            engine.dispose()

    def _with_database(self, name: str) -> str:
        head, _, _ = self._base.rpartition("/")
        return f"{head}/{name}"

    def stop(self) -> None:
        self._container.stop()


class MysqlFamilyBackend(ServerBackend):
    """MySQL and MariaDB, where ``eventlog`` is a database of its own on the server: the event
    tables are the server's, not a logical database's.

    So they are created once per server, from ``migrations/mysql-eventlog`` - Java's
    ``V0000__create_eventlog_database.sql``, then the tables - and each logical database migrates
    only what it has of its own. Emptying a database between tests empties its server's event
    tables with it, which is right while one test runs at a time. A database that needs event
    tables of its own beside another's goes on a second server (:meth:`apart`).
    """

    #: The database that holds the history of the server's ``eventlog`` migration.
    EVENTLOG_OWNER = "ekbatan_eventlog"

    def __init__(
        self, dialect: str, container: Any, driver: str, start: Callable[[], Any] | None = None
    ) -> None:
        super().__init__(dialect, container, driver)
        #: Starts another server like this one, for :meth:`apart`.
        self._start = start
        self._apart: set[str] = set()
        self._other: MysqlFamilyBackend | None = None

    def apart(self, name: str) -> None:
        self._apart.add(name)

    def _home(self, name: str) -> MysqlFamilyBackend:
        """The server the database ``name`` is on: this one, or the second for :meth:`apart`."""
        if name not in self._apart:
            return self
        if self._other is None:
            assert self._start is not None, "a second server cannot start a third"
            self._other = MysqlFamilyBackend(self.dialect, self._start(), self._driver)
        return self._other

    def url(self, name: str) -> str:
        home = self._home(name)
        return super().url(name) if home is self else home.url(name)

    def prepare(self, name: str, metadata: MetaData) -> None:
        self._home(name).ensure_eventlog()
        super().prepare(name, metadata)

    def ensure_eventlog(self) -> None:
        """This server's ``eventlog``, as the suite's migration builds it - built again if a test
        threw it away (:meth:`drop_eventlog`)."""
        from ekbatan.migrations import migrate
        from fixtures.logins import single

        engine = create_engine(self._base, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                found = conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.tables WHERE (table_schema ="
                        " 'eventlog' AND table_name IN ('events', 'event_notifications')) OR"
                        " (table_schema = :owner AND table_name = 'ekbatan_schema_history')"
                    ),
                    {"owner": self.EVENTLOG_OWNER},
                ).scalar()
        finally:
            engine.dispose()
        if found == 3:
            return
        self._drop_eventlog_here()
        migrate(single(self.url(self.EVENTLOG_OWNER)), self.MIGRATIONS / "mysql-eventlog")

    def drop_eventlog(self, name: str) -> None:
        self._home(name)._drop_eventlog_here()

    def _drop_eventlog_here(self) -> None:
        """This server's ``eventlog`` gone, with the history of the migration that made it."""
        engine = create_engine(self._base, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                conn.execute(text("DROP DATABASE IF EXISTS eventlog"))
                conn.execute(text(f"DROP DATABASE IF EXISTS {self.EVENTLOG_OWNER}"))
        finally:
            engine.dispose()
        self._created.discard(self.EVENTLOG_OWNER)

    def stop(self) -> None:
        if self._other is not None:
            self._other.stop()
        super().stop()

    def _disable_constraints(self, conn: Any) -> None:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))

    def _enable_constraints(self, conn: Any) -> None:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))


def start(dialect: str, tmp_root: Path) -> Backend:
    if dialect == "sqlite":
        directory = tmp_root / "sqlite"
        directory.mkdir(parents=True, exist_ok=True)
        return SqliteBackend(directory)

    if not docker_is_available():
        pytest.skip(
            f"{dialect} needs a container and Docker is not reachable. "
            f"Run with --dialects=sqlite, or start Docker and use --dialects=all.",
            allow_module_level=True,
        )

    if dialect == "postgres":
        from testcontainers.community.postgres import PostgresContainer

        container = PostgresContainer(IMAGES["postgres"], driver="psycopg")
        container.start()
        return ServerBackend("postgres", container, "psycopg")

    from testcontainers.community.mysql import MySqlContainer

    # `dialect=` here is the *driver* only. testcontainers prefixes `mysql+` itself and, since
    # 4.15, refuses a value that already carries the prefix - which is how the MySQL and MariaDB
    # backends came to raise at container start rather than run. `ServerBackend._scheme` puts
    # the right scheme back on afterwards, which is also what makes MariaDB be MariaDB.
    # `username="root"`, because this backend creates a logical database per fixture and
    # testcontainers' default `test` user is granted rights on the `test` schema only -
    # `CREATE DATABASE` comes back as "Access denied for user 'test'@'%'".
    #
    # No dedicated MariaDB module; the MySQL one drives it given the right image. The default
    # readiness line is the right one: both servers announce "ready for connections" twice - once
    # from the temporary server the entrypoint runs to initialise the data directory, then again
    # from the real one. Waiting for the first connects to a server that is about to shut down,
    # which arrives as "Lost connection to MySQL server during query" some tests later.
    def server() -> Any:
        started = MySqlContainer(IMAGES[dialect], dialect="pymysql", username="root")
        started.start()
        return started

    return MysqlFamilyBackend(dialect, server(), "pymysql", server)
