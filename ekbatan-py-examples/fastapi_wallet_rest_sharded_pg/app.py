"""The wiring - and the only file that knows how many databases there are.

Add a shard by adding a member to a group - or a group - in the layout below. Nothing above this
file changes: the repository already routes by id, and the actions already never name a database.
The layout is Java's: groups of members, each member a named map of datasources.
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
    SingleTableJsonEventPersister,
)
from ekbatan.clock import Clock, SystemClock
from ekbatan.config import (
    DataSourceConfig,
    ShardGroupConfig,
    ShardGroupConfigBuilder,
    ShardMemberConfig,
)
from ekbatan.migrations import migrate

from .actions import GLOBAL, MEXICO, DepositMoneyAction, OpenWalletAction, TransferWithinShardAction
from .repository import WalletRepository
from .schema import EVENTS

#: Where the migration files live - `V<version>__<description>.sql`. Migrations run from files
#: rather than from `create_all`, which is the difference between an example and something you
#: could deploy: `create_all` has no answer for a column that changed.
MIGRATIONS = Path(__file__).resolve().parent / "migrations"


@dataclass(frozen=True, slots=True)
class ShardedWallets:
    registry: DatabaseRegistry
    wallets: WalletRepository
    executor: ActionExecutor

    def close(self) -> None:
        self.registry.close()


#: What each group is called - Java's `ShardGroupConfig.name`, which is required.
GROUP_NAMES = {GLOBAL.group: "global", MEXICO.group: "mexico"}


def build(urls: Mapping[ShardIdentifier, str], *, clock: Clock | None = None) -> ShardedWallets:
    """Wire the layout. `urls` is the whole of it - two databases here, two hundred later."""
    running = clock or SystemClock()

    groups: dict[int, ShardGroupConfigBuilder] = {}
    for shard, url in urls.items():
        groups.setdefault(
            shard.group,
            ShardGroupConfig.builder()
            .group(shard.group)
            .name(GROUP_NAMES.get(shard.group, f"group-{shard.group}")),
        ).with_member(
            ShardMemberConfig.builder()
            .member(shard.member)
            .name(str(shard))
            .primary_config(DataSourceConfig.builder().url(url).build())
            .build()
        )
    layout = (
        ShardingConfig.builder()
        # Where a row goes when its id names a shard this deployment has not configured -
        # which is what lets one environment read another's ids instead of refusing them.
        .default_shard(GLOBAL)
        .groups(group.build() for group in groups.values())
        .build()
    )

    # Every shard, one at a time, fail-fast - and refused outright if a revision that was
    # already applied has since been edited.
    migrate(layout, MIGRATIONS)
    registry = DatabaseRegistry.from_config(layout)

    wallets = WalletRepository(registry, clock=running)
    return ShardedWallets(
        registry=registry,
        wallets=wallets,
        executor=(
            ActionExecutor.builder()
            .namespace("example.fastapi_wallet_rest_sharded_pg")
            .database_registry(registry)
            .repository_registry(
                RepositoryRegistry.builder().with_model_repository(wallets).build()
            )
            .action_registry(
                ActionRegistry.builder()
                .with_action(OpenWalletAction(running))
                .with_action(DepositMoneyAction(wallets, running))
                .with_action(TransferWithinShardAction(wallets, running))
                .build()
            )
            .event_persister(SingleTableJsonEventPersister(registry, EVENTS))
            .clock(running)
            .build()
        ),
    )


__all__ = ["GLOBAL", "MEXICO", "ShardedWallets", "build"]
