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

`unit/` needs none of this and asks for none of it: nothing there takes a database fixture, so
nothing there is parametrised, and the whole of it runs in about a second.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

# Only the tests' own directory. The examples are not on the path and are not importable from
# here on purpose - see tests/test_structure.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backends import Backend, requested_dialects, start
from ekbatan.clock import VirtualClock
from ekbatan.config import DataSourceConfig, ShardConfig, ShardingConfig
from ekbatan.persistence import DatabaseRegistry
from ekbatan.sharding import ShardIdentifier
from fixtures.schema import metadata

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
def clock() -> VirtualClock:
    return VirtualClock(datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC))


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
def wallets(registry: DatabaseRegistry, clock: VirtualClock) -> Any:
    from ekbatan.repository import Repository
    from fixtures.domain import Wallet
    from fixtures.schema import WALLETS

    return Repository(Wallet, WALLETS, registry, clock=clock)


@pytest.fixture
def notifications(registry: DatabaseRegistry, clock: VirtualClock) -> Any:
    from ekbatan.repository import Repository
    from fixtures.domain import Notification
    from fixtures.schema import NOTIFICATIONS

    return Repository(Notification, NOTIFICATIONS, registry, clock=clock)


@pytest.fixture
def executor(
    registry: DatabaseRegistry, clock: VirtualClock, wallets: Any, notifications: Any
) -> Any:
    from ekbatan.action import ActionExecutor
    from fixtures.schema import EVENTS

    return ActionExecutor(
        namespace="example.wallet",
        registry=registry,
        repositories=[wallets, notifications],
        events=EVENTS,
        clock=clock,
    )


@pytest.fixture
def sharded(sharded_registry: DatabaseRegistry, clock: VirtualClock) -> Any:
    """An executor over two databases, routing by the shard encoded in each id."""
    from ekbatan.action import ActionExecutor
    from ekbatan.repository import Repository
    from ekbatan.sharding import EmbeddedBitsShardingStrategy
    from fixtures.domain import Wallet
    from fixtures.schema import EVENTS, WALLETS

    repo = Repository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
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
    """Every outbox row on every shard, as plain dicts, ordered as Java orders them.

    By ``event_date`` and nothing else, which is what `EventEntityRepository` does. That is a
    total order across invocations under a real clock and no order at all *within* one - every
    row of an invocation carries its completion instant. Tests here run on a frozen clock, so
    even the across-invocation part ties: select the row you mean by `action_name` or
    `action_id` rather than by position, exactly as Java's own persister test does.
    """
    from sqlalchemy import select

    from fixtures.schema import EVENTS

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
def pipeline(registry: DatabaseRegistry, clock: VirtualClock) -> Any:
    """Fan-out and dispatch over a registry, wired to a fresh handler registry."""
    from ekbatan.events import EventDispatch, EventFanout, EventHandlerRegistry
    from fixtures.schema import EVENT_NOTIFICATIONS, EVENTS

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

    from sqlalchemy import select

    from fixtures.schema import EVENT_NOTIFICATIONS

    def count() -> Counter[str]:
        seen: Counter[str] = Counter()
        for shard in registry.shards:
            with registry.manager(shard).connection(readonly=True) as conn:
                seen.update(r[0] for r in conn.execute(select(EVENT_NOTIFICATIONS.c.state)))
        return seen

    return count
