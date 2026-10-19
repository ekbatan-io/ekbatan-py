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

import importlib
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

# Only the tests' own directory. The examples are not on the path and are not importable from
# here on purpose - see tests/test_structure.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import modes
from backends import ALL_DIALECTS, Backend, requested_dialects, start
from ekbatan.aio import AsyncDatabaseRegistry
from ekbatan.clock import VirtualClock
from ekbatan.config import (
    ShardGroupConfig,
    ShardGroupConfigBuilder,
    ShardingConfig,
    ShardMemberConfig,
)
from ekbatan.persistence import DatabaseRegistry
from ekbatan.sharding import ShardIdentifier
from fixtures.logins import data_source
from fixtures.repositories import NotificationRepository, WalletRepository
from fixtures.schema import metadata
from modes import Portal

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--dialects",
        default=None,
        help="comma-separated: sqlite, postgres, mysql, mariadb - or 'all'. "
        "Also readable from EKBATAN_DIALECTS.",
    )
    parser.addoption(
        "--async-driver",
        default=None,
        help="the driver the async runs use on PostgreSQL: asyncpg (the default) or "
        "psycopg_async, psycopg's own async mode. Also readable from EKBATAN_ASYNC_DRIVER.",
    )


#: The PostgreSQL drivers an async run may be asked to use.
ASYNC_PG_DRIVERS = ("asyncpg", "psycopg_async")


def pytest_configure(config: Any) -> None:
    """``--async-driver=psycopg_async``: every async run on PostgreSQL connects through
    psycopg's async mode - as a url naming ``postgresql+psycopg_async://`` would - by making it
    the default for the session. Everything else about the run is unchanged."""
    import os

    driver = config.getoption("--async-driver") or os.environ.get("EKBATAN_ASYNC_DRIVER")
    if not driver:
        return
    if driver not in ASYNC_PG_DRIVERS:
        raise pytest.UsageError(f"--async-driver must be one of {ASYNC_PG_DRIVERS}, not {driver!r}")
    from types import MappingProxyType

    import ekbatan.aio
    import ekbatan.aio.persistence

    chosen = MappingProxyType(dict(ekbatan.aio.ASYNC_DEFAULT_DRIVERS) | {"postgres": driver})
    ekbatan.aio.ASYNC_DEFAULT_DRIVERS = chosen  # type: ignore[misc]
    ekbatan.aio.persistence.ASYNC_DEFAULT_DRIVERS = chosen  # type: ignore[misc]


def pytest_report_header(config: Any) -> str | None:
    from ekbatan.aio import ASYNC_DEFAULT_DRIVERS

    return f"ekbatan.aio on PostgreSQL: {ASYNC_DEFAULT_DRIVERS['postgres']}"


def pytest_generate_tests(metafunc: Any) -> None:
    # A module that runs on `ekbatan.aio` too says so with `MODES = ("sync", "async")`; see
    # tests/modes.py. Every other module runs once, sync, under its usual ids.
    modes = getattr(metafunc.module, "MODES", None)
    if modes and "mode" in metafunc.fixturenames:
        metafunc.parametrize("mode", modes, indirect=True)
    # A module whose handlers run both ways says so with `HANDLER_KINDS = ("sync", "async")`:
    # in the async half `pipeline` registers each handler as an async one (fixtures.handlers).
    kinds = getattr(metafunc.module, "HANDLER_KINDS", None)
    if kinds and "handler_kind" in metafunc.fixturenames:
        metafunc.parametrize("handler_kind", kinds, indirect=True)
    if "dialect" in metafunc.fixturenames:
        chosen = requested_dialects(metafunc.config)
        metafunc.parametrize("dialect", chosen, scope="session", ids=list(chosen))
        # A module whose cases differ per database - schemagen's type table - names them in
        # `CASES_BY_DIALECT`; a test asking for `case_id` gets each case on the databases that
        # have it, and the other pairs are dropped below. Not a joint (dialect, case)
        # parameter: that makes every pair a new session-scoped `dialect`, and the session's
        # container is stopped and started again for each one.
        cases = getattr(metafunc.module, "CASES_BY_DIALECT", None)
        if cases is not None and "case_id" in metafunc.fixturenames:
            ids = list(dict.fromkeys(c.id for d in chosen for c in cases.get(d, ())))
            metafunc.parametrize("case_id", ids)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    """Drop the (database, case) pairs a database does not have - see `pytest_generate_tests` -
    and keep each database's tests together: with `case_id` beside `dialect`, pytest's own
    ordering interleaves the databases, and every switch stops one container and starts it again.
    """
    keep, drop = [], []
    for item in items:
        params = getattr(getattr(item, "callspec", None), "params", {})
        cases = getattr(getattr(item, "module", None), "CASES_BY_DIALECT", None)
        if (
            cases is not None
            and "case_id" in params
            and params["case_id"] not in {c.id for c in cases.get(params["dialect"], ())}
        ):
            drop.append(item)
            continue
        keep.append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
    order = {d: i for i, d in enumerate(ALL_DIALECTS)}
    slots = [i for i, item in enumerate(keep) if _has_cases(item)]
    grouped = sorted(
        (keep[i] for i in slots), key=lambda item: order.get(item.callspec.params["dialect"], 0)
    )
    for slot, item in zip(slots, grouped, strict=True):
        keep[slot] = item
    items[:] = keep


