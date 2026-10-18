"""The wiring, all of it in one place.

Which database, which pools, which handlers, which jobs. Nothing above this file knows any of
it - `api.py` is handed a built `WalletService` and asks it for work.

**Two sides, one database, one config file.** The HTTP requests run on `ekbatan.aio`: an
`AsyncDatabaseRegistry`, async repositories, an `AsyncActionExecutor`, and every endpoint an
`async def` that awaits. Event handlers and jobs run on threads, as they always do, through the
sync API. Both are built from the same `ekbatan.yaml`, and the urls in it name the sync driver -
psycopg, or PyMySQL: the sync side connects with it, and the async registry reads the same url
as naming the database only and connects through that database's async default - asyncpg, or
aiomysql.

Three pools, each sized for what uses it:

**Requests** get `primaryConfig`, async. It is also the cap on requests waiting on the
database: past `pool_size` they queue for a connection, and one that waits longer than
`pool_timeout` fails rather than queueing for ever. Limit what reaches the app at the server
too - see `__main__.py`.

**Handlers** get `handlersConfig`, sync - dispatch runs a batch of them at once on threads.

**Jobs** get `jobsConfig`. db-scheduler polls continuously and writes a heartbeat; sharing a
pool means a burst can starve that heartbeat, and an execution whose heartbeat stops is
declared dead and run again somewhere else.

Keyed locks are not used here: a lock held across an async unit of work is not provided yet.
The deposit relies on the optimistic lock instead - see `api.py`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine

from ekbatan import (
    ActionExecutor,
    ActionRegistry,
    DatabaseRegistry,
    RepositoryRegistry,
    SingleTableJsonEventPersister,
    TransactionManager,
    engine_for,
)
from ekbatan.aio import (
    AsyncActionExecutor,
    AsyncDatabaseRegistry,
    AsyncSingleTableJsonEventPersister,
)
from ekbatan.clock import Clock, SystemClock
from ekbatan.config import DataSourceConfig, ShardMemberConfig
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob
from ekbatan.migrations import migrate

from .actions import CloseWalletAction, DepositMoneyAction, OpenWalletAction
from .handlers import MoneyDepositedEventHandler, RaiseAlertAction
from .repository import AlertRepository, HandlerAlertRepository, WalletRepository
from .schema import EVENT_NOTIFICATIONS, EVENTS

#: Where the migration files live - `V<version>__<description>.sql`.
MIGRATIONS = Path(__file__).resolve().parent / "migrations"

#: The service's Ekbatan settings - database layout, namespace, event handling - in one file.
CONFIG_FILE = Path(__file__).resolve().parent / "ekbatan.yaml"


@dataclass(frozen=True, slots=True)
class Settings:
    """What the service needs from its environment beyond `ekbatan.yaml`."""

    config_file: Path = Path(os.environ.get("WALLET_CONFIG_FILE", CONFIG_FILE))
    #: Where the file's `${...}` placeholders are looked up - the url and the login.
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)
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
    #: The handler side, sync, on threads.
    handler_registry: DatabaseRegistry
    handler_executor: ActionExecutor
    jobs: JobRegistry
    #: Built from `jobsConfig`; the registries own only what they built, so this is disposed
    #: here.
    jobs_engine: Engine

    def start(self) -> None:
        """Begin polling for jobs. Fan-out and dispatch are two of them, so this is also what
        starts the event pipeline - on its own threads, off the event loop."""
        self.jobs.start()

    async def aclose(self) -> None:
        # Order matters: stop taking work before closing what the work runs on.
        self.jobs.stop()
        self.jobs_engine.dispose()
        self.handler_registry.close()
        await self.registry.aclose()


def build(settings: Settings | None = None, *, clock: Clock | None = None) -> WalletService:
    """Wire the service. Nothing here is specific to HTTP, and nothing here awaits: building a
    pool opens no connection."""
    config = settings or Settings()
    running = clock or SystemClock()

    ekbatan = load_ekbatan_config(config.config_file, environ=config.environ)
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
        .event_persister(AsyncSingleTableJsonEventPersister(registry, EVENTS))
        .clock(running)
        .build()
    )

    # -- the handler side ---------------------------------------------------------------------
    member = layout.groups[0].members[0]
    handlers_engine = engine_for(_required(member, "handlersConfig"))
    handler_registry = (
        DatabaseRegistry.builder()
        .with_default_database(TransactionManager(layout.default_shard, handlers_engine))
        .build()
    )
    handler_executor = (
        ActionExecutor.builder()
        .namespace(ekbatan.namespace)
        .database_registry(handler_registry)
        .repository_registry(
            RepositoryRegistry.builder()
            .with_entity_repository(HandlerAlertRepository(handler_registry, clock=running))
            .build()
        )
        .action_registry(ActionRegistry.builder().with_action(RaiseAlertAction(running)).build())
        .event_persister(SingleTableJsonEventPersister(handler_registry, EVENTS))
        .clock(running)
        .build()
    )
    handlers = (
        EventHandlerRegistry.builder()
        .with_handler(MoneyDepositedEventHandler(handler_executor))
        .build()
    )
    events_config = ekbatan.local_event_handler
    jobs_engine = engine_for(_required(member, "jobsConfig"))

    return WalletService(
        registry=registry,
        wallets=wallets,
        alerts=alerts,
        executor=executor,
        handler_registry=handler_registry,
        handler_executor=handler_executor,
        jobs_engine=jobs_engine,
        jobs=(
            job_registry()
            .engine(jobs_engine)
            .with_jobs(
                [
                    EventFanoutJob.builder()
                    .registry(handler_registry)
                    .handlers(handlers)
                    .events(EVENTS)
                    .notifications(EVENT_NOTIFICATIONS)
                    .config(events_config)
                    .clock(running)
                    .build(),
                    EventHandlingJob.builder()
                    .registry(handler_registry)
                    .handlers(handlers)
                    .notifications(EVENT_NOTIFICATIONS)
                    .config(events_config)
                    # Off unless `handling.enabled` is set in `ekbatan.yaml`.
                    .enabled(events_config.handling.enabled)
                    .clock(running)
                    .build(),
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
