"""A worker process: jobs, no HTTP - with their work on `ekbatan.aio`.

The wiring is the same as any other service's minus the web layer - which is the point. A
deployment usually runs both shapes from one image: the API replicas serve requests, the worker
replicas run this, and the jobs claim their rows against the same database either way.

**Jobs on threads, their work on a loop.** Distributed jobs are sync: db-scheduler claims a
job's row and calls `execute` on one of its worker threads. Here each job hands its work to the
**worker loop** - a `HandlerLoop`, one event loop on a thread of its own - with `loop.run(...)`
and waits for it, so the work awaits an `AsyncActionExecutor` and async repositories, on
asyncpg. The event handler is `async def` and runs on the same loop: dispatch is handed it.
Everything that awaits was built for that loop and is closed on it, last.

Two pools:

**The work** gets `primaryConfig`, async, used only on the worker loop - by the jobs' bodies and
the event handler.

**The scheduler** gets `jobsConfig`, sync, sized above the worker count: db-scheduler, and the
fan-out and dispatch jobs' reads and writes of the queue. db-scheduler writes heartbeats from a
housekeeping thread; a job body holding the last connection starves it, and an execution whose
heartbeat stops is declared dead and run again somewhere else. The framework warns at startup
when the two are mismatched - listen to it.

**SIGTERM is the one exit that needs catching.** A started registry drains on any normal exit
and on Ctrl-C by default - it registers with `atexit`, which is additive, like Java's hook. But
CPython kills the process on SIGTERM without running `atexit`, so a container stop would end
it with executions still picked and their rows waiting out the dead-execution window.
`__main__.run()` catches the signal and calls `close()`, which is what makes a rolling deploy
clean rather than merely survivable.
"""

from __future__ import annotations

from collections.abc import Mapping
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
)
from ekbatan.clock import Clock, SystemClock
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob, HandlerLoop
from ekbatan.migrations import migrate

from .actions import (
    CloseWalletAction,
    DepositMoneyAction,
    OpenWalletAction,
    RaiseAlertAction,
)
from .handlers import MoneyDepositedEventHandler
from .jobs import PayStipendJob, WalletReportJob
from .repository import AlertRepository, WalletRepository
from .schema import EVENT_NOTIFICATIONS, EVENTS

#: Where the migration files live - `V<version>__<description>.sql`. Migrations run from files
#: rather than from `create_all`, which is the difference between an example and something you
#: could deploy: `create_all` has no answer for a column that changed.
MIGRATIONS = Path(__file__).resolve().parent / "migrations"

#: The worker's Ekbatan settings - database layout, namespace, polling interval, event handling -
#: in the same keys as the Java job worker's `application.yml`.
CONFIG_FILE = Path(__file__).resolve().parent / "ekbatan.yaml"


@dataclass(frozen=True, slots=True)
class Worker:
    #: The loop the jobs' work and the event handler run on, and what they await on it.
    loop: HandlerLoop
    registry: AsyncDatabaseRegistry
    wallets: WalletRepository
    alerts: AlertRepository
    executor: AsyncActionExecutor
    report: WalletReportJob
    #: What polls, sync, on threads: the scheduler, and fan-out and dispatch over
    #: `events_registry`.
    events_registry: DatabaseRegistry
    jobs: JobRegistry
    #: Built from the member's `jobsConfig`. The registries own only what they built, so this
    #: one is disposed here.
    jobs_engine: Engine

    def start(self) -> None:
        self.jobs.start()

    def close(self) -> None:
        # Stop taking work first: this waits for the job bodies in flight, which are waiting on
        # the loop. Then what they awaited, closed on the loop it belongs to; then the loop.
        self.jobs.stop()
        self.loop.run(self.registry.aclose())
        self.loop.close()
        self.events_registry.close()
        self.jobs_engine.dispose()


def build(
    environ: Mapping[str, str] | None = None,
    *,
    config_file: Path = CONFIG_FILE,
    clock: Clock | None = None,
    workers: int = 4,
) -> Worker:
    """Wire the worker on `config_file`; `environ` fills its `${...}` placeholders, `os.environ`
    when left out. `workers` has no key in Java, whose job bodies run on virtual threads."""
    running = clock or SystemClock()
    ekbatan = load_ekbatan_config(config_file, environ=environ)
    layout = ekbatan.sharding
    migrate(layout, MIGRATIONS)

    # -- the work, on the worker loop -----------------------------------------------------------
    # Built here, used only there: an async pool belongs to the loop it is used on. Building it
    # opens no connection; the loop's first use does.
    loop = HandlerLoop(name="worker-loop")
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
            .with_action(RaiseAlertAction(running))
            .build()
        )
        .event_persister(AsyncSingleTableJsonEventPersister(registry, EVENTS))
        .clock(running)
        .build()
    )
    handlers = (
        EventHandlerRegistry.builder().with_handler(MoneyDepositedEventHandler(executor)).build()
    )
    report = WalletReportJob(loop, wallets)

    # -- what polls, on threads -----------------------------------------------------------------
    jobs_config = layout.groups[0].members[0].config_for("jobsConfig")
    if jobs_config is None:
        raise ValueError("groups[0].members[0].configs.jobsConfig is required")
    jobs_engine = engine_for(jobs_config)
    events_registry = (
        DatabaseRegistry.builder()
        .with_default_database(TransactionManager(layout.default_shard, jobs_engine))
        .build()
    )
    events_config = ekbatan.local_event_handler

    return Worker(
        loop=loop,
        registry=registry,
        wallets=wallets,
        alerts=alerts,
        executor=executor,
        report=report,
        events_registry=events_registry,
        jobs_engine=jobs_engine,
        jobs=(
            job_registry()
            .engine(jobs_engine)
            .with_jobs(
                [
                    # The event pipeline is two jobs like any other, claimed through the same
                    # table - which is how a cluster fans out and dispatches once rather than
                    # once per replica.
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
                    # The async handler runs on the worker loop, beside the jobs' work: the
                    # executor it awaits was built for that loop.
                    .handler_loop(loop)
                    .notifications(EVENT_NOTIFICATIONS)
                    .config(events_config)
                    # Java's DI makes this job only if `handling.enabled`; this is that check.
                    .enabled(events_config.handling.enabled)
                    .clock(running)
                    .build(),
                    PayStipendJob(loop, executor, wallets),
                    report,
                ]
            )
            .with_config(ekbatan.jobs)
            .workers(workers)
            .build()
        ),
    )
