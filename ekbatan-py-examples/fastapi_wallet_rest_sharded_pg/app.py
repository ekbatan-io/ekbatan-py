"""The wiring - and the only file that knows how many databases there are.

Add a shard by adding a `ShardConfig`. Nothing above this file changes: the repository already
routes by id, and the actions already never name a database.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ekbatan import (
    ActionExecutor,
    ActionRegistry,
    DatabaseRegistry,
    RepositoryRegistry,
    ShardIdentifier,
    ShardingConfig,
)
from ekbatan.clock import Clock, SystemClock
from ekbatan.config import DataSourceConfig, ShardConfig
from ekbatan.migrations import migrate

from .actions import GLOBAL, MEXICO, DepositMoneyAction, OpenWalletAction, TransferWithinShardAction
from .repository import WalletRepository
from .schema import EVENTS

#: Where the Alembic revisions live. Migrations run from files, not from `create_all`, which is
#: the difference between an example and something you could deploy: `create_all` has no answer
#: for a column that changed.
MIGRATIONS = Path(__file__).resolve().parent / "migrations"


@dataclass(frozen=True, slots=True)
class ShardedWallets:
    registry: DatabaseRegistry
    wallets: WalletRepository
    executor: ActionExecutor

    def close(self) -> None:
        self.registry.close()


def build(urls: Mapping[ShardIdentifier, str], *, clock: Clock | None = None) -> ShardedWallets:
    """Wire the layout. `urls` is the whole of it - two databases here, two hundred later."""
    running = clock or SystemClock()

    layout = ShardingConfig(
        shards=tuple(
            ShardConfig(shard=shard, primary=DataSourceConfig(url=url), name=str(shard))
            for shard, url in urls.items()
        ),
        # Where a row goes when its id names a shard this deployment has not configured -
        # which is what lets one environment read another's ids instead of refusing them.
        default_shard=GLOBAL,
    )

    # Every shard, one at a time, fail-fast - and refused outright if a revision that was
    # already applied has since been edited.
    migrate(layout, MIGRATIONS)
    registry = DatabaseRegistry.from_config(layout)

    wallets = WalletRepository(registry, clock=running)
    return ShardedWallets(
        registry=registry,
        wallets=wallets,
        executor=ActionExecutor(
            namespace="example.fastapi_wallet_rest_sharded_pg",
            registry=registry,
            repositories=RepositoryRegistry.builder().with_model_repository(wallets).build(),
            actions=(
                ActionRegistry.builder()
                .with_action(OpenWalletAction())
                .with_action(DepositMoneyAction(wallets))
                .with_action(TransferWithinShardAction(wallets))
                .build()
            ),
            events=EVENTS,
            clock=running,
        ),
    )


__all__ = ["GLOBAL", "MEXICO", "ShardedWallets", "build"]
