"""The wiring - and the only file that knows how many databases there are.

Add a shard by adding a member to a group - or a group - in `ekbatan.yaml`. No code changes: the
repository already routes by id, and the actions already never name a database. The layout is
Java's, in the same keys as its `application.yml`: groups of members, each member a named map of
datasources.

**Locks** get the global member's `lockConfig`. A lease holds its connection for as long as it is
held. One database holds every key: a lock is about a wallet's key, not its rows, so the deposit
takes it on the global member whichever shard the wallet is on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ekbatan import (
    ActionExecutor,
    ActionRegistry,
    DatabaseRegistry,
    DataSource,
    RepositoryRegistry,
)
from ekbatan.clock import Clock, SystemClock
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob
from ekbatan.locks import KeyedLockProvider, PostgresKeyedLockProvider
from ekbatan.migrations import migrate

from .actions import (
    GLOBAL,
    MEXICO,
    DepositMoneyAction,
    OpenWalletAction,
    RaiseAlertAction,
    TransferWithinShardAction,
)
from .handlers import MoneyDepositedEventHandler
from .repository import AlertRepository, WalletRepository

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
    alerts: AlertRepository
    executor: ActionExecutor
    #: The deposit's keyed lock, over the global member's `lockConfig`.
    locks: KeyedLockProvider
    jobs: JobRegistry
    #: Built from the global member's `lockConfig` and the default shard's `jobsConfig`. The
    #: registry owns only the pools it built, so these two are closed here.
    lock_data_source: DataSource
    jobs_data_source: DataSource

    def start(self) -> None:
        """Begin polling. Fan-out and handling are two of the jobs, so this is what starts the
        event pipeline - across every shard, from one scheduler."""
        self.jobs.start()

    def close(self) -> None:
        # Stop taking work before closing what the work runs on.
        self.jobs.stop()
        self.locks.close()
        self.lock_data_source.close()
        self.jobs_data_source.close()
        self.registry.close()


def build(
    *,
    config_file: Path = CONFIG_FILE,
    clock: Clock | None = None,
) -> ShardedWallets:
    """Wire the layout `config_file` describes - two databases here, two hundred later. Its
    `${...}` placeholders are read from the process environment."""
    running = clock or SystemClock()
    # `ekbatan.yaml`'s `${...}` placeholders - the urls and the login - are read from the
    # process environment as the file is loaded, each by its name.
    ekbatan = load_ekbatan_config(config_file)
    layout = ekbatan.sharding

    # Every shard, one at a time, fail-fast - and refused outright if a revision that was
    # already applied has since been edited.
    migrate(layout, MIGRATIONS)
    registry = DatabaseRegistry.from_config(layout)

    wallets = WalletRepository(registry, clock=running)
    alerts = AlertRepository(registry, clock=running)
    executor = (
        ActionExecutor.builder()
        .namespace(ekbatan.namespace)
        .database_registry(registry)
        .repository_registry(
            RepositoryRegistry.builder()
            .with_model_repository(wallets)
            .with_entity_repository(alerts)
            .build()
        )
        .action_registry(
            ActionRegistry.builder()
            .with_action(OpenWalletAction(running))
            .with_action(DepositMoneyAction(wallets, running))
            .with_action(TransferWithinShardAction(wallets, running))
            .with_action(RaiseAlertAction(running))
            .build()
        )
        .clock(running)
        .build()
    )

    # One scheduler for the whole layout, on the default shard's `jobsConfig` pool. The fan-out
    # and handling jobs it runs read and write every shard through the registry - one job each,
    # not one per shard.
    default = layout.groups[0].members[0]
    jobs_config = default.config_for("jobsConfig")
    if jobs_config is None:
        raise ValueError("groups[0].members[0].configs.jobsConfig is required")
    jobs_data_source = DataSource.from_config(jobs_config)
    lock_config = default.config_for("lockConfig")
    if lock_config is None:
        raise ValueError("groups[0].members[0].configs.lockConfig is required")
    lock_data_source = DataSource.from_config(lock_config)
    handlers = (
        EventHandlerRegistry.builder().with_handler(MoneyDepositedEventHandler(executor)).build()
    )
    events_config = ekbatan.local_event_handler

    return ShardedWallets(
        registry=registry,
        wallets=wallets,
        alerts=alerts,
        executor=executor,
        locks=PostgresKeyedLockProvider.builder().data_source(lock_data_source).build(),
        lock_data_source=lock_data_source,
        jobs_data_source=jobs_data_source,
        jobs=(
            job_registry()
            .data_source(jobs_data_source)
            .with_jobs(
                [
                    EventFanoutJob.builder()
                    .registry(registry)
                    .handlers(handlers)
                    .config(events_config)
                    .clock(running)
                    .build(),
                    EventHandlingJob.builder()
                    .registry(registry)
                    .handlers(handlers)
                    .config(events_config)
                    # Java's DI makes this job only if `handling.enabled`; this is that check.
                    .enabled(events_config.handling.enabled)
                    .clock(running)
                    .build(),
                ]
            )
            .with_config(ekbatan.jobs)
            .build()
        ),
    )


__all__ = ["GLOBAL", "MEXICO", "ShardedWallets", "build"]
