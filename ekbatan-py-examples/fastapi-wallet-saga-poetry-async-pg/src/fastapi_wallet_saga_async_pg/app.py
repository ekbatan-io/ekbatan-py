"""The wiring.

PostgreSQL, as Java's `spring-boot-wallet-saga-gradle-pg` uses - a saga is about steps that
commit independently, and a database that serialises every writer makes that harder to see
rather than easier.

Every step after the first is started by the event pipeline: the fan-out and handling jobs run
in the background, as in any service, and nobody calls them. `ekbatan.yaml` switches handling
on - without it the saga stops after its first step.

**Async where it waits, threads where they poll, one config file.** The requests run on
`ekbatan.aio`: an `AsyncDatabaseRegistry`, async repositories, an `AsyncActionExecutor`, every
endpoint an `async def`. The saga's later steps are run by `async def` handlers, on dispatch's
handler loop, through an executor of their own. What polls - db-scheduler, and the fan-out and
dispatch jobs it runs - stays on threads, through the sync API. Three pools:

**Requests** get `primaryConfig`, async: step one, and every read a client makes.

**The handlers** get `handlersConfig`, async, used only on the handler loop: an async pool
belongs to the loop it is used on, so theirs is their own, and it is closed on that loop.

**Jobs** get `jobsConfig`, sync: db-scheduler, and fan-out and dispatch reading the queue.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine

from ekbatan import (
    ActionRegistry,
    DatabaseRegistry,
    RepositoryRegistry,
    TransactionManager,
    engine_for,
)
from ekbatan.aio import (
    AsyncActionExecutor,
    AsyncDatabaseRegistry,
    AsyncSingleTableJsonEventPersister,
    AsyncTransactionManager,
    async_engine_for,
)
from ekbatan.clock import Clock, SystemClock
from ekbatan.config import DataSourceConfig, ShardMemberConfig
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob, HandlerLoop
from ekbatan.migrations import migrate

from .actions import (
    CompleteTransferAction,
    InitiateTransferAction,
    OpenWalletAction,
    RefundTransferAction,
)
from .handlers import TransferFailedEventHandler, TransferInitiatedEventHandler
from .repository import TransferStepRepository, WalletRepository
from .schema import EVENT_NOTIFICATIONS, EVENTS

#: Where the migration files live - `V<version>__<description>.sql`. Migrations run from files
#: rather than from `create_all`, which is the difference between an example and something you
#: could deploy: `create_all` has no answer for a column that changed.
MIGRATIONS = Path(__file__).resolve().parent / "migrations"

#: The service's Ekbatan settings - the database, the namespace and the event handling switch -
#: in the same keys as the Java examples' `application.yml`.
CONFIG_FILE = Path(__file__).resolve().parent / "ekbatan.yaml"


@dataclass(frozen=True, slots=True)
class SagaService:
    #: The request side, on `ekbatan.aio`.
    registry: AsyncDatabaseRegistry
    wallets: WalletRepository
    steps: TransferStepRepository
    executor: AsyncActionExecutor
    #: The handler side: its own async registry and executor, used only on `handler_loop`.
    handler_loop: HandlerLoop
    handler_registry: AsyncDatabaseRegistry
    handler_executor: AsyncActionExecutor
    #: What polls, sync, on threads: the scheduler, and fan-out and dispatch over `events_registry`.
    events_registry: DatabaseRegistry
    jobs: JobRegistry
    #: Built from the member's `jobsConfig`. The registries own only what they built, so this
    #: one is disposed here.
    jobs_engine: Engine

    def start(self) -> None:
        """Begin polling. Fan-out and handling are two of the jobs, so this is what lets the saga
        move past its first step - on their own threads, off the event loop."""
        self.jobs.start()

    async def aclose(self) -> None:
        # Order matters: stop taking work before closing what the work runs on. Stopping the
        # jobs waits for a dispatch in flight, so it runs on a thread, off this loop.
        await asyncio.to_thread(self.jobs.stop)
        # The handlers' pool belongs to the handler loop: closed there, then the loop.
        await asyncio.to_thread(self.handler_loop.run, self.handler_registry.aclose())
        self.handler_loop.close()
        self.events_registry.close()
        self.jobs_engine.dispose()
        await self.registry.aclose()


def build(
    *,
    config_file: Path = CONFIG_FILE,
    clock: Clock | None = None,
) -> SagaService:
    """Wire the service on `config_file`; its `${...}` placeholders are read from the process
    environment. Nothing here awaits: building a pool opens no connection."""
    running = clock or SystemClock()
    # `ekbatan.yaml`'s `${...}` placeholders - the url and the login - are read from the
    # process environment as the file is loaded, each by its name.
    ekbatan = load_ekbatan_config(config_file)
    layout = ekbatan.sharding
    # Sync, once, before any pool is opened against a schema that might not be there yet.
    migrate(layout, MIGRATIONS)

    # -- the request side ---------------------------------------------------------------------
    registry = AsyncDatabaseRegistry.from_config(layout)
    wallets = WalletRepository(registry, clock=running)
    steps = TransferStepRepository(registry, clock=running)
    executor = (
        AsyncActionExecutor.builder()
        .namespace(ekbatan.namespace)
        .database_registry(registry)
        .repository_registry(
            RepositoryRegistry.builder()
            .with_model_repository(wallets)
            .with_entity_repository(steps)
            .build()
        )
        .action_registry(
            ActionRegistry.builder()
            .with_action(OpenWalletAction(running))
            .with_action(InitiateTransferAction(wallets, running))
            # Steps two and three run on the handler loop; they are here too so a replay tool,
            # or a test, can run one by hand.
            .with_action(CompleteTransferAction(wallets, running))
            .with_action(RefundTransferAction(wallets, running))
            .build()
        )
        .event_persister(AsyncSingleTableJsonEventPersister(registry, EVENTS))
        .clock(running)
        .build()
    )

    # -- the handler side ---------------------------------------------------------------------
    # Steps two and three, async, on dispatch's handler loop: an async pool belongs to the loop
    # it is used on, so the handlers' registry, repositories and actions are their own.
    member = layout.groups[0].members[0]
    handler_loop = HandlerLoop()
    handler_registry = (
        AsyncDatabaseRegistry.builder()
        .with_default_database(
            AsyncTransactionManager(
                layout.default_shard, async_engine_for(_required(member, "handlersConfig"))
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
            .with_entity_repository(TransferStepRepository(handler_registry, clock=running))
            .build()
        )
        .action_registry(
            ActionRegistry.builder()
            .with_action(CompleteTransferAction(handler_wallets, running))
            .with_action(RefundTransferAction(handler_wallets, running))
            .build()
        )
        .event_persister(AsyncSingleTableJsonEventPersister(handler_registry, EVENTS))
        .clock(running)
        .build()
    )
    handlers = (
        EventHandlerRegistry.builder()
        .with_handler(TransferInitiatedEventHandler(handler_executor))
        .with_handler(TransferFailedEventHandler(handler_executor))
        .build()
    )

    # -- what polls, on threads -----------------------------------------------------------------
    jobs_engine = engine_for(_required(member, "jobsConfig"))
    events_registry = (
        DatabaseRegistry.builder()
        .with_default_database(TransactionManager(layout.default_shard, jobs_engine))
        .build()
    )
    events_config = ekbatan.local_event_handler

    return SagaService(
        registry=registry,
        wallets=wallets,
        steps=steps,
        executor=executor,
        handler_loop=handler_loop,
        handler_registry=handler_registry,
        handler_executor=handler_executor,
        events_registry=events_registry,
        jobs_engine=jobs_engine,
        jobs=(
            job_registry()
            .engine(jobs_engine)
            .with_jobs(
                [
                    EventFanoutJob.builder()
                    .registry(events_registry)
                    .handlers(handlers)
                    .events(EVENTS)
                    .notifications(EVENT_NOTIFICATIONS)
                    .config(events_config)
                    .clock(running)
                    .build(),
                    EventHandlingJob.builder()
                    .registry(events_registry)
                    .handlers(handlers)
                    # The loop the async handlers run on - ours, so `aclose` can close their
                    # pool on it first.
                    .handler_loop(handler_loop)
                    .notifications(EVENT_NOTIFICATIONS)
                    .config(events_config)
                    # Java's DI makes this job only if `handling.enabled`; this is that check.
                    .enabled(events_config.handling.enabled)
                    .clock(running)
                    .build(),
                ]
            )
            .with_config(ekbatan.jobs)
            .build()
        ),
    )


def _required(member: ShardMemberConfig, name: str) -> DataSourceConfig:
    """The named pool's settings, or an error naming where the setting goes."""
    found = member.config_for(name)
    if found is None:
        raise ValueError(f"groups[0].members[0].configs.{name} is required")
    return found