def _has_cases(item: Any) -> bool:
    params = getattr(getattr(item, "callspec", None), "params", {})
    return "dialect" in params and hasattr(getattr(item, "module", None), "CASES_BY_DIALECT")


@pytest.fixture(scope="session")
def backend(dialect: str, tmp_path_factory: Any) -> Iterator[Backend]:
    """One container - or one directory - per dialect, for the whole session."""
    started = start(dialect, tmp_path_factory.mktemp("ekbatan"))
    try:
        yield started
    finally:
        started.stop()


def under_gevent() -> bool:
    """Whether `make test-gevent` monkey-patched this process."""
    monkey = sys.modules.get("gevent.monkey")
    return bool(monkey is not None and monkey.is_module_patched("threading"))


#: Why an async run is skipped under gevent. Measured: aiosqlite's worker threads become
#: greenlets that block the event loop's own thread, and units of work stall at the pool.
#: gevent and asyncio are two answers to the same question; a service picks one.
NOT_UNDER_GEVENT = "ekbatan.aio does not run under gevent's monkey-patching: a service picks one"


@pytest.fixture
def mode(request: Any) -> str:
    """``"sync"``, or ``"async"`` in the second run of a module with ``MODES``."""
    chosen = str(getattr(request, "param", "sync"))
    if chosen == "async" and under_gevent():
        pytest.skip(NOT_UNDER_GEVENT)
    return chosen


@pytest.fixture
def portal(mode: str) -> Iterator[Portal | None]:
    """The event loop an async run's objects live on; ``None`` in a sync run."""
    if mode != "async":
        yield None
        return
    made = Portal()
    modes.CURRENT.append(made)
    try:
        yield made
    finally:
        modes.CURRENT.pop()
        made.close()


@pytest.fixture(autouse=True)
def _async_names(request: Any, mode: str, monkeypatch: Any) -> None:
    """In an async run, rebind the module's names to their async twins - see tests/modes.py."""
    if mode != "async" or not getattr(request.module, "MODES", None):
        return
    request.getfixturevalue("portal")
    modes.unwrap_on_add(monkeypatch)
    modes.rebind(request.module, monkeypatch)
    for name in modes.SUPPORT_MODULES:
        modes.rebind(importlib.import_module(name), monkeypatch)


def of_mode(mode: str, sync: Any) -> Any:
    """``sync`` in a sync run; its async twin, made blocking, in an async run."""
    return sync if mode == "sync" else modes.portaled(modes.TWINS[sync])


@pytest.fixture
def clock() -> VirtualClock:
    return VirtualClock(datetime(2026, 1, 1, 10, 0, 0))


def _registry(
    backend: Backend,
    names: dict[ShardIdentifier, str],
    *,
    kind: Any = DatabaseRegistry,
    driver: str | None = None,
) -> Any:
    """A registry over freshly emptied databases.

    ``kind`` is for ``ekbatan.aio``'s runs: an ``AsyncDatabaseRegistry`` over the same databases.
    The urls stay the sync ones - the async registry connects through each database's default
    async driver in their place - and ``driver`` names a driver of its own instead. The schema
    is still migrated and emptied on the sync driver, as everywhere else.
    """
    for name in names.values():
        backend.prepare(name, metadata)
        backend.truncate(name, metadata)

    def url(name: str) -> str:
        plain = backend.url(name)
        if driver is None:
            return plain
        scheme, _, rest = plain.partition("://")
        return f"{scheme.split('+', 1)[0]}+{driver}://{rest}"

    groups: dict[int, ShardGroupConfigBuilder] = {}
    for shard, name in names.items():
        groups.setdefault(
            shard.group, ShardGroupConfig.builder().group(shard.group).name(f"group-{shard.group}")
        ).with_member(
            ShardMemberConfig.builder()
            .member(shard.member)
            .name(name)
            .primary_config(data_source(url(name), pool_size=5))
            .build()
        )
    config = (
        ShardingConfig.builder()
        .default_shard(next(iter(names)))
        .groups(g.build() for g in groups.values())
        .build()
    )
    return kind.from_config(config)


