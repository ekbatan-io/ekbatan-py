"""The wiring, all of it in one place - on `ekbatan.aio`.

The units of work run on an `AsyncActionExecutor` over aiosqlite; the event handler and the
stipend job's work on dispatch's handler loop, over a registry of their own. What polls stays on
threads, through a sync registry. One SQLite file; the tables come from `schema.py` through
`create_all`, which is for development only - a real deployment migrates from files.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from ekbatan import ActionRegistry, DatabaseRegistry, RepositoryRegistry
from ekbatan.aio import (
    AsyncActionExecutor,
    AsyncDatabaseRegistry,
    AsyncDataSource,
    AsyncTransactionManager,
)
from ekbatan.clock import Clock, SystemClock
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob, HandlerLoop

from .actions import CreateNotificationAction, WalletCreateAction, WalletDepositMoneyAction
from .handlers import WalletMoneyDepositedEventHandler
from .jobs import WalletStipendJob
from .repository import NotificationRepository, WalletRepository
from .schema import metadata

CONFIG_FILE = Path(__file__).resolve().parent / "ekbatan.yaml"


@dataclass(frozen=True, slots=True)
class WalletService:
    database_registry: AsyncDatabaseRegistry
    wallet_repository: WalletRepository
    notification_repository: NotificationRepository
    executor: AsyncActionExecutor
    handler_loop: HandlerLoop
    handler_registry: AsyncDatabaseRegistry
    events_registry: DatabaseRegistry
    job_registry: JobRegistry

    async def start(self) -> None:
        await asyncio.to_thread(self.job_registry.start)

    async def aclose(self) -> None:
        await asyncio.to_thread(self.job_registry.stop)
        await asyncio.to_thread(self.handler_loop.run, self.handler_registry.aclose())
        self.handler_loop.close()
        self.events_registry.close()
        await self.database_registry.aclose()


def build(*, config_file: Path = CONFIG_FILE, clock: Clock | None = None) -> WalletService:
    clock = clock or SystemClock()
    ekbatan = load_ekbatan_config(config_file)
    layout = ekbatan.sharding
    # What polls: a sync registry. It makes the tables too - development only.
    events_registry = DatabaseRegistry.from_config(layout)
    metadata.create_all(events_registry.default.primary.sqlalchemy_engine)

    database_registry = AsyncDatabaseRegistry.from_config(layout)
    wallet_repository = WalletRepository(database_registry)
    notification_repository = NotificationRepository(database_registry)
    executor = (
        AsyncActionExecutor.builder()
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
            .build()
        )
        .clock(clock)
        .build()
    )

    primary = layout.groups[0].members[0].config_for("primaryConfig")
    if primary is None:
        raise ValueError("groups[0].members[0].configs.primaryConfig is required")
    handler_loop = HandlerLoop()
    handler_registry = (
        AsyncDatabaseRegistry.builder()
        .with_default_database(
            AsyncTransactionManager(layout.default_shard, AsyncDataSource.from_config(primary))
        )
        .build()
    )
    handler_wallet_repository = WalletRepository(handler_registry)
    handler_executor = (
        AsyncActionExecutor.builder()
        .namespace(ekbatan.namespace)
        .database_registry(handler_registry)
        .repository_registry(
            RepositoryRegistry.builder()
            .with_model_repository(handler_wallet_repository)
            .with_entity_repository(NotificationRepository(handler_registry))
            .build()
        )
        .action_registry(
            ActionRegistry.builder()
            .with_action(WalletDepositMoneyAction(clock, handler_wallet_repository))
            .with_action(CreateNotificationAction(clock))
            .build()
        )
        .clock(clock)
        .build()
    )
    handlers = (
        EventHandlerRegistry.builder()
        .with_handler(WalletMoneyDepositedEventHandler(handler_executor))
        .build()
    )
    events_config = ekbatan.local_event_handler

    return WalletService(
        database_registry=database_registry,
        wallet_repository=wallet_repository,
        notification_repository=notification_repository,
        executor=executor,
        handler_loop=handler_loop,
        handler_registry=handler_registry,
        events_registry=events_registry,
        job_registry=(
            job_registry()
            .data_source(events_registry.default.primary)
            .with_jobs(
                [
                    EventFanoutJob.builder()
                    .registry(events_registry)
                    .handlers(handlers)
                    .config(events_config)
                    .clock(clock)
                    .build(),
                    EventHandlingJob.builder()
                    .registry(events_registry)
                    .handlers(handlers)
                    .handler_loop(handler_loop)
                    .config(events_config)
                    .enabled(events_config.handling.enabled)
                    .clock(clock)
                    .build(),
                    WalletStipendJob(handler_loop, handler_executor, handler_wallet_repository),
                ]
            )
            .with_config(ekbatan.jobs)
            .build()
        ),
    )
