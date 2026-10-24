"""A worker process: jobs, no HTTP - with their work on `ekbatan.aio`.

The wiring is the same as any other service's minus the web layer - which is the point. A
deployment usually runs both shapes from one image: the API replicas serve requests, the worker
replicas run this, and the jobs claim their rows against the same databases either way.

**Jobs on threads, their work on a loop.** Distributed jobs are sync: db-scheduler claims a
job's row and calls `execute` on one of its worker threads. Here each job hands its work to the
**worker loop** - a `HandlerLoop`, one event loop on a thread of its own - with `loop.run(...)`
and waits for it, so the work awaits an `AsyncActionExecutor`, async repositories and an async
keyed lock. The event handler is `async def` and runs on the same loop. Everything that awaits
was built for that loop and is closed on it, last.

Two shards, `global` and `mexico`, each its own database, declared in `ekbatan.yaml`. The pools:

**The work** gets each shard's `primaryConfig`, async, used only on the worker loop.

**Locks** get the default member's `lockConfig`, async, on the worker loop too.

**The scheduler** gets each shard's `jobsConfig`, sync: one scheduler on the default one, and
fan-out and dispatch reading and writing every shard's `events` and `event_notifications`.

**SIGTERM is the one exit that needs catching.** A started registry drains on any normal exit
and on Ctrl-C by default - it registers with `atexit`. But CPython kills the process on SIGTERM
without running `atexit`; `__main__.run()` catches the signal and calls `close()`.
"""

from __future__ import annotations

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
from ekbatan.aio import AsyncActionExecutor, AsyncDatabaseRegistry, AsyncDataSource
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
from .jobs import WalletReportJob, WalletStipendJob
from .repository import NotificationRepository, WalletRepository

#: Where the migration files live - `V<version>__<description>.sql`. Every shard gets them all.
MIGRATIONS = Path(__file__).resolve().parent / "migrations"

#: The worker's Ekbatan settings - the shard layout, the namespace, the jobs, event handling.
CONFIG_FILE = Path(__file__).resolve().parent / "ekbatan.yaml"


@dataclass(frozen=True, slots=True)
class Worker:
    #: The loop the jobs' work and the event handler run on, and what they await on it.
    loop: HandlerLoop
    database_registry: AsyncDatabaseRegistry
    wallet_repository: WalletRepository
    notification_repository: NotificationRepository
    executor: AsyncActionExecutor
    lock_provider: AsyncKeyedLockProvider
    lock_data_source: AsyncDataSource
    #: What polls, sync, on threads: the scheduler, and fan-out and dispatch over every shard.
    events_registry: DatabaseRegistry
    job_registry: JobRegistry
    #: Each shard's `jobsConfig` pool. `events_registry` closes them: its managers were given
    #: them, and a manager closes the data sources it was given. The job registry does not.
    jobs_data_sources: tuple[DataSource, ...]

    def start(self) -> None:
        self.job_registry.start()

    def close(self) -> None:
        # Stop taking work first: this waits for the job bodies in flight, which are waiting on
        # the loop. Then what they awaited, closed on the loop it belongs to; then the loop.
        self.job_registry.stop()
        self.lock_provider.close()
        self.loop.run(self.lock_data_source.aclose())
        self.loop.run(self.database_registry.aclose())
        self.loop.close()
        self.events_registry.close()


def build(
    *,
    config_file: Path = CONFIG_FILE,
    clock: Clock | None = None,
    workers: int = 4,
) -> Worker:
    """Wire the worker on `config_file`. Its `${...}` placeholders are read from the process
    environment. `workers` - the job threads - has no key in Java, whose job bodies run on
    virtual threads."""
    clock = clock or SystemClock()
    ekbatan = load_ekbatan_config(config_file)
    layout = ekbatan.sharding
    # Every shard, one at a time, before the scheduler can start a job against a schema that
    # might not be there yet.
    migrate(layout, MIGRATIONS)

    # -- the work, on the worker loop -----------------------------------------------------------
    # Built here, used only there: an async pool belongs to the loop it is used on. Building it
    # opens no connection; the loop's first use does.
    loop = HandlerLoop(name="worker-loop")
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
            .with_action(CreateNotificationAction(clock))
            .build()
        )
        .clock(clock)
        .build()
    )
    default_member = layout.groups[0].members[0]
    lock_data_source = AsyncDataSource.from_config(_required(default_member, "lockConfig"))
    lock_provider = AsyncPostgresKeyedLockProvider.builder().data_source(lock_data_source).build()
    handlers = (
        EventHandlerRegistry.builder()
        .with_handler(WalletMoneyDepositedEventHandler(executor))
        .build()
    )

    # -- what polls, on threads -----------------------------------------------------------------
    # A sync registry over every shard, on each member's `jobsConfig`: an event commits on its
    # wallet's shard, so fan-out and dispatch must reach every one.
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

    return Worker(
        loop=loop,
        database_registry=database_registry,
        wallet_repository=wallet_repository,
        notification_repository=notification_repository,
        executor=executor,
        lock_provider=lock_provider,
        lock_data_source=lock_data_source,
        events_registry=events_registry,
        jobs_data_sources=tuple(jobs_data_sources.values()),
        job_registry=(
            job_registry()
            .data_source(jobs_data_sources[layout.default_shard])
            .with_jobs(
                [
                    # The event pipeline is two jobs like any other, claimed through the same
                    # table - which is how a cluster fans out and dispatches once rather than
                    # once per replica.
                    EventFanoutJob.builder()
                    .registry(events_registry)
                    .handlers(handlers)
                    .config(events_config)
                    .clock(clock)
                    .build(),
                    EventHandlingJob.builder()
                    .registry(events_registry)
                    .handlers(handlers)
                    # The async handler runs on the worker loop, beside the jobs' work: the
                    # executor it awaits was built for that loop.
                    .handler_loop(loop)
                    .config(events_config)
                    # Off unless `handling.enabled` is set in `ekbatan.yaml`.
                    .enabled(events_config.handling.enabled)
                    .clock(clock)
                    .build(),
                    WalletStipendJob(loop, executor, lock_provider, wallet_repository),
                    WalletReportJob(loop, wallet_repository),
                ]
            )
            .with_config(ekbatan.jobs)
            .workers(workers)
            .build()
        ),
    )


def _required(member: ShardMemberConfig, name: str) -> DataSourceConfig:
    """The named pool's settings, or an error naming where the setting goes."""
    found = member.config_for(name)
    if found is None:
        raise ValueError(f"{member.name or 'member'}.configs.{name} is required")
    return found
