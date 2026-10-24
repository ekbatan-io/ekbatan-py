"""The wiring, all of it in one place.

Which database, which pools, which handlers, which jobs. Nothing above this file knows any of
it - `api.py` is handed a built `WalletService` and asks it for work.

**Async where it waits, threads where they poll, one config file.** The HTTP requests run on
`ekbatan.aio`: an `AsyncDatabaseRegistry`, async repositories, an `AsyncActionExecutor`, an async
keyed lock, and every endpoint an `async def` that awaits. The event handler is `async def` too
and awaits an executor of its own. What polls - db-scheduler, and the fan-out and dispatch jobs it
runs - stays on threads, through the sync API. All of it is built from the same `ekbatan.yaml`,
and the urls in it name the sync driver - psycopg, or PyMySQL: the sync side connects with it,
and the async side reads the same url as naming the database only and connects through that
database's async default - asyncpg, or aiomysql.

Four pools, each sized for what uses it:

**Requests** get `primaryConfig`, async. It is also the cap on requests waiting on the
database: past `pool_size` they queue for a connection, and one that waits longer than
`pool_timeout` fails rather than queueing for ever. Limit what reaches the app at the server
too - see `__main__.py`.

**Locks** get `lockConfig`, async. A keyed lock holds its connection for as long as the lease
is held - that is what the lock *is* - so a lease taken during a request would otherwise sit on
a request connection. Size it for the leases held at once, not for traffic.

**The handler** gets `handlersConfig`, async, used only on dispatch's handler loop: an async pool
belongs to the loop it is used on, so the handler's is its own, and it is closed on that loop.
The stipend job's work runs on the same loop, over the same pool.

**Jobs** get `jobsConfig`, sync: db-scheduler, and the fan-out and dispatch jobs' reads and
writes of the queue. db-scheduler writes a heartbeat; size the pool past the job workers so a
job body never holds the last connection.
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
from ekbatan.aio.locks import AsyncKeyedLockProvider, AsyncMySQLKeyedLockProvider
from ekbatan.clock import Clock, SystemClock
from ekbatan.config import DataSourceConfig, ShardMemberConfig
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob, HandlerLoop
from ekbatan.migrations import migrate

from .actions import CloseWalletAction, DepositMoneyAction, OpenWalletAction, RaiseAlertAction
from .handlers import MoneyDepositedEventHandler
from .jobs import PayStipendJob
from .repository import AlertRepository, WalletRepository

#: Where the migration files live - `V<version>__<description>.sql`.
MIGRATIONS = Path(__file__).resolve().parent / "migrations"

#: The service's Ekbatan settings - database layout, namespace, event handling - in one file.
CONFIG_FILE = Path(__file__).resolve().parent / "ekbatan.yaml"


@dataclass(frozen=True, slots=True)
class Settings:
    """What the service needs from its environment beyond `ekbatan.yaml`."""

    config_file: Path = Path(os.environ.get("WALLET_CONFIG_FILE", CONFIG_FILE))
    #: Threads running job bodies - each a real OS thread, so the number is chosen.
    job_workers: int = int(os.environ.get("WALLET_JOB_WORKERS", "3"))
    run_migrations: bool = os.environ.get("WALLET_MIGRATE", "1") != "0"


@dataclass(frozen=True, slots=True)
class WalletService:
    """Everything wired together, and the only object the HTTP layer is handed."""

    #: The request side, on `ekbatan.aio`.
    registry: AsyncDatabaseRegistry
    wallets: WalletRepository
    alerts: AlertRepository
    executor: AsyncActionExecutor
    #: The deposit's keyed lock, async, over `lockConfig`.
    locks: AsyncKeyedLockProvider
    lock_data_source: AsyncDataSource
    #: The handler side: its own async registry and executor, used only on `handler_loop` -
    #: by the event handler and by the stipend job's work.
    handler_loop: HandlerLoop
    handler_registry: AsyncDatabaseRegistry
    handler_wallets: WalletRepository
    handler_executor: AsyncActionExecutor
    #: What polls, sync, on threads: the scheduler, and fan-out and dispatch over `events_registry`.
    events_registry: DatabaseRegistry
    jobs: JobRegistry
    #: Built from `jobsConfig`. `events_registry` closes it: its manager was given it, and a
    #: manager closes the data sources it was given. The job registry, which polls it, does not.
    jobs_data_source: DataSource

    def start(self) -> None:
        """Begin polling for jobs. Fan-out and dispatch are two of them, so this is also what
        starts the event pipeline - on its own threads, off the event loop."""
        self.jobs.start()

    async def aclose(self) -> None:
        # Order matters: stop taking work before closing what the work runs on. Stopping the
        # jobs waits for a dispatch in flight, so it runs on a thread, off this loop.
        await asyncio.to_thread(self.jobs.stop)
        # The handler's pool belongs to the handler loop: closed there, then the loop.
        await asyncio.to_thread(self.handler_loop.run, self.handler_registry.aclose())
        self.handler_loop.close()
        self.events_registry.close()
        self.locks.close()
        await self.lock_data_source.aclose()
        await self.registry.aclose()


def build(settings: Settings | None = None, *, clock: Clock | None = None) -> WalletService:
    """Wire the service. Nothing here is specific to HTTP, and nothing here awaits: building a
    pool opens no connection."""
    config = settings or Settings()
    running = clock or SystemClock()

    # `ekbatan.yaml`'s `${...}` placeholders - the url and the login - are read from the
    # process environment as the file is loaded, each by its name.
    ekbatan = load_ekbatan_config(config.config_file)
    layout = ekbatan.sharding

    if config.run_migrations:
        # Migrations are sync, and run once at startup before any pool is opened against a
        # schema that might not be there yet.
        migrate(layout, MIGRATIONS)

    # -- the request side ---------------------------------------------------------------------
    registry = AsyncDatabaseRegistry.from_config(layout)
    wallets = WalletRepository(registry, clock=running)
    alerts = AlertRepository(registry, clock=running)
    executor = (
        AsyncActionExecutor.builder()
        .namespace(ekbatan.namespace)
        .database_registry(registry)
        .repository_registry(
            RepositoryRegistry.builder()
            .with_model_repository(wallets)
            .with_entity_repository(alerts)
            .build()
        )
        .action_registry(
            ActionRegistry.builder()
            .with_action(OpenWalletAction(running))
            .with_action(DepositMoneyAction(wallets, running))
            .with_action(CloseWalletAction(wallets, running))
            .build()
        )
        .clock(running)
        .build()
    )

    member = layout.groups[0].members[0]
    lock_data_source = AsyncDataSource.from_config(_required(member, "lockConfig"))
    locks = AsyncMySQLKeyedLockProvider.builder().data_source(lock_data_source).build()

    # -- the handler side ---------------------------------------------------------------------
    # Async, on dispatch's handler loop: an async pool belongs to the loop it is used on, so the
    # handler's registry is its own. Building it opens no connection; the loop's first use does.
    # The stipend job hands its work to the same loop, so it runs on this side too.
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
    handler_wallets = WalletRepository(handler_registry, clock=running)
    handler_executor = (
        AsyncActionExecutor.builder()
        .namespace(ekbatan.namespace)
        .database_registry(handler_registry)
        .repository_registry(
            RepositoryRegistry.builder()
            .with_model_repository(handler_wallets)
            .with_entity_repository(AlertRepository(handler_registry, clock=running))
            .build()
        )
        .action_registry(
            ActionRegistry.builder()
            .with_action(DepositMoneyAction(handler_wallets, running))
            .with_action(RaiseAlertAction(running))
            .build()
        )
        .clock(running)
        .build()
    )
    handlers = (
        EventHandlerRegistry.builder()
        .with_handler(MoneyDepositedEventHandler(handler_executor))
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
        registry=registry,
        wallets=wallets,
        alerts=alerts,
        executor=executor,
        locks=locks,
        lock_data_source=lock_data_source,
        handler_loop=handler_loop,
        handler_registry=handler_registry,
        handler_wallets=handler_wallets,
        handler_executor=handler_executor,
        events_registry=events_registry,
        jobs_data_source=jobs_data_source,
        jobs=(
            job_registry()
            .data_source(jobs_data_source)
            .with_jobs(
                [
                    EventFanoutJob.builder()
                    .registry(events_registry)
                    .handlers(handlers)
                    .config(events_config)
                    .clock(running)
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
                    .clock(running)
                    .build(),
                    PayStipendJob(handler_loop, handler_executor, handler_wallets),
                ]
            )
            .with_config(ekbatan.jobs)
            .workers(config.job_workers)
            .build()
        ),
    )


def _required(member: ShardMemberConfig, name: str) -> DataSourceConfig:
    """The named pool's settings, or an error naming where the setting goes."""
    found = member.config_for(name)
    if found is None:
        raise ValueError(f"groups[0].members[0].configs.{name} is required")
    return found
