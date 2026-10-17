"""The wiring, all of it in one place.

Which database, which pools, which handlers, which jobs. Nothing above this file knows any of
it, and nothing below it does either - `api.py` is handed a built `WalletService` and asks it
for work.

Three separate pools, which is the part worth copying into a real deployment:

**Requests** get the main engine. Sized for how many requests you serve at once.

**Jobs** get their own. db-scheduler polls continuously and writes a heartbeat from a
housekeeping thread; sharing the request pool means a burst of traffic can starve that
heartbeat, and an execution whose heartbeat stops is declared dead and run again somewhere
else. A pool of its own costs a handful of connections and removes the failure mode.

**Locks** get a third. A keyed lock holds its connection for as long as the lease is held - that
is what the lock *is* - so a lease taken during a request would otherwise sit on a request
connection for the duration. Size it for the maximum number of leases held at once, not for
traffic.

All three are declared on the shard member in `ekbatan.yaml`, the way Java's `application.yml`
declares them: `primaryConfig` for requests, and `jobsConfig` and `lockConfig` beside it. The
registry builds the first; the other two are this file's to build with `engine_for`, and this
file's to dispose. The same file holds the namespace and turns the event handling job on.
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
    engine_for,
)
from ekbatan.clock import Clock, SystemClock
from ekbatan.config import DataSourceConfig, ShardMemberConfig
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob
from ekbatan.locks import KeyedLockProvider, MySQLKeyedLockProvider
from ekbatan.migrations import migrate

from .actions import CloseWalletAction, DepositMoneyAction, OpenWalletAction, RaiseAlertAction
from .handlers import MoneyDepositedEventHandler
from .jobs import PayStipendJob
from .repository import AlertRepository, WalletRepository
from .schema import EVENT_NOTIFICATIONS, EVENTS

#: Where the migration files live - `V<version>__<description>.sql`. Migrations run from files
#: rather than from `create_all`, which is the difference between an example and something you
#: could deploy: `create_all` has no answer for a column that changed.
MIGRATIONS = Path(__file__).resolve().parent / "migrations"

#: The service's Ekbatan settings - database layout, namespace, event handling - in the same keys
#: as the Java examples' `application.yml`.
CONFIG_FILE = Path(__file__).resolve().parent / "ekbatan.yaml"


@dataclass(frozen=True, slots=True)
class Settings:
    """What the service needs from its environment beyond `ekbatan.yaml`.

    Read from the environment so the same image runs in every environment, and defaulted so
    that running the module works against a local server with no setup.
    """

    config_file: Path = Path(os.environ.get("WALLET_CONFIG_FILE", CONFIG_FILE))
    #: Where the file's `${...}` placeholders are looked up - the url and the login.
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)
    #: Threads running job bodies. Java gets unbounded virtual threads and has no such key;
    #: here the workers are real OS threads, so the number is chosen.
    job_workers: int = int(os.environ.get("WALLET_JOB_WORKERS", "3"))
    run_migrations: bool = os.environ.get("WALLET_MIGRATE", "1") != "0"


@dataclass(frozen=True, slots=True)
class WalletService:
    """Everything wired together, and the only object the HTTP layer is handed."""

    registry: DatabaseRegistry
    wallets: WalletRepository
    alerts: AlertRepository
    executor: ActionExecutor
    locks: KeyedLockProvider
    jobs: JobRegistry
    #: The pools built from `lockConfig` and `jobsConfig`. The registry owns only the ones it
    #: built, so these two are disposed here.
    lock_engine: Engine
    jobs_engine: Engine

    def start(self) -> None:
        """Begin polling for jobs. Fan-out and dispatch are two of them, so this is also what
        starts the event pipeline."""
        self.jobs.start()

    def close(self) -> None:
        # Order matters: stop taking work before closing what the work runs on.
        self.jobs.stop()
        self.locks.close()
        self.lock_engine.dispose()
        self.jobs_engine.dispose()
        self.registry.close()


def build(settings: Settings | None = None, *, clock: Clock | None = None) -> WalletService:
    """Wire the service. Nothing here is specific to HTTP."""
    config = settings or Settings()
    running = clock or SystemClock()

    ekbatan = load_ekbatan_config(config.config_file, environ=config.environ)
    layout = ekbatan.sharding

    if config.run_migrations:
        # Every shard, one at a time, fail-fast - and refused outright if a revision that was
        # already applied has since been edited. Run before anything opens a pool against a
        # schema that might not be there yet.
        migrate(layout, MIGRATIONS)

    registry = DatabaseRegistry.from_config(layout)

    wallets = WalletRepository(registry, clock=running)
    alerts = AlertRepository(registry, clock=running)

    repositories = (
        RepositoryRegistry.builder()
        .with_model_repository(wallets)
        .with_entity_repository(alerts)
        .build()
    )

    executor = (
        ActionExecutor.builder()
        .namespace(ekbatan.namespace)
        .database_registry(registry)
        .repository_registry(repositories)
        .action_registry(
            ActionRegistry.builder()
            .with_action(OpenWalletAction(running))
            .with_action(DepositMoneyAction(wallets, running))
            .with_action(CloseWalletAction(wallets, running))
            .with_action(RaiseAlertAction(running))
            .build()
        )
        .event_persister(SingleTableJsonEventPersister(registry, EVENTS))
        .clock(running)
        .build()
    )

    # Java's `shardingConfig.groups.getFirst().members.getFirst().configFor("lockConfig")`.
    member = layout.groups[0].members[0]
    lock_engine = engine_for(_required(member, "lockConfig"))
    jobs_engine = engine_for(_required(member, "jobsConfig"))

    handlers = (
        EventHandlerRegistry.builder().with_handler(MoneyDepositedEventHandler(executor)).build()
    )
    events_config = ekbatan.local_event_handler

    return WalletService(
        registry=registry,
        wallets=wallets,
        alerts=alerts,
        executor=executor,
        locks=MySQLKeyedLockProvider.builder().engine(lock_engine).build(),
        lock_engine=lock_engine,
        jobs_engine=jobs_engine,
        jobs=(
            job_registry()
            .engine(jobs_engine)
            .with_jobs(
                [
                    EventFanoutJob.builder()
                    .registry(registry)
                    .handlers(handlers)
                    .events(EVENTS)
                    .notifications(EVENT_NOTIFICATIONS)
                    .config(events_config)
                    .clock(running)
                    .build(),
                    EventHandlingJob.builder()
                    .registry(registry)
                    .handlers(handlers)
                    .notifications(EVENT_NOTIFICATIONS)
                    .config(events_config)
                    # Java's DI makes this job only if `handling.enabled`; this is that check.
                    .enabled(events_config.handling.enabled)
                    .clock(running)
                    .build(),
                    PayStipendJob(executor, wallets),
                ]
            )
            .with_config(ekbatan.jobs)
            .workers(config.job_workers)
            .build()
        ),
    )


def _required(member: ShardMemberConfig, name: str) -> DataSourceConfig:
    """Java's `configFor(name).orElseThrow(...)`, naming where the setting goes."""
    found = member.config_for(name)
    if found is None:
        raise ValueError(f"groups[0].members[0].configs.{name} is required")
    return found
