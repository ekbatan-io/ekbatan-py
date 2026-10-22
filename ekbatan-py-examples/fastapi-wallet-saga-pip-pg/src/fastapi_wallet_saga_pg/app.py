"""The wiring.

PostgreSQL, as Java's `spring-boot-wallet-saga-gradle-pg` uses - a saga is about steps that
commit independently, and a database that serialises every writer makes that harder to see
rather than easier. For the zero-setup read, `wallet_service_sqlite` is the one to start with.

Every step after the first is started by the event pipeline: the fan-out and handling jobs run
in the background, as in any service, and nobody calls them. `ekbatan.yaml` switches handling
on, as Java's `application.yml` does - without it the saga stops after its first step.
"""

from __future__ import annotations

from dataclasses import dataclass
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
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob
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
    registry: DatabaseRegistry
    wallets: WalletRepository
    steps: TransferStepRepository
    executor: ActionExecutor
    jobs: JobRegistry
    #: Built from the member's `jobsConfig`. The registry owns only the pools it built, so this
    #: one is disposed here.
    jobs_engine: Engine

    def start(self) -> None:
        """Begin polling. Fan-out and handling are two of the jobs, so this is what lets the saga
        move past its first step."""
        self.jobs.start()

    def close(self) -> None:
        # Stop taking work before closing what the work runs on.
        self.jobs.stop()
        self.jobs_engine.dispose()
        self.registry.close()


def build(
    *,
    config_file: Path = CONFIG_FILE,
    clock: Clock | None = None,
) -> SagaService:
    """Wire the service on `config_file`; its `${...}` placeholders are read from the process
    environment."""
    running = clock or SystemClock()
    # `ekbatan.yaml`'s `${...}` placeholders - the url and the login - are read from the
    # process environment as the file is loaded, each by its name.
    ekbatan = load_ekbatan_config(config_file)
    layout = ekbatan.sharding
    migrate(layout, MIGRATIONS)
    registry = DatabaseRegistry.from_config(layout)

    wallets = WalletRepository(registry, clock=running)
    steps = TransferStepRepository(registry, clock=running)

    executor = (
        ActionExecutor.builder()
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
            .with_action(CompleteTransferAction(wallets, running))
            .with_action(RefundTransferAction(wallets, running))
            .build()
        )
        .event_persister(SingleTableJsonEventPersister(registry, EVENTS))
        .clock(running)
        .build()
    )

    handlers = (
        EventHandlerRegistry.builder()
        .with_handler(TransferInitiatedEventHandler(executor))
        .with_handler(TransferFailedEventHandler(executor))
        .build()
    )

    jobs_config = layout.groups[0].members[0].config_for("jobsConfig")
    if jobs_config is None:
        raise ValueError("groups[0].members[0].configs.jobsConfig is required")
    jobs_engine = engine_for(jobs_config)
    events_config = ekbatan.local_event_handler

    return SagaService(
        registry=registry,
        wallets=wallets,
        steps=steps,
        executor=executor,
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
                ]
            )
            .with_config(ekbatan.jobs)
            .build()
        ),
    )
