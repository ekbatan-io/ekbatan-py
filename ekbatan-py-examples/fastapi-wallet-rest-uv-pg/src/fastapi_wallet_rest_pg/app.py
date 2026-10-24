"""The wiring, all of it in one place - and the only file that knows how many databases there
are.

Two shards, `global` and `mexico`, each its own database, declared in `ekbatan.yaml`. Add a shard
by adding a group or a member there: the repository already routes by id, and the actions never
name a database.

Three separate pools per member, which is the part worth copying into a real deployment:

**Requests** get the main pool, `primaryConfig`. Sized for how many requests you serve at once.

**Jobs** get their own, `jobsConfig`. db-scheduler polls continuously and writes a heartbeat from
a housekeeping thread; sharing the request pool means a burst of traffic can starve that
heartbeat, and an execution whose heartbeat stops is declared dead and run again somewhere
else. One scheduler runs for the whole layout, on the default member's `jobsConfig`; the
fan-out and handling jobs it runs read and write every shard through the registry.

**Locks** get a third, `lockConfig`. A keyed lock holds its connection for as long as the lease
is held - that is what the lock *is*. One database holds every key: a lock is about a wallet's
key, not its rows, so the deposit takes it on the default member whichever shard the wallet is
on.

The registry builds the request pools; the other two are this file's to build with
`DataSource.from_config`, and this file's to close.
"""

from __future__ import annotations

import os
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
from .repository import NotificationRepository, WalletRepository

#: Where the migration files live - `V<version>__<description>.sql`. Every shard gets them all.
MIGRATIONS = Path(__file__).resolve().parent / "migrations"

#: The service's Ekbatan settings - the shard layout, the namespace, event handling - in the same
#: keys as Java's `application.yml`.
CONFIG_FILE = Path(__file__).resolve().parent / "ekbatan.yaml"


@dataclass(frozen=True, slots=True)
class Settings:
    """What the service needs from its environment beyond `ekbatan.yaml`.

    Read from the environment so the same image runs in every environment, and defaulted so
    that running the module works against local servers with no setup.
    """

    config_file: Path = Path(os.environ.get("WALLET_CONFIG_FILE", CONFIG_FILE))
    #: Threads running job bodies. Each worker is a real OS thread, so the number is chosen
    #: rather than unbounded - size it for how many jobs can be due at once.
    job_workers: int = int(os.environ.get("WALLET_JOB_WORKERS", "3"))
    run_migrations: bool = os.environ.get("WALLET_MIGRATE", "1") != "0"


@dataclass(frozen=True, slots=True)
class WalletService:
    """Everything wired together, and the only object the HTTP layer is handed."""

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
        """Begin polling for jobs. Fan-out and handling are two of them, so this is also what
        starts the event pipeline - across every shard, from one scheduler."""
        self.job_registry.start()

    def close(self) -> None:
        # Order matters: stop taking work before closing what the work runs on.
        self.job_registry.stop()
        self.lock_provider.close()
        self.lock_data_source.close()
        self.jobs_data_source.close()
        self.database_registry.close()


def build(settings: Settings | None = None, *, clock: Clock | None = None) -> WalletService:
    """Wire the service. Nothing here is specific to HTTP."""
    settings = settings or Settings()
    clock = clock or SystemClock()

    # `ekbatan.yaml`'s `${...}` placeholders - the urls and the login - are read from the
    # process environment as the file is loaded, each by its name.
    ekbatan = load_ekbatan_config(settings.config_file)
    layout = ekbatan.sharding

    if settings.run_migrations:
        # Every shard, one at a time, fail-fast - and refused outright if a revision that was
        # already applied has since been edited. Run before anything opens a pool against a
        # schema that might not be there yet.
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

    # The default member's other two pools, as `ekbatan.yaml` declares them.
    default_member = layout.groups[0].members[0]
    lock_data_source = DataSource.from_config(_required(default_member, "lockConfig"))
    jobs_data_source = DataSource.from_config(_required(default_member, "jobsConfig"))

    handlers = (
        EventHandlerRegistry.builder()
        .with_handler(WalletMoneyDepositedEventHandler(executor))
        .build()
    )
    events_config = ekbatan.local_event_handler

    return WalletService(
        database_registry=database_registry,
        wallet_repository=wallet_repository,
        notification_repository=notification_repository,
        executor=executor,
        lock_provider=PostgresKeyedLockProvider.builder().data_source(lock_data_source).build(),
        lock_data_source=lock_data_source,
        jobs_data_source=jobs_data_source,
        job_registry=(
            job_registry()
            .data_source(jobs_data_source)
            .with_jobs(
                [
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
                ]
            )
            .with_config(ekbatan.jobs)
            .workers(settings.job_workers)
            .build()
        ),
    )


def _required(member: ShardMemberConfig, name: str) -> DataSourceConfig:
    """The named pool's settings, or an error naming where the setting goes."""
    found = member.config_for(name)
    if found is None:
        raise ValueError(f"groups[0].members[0].configs.{name} is required")
    return found
