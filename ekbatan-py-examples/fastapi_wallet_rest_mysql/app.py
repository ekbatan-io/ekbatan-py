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
traffic. Java's examples carry the same three, under a `lockConfig` slot in `application.yml`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine

from ekbatan import (
    ActionExecutor,
    ActionRegistry,
    DatabaseRegistry,
    RepositoryRegistry,
    ShardingConfig,
)
from ekbatan.clock import Clock, SystemClock
from ekbatan.config import DataSourceConfig, ShardConfig
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import (
    EventFanoutJob,
    EventHandlerRegistry,
    EventHandlingJob,
    HandlingConfig,
    LocalEventHandlerConfig,
)
from ekbatan.locks import KeyedLockProvider, MySQLKeyedLockProvider
from ekbatan.migrations import migrate
from ekbatan.sharding import DEFAULT_SHARD

from .actions import CloseWalletAction, DepositMoneyAction, OpenWalletAction, RaiseAlertAction
from .handlers import MoneyDepositedEventHandler
from .jobs import PayStipendJob
from .repository import AlertRepository, WalletRepository
from .schema import EVENT_NOTIFICATIONS, EVENTS

#: Where the Alembic revisions live. Migrations run from files here rather than from
#: `create_all`, which is the difference between an example and something you could deploy:
#: `create_all` has no answer for a column that changed.
MIGRATIONS = Path(__file__).resolve().parent / "migrations"

DEFAULT_URL = "mysql+pymysql://root:ekbatan@localhost:3306/wallet"


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the service needs to know about its environment.

    Read from the environment so the same image runs in every environment, and defaulted
    so that running the module works against a local server with no setup.
    """

    url: str = os.environ.get("WALLET_DATABASE_URL", DEFAULT_URL)
    #: Connections for serving requests.
    pool_size: int = int(os.environ.get("WALLET_POOL_SIZE", "10"))
    #: Connections for the scheduler. At least twice `job_workers`: a job body holding the
    #: last connection would starve the heartbeat, and an execution whose heartbeat stops is
    #: declared dead and run again elsewhere. db-scheduler warns at startup if this is short -
    #: and did, while this example was being written, which is why the default is not 3.
    job_pool_size: int = int(os.environ.get("WALLET_JOB_POOL_SIZE", "8"))
    #: Connections for held lock leases. One per lease that can be held at once.
    lock_pool_size: int = int(os.environ.get("WALLET_LOCK_POOL_SIZE", "5"))
    #: Threads running job bodies. Java gets unbounded virtual threads and needs no such
    #: setting; here the workers are real OS threads, so the number is chosen.
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

    def start(self) -> None:
        """Begin polling for jobs. Fan-out and dispatch are two of them, so this is also what
        starts the event pipeline."""
        self.jobs.start()

    def close(self) -> None:
        # Order matters: stop taking work before closing what the work runs on.
        self.jobs.stop()
        self.locks.close()
        self.registry.close()


def build(settings: Settings | None = None, *, clock: Clock | None = None) -> WalletService:
    """Wire the service. Nothing here is specific to HTTP."""
    config = settings or Settings()
    running = clock or SystemClock()

    layout = ShardingConfig(
        shards=(
            ShardConfig(
                shard=DEFAULT_SHARD,
                primary=DataSourceConfig(url=config.url, pool_size=config.pool_size),
            ),
        ),
        default_shard=DEFAULT_SHARD,
    )

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

    executor = ActionExecutor(
        namespace="example.fastapi_wallet_rest_mysql",
        registry=registry,
        repositories=repositories,
        actions=(
            ActionRegistry.builder()
            .with_action(OpenWalletAction())
            .with_action(DepositMoneyAction(wallets))
            .with_action(CloseWalletAction(wallets))
            .with_action(RaiseAlertAction())
            .build()
        ),
        events=EVENTS,
        clock=running,
    )

    handlers = EventHandlerRegistry([MoneyDepositedEventHandler(executor)])
    events_config = LocalEventHandlerConfig(handling=HandlingConfig(enabled=True))

    return WalletService(
        registry=registry,
        wallets=wallets,
        alerts=alerts,
        executor=executor,
        locks=MySQLKeyedLockProvider(
            create_engine(config.url, pool_size=config.lock_pool_size, max_overflow=0)
        ),
        jobs=(
            job_registry()
            .engine(create_engine(config.url, pool_size=config.job_pool_size, max_overflow=0))
            .with_jobs(
                [
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
                ]
            )
            .workers(config.job_workers)
            .build()
        ),
    )
