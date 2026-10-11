"""The wiring - and the only file that knows how many databases there are.

Add a shard by adding a member to a group - or a group - in `ekbatan.yaml`. No code changes: the
repository already routes by id, and the actions already never name a database. The layout is
Java's, in the same keys as its `application.yml`: groups of members, each member a named map of
datasources.
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
    SingleTableJsonEventPersister,
)
from ekbatan.clock import Clock, SystemClock
from ekbatan.config_files import load_ekbatan_config
from ekbatan.migrations import migrate

from .actions import GLOBAL, MEXICO, DepositMoneyAction, OpenWalletAction, TransferWithinShardAction
from .repository import WalletRepository
from .schema import EVENTS

#: Where the migration files live - `V<version>__<description>.sql`. Migrations run from files
#: rather than from `create_all`, which is the difference between an example and something you
#: could deploy: `create_all` has no answer for a column that changed.
MIGRATIONS = Path(__file__).resolve().parent / "migrations"

#: The service's Ekbatan settings - the shard layout and the namespace - in the same keys as the
#: Java examples' `application.yml`.
CONFIG_FILE = Path(__file__).resolve().parent / "ekbatan.yaml"


@dataclass(frozen=True, slots=True)
class ShardedWallets:
    registry: DatabaseRegistry
    wallets: WalletRepository
    executor: ActionExecutor

    def close(self) -> None:
        self.registry.close()


def build(
    environ: Mapping[str, str] | None = None,
    *,
    config_file: Path = CONFIG_FILE,
    clock: Clock | None = None,
) -> ShardedWallets:
    """Wire the layout `config_file` describes - two databases here, two hundred later.
    `environ` fills its `${...}` placeholders, `os.environ` when left out."""
    running = clock or SystemClock()
    ekbatan = load_ekbatan_config(config_file, environ=environ)
    layout = ekbatan.sharding

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
            .namespace(ekbatan.namespace)
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
