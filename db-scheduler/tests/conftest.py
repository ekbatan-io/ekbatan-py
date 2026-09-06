"""Fixtures, parametrised over every dialect the run asks for.

db-scheduler proves its repository with one `CompatibilityTest` subclass per database. Here the
contract is written once and the fixture is parametrised, so a dialect cannot be quietly left
out of a scenario.

    pytest                          sqlite, about a second
    pytest --dialects=postgres      one container
    pytest --dialects=all           sqlite, postgres, mysql, mariadb - the same assertions, four
                                    times over
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, MetaData, create_engine

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db_scheduler.clock import SettableClock
from db_scheduler.event import SchedulerListeners
from db_scheduler.jdbc import JdbcTaskRepository, scheduled_tasks_table
from db_scheduler.resolver import TaskResolver
from db_scheduler.task.helper import Tasks

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

#: SQLite is what keeps the inner loop fast. It is not a database this library claims to
#: support - the claim is Postgres, MySQL and MariaDB, and that claim rests on the containers.
DEFAULT_DIALECTS = ("sqlite",)
ALL_DIALECTS = ("sqlite", "postgres", "mysql", "mariadb")

IMAGES = {
    "postgres": os.environ.get("DB_SCHEDULER_POSTGRES_IMAGE", "postgres:17-alpine"),
    "mysql": os.environ.get("DB_SCHEDULER_MYSQL_IMAGE", "mysql:8.4"),
    "mariadb": os.environ.get("DB_SCHEDULER_MARIADB_IMAGE", "mariadb:11.4"),
}


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--dialects",
        default=None,
        help="comma-separated: sqlite, postgres, mysql, mariadb - or 'all'.",
    )


def requested_dialects(config: Any) -> tuple[str, ...]:
    chosen = config.getoption("--dialects") or os.environ.get("DB_SCHEDULER_DIALECTS")
    if not chosen:
        return DEFAULT_DIALECTS
    if chosen == "all":
        return ALL_DIALECTS
    return tuple(part.strip() for part in chosen.split(",") if part.strip())


def pytest_generate_tests(metafunc: Any) -> None:
    if "dialect" in metafunc.fixturenames:
        chosen = requested_dialects(metafunc.config)
        metafunc.parametrize("dialect", chosen, scope="session", ids=list(chosen))


@pytest.fixture(scope="session")
def engine_url(dialect: str, tmp_path_factory: Any) -> Iterator[str]:
    """A database to run against - a file for SQLite, a container for the rest."""
    if dialect == "sqlite":
        path = tmp_path_factory.mktemp("db-scheduler") / "scheduler.db"
        yield f"sqlite:///{path}"
        return

    if dialect == "postgres":
        from testcontainers.postgres import PostgresContainer

        with PostgresContainer(IMAGES["postgres"], driver="psycopg") as container:
            url = container.get_connection_url()
            _wait_for(url)
            yield url
        return

    from testcontainers.mysql import MySqlContainer

    if dialect == "mysql":
        with MySqlContainer(IMAGES["mysql"]) as container:
            url = container.get_connection_url().replace("mysql://", "mysql+pymysql://")
            _wait_for(url)
            yield url
        return

    if dialect == "mariadb":
        # No dedicated MariaDB module; MySqlContainer plus the image is the whole difference,
        # and the dialect prefix is what makes SQLAlchemy pick the right customization.
        with MySqlContainer(
            IMAGES["mariadb"], username="test", password="test", dbname="test"
        ) as container:
            url = container.get_connection_url().replace("mysql://", "mariadb+pymysql://")
            _wait_for(url)
            yield url
        return

    raise ValueError(f"unknown dialect: {dialect}")


def _wait_for(url: str, attempts: int = 60) -> None:
    """Poll until the server actually answers.

    Postgres restarts itself part-way through first-time initialisation, so a container whose
    log line has appeared can still refuse the next connection. Waiting here rather than
    retrying inside the library keeps that container quirk out of the production path.
    """
    import time

    from sqlalchemy import text

    last: Exception | None = None
    probe = create_engine(url, future=True)
    try:
        for _ in range(attempts):
            try:
                with probe.connect() as conn:
                    conn.execute(text("select 1"))
                    return
            except Exception as exc:
                last = exc
                time.sleep(1)
    finally:
        probe.dispose()
    raise RuntimeError(f"database at {url} never became ready: {last}")


@pytest.fixture
def engine(engine_url: str) -> Iterator[Engine]:
    built = create_engine(engine_url, future=True)
    yield built
    built.dispose()


#: Both timestamp shapes db-scheduler supports. `False` - zoned columns where the dialect has
#: them - is db-scheduler's *default*, so it is the shape of a table a Java instance created;
#: `True` is what it recommends and what this port defaults to. Both have to work, because
#: reading a table in the wrong shape is an hour's silent drift, not an error.
TIMESTAMP_SHAPES = (True, False)


@pytest.fixture(params=TIMESTAMP_SHAPES, ids=["utc", "zoned"])
def persist_timestamp_in_utc(request: Any) -> bool:
    return bool(request.param)


@pytest.fixture
def table(engine: Engine, persist_timestamp_in_utc: bool) -> Iterator[Any]:
    metadata = MetaData()
    created = scheduled_tasks_table(
        metadata, persist_timestamp_in_utc=persist_timestamp_in_utc
    )
    metadata.drop_all(engine)
    metadata.create_all(engine)
    yield created
    metadata.drop_all(engine)


@pytest.fixture
def clock() -> SettableClock:
    return SettableClock(NOW)


#: The task names the repository contract writes rows for. A repository resolves every row it
#: reads back to a task, and drops the ones it cannot - so a test that schedules a task the
#: resolver has never heard of gets nothing back, exactly as a real scheduler would.
CONTRACT_TASK_NAMES = ("a-task", "other", "a", "b", "n" * 100)


@pytest.fixture
def task_resolver(clock: SettableClock) -> TaskResolver:
    return TaskResolver(
        SchedulerListeners(),
        clock,
        [Tasks.one_time(name).execute(lambda i, c: None) for name in CONTRACT_TASK_NAMES],
    )


@pytest.fixture
def repository(
    engine: Engine,
    table: Any,
    clock: SettableClock,
    task_resolver: TaskResolver,
    persist_timestamp_in_utc: bool,
) -> JdbcTaskRepository:
    return JdbcTaskRepository(
        engine,
        scheduler_name="test-scheduler",
        table=table,
        clock=clock,
        task_resolver=task_resolver,
        persist_timestamp_in_utc=persist_timestamp_in_utc,
    )


@pytest.fixture
def repository_by_priority(
    engine: Engine,
    table: Any,
    clock: SettableClock,
    task_resolver: TaskResolver,
    persist_timestamp_in_utc: bool,
) -> JdbcTaskRepository:
    return JdbcTaskRepository(
        engine,
        scheduler_name="test-scheduler",
        table=table,
        clock=clock,
        order_by_priority=True,
        task_resolver=task_resolver,
        persist_timestamp_in_utc=persist_timestamp_in_utc,
    )
