"""The wiring, all of it in one place.

**Async where it waits, threads where they poll, one config file.** The HTTP requests run on
`ekbatan.aio`: an `AsyncDatabaseRegistry`, async repositories, an `AsyncActionExecutor`, an async
keyed lock. The saga's later steps run in the event handlers, `async def`, on dispatch's handler
loop, with an executor of their own. What polls - db-scheduler, and the fan-out and dispatch
jobs it runs - stays on threads, through the sync API.

One database, declared in `ekbatan.yaml`, with four pools: `primaryConfig` for requests,
`handlersConfig` for the handlers - an async pool belongs to the loop it is used on, so theirs is
their own - `lockConfig` for the deposit's keyed lock, and `jobsConfig` for the scheduler.
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
from ekbatan.aio.locks import AsyncKeyedLockProvider, AsyncPostgresKeyedLockProvider
from ekbatan.clock import Clock, SystemClock
from ekbatan.config import DataSourceConfig, ShardMemberConfig
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob, HandlerLoop
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

    #: The request side, on `ekbatan.aio`.
    database_registry: AsyncDatabaseRegistry
    wallet_repository: WalletRepository
    transfer_step_repository: TransferStepRepository
    executor: AsyncActionExecutor
    lock_provider: AsyncKeyedLockProvider
    lock_data_source: AsyncDataSource
    #: The handler side: steps two and three, on `handler_loop` only.
    handler_loop: HandlerLoop
    handler_registry: AsyncDatabaseRegistry
    handler_executor: AsyncActionExecutor
    #: What polls, sync, on threads.
    events_registry: DatabaseRegistry
    job_registry: JobRegistry
    #: Built from `jobsConfig`. `events_registry` closes it: its manager was given it, and a
    #: manager closes the data sources it was given. The job registry, which polls it, does not.
    jobs_data_source: DataSource

    def start(self) -> None:
        self.job_registry.start()

    async def aclose(self) -> None:
        # Order matters: stop taking work before closing what the work runs on. Stopping the
        # jobs waits for a dispatch in flight, so it runs on a thread, off this loop.
        await asyncio.to_thread(self.job_registry.stop)
        # The handlers' pool belongs to the handler loop: closed there, then the loop.
        await asyncio.to_thread(self.handler_loop.run, self.handler_registry.aclose())
        self.handler_loop.close()
        self.events_registry.close()
        self.lock_provider.close()
        await self.lock_data_source.aclose()
        await self.database_registry.aclose()


def build(settings: Settings | None = None, *, clock: Clock | None = None) -> WalletService:
    """Wire the service. Nothing here awaits: building a pool opens no connection."""
    settings = settings or Settings()
    clock = clock or SystemClock()
    ekbatan = load_ekbatan_config(settings.config_file)
    layout = ekbatan.sharding

    if settings.run_migrations:
        # Sync, once, before any pool is opened against a schema that might not be there yet.
        migrate(layout, MIGRATIONS)

    # -- the request side ---------------------------------------------------------------------
    database_registry = AsyncDatabaseRegistry.from_config(layout)
    wallet_repository = WalletRepository(database_registry)
    transfer_step_repository = TransferStepRepository(database_registry)
    executor = (
        AsyncActionExecutor.builder()
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
            # Steps two and three run on the handler loop; they are here too so a replay tool,
            # or a test, can run one by hand.
            .with_action(CompleteTransferAction(clock, wallet_repository, transfer_step_repository))
            .with_action(RefundTransferAction(clock, wallet_repository, transfer_step_repository))
            .build()
        )
        .clock(clock)
        .build()
    )
    member = layout.groups[0].members[0]
    lock_data_source = AsyncDataSource.from_config(_required(member, "lockConfig"))
    lock_provider = AsyncPostgresKeyedLockProvider.builder().data_source(lock_data_source).build()

    # -- the handler side ---------------------------------------------------------------------
    # Steps two and three, async, on dispatch's handler loop: an async pool belongs to the loop
    # it is used on, so the handlers' registry, repositories and actions are their own.
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
    handler_transfer_step_repository = TransferStepRepository(handler_registry)
    handler_executor = (
        AsyncActionExecutor.builder()
        .namespace(ekbatan.namespace)
        .database_registry(handler_registry)
        .repository_registry(
            RepositoryRegistry.builder()
            .with_model_repository(handler_wallet_repository)
            .with_entity_repository(handler_transfer_step_repository)
            .build()
        )
        .action_registry(
            ActionRegistry.builder()
            .with_action(
                CompleteTransferAction(
                    clock, handler_wallet_repository, handler_transfer_step_repository
                )
            )
            .with_action(
                RefundTransferAction(
                    clock, handler_wallet_repository, handler_transfer_step_repository
                )
            )
            .build()
        )
        .clock(clock)
        .build()
    )
    handlers = (
        EventHandlerRegistry.builder()
        .with_handler(TransferInitiatedEventHandler(handler_executor))
        .with_handler(TransferFailedEventHandler(handler_executor))
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
        transfer_step_repository=transfer_step_repository,
        executor=executor,
        lock_provider=lock_provider,
        lock_data_source=lock_data_source,
        handler_loop=handler_loop,
        handler_registry=handler_registry,
        handler_executor=handler_executor,
        events_registry=events_registry,
        jobs_data_source=jobs_data_source,
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
                    # The loop the async handlers run on - ours, so `aclose` can close their
                    # pool on it first.
                    .handler_loop(handler_loop)
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
