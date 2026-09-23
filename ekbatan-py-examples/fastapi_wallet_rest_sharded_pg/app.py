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
)
from ekbatan.clock import Clock, SystemClock
from ekbatan.config import DataSourceConfig, ShardGroupConfig, ShardMemberConfig
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


#: What each group is called - Java's `ShardGroupConfig.name`, which is required.
GROUP_NAMES = {GLOBAL.group: "global", MEXICO.group: "mexico"}


def build(urls: Mapping[ShardIdentifier, str], *, clock: Clock | None = None) -> ShardedWallets:
    """Wire the layout. `urls` is the whole of it - two databases here, two hundred later."""
    running = clock or SystemClock()

    members: dict[int, list[ShardMemberConfig]] = {}
    for shard, url in urls.items():
        members.setdefault(shard.group, []).append(
            ShardMemberConfig(
                member=shard.member,
                name=str(shard),
                configs={"primaryConfig": DataSourceConfig(url=url)},
            )
        )
    layout = ShardingConfig(
        # Where a row goes when its id names a shard this deployment has not configured -
        # which is what lets one environment read another's ids instead of refusing them.
        default_shard=GLOBAL,
        groups=tuple(
            ShardGroupConfig(
                group=group, name=GROUP_NAMES.get(group, f"group-{group}"), members=tuple(m)
            )
            for group, m in members.items()
        ),
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
                .with_action(OpenWalletAction(running))
                .with_action(DepositMoneyAction(wallets, running))
                .with_action(TransferWithinShardAction(wallets, running))
                .build()
            ),
            events=EVENTS,
            clock=running,
        ),
    )


__all__ = ["GLOBAL", "MEXICO", "ShardedWallets", "build"]
