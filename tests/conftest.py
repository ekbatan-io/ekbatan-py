"""Fixtures, parametrised over every dialect the run asks for.

The default is SQLite, because a suite that needs a daemon to run at all is a suite people stop
running. `--dialects=all` runs the same tests against Postgres, MySQL and MariaDB in
containers, which is what CI does and what any claim about behaviour rests on.

    pytest                                  # sqlite, about two seconds
    pytest --dialects=postgres
    pytest --dialects=all                   # every test, four times

There are no dialect-specific test classes. Java needs one subclass per dialect per suite -
PgRepositoryTest, MysqlRepositoryTest, MariadbRepositoryTest - because that is how JUnit
parametrises a whole class. Here one fixture is parametrised and every test in the suite runs
on every dialect, so a test cannot be accidentally left out of one.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from examples.wallet.schema import metadata

from backends import Backend, requested_dialects, start
from ekbatan.clock import FixedClock
from ekbatan.config import DataSourceConfig, ShardConfig, ShardingConfig
from ekbatan.persistence import DatabaseRegistry
from ekbatan.sharding import ShardIdentifier

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--dialects",
        default=None,
        help="comma-separated: sqlite, postgres, mysql, mariadb - or 'all'. "
        "Also readable from EKBATAN_DIALECTS.",
    )


def pytest_generate_tests(metafunc: Any) -> None:
    if "dialect" in metafunc.fixturenames:
        chosen = requested_dialects(metafunc.config)
        metafunc.parametrize("dialect", chosen, scope="session", ids=list(chosen))


@pytest.fixture(scope="session")
def backend(dialect: str, tmp_path_factory: Any) -> Iterator[Backend]:
    """One container - or one directory - per dialect, for the whole session."""
    started = start(dialect, tmp_path_factory.mktemp("ekbatan"))
    try:
        yield started
    finally:
        started.stop()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC))


def _registry(backend: Backend, names: dict[ShardIdentifier, str]) -> DatabaseRegistry:
    for name in names.values():
        backend.prepare(name, metadata)
        backend.truncate(name, metadata)

    config = ShardingConfig(
        shards=tuple(
            ShardConfig(
                shard=shard,
                primary=DataSourceConfig(url=backend.url(name), pool_size=5),
                name=name,
            )
            for shard, name in names.items()
        ),
        default_shard=next(iter(names)),
    )
    return DatabaseRegistry.from_config(config)


@pytest.fixture
def registry(backend: Backend) -> Iterator[DatabaseRegistry]:
    """One database."""
    with _registry(backend, {GLOBAL: "ekbatan_main"}) as reg:
        yield reg


@pytest.fixture
def sharded_registry(backend: Backend) -> Iterator[DatabaseRegistry]:
    """Two databases, so routing and isolation are real rather than simulated."""
    with _registry(backend, {GLOBAL: "ekbatan_global", MEXICO: "ekbatan_mexico"}) as reg:
        yield reg


@pytest.fixture
def wallets(registry: DatabaseRegistry, clock: FixedClock) -> Any:
    from examples.wallet.model import Wallet
    from examples.wallet.schema import WALLETS

    from ekbatan.repository import Repository

    return Repository(Wallet, WALLETS, registry, clock=clock)


@pytest.fixture
def notifications(registry: DatabaseRegistry, clock: FixedClock) -> Any:
    from examples.wallet.model import Notification
    from examples.wallet.schema import NOTIFICATIONS

    from ekbatan.repository import Repository

    return Repository(Notification, NOTIFICATIONS, registry, clock=clock)


@pytest.fixture
def executor(
    registry: DatabaseRegistry, clock: FixedClock, wallets: Any, notifications: Any
) -> Any:
    from examples.wallet.schema import EVENTS

    from ekbatan.action import ActionExecutor

    return ActionExecutor(
        namespace="example.wallet",
        registry=registry,
        repositories=[wallets, notifications],
        events=EVENTS,
        clock=clock,
    )


@pytest.fixture
def sharded(sharded_registry: DatabaseRegistry, clock: FixedClock) -> Any:
    """An executor over two databases, routing by the shard encoded in each id."""
    from examples.wallet.model import Wallet
    from examples.wallet.schema import EVENTS, WALLETS

    from ekbatan.action import ActionExecutor
    from ekbatan.repository import Repository
    from ekbatan.sharding import EmbeddedBitsSharding

    repo = Repository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsSharding(), clock=clock
    )
    executor = ActionExecutor(
        namespace="example.wallet",
        registry=sharded_registry,
        repositories=[repo],
        events=EVENTS,
        clock=clock,
    )
    return executor, repo


@pytest.fixture
def outbox(registry: DatabaseRegistry) -> Any:
    """Every outbox row on every shard, oldest first, as plain dicts."""
    from examples.wallet.schema import EVENTS
    from sqlalchemy import select

    def read() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for shard in registry.shards:
            with registry.manager(shard).connection(readonly=True) as conn:
                rows += [
                    dict(r._mapping) | {"shard": shard}
                    for r in conn.execute(select(EVENTS).order_by(EVENTS.c.event_date))
                ]
        return rows

    return read


@pytest.fixture
def pipeline(registry: DatabaseRegistry, clock: FixedClock) -> Any:
    """Fan-out and dispatch over a registry, wired to a fresh handler registry."""
    from examples.wallet.schema import EVENT_NOTIFICATIONS, EVENTS

    from ekbatan.events import EventDispatch, EventFanout, EventHandlerRegistry

    def build(*handlers: Any, **dispatch_options: Any) -> Any:
        subscriptions = EventHandlerRegistry(handlers)
        fanout = EventFanout(
            registry,
            subscriptions,
            events=EVENTS,
            notifications=EVENT_NOTIFICATIONS,
            clock=clock,
        )
        dispatch = EventDispatch(
            registry,
            subscriptions,
            notifications=EVENT_NOTIFICATIONS,
            clock=clock,
            **dispatch_options,
        )
        return fanout, dispatch

    return build


@pytest.fixture
def notifications_in(registry: DatabaseRegistry) -> Any:
    """Count queue rows by state, across every shard."""
    from collections import Counter

    from examples.wallet.schema import EVENT_NOTIFICATIONS
    from sqlalchemy import select

    def count() -> Counter[str]:
        seen: Counter[str] = Counter()
        for shard in registry.shards:
            with registry.manager(shard).connection(readonly=True) as conn:
                seen.update(r[0] for r in conn.execute(select(EVENT_NOTIFICATIONS.c.state)))
        return seen

    return count
