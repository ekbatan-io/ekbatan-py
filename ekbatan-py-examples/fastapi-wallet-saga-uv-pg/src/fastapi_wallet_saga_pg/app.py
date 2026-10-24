"""The wiring, all of it in one place.

One database, declared in `ekbatan.yaml`, with three pools: `primaryConfig` for requests and the
saga's steps, `jobsConfig` for the scheduler that runs fan-out and dispatch, and `lockConfig`
for the deposit's keyed lock. The registry builds the first; the other two are this file's to
build with `DataSource.from_config`, and this file's to close.
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
    CompleteTransferAction,
    InitiateTransferAction,
    RefundTransferAction,
    WalletCloseAction,
    WalletCreateAction,
    WalletDepositMoneyAction,
)
from .handlers import TransferFailedEventHandler, TransferInitiatedEventHandler
from .repository import TransferStepRepository, WalletRepository

#: Where the migration files live - `V<version>__<description>.sql`.
MIGRATIONS = Path(__file__).resolve().parent / "migrations"

#: The service's Ekbatan settings - the database, the namespace, event handling.
CONFIG_FILE = Path(__file__).resolve().parent / "ekbatan.yaml"


@dataclass(frozen=True, slots=True)
class Settings:
    """What the service needs from its environment beyond `ekbatan.yaml`."""

    config_file: Path = Path(os.environ.get("WALLET_CONFIG_FILE", CONFIG_FILE))
    job_workers: int = int(os.environ.get("WALLET_JOB_WORKERS", "3"))
    run_migrations: bool = os.environ.get("WALLET_MIGRATE", "1") != "0"


@dataclass(frozen=True, slots=True)
class WalletService:
    """Everything wired together, and the only object the HTTP layer is handed."""

    database_registry: DatabaseRegistry
    wallet_repository: WalletRepository
    transfer_step_repository: TransferStepRepository
    executor: ActionExecutor
    lock_provider: KeyedLockProvider
    job_registry: JobRegistry
    #: The pools built from `lockConfig` and `jobsConfig`. The registry closes only the pools it
    #: built, so these two are closed here.
    lock_data_source: DataSource
    jobs_data_source: DataSource

    def start(self) -> None:
        """Begin polling for jobs. Fan-out and handling are two of them, so this is also what
        starts the saga's later steps."""
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
    # `ekbatan.yaml`'s `${...}` placeholders - the url and the login - are read from the process
    # environment as the file is loaded, each by its name.
    ekbatan = load_ekbatan_config(settings.config_file)
    layout = ekbatan.sharding

    if settings.run_migrations:
        migrate(layout, MIGRATIONS)

    database_registry = DatabaseRegistry.from_config(layout)
    wallet_repository = WalletRepository(database_registry)
    transfer_step_repository = TransferStepRepository(database_registry)
    executor = (
        ActionExecutor.builder()
        .namespace(ekbatan.namespace)
        .database_registry(database_registry)
        .repository_registry(
            RepositoryRegistry.builder()
            .with_model_repository(wallet_repository)
            .with_entity_repository(transfer_step_repository)
            .build()
        )
        .action_registry(
            ActionRegistry.builder()
            .with_action(WalletCreateAction(clock))
            .with_action(WalletDepositMoneyAction(clock, wallet_repository))
            .with_action(WalletCloseAction(clock, wallet_repository))
            .with_action(InitiateTransferAction(clock, wallet_repository))
            .with_action(CompleteTransferAction(clock, wallet_repository, transfer_step_repository))
            .with_action(RefundTransferAction(clock, wallet_repository, transfer_step_repository))
            .build()
        )
        .clock(clock)
        .build()
    )

    member = layout.groups[0].members[0]
    lock_data_source = DataSource.from_config(_required(member, "lockConfig"))
    jobs_data_source = DataSource.from_config(_required(member, "jobsConfig"))
    handlers = (
        EventHandlerRegistry.builder()
        .with_handler(TransferInitiatedEventHandler(executor))
        .with_handler(TransferFailedEventHandler(executor))
        .build()
    )
    events_config = ekbatan.local_event_handler

    return WalletService(
        database_registry=database_registry,
        wallet_repository=wallet_repository,
        transfer_step_repository=transfer_step_repository,
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
