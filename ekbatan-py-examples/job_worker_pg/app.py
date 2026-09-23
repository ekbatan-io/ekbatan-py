"""A worker process: jobs, no HTTP.

The wiring is the same as any other service's minus the web layer - which is the point. A
deployment usually runs both shapes from one image: the API replicas serve requests, the worker
replicas run this, and the jobs claim their rows against the same database either way.

Two things here are worth copying into a real worker.

**The scheduler gets its own engine**, sized above the worker count. db-scheduler writes
heartbeats from a housekeeping thread; a job body holding the last connection starves it, and an
execution whose heartbeat stops is declared dead and run again somewhere else. The framework
warns at startup when the two are mismatched - listen to it.

**SIGTERM is the one exit that needs catching.** A started registry drains on any normal exit
and on Ctrl-C by default - it registers with `atexit`, which is additive, like Java's hook. But
CPython kills the process on SIGTERM without running `atexit`, so a container stop would end
it with executions still picked and their rows waiting out the dead-execution window.
`__main__.run()` catches the signal and calls `stop()`, which is what makes a rolling deploy
clean rather than merely survivable.

`JobRegistryBuilder.register_shutdown_hook()` does the same thing inside the framework, and
correctly - it keeps the handler that was there and calls it afterwards. It is off by default
because CPython has one handler slot per signal, where a JVM shutdown hook is additive and
Java's `JobRegistry` can therefore turn its own on without asking.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine

from ekbatan import (
    ActionExecutor,
    ActionRegistry,
    DatabaseRegistry,
    RepositoryRegistry,
    ShardingConfig,
    engine_for,
)
from ekbatan.clock import Clock, SystemClock
from ekbatan.config import DataSourceConfig, ShardGroupConfig, ShardMemberConfig
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import (
    EventFanoutJob,
    EventHandlerRegistry,
    EventHandlingJob,
    HandlingConfig,
    LocalEventHandlerConfig,
)
from ekbatan.migrations import migrate
from ekbatan.sharding import DEFAULT_SHARD

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

DEFAULT_URL = os.environ.get(
    "WORKER_DATABASE_URL", "postgresql+psycopg://ekbatan:ekbatan@localhost:5432/wallet"
)

#: Where the Alembic revisions live. Migrations run from files, not from `create_all`, which is
#: the difference between an example and something you could deploy: `create_all` has no answer
#: for a column that changed.
MIGRATIONS = Path(__file__).resolve().parent / "migrations"


@dataclass(frozen=True, slots=True)
class Worker:
    registry: DatabaseRegistry
    wallets: WalletRepository
    alerts: AlertRepository
    executor: ActionExecutor
    report: WalletReportJob
    jobs: JobRegistry
    #: Built from the member's `jobsConfig`. The registry owns only the pools it built, so this
    #: one is disposed here.
    jobs_engine: Engine

    def start(self) -> None:
        self.jobs.start()

    def close(self) -> None:
        self.jobs.stop()
        self.jobs_engine.dispose()
        self.registry.close()


def build(url: str = DEFAULT_URL, *, clock: Clock | None = None, workers: int = 4) -> Worker:
    running = clock or SystemClock()
    # Java's layout: one group, one member, and the scheduler's pool declared on the member as
    # `jobsConfig` beside the primary - more connections than workers, see the module docstring.
    layout = ShardingConfig(
        default_shard=DEFAULT_SHARD,
        groups=(
            ShardGroupConfig(
                group=0,
                name="global",
                members=(
                    ShardMemberConfig(
                        member=0,
                        name="global",
                        configs={
                            "primaryConfig": DataSourceConfig(url=url),
                            "jobsConfig": DataSourceConfig(url=url, pool_size=workers * 2),
                        },
                    ),
                ),
            ),
        ),
    )
    migrate(layout, MIGRATIONS)
    registry = DatabaseRegistry.from_config(layout)

    wallets = WalletRepository(registry, clock=running)
    alerts = AlertRepository(registry, clock=running)

    executor = ActionExecutor(
        namespace="example.job_worker_pg",
        registry=registry,
        repositories=(
            RepositoryRegistry.builder()
            .with_model_repository(wallets)
            .with_entity_repository(alerts)
            .build()
        ),
        actions=(
            ActionRegistry.builder()
            .with_action(OpenWalletAction(running))
            .with_action(DepositMoneyAction(wallets, running))
            .with_action(CloseWalletAction(wallets, running))
            .with_action(RaiseAlertAction(running))
            .build()
        ),
        events=EVENTS,
        clock=running,
    )

    handlers = EventHandlerRegistry([MoneyDepositedEventHandler(executor)])
    events_config = LocalEventHandlerConfig(handling=HandlingConfig(enabled=True))
    report = WalletReportJob(wallets)
    jobs_config = layout.groups[0].members[0].config_for("jobsConfig")
    if jobs_config is None:
        raise ValueError("groups[0].members[0].configs.jobsConfig is required")
    jobs_engine = engine_for(jobs_config)

    return Worker(
        registry=registry,
        wallets=wallets,
        alerts=alerts,
        executor=executor,
        report=report,
        jobs_engine=jobs_engine,
        jobs=(
            job_registry()
            .engine(jobs_engine)
            .with_jobs(
                [
                    # The event pipeline is two jobs like any other, claimed through the same
                    # table - which is how a cluster fans out and dispatches once rather than
                    # once per replica.
                    EventFanoutJob(
                        registry,
                        handlers,
                        events=EVENTS,
                        notifications=EVENT_NOTIFICATIONS,
                        config=events_config,
                        clock=running,
                    ),
                    EventHandlingJob(
                        registry,
                        handlers,
                        notifications=EVENT_NOTIFICATIONS,
                        config=events_config,
                        clock=running,
                    ),
                    PayStipendJob(executor, wallets),
                    report,
                ]
            )
            .workers(workers)
            .build()
        ),
    )
