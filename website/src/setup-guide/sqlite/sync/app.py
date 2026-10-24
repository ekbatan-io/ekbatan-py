"""The wiring, all of it in one place: the database, the handler and the jobs.

One SQLite file. The tables come from `schema.py` through `create_all`, which is for development
only - a real deployment migrates from files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ekbatan import ActionExecutor, ActionRegistry, DatabaseRegistry, RepositoryRegistry
from ekbatan.clock import Clock, SystemClock
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob

from .actions import CreateNotificationAction, WalletCreateAction, WalletDepositMoneyAction
from .handlers import WalletMoneyDepositedEventHandler
from .jobs import WalletStipendJob
from .repository import NotificationRepository, WalletRepository
from .schema import metadata

CONFIG_FILE = Path(__file__).resolve().parent / "ekbatan.yaml"


@dataclass(frozen=True, slots=True)
class WalletService:
    database_registry: DatabaseRegistry
    wallet_repository: WalletRepository
    notification_repository: NotificationRepository
    executor: ActionExecutor
    job_registry: JobRegistry

    def start(self) -> None:
        self.job_registry.start()

    def close(self) -> None:
        self.job_registry.stop()
        self.database_registry.close()


def build(*, config_file: Path = CONFIG_FILE, clock: Clock | None = None) -> WalletService:
    clock = clock or SystemClock()
    ekbatan = load_ekbatan_config(config_file)
    database_registry = DatabaseRegistry.from_config(ekbatan.sharding)
    metadata.create_all(database_registry.default.primary.sqlalchemy_engine)

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
        # One pool for everything keeps the example a single readable script; a real deployment
        # gives the scheduler a pool of its own.
        job_registry=(
            job_registry()
            .data_source(database_registry.default.primary)
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
