"""Test fixtures.

These run on SQLite, which is not a dialect this framework supports in production. It is here
because it needs no daemon, so the whole suite runs in a second and the feedback loop stays
tight enough to actually use. Everything dialect-specific - RETURNING, batch rowcount, lock
primitives, JSON columns - is asserted against Postgres, MySQL and MariaDB by the conformance
suite in ekbatan-spec, which is where those questions belong.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples.wallet.schema import metadata

from ekbatan.clock import FixedClock
from ekbatan.config import DataSourceConfig, ShardConfig, ShardingConfig
from ekbatan.persistence import DatabaseRegistry
from ekbatan.sharding import ShardIdentifier

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC))


@pytest.fixture
def registry(tmp_path: Path) -> Iterator[DatabaseRegistry]:
    """One database."""
    config = ShardingConfig.single(f"sqlite:///{tmp_path / 'wallet.db'}")
    with DatabaseRegistry.from_config(config) as reg:
        metadata.create_all(reg.default.primary)
        yield reg


@pytest.fixture
def sharded_registry(tmp_path: Path) -> Iterator[DatabaseRegistry]:
    """Two databases, so routing and isolation are real rather than simulated."""
    config = ShardingConfig(
        shards=(
            ShardConfig(
                shard=GLOBAL,
                primary=DataSourceConfig(url=f"sqlite:///{tmp_path / 'global.db'}"),
                name="global",
            ),
            ShardConfig(
                shard=MEXICO,
                primary=DataSourceConfig(url=f"sqlite:///{tmp_path / 'mexico.db'}"),
                name="mexico",
            ),
        ),
        default_shard=GLOBAL,
    )
    with DatabaseRegistry.from_config(config) as reg:
        for shard in reg.shards:
            metadata.create_all(reg.manager(shard).primary)
        yield reg


@pytest.fixture
def wallets(registry: DatabaseRegistry, clock: FixedClock):
    from examples.wallet.model import Wallet
    from examples.wallet.schema import WALLETS

    from ekbatan.repository import Repository

    return Repository(Wallet, WALLETS, registry, clock=clock)


@pytest.fixture
def notifications(registry: DatabaseRegistry, clock: FixedClock):
    from examples.wallet.model import Notification
    from examples.wallet.schema import NOTIFICATIONS

    from ekbatan.repository import Repository

    return Repository(Notification, NOTIFICATIONS, registry, clock=clock)


@pytest.fixture
def executor(registry: DatabaseRegistry, clock: FixedClock, wallets, notifications):
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
def outbox(registry: DatabaseRegistry):
    """Every outbox row on every shard, oldest first, as plain dicts."""
    from examples.wallet.schema import EVENTS
    from sqlalchemy import select

    def read() -> list[dict]:
        rows: list[dict] = []
        for shard in registry.shards:
            with registry.manager(shard).connection(readonly=True) as conn:
                rows += [dict(r._mapping) | {"shard": shard} for r in conn.execute(select(EVENTS))]
        return rows

    return read


@pytest.fixture
def sharded(sharded_registry: DatabaseRegistry, clock: FixedClock):
    """An executor over two databases, routing by the shard encoded in each id."""
    from examples.wallet.model import Wallet
    from examples.wallet.schema import EVENTS, WALLETS

    from ekbatan.action import ActionExecutor
    from ekbatan.repository import Repository
    from ekbatan.sharding import EmbeddedBitsSharding

    wallets = Repository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsSharding(), clock=clock
    )
    executor = ActionExecutor(
        namespace="example.wallet",
        registry=sharded_registry,
        repositories=[wallets],
        events=EVENTS,
        clock=clock,
    )
    return executor, wallets
