"""A worker process: jobs, no HTTP.

The wiring is the same as any other service's minus the web layer - which is the point. A
deployment usually runs both shapes from one image: the API replicas serve requests, the worker
replicas run this, and the jobs claim their rows against the same databases either way.

Two shards, `global` and `mexico`, each its own database, declared in `ekbatan.yaml`. Three pools
per member: `primaryConfig` for the work, `jobsConfig` for the scheduler - one scheduler for the
whole layout, on the default member's - and `lockConfig` for the stipend job's per-wallet lock.

**Stopping.** As in Java, the job registry lets its running jobs finish however the process
stops - a normal exit, Ctrl-C, or SIGTERM from a container stop - because
`register_shutdown_hook` is on by default. `__main__.run()` has no signal handling of its own.
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
from ekbatan.config import DataSourceConfig, ShardMemberConfig
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob
from ekbatan.locks import KeyedLockProvider, PostgresKeyedLockProvider
from ekbatan.migrations import migrate

from .actions import (
    CreateNotificationAction,
    WalletCloseAction,
    WalletCreateAction,
    WalletDepositMoneyAction,
)
from .handlers import WalletMoneyDepositedEventHandler
from .jobs import WalletReportJob, WalletStipendJob
from .repository import NotificationRepository, WalletRepository

#: Where the migration files live - `V<version>__<description>.sql`. Every shard gets them all.
MIGRATIONS = Path(__file__).resolve().parent / "migrations"

#: The worker's Ekbatan settings - the shard layout, the namespace, the jobs, event handling.
CONFIG_FILE = Path(__file__).resolve().parent / "ekbatan.yaml"


@dataclass(frozen=True, slots=True)
class Worker:
    database_registry: DatabaseRegistry
    wallet_repository: WalletRepository
    notification_repository: NotificationRepository
    executor: ActionExecutor
    lock_provider: KeyedLockProvider
    job_registry: JobRegistry
    #: The pools built from `lockConfig` and `jobsConfig`. The registry closes only the pools it
    #: built, so these two are closed here.
    lock_data_source: DataSource
    jobs_data_source: DataSource

    def start(self) -> None:
        self.job_registry.start()

    def close(self) -> None:
        # Stop taking work first: this waits for the job bodies in flight.
        self.job_registry.stop()
        self.lock_provider.close()
        self.lock_data_source.close()
        self.jobs_data_source.close()
        self.database_registry.close()


def build(
    *,
    config_file: Path = CONFIG_FILE,
    clock: Clock | None = None,
    workers: int = 4,
) -> Worker:
    """Wire the worker on `config_file`. Its `${...}` placeholders are read from the process
    environment. `workers` - the job threads - has no key in Java, whose job bodies run on
    virtual threads."""
    clock = clock or SystemClock()
    ekbatan = load_ekbatan_config(config_file)
    layout = ekbatan.sharding
    # Every shard, one at a time, before the scheduler can start a job against a schema that
    # might not be there yet.
    migrate(layout, MIGRATIONS)

    database_registry = DatabaseRegistry.from_config(layout)
    wallet_repository = WalletRepository(database_registry)
    notification_repository = NotificationRepository(database_registry)
    executor = (
        ActionExecutor.builder()
        .namespace(ekbatan.namespace)
        .database_registry(database_registry)
        .repository_registry(
            RepositoryRegistry.builder()
            .with_model_repository(wallet_repository)
            .with_entity_repository(notification_repository)
            .build()
        )
        .action_registry(
            ActionRegistry.builder()
            .with_action(WalletCreateAction(clock))
            .with_action(WalletDepositMoneyAction(clock, wallet_repository))
            .with_action(WalletCloseAction(clock, wallet_repository))
            .with_action(CreateNotificationAction(clock))
            .build()
        )
        .clock(clock)
        .build()
    )

    default_member = layout.groups[0].members[0]
    lock_data_source = DataSource.from_config(_required(default_member, "lockConfig"))
    lock_provider = PostgresKeyedLockProvider.builder().data_source(lock_data_source).build()
    jobs_data_source = DataSource.from_config(_required(default_member, "jobsConfig"))
    handlers = (
        EventHandlerRegistry.builder()
        .with_handler(WalletMoneyDepositedEventHandler(executor))
        .build()
    )
    events_config = ekbatan.local_event_handler

    return Worker(
        database_registry=database_registry,
        wallet_repository=wallet_repository,
        notification_repository=notification_repository,
        executor=executor,
        lock_provider=lock_provider,
        lock_data_source=lock_data_source,
        jobs_data_source=jobs_data_source,
        job_registry=(
            job_registry()
            .data_source(jobs_data_source)
            .with_jobs(
                [
                    # The event pipeline is two jobs like any other, claimed through the same
                    # table - which is how a cluster fans out and dispatches once rather than
                    # once per replica.
                    EventFanoutJob.builder()
                    .registry(database_registry)
                    .handlers(handlers)
                    .config(events_config)
                    .clock(clock)
                    .build(),
                    EventHandlingJob.builder()
                    .registry(database_registry)
                    .handlers(handlers)
                    .config(events_config)
                    # Off unless `handling.enabled` is set in `ekbatan.yaml`.
                    .enabled(events_config.handling.enabled)
                    .clock(clock)
                    .build(),
                    WalletStipendJob(executor, lock_provider, wallet_repository),
                    WalletReportJob(wallet_repository),
                ]
            )
            .with_config(ekbatan.jobs)
            .workers(workers)
            .build()
        ),
    )


def _required(member: ShardMemberConfig, name: str) -> DataSourceConfig:
    """The named pool's settings, or an error naming where the setting goes."""
    found = member.config_for(name)
    if found is None:
        raise ValueError(f"groups[0].members[0].configs.{name} is required")
    return found