def _either_registry(
    backend: Backend, names: dict[ShardIdentifier, str], portal: Portal | None
) -> Iterator[Any]:
    """A sync registry, or - in an async run - an async one over the same databases, through
    each database's default async driver, made blocking."""
    if portal is None:
        with _registry(backend, names) as reg:
            yield reg
        return
    made = _registry(backend, names, kind=AsyncDatabaseRegistry)
    try:
        yield portal.wrap(made)
    finally:
        portal.call(made.aclose())


@pytest.fixture
def registry(backend: Backend, portal: Portal | None) -> Iterator[DatabaseRegistry]:
    """One database."""
    yield from _either_registry(backend, {GLOBAL: "ekbatan_main"}, portal)


@pytest.fixture
def sharded_registry(backend: Backend, portal: Portal | None) -> Iterator[DatabaseRegistry]:
    """Two databases, so routing and isolation are real rather than simulated."""
    yield from _either_registry(
        backend, {GLOBAL: "ekbatan_global", MEXICO: "ekbatan_mexico"}, portal
    )


@pytest.fixture
def wallets(registry: DatabaseRegistry, clock: VirtualClock, mode: str) -> Any:
    from fixtures.domain import Wallet
    from fixtures.schema import WALLETS

    return of_mode(mode, WalletRepository)(Wallet, WALLETS, registry, clock=clock)


@pytest.fixture
def notifications(registry: DatabaseRegistry, clock: VirtualClock, mode: str) -> Any:
    from fixtures.domain import Notification
    from fixtures.schema import NOTIFICATIONS

    return of_mode(mode, NotificationRepository)(Notification, NOTIFICATIONS, registry, clock=clock)


@pytest.fixture
def executor(
    registry: DatabaseRegistry, clock: VirtualClock, wallets: Any, notifications: Any, mode: str
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
    def of(sync: Any) -> Any:
        return of_mode(mode, sync)

    return (
        of(ActionExecutor)
        .builder()
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
            .with_action(of(CreateWalletAction)(clock))
            .with_action(of(DepositMoneyAction)(wallets, clock))
            .with_action(of(CloseWalletAction)(wallets, clock))
            .with_action(of(TransferMoneyAction)(wallets, clock))
            .with_action(of(NotifyAction)(clock))
            .build()
        )
        .event_persister(of(SingleTableJsonEventPersister)(registry, EVENTS))
        .clock(clock)
        .build()
    )


@pytest.fixture
def sharded(sharded_registry: DatabaseRegistry, clock: VirtualClock, mode: str) -> Any:
    """An executor over two databases, routing by the shard encoded in each id."""
    from ekbatan.action import ActionExecutor
    from ekbatan.sharding import EmbeddedBitsShardingStrategy
    from fixtures.domain import Wallet
    from fixtures.schema import EVENTS, WALLETS

    repo = of_mode(mode, WalletRepository)(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
    )
    executor = of_mode(mode, ActionExecutor)(
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
def handler_kind(request: Any) -> str:
    """``"sync"``, or ``"async"`` in the second run of a module with ``HANDLER_KINDS``."""
    chosen = str(getattr(request, "param", "sync"))
    if chosen == "async" and under_gevent():
        pytest.skip(NOT_UNDER_GEVENT)
    return chosen


@pytest.fixture
def pipeline(registry: DatabaseRegistry, clock: VirtualClock, handler_kind: str) -> Iterator[Any]:
    """Fan-out and dispatch over a registry, wired to a fresh handler registry - each handler
    made async first in the async half of a module with ``HANDLER_KINDS``."""
    from ekbatan.events import EventDispatch, EventFanout, EventHandlerRegistry
    from fixtures.handlers import of_kind
    from fixtures.schema import EVENT_NOTIFICATIONS, EVENTS

    built: list[Any] = []

    def build(*handlers: Any, **dispatch_options: Any) -> Any:
        subscriptions = EventHandlerRegistry([of_kind(handler_kind, h) for h in handlers])
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
        built.append(dispatch)
        return fanout, dispatch

    yield build
    for dispatch in built:
        dispatch.close()


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
