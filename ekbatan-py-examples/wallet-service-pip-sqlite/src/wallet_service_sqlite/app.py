"""The wiring, all of it in one place.

Two shards, `global` and `mexico`, each a SQLite file of its own, declared in `ekbatan.yaml`. The
wallet pieces are the HTTP examples' - the same model, actions, handler and repositories - with
no web layer and no lock: this example is a script.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ekbatan import ActionExecutor, ActionRegistry, DatabaseRegistry, RepositoryRegistry
from ekbatan.clock import Clock, SystemClock
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob

from .actions import (
    CreateNotificationAction,
    WalletCloseAction,
    WalletCreateAction,
    WalletDepositMoneyAction,
)
from .handlers import WalletMoneyDepositedEventHandler
from .repository import NotificationRepository, WalletRepository
from .schema import metadata

#: The service's Ekbatan settings - the two files, the namespace, the handling switch.
CONFIG_FILE = Path(__file__).resolve().parent / "ekbatan.yaml"


@dataclass(frozen=True, slots=True)
class WalletService:
    """Everything wired together, and the only object the outside world needs."""

    database_registry: DatabaseRegistry
    wallet_repository: WalletRepository
    notification_repository: NotificationRepository
    executor: ActionExecutor
    job_registry: JobRegistry

    def start(self) -> None:
        """Begin polling. Fan-out and handling are two jobs, so this is what starts the event
        pipeline: from here on, a committed event reaches its handler on its own."""
        self.job_registry.start()

    def close(self) -> None:
        # Stop taking work before closing what the work runs on.
        self.job_registry.stop()
        self.database_registry.close()


def build(*, config_file: Path = CONFIG_FILE, clock: Clock | None = None) -> WalletService:
    """Wire the service on `config_file`."""
    clock = clock or SystemClock()
    # `ekbatan.yaml`'s `${...}` placeholders - the two files - are read from the process
    # environment as the file is loaded, each by its name.
    ekbatan = load_ekbatan_config(config_file)
    database_registry = DatabaseRegistry.from_config(ekbatan.sharding)

    # Development only. A real deployment migrates from files - `ekbatan.migrations.migrate` -
    # because create_all has no answer for a column that changed.
    for shard in database_registry.shards:
        metadata.create_all(database_registry.manager(shard).primary.sqlalchemy_engine)

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
        # The jobs run on db-scheduler, against the default shard's `scheduled_tasks`, so
        # every instance coordinates through one row per job. A real deployment gives them a
        # pool of their own; one pool here keeps the example a single readable script.
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
                    # Off unless `handling.enabled` is set in `ekbatan.yaml`.
                    .enabled(events_config.handling.enabled)
                    .clock(clock)
                    .build(),
                ]
            )
            .with_config(ekbatan.jobs)
            .build()
        ),
    )
