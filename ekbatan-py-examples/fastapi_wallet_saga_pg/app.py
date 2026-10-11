"""The wiring.

PostgreSQL, as Java's `spring-boot-wallet-saga-gradle-pg` uses - a saga is about steps that
commit independently, and a database that serialises every writer makes that harder to see
rather than easier. For the zero-setup read, `wallet_service_sqlite` is the one to start with.

The fan-out and dispatch jobs are driven by hand here, through `deliver_pending_events`, so the
steps happen at points the reader can see rather than whenever a background thread got round to
it. In a service they are two distributed jobs and nobody calls them - `fastapi_wallet_rest_pg`
shows that shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ekbatan import (
    ActionExecutor,
    ActionRegistry,
    DatabaseRegistry,
    RepositoryRegistry,
    ShardingConfig,
    SingleTableJsonEventPersister,
)
from ekbatan.clock import Clock, SystemClock
from ekbatan.events import (
    EventDispatch,
    EventFanout,
    EventHandlerRegistry,
)
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


@dataclass(frozen=True, slots=True)
class SagaService:
    registry: DatabaseRegistry
    wallets: WalletRepository
    steps: TransferStepRepository
    executor: ActionExecutor
    fanout: EventFanout
    dispatch: EventDispatch

    def deliver_pending_events(self) -> int:
        """Run the saga forward until nothing is left to deliver.

        Each round turns committed events into deliveries and runs them; a delivery can commit
        a new event, which the next round picks up. So one call walks the whole chain -
        initiate, complete, and if needed fail and refund - and returns how many deliveries ran.
        """
        delivered = 0
        while True:
            self.fanout.drain_until_idle()
            ran = self.dispatch.drain_until_idle()
            delivered += ran
            if ran == 0:
                return delivered

    def close(self) -> None:
        self.registry.close()


def build(
    url: str,
    *,
    username: str | None = None,
    password: str | None = None,
    clock: Clock | None = None,
) -> SagaService:
    running = clock or SystemClock()
    # The login is kept apart from the url, which is logged and repeated.
    layout = ShardingConfig.single(url, username=username, password=password)
    migrate(layout, MIGRATIONS)
    registry = DatabaseRegistry.from_config(layout)

    wallets = WalletRepository(registry, clock=running)
    steps = TransferStepRepository(registry, clock=running)

    executor = (
        ActionExecutor.builder()
        .namespace("example.fastapi_wallet_saga_pg")
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

    return SagaService(
        registry=registry,
        wallets=wallets,
        steps=steps,
        executor=executor,
        fanout=EventFanout(
            registry, handlers, events=EVENTS, notifications=EVENT_NOTIFICATIONS, clock=running
        ),
        dispatch=EventDispatch(
            registry, handlers, notifications=EVENT_NOTIFICATIONS, clock=running
        ),
    )
