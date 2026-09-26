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
from ekbatan.config import (
    DataSourceConfig,
    ShardGroupConfig,
    ShardGroupConfigBuilder,
    ShardingConfig,
    ShardMemberConfig,
)
from ekbatan.persistence import DatabaseRegistry
from ekbatan.sharding import ShardIdentifier
from fixtures.repositories import NotificationRepository, WalletRepository
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

    groups: dict[int, ShardGroupConfigBuilder] = {}
    for shard, name in names.items():
        groups.setdefault(
            shard.group, ShardGroupConfig.builder().group(shard.group).name(f"group-{shard.group}")
        ).with_member(
            ShardMemberConfig.builder()
            .member(shard.member)
            .name(name)
            .primary_config(DataSourceConfig.builder().url(backend.url(name)).pool_size(5).build())
            .build()
        )
    config = (
        ShardingConfig.builder()
        .default_shard(next(iter(names)))
        .groups(g.build() for g in groups.values())
        .build()
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
    from fixtures.domain import Wallet
    from fixtures.schema import WALLETS

    return WalletRepository(Wallet, WALLETS, registry, clock=clock)


@pytest.fixture
def notifications(registry: DatabaseRegistry, clock: VirtualClock) -> Any:
    from fixtures.domain import Notification
    from fixtures.schema import NOTIFICATIONS

    return NotificationRepository(Notification, NOTIFICATIONS, registry, clock=clock)


@pytest.fixture
def executor(
    registry: DatabaseRegistry, clock: VirtualClock, wallets: Any, notifications: Any
) -> Any:
    from ekbatan.action import ActionExecutor, ActionRegistry, SingleTableJsonEventPersister
    from ekbatan.repository import RepositoryRegistry
    from fixtures.actions import (
        CloseWalletAction,
        CreateWalletAction,
        DepositMoneyAction,
        NotifyAction,
        TransferMoneyAction,
    )
    from fixtures.schema import EVENTS

    # The suite's application actions, registered as an application's build() registers its
    # own: the executor runs an action by type, as Java's does, so the fixture handler and job
    # reach these by name. An action a single test builds for itself goes through
    # `fixtures.executing.run`, which registers it on an executor like this one.
    return (
        ActionExecutor.builder()
        .namespace("example.wallet")
        .database_registry(registry)
        .repository_registry(
            RepositoryRegistry.builder()
            .with_model_repository(wallets)
            .with_entity_repository(notifications)
            .build()
        )
        .action_registry(
            ActionRegistry.builder()
            .with_action(CreateWalletAction(clock))
            .with_action(DepositMoneyAction(wallets, clock))
            .with_action(CloseWalletAction(wallets, clock))
            .with_action(TransferMoneyAction(wallets, clock))
            .with_action(NotifyAction(clock))
            .build()
        )
        .event_persister(SingleTableJsonEventPersister(registry, EVENTS))
        .clock(clock)
        .build()
    )


@pytest.fixture
def sharded(sharded_registry: DatabaseRegistry, clock: VirtualClock) -> Any:
    """An executor over two databases, routing by the shard encoded in each id."""
    from ekbatan.action import ActionExecutor
    from ekbatan.sharding import EmbeddedBitsShardingStrategy
    from fixtures.domain import Wallet
    from fixtures.schema import EVENTS, WALLETS

    repo = WalletRepository(
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


@pytest.fixture(scope="session")
def redis_client() -> Iterator[Any]:
    """One Redis container for the whole session, or a skip.

    Its own container rather than a dialect in `--dialects`: the Redis lock is not a database
    backend and does not vary by one. A run with `--dialects=sqlite` still exercises it, which
    is what keeps it from rotting.
    """
    redis = pytest.importorskip("redis")
    from tests.backends import docker_is_available

    if not docker_is_available():
        pytest.skip("the Redis lock needs a container and Docker is not reachable")

    from testcontainers.community.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as container:
        client = redis.Redis(
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(6379)),
            decode_responses=True,
        )
        client.ping()
        yield client
        client.close()
