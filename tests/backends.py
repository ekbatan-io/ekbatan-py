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
        from ekbatan.config import ShardingConfig
        from ekbatan.migrations import migrate

        migrate(
            ShardingConfig.single(self.url(name)),
            self.MIGRATIONS / self.MIGRATION_DIRECTORIES[self.dialect],
        )

    def truncate(self, name: str, metadata: MetaData) -> None:
        """Empty every table, leaving the schema alone.

        Between tests rather than recreating: DDL is the slow part on a real server, and a
        suite that pays it 125 times per dialect is a suite nobody waits for.
        """
        engine = create_engine(self.url(name))
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
    if dialect == "mysql":
        container = MySqlContainer(IMAGES["mysql"], dialect="pymysql", username="root")
    else:
        # No dedicated MariaDB module; the MySQL one drives it given the right image. The
        # default readiness line is the right one: both servers announce "ready for
        # connections" twice - once from the temporary server the entrypoint runs to
        # initialise the data directory, then again from the real one. Waiting for the first
        # connects to a server that is about to shut down, which arrives as
        # "Lost connection to MySQL server during query" some tests later.
        container = MySqlContainer(IMAGES["mariadb"], dialect="pymysql", username="root")
    container.start()
    return MysqlFamilyBackend(dialect, container, "pymysql")
