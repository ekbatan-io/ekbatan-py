"""The wiring, all of it in one place: which database, which pools, which handlers, which jobs.

Two pools, both declared on the shard member in `ekbatan.yaml`: `primaryConfig` for requests,
which the registry builds, and `jobsConfig` for the scheduler - db-scheduler polls continuously
and writes a heartbeat, and sharing the request pool would let a burst of traffic starve it.
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
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob
from ekbatan.migrations import migrate

from .actions import CreateNotificationAction, WalletCreateAction, WalletDepositMoneyAction
from .handlers import WalletMoneyDepositedEventHandler
from .jobs import WalletStipendJob
from .repository import NotificationRepository, WalletRepository

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
CONFIG_FILE = Path(__file__).resolve().parent / "ekbatan.yaml"


@dataclass(frozen=True, slots=True)
class Settings:
    config_file: Path = Path(os.environ.get("WALLET_CONFIG_FILE", CONFIG_FILE))


@dataclass(frozen=True, slots=True)
class WalletService:
    database_registry: DatabaseRegistry
    wallet_repository: WalletRepository
    notification_repository: NotificationRepository
    executor: ActionExecutor
    job_registry: JobRegistry
    #: The registry closes only the pools it built, so this one is closed here.
    jobs_data_source: DataSource

    def start(self) -> None:
        """Begin polling. Fan-out and handling are two of the jobs, so this is also what starts
        the event pipeline."""
        self.job_registry.start()

    def close(self) -> None:
        self.job_registry.stop()
        self.jobs_data_source.close()
        self.database_registry.close()


def build(settings: Settings | None = None, *, clock: Clock | None = None) -> WalletService:
    settings = settings or Settings()
    clock = clock or SystemClock()
    ekbatan = load_ekbatan_config(settings.config_file)
    # Every shard, before anything opens a pool against a schema that might not be there yet.
    migrate(ekbatan.sharding, MIGRATIONS)

    database_registry = DatabaseRegistry.from_config(ekbatan.sharding)
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
            .with_action(CreateNotificationAction(clock))
            .build()
        )
        .clock(clock)
        .build()
    )

    jobs_config = ekbatan.sharding.groups[0].members[0].config_for("jobsConfig")
    if jobs_config is None:
        raise ValueError("groups[0].members[0].configs.jobsConfig is required")
    jobs_data_source = DataSource.from_config(jobs_config)
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
                    .enabled(events_config.handling.enabled)
                    .clock(clock)
                    .build(),
                    WalletStipendJob(executor, wallet_repository),
                ]
            )
            .with_config(ekbatan.jobs)
            .build()
        ),
    )
