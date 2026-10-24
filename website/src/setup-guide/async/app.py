"""The wiring, all of it in one place - on `ekbatan.aio`.

The requests run on an `AsyncDatabaseRegistry` and an `AsyncActionExecutor`. The event handler
and the stipend job's work run on dispatch's handler loop, over a registry of their own - an async
pool belongs to the loop it is used on. What polls - db-scheduler, and the fan-out and dispatch
jobs it runs - stays on threads, through the sync API, on `jobsConfig`.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from ekbatan import (
    ActionRegistry,
    DatabaseRegistry,
    DataSource,
    RepositoryRegistry,
    TransactionManager,
)
from ekbatan.aio import (
    AsyncActionExecutor,
    AsyncDatabaseRegistry,
    AsyncDataSource,
    AsyncTransactionManager,
)
from ekbatan.clock import Clock, SystemClock
from ekbatan.config import DataSourceConfig, ShardMemberConfig
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob, HandlerLoop
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
    database_registry: AsyncDatabaseRegistry
    wallet_repository: WalletRepository
    notification_repository: NotificationRepository
    executor: AsyncActionExecutor
    #: The handler side: used only on `handler_loop`, by the handler and the stipend job.
    handler_loop: HandlerLoop
    handler_registry: AsyncDatabaseRegistry
    #: What polls, sync, on threads. It closes `jobsConfig`'s pool: its manager was given it.
    events_registry: DatabaseRegistry
    job_registry: JobRegistry

    def start(self) -> None:
        self.job_registry.start()

    async def aclose(self) -> None:
        await asyncio.to_thread(self.job_registry.stop)
        await asyncio.to_thread(self.handler_loop.run, self.handler_registry.aclose())
        self.handler_loop.close()
        self.events_registry.close()
        await self.database_registry.aclose()


def build(settings: Settings | None = None, *, clock: Clock | None = None) -> WalletService:
    settings = settings or Settings()
    clock = clock or SystemClock()
    ekbatan = load_ekbatan_config(settings.config_file)
    layout = ekbatan.sharding
    migrate(layout, MIGRATIONS)

    # -- the requests ---------------------------------------------------------------------------
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

    # -- the handler and the stipend, on the handler loop ----------------------------------------
    member = layout.groups[0].members[0]
    handler_loop = HandlerLoop()
    handler_registry = (
        AsyncDatabaseRegistry.builder()
        .with_default_database(
            AsyncTransactionManager(
                layout.default_shard,
                AsyncDataSource.from_config(_required(member, "handlersConfig")),
            )
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

    # -- what polls, on threads -----------------------------------------------------------------
    jobs_data_source = DataSource.from_config(_required(member, "jobsConfig"))
    events_registry = (
        DatabaseRegistry.builder()
        .with_default_database(TransactionManager(layout.default_shard, jobs_data_source))
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
            .data_source(jobs_data_source)
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


def _required(member: ShardMemberConfig, name: str) -> DataSourceConfig:
    found = member.config_for(name)
    if found is None:
        raise ValueError(f"groups[0].members[0].configs.{name} is required")
    return found
