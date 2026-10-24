"""The wiring, all of it in one place - on `ekbatan.aio`.

Two shards, `global` and `mexico`, each a SQLite file of its own, declared in `ekbatan.yaml`. The
wallet pieces are the HTTP examples' - the same model, actions, handler and repositories - with
no web layer and no lock: this example is a script.

**Async where it waits, threads where they poll.** The units of work run on an
`AsyncActionExecutor` over aiosqlite. The event handler is `async def` and awaits an executor
of its own, on dispatch's handler loop: an async pool belongs to the loop it is used on. What
polls - db-scheduler, and the fan-out and dispatch jobs it runs - stays on threads, through a
sync registry over both files.
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

    #: The units of work, on `ekbatan.aio`.
    database_registry: AsyncDatabaseRegistry
    wallet_repository: WalletRepository
    notification_repository: NotificationRepository
    executor: AsyncActionExecutor
    #: The handler side: its own async registry and executor, used only on `handler_loop`.
    handler_loop: HandlerLoop
    handler_registry: AsyncDatabaseRegistry
    handler_executor: AsyncActionExecutor
    #: What polls, sync, on threads: the scheduler, and fan-out and dispatch over both files.
    events_registry: DatabaseRegistry
    job_registry: JobRegistry

    async def start(self) -> None:
        """Begin polling. Fan-out and handling are two jobs, so this is what starts the event
        pipeline - on its own threads, off the event loop. Starting registers the jobs in the
        database, sync, so it runs on a worker thread."""
        await asyncio.to_thread(self.job_registry.start)

    async def aclose(self) -> None:
        # Stop taking work before closing what the work runs on. Stopping the jobs waits for a
        # dispatch in flight, so it runs on a thread, off this loop.
        await asyncio.to_thread(self.job_registry.stop)
        # The handler's pool belongs to the handler loop: closed there, then the loop.
        await asyncio.to_thread(self.handler_loop.run, self.handler_registry.aclose())
        self.handler_loop.close()
        self.events_registry.close()
        await self.database_registry.aclose()


def build(*, config_file: Path = CONFIG_FILE, clock: Clock | None = None) -> WalletService:
    """Wire the service on `config_file`. Nothing here awaits: building a pool opens no
    connection."""
    clock = clock or SystemClock()
    ekbatan = load_ekbatan_config(config_file)
    layout = ekbatan.sharding

    # What polls: a sync registry over both files. It also makes the tables - development only;
    # a real deployment migrates from files, because create_all has no answer for a column that
    # changed.
    events_registry = DatabaseRegistry.from_config(layout)
    for shard in events_registry.shards:
        metadata.create_all(events_registry.manager(shard).primary.sqlalchemy_engine)

    # -- the units of work --------------------------------------------------------------------
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
            .with_action(WalletCloseAction(clock, wallet_repository))
            .build()
        )
        .clock(clock)
        .build()
    )

    # -- the handler side ---------------------------------------------------------------------
    # Notifications all live on the default shard, so that is the one file the handler needs.
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
    handler_executor = (
        AsyncActionExecutor.builder()
        .namespace(ekbatan.namespace)
        .database_registry(handler_registry)
        .repository_registry(
            RepositoryRegistry.builder()
            .with_entity_repository(NotificationRepository(handler_registry))
            .build()
        )
        .action_registry(
            ActionRegistry.builder().with_action(CreateNotificationAction(clock)).build()
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
        handler_executor=handler_executor,
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
