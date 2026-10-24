"""The wiring, all of it in one place - and the only file that knows how many databases there
are.

Two shards, `global` and `mexico`, each its own database, declared in `ekbatan.yaml`. Add a shard
by adding a group or a member there: the repository already routes by id, and the actions never
name a database.

**Async where it waits, threads where they poll, one config file.** The HTTP requests run on
`ekbatan.aio`: an `AsyncDatabaseRegistry` over every shard, async repositories, an
`AsyncActionExecutor`, an async keyed lock. The event handler is `async def` and awaits an
executor of its own. What polls - db-scheduler, and the fan-out and dispatch jobs it runs - stays
on threads, through a sync registry over every shard. The urls name the sync driver; the async
side reads the same urls and connects through the database's async default.

The pools, each sized for what uses it:

**Requests** get each shard's `primaryConfig`, async. Past `pool_size` they queue for a
connection, and one that waits longer than `pool_timeout` fails rather than queueing for ever.

**Locks** get the default member's `lockConfig`, async. A lease holds its connection for as long
as it is held. One database holds every key: a lock is about a wallet's key, not its rows.

**The handler** gets the default member's `handlersConfig`, async, used only on dispatch's
handler loop: an async pool belongs to the loop it is used on. Notifications all live on the
default shard, so that is the one database it needs.

**Jobs** get each shard's `jobsConfig`, sync: one scheduler on the default one, and fan-out and
dispatch reading and writing every shard's `events` and `event_notifications`.
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
    ShardIdentifier,
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
    """What the service needs from its environment beyond `ekbatan.yaml`."""

    config_file: Path = Path(os.environ.get("WALLET_CONFIG_FILE", CONFIG_FILE))
    #: Threads running job bodies - fan-out and dispatch here. Each is a real OS thread.
    job_workers: int = int(os.environ.get("WALLET_JOB_WORKERS", "3"))
    run_migrations: bool = os.environ.get("WALLET_MIGRATE", "1") != "0"


@dataclass(frozen=True, slots=True)
class WalletService:
    """Everything wired together, and the only object the HTTP layer is handed."""

    #: The request side, on `ekbatan.aio`, over every shard.
    database_registry: AsyncDatabaseRegistry
    wallet_repository: WalletRepository
    notification_repository: NotificationRepository
    executor: AsyncActionExecutor
    #: The deposit's keyed lock, async, over the default member's `lockConfig`.
    lock_provider: AsyncKeyedLockProvider
    lock_data_source: AsyncDataSource
    #: The handler side: its own async registry and executor, used only on `handler_loop`.
    handler_loop: HandlerLoop
    handler_registry: AsyncDatabaseRegistry
    handler_executor: AsyncActionExecutor
    #: What polls, sync, on threads: the scheduler, and fan-out and dispatch over every shard.
    events_registry: DatabaseRegistry
    job_registry: JobRegistry
    #: Each shard's `jobsConfig` pool. `events_registry` closes them: its managers were given
    #: them, and a manager closes the data sources it was given. The job registry, which polls
    #: the default shard's, does not.
    jobs_data_sources: tuple[DataSource, ...]

    def start(self) -> None:
        """Begin polling. Fan-out and handling are two of the jobs, so this is what starts the
        event pipeline - across every shard, from one scheduler, off the event loop."""
        self.job_registry.start()

    async def aclose(self) -> None:
        # Order matters: stop taking work before closing what the work runs on. Stopping the
        # jobs waits for a dispatch in flight, so it runs on a thread, off this loop.
        await asyncio.to_thread(self.job_registry.stop)
        # The handler's pool belongs to the handler loop: closed there, then the loop.
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

    # `ekbatan.yaml`'s `${...}` placeholders - the urls and the login - are read from the
    # process environment as the file is loaded, each by its name.
    ekbatan = load_ekbatan_config(settings.config_file)
    layout = ekbatan.sharding

    if settings.run_migrations:
        # Every shard, one at a time, fail-fast. Sync, once, before any pool is opened against
        # a schema that might not be there yet.
        migrate(layout, MIGRATIONS)

    # -- the request side ---------------------------------------------------------------------
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

    default_member = layout.groups[0].members[0]
    lock_data_source = AsyncDataSource.from_config(_required(default_member, "lockConfig"))
    lock_provider = AsyncPostgresKeyedLockProvider.builder().data_source(lock_data_source).build()

    # -- the handler side ---------------------------------------------------------------------
    # Async, on dispatch's handler loop: an async pool belongs to the loop it is used on, so the
    # handler's registry is its own. Building it opens no connection; the loop's first use does.
    handler_loop = HandlerLoop()
    handler_registry = (
        AsyncDatabaseRegistry.builder()
        .with_default_database(
            AsyncTransactionManager(
                layout.default_shard,
                AsyncDataSource.from_config(_required(default_member, "handlersConfig")),
            )
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

    # -- what polls, on threads -----------------------------------------------------------------
    # A sync registry over every shard, on each member's `jobsConfig`: an event commits on its
    # wallet's shard, so fan-out and dispatch must reach every one. One scheduler for the whole
    # layout, on the default shard's pool - one job each, not one per shard.
    jobs_data_sources: dict[ShardIdentifier, DataSource] = {}
    events_builder = DatabaseRegistry.builder()
    for shard, member in layout.members():
        source = DataSource.from_config(_required(member, "jobsConfig"))
        jobs_data_sources[shard] = source
        manager = TransactionManager(shard, source)
        if shard == layout.default_shard:
            events_builder.with_default_database(manager)
        else:
            events_builder.with_database(manager)
    events_registry = events_builder.build()
    events_config = ekbatan.local_event_handler

    return WalletService(
        database_registry=database_registry,
        wallet_repository=wallet_repository,
        notification_repository=notification_repository,
        executor=executor,
        lock_provider=lock_provider,
        lock_data_source=lock_data_source,
        handler_loop=handler_loop,
        handler_registry=handler_registry,
        handler_executor=handler_executor,
        events_registry=events_registry,
        jobs_data_sources=tuple(jobs_data_sources.values()),
        job_registry=(
            job_registry()
            .data_source(jobs_data_sources[layout.default_shard])
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
                    # The loop the async handler runs on - ours, so `aclose` can close the
                    # handler's pool on it first.
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
        raise ValueError(f"{member.name or 'member'}.configs.{name} is required")
    return found
