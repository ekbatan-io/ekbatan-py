"""The wiring.

Deliberately the same shape as `wallet_service`'s, and deliberately on SQLite: this example is
about the saga, and a reader should be able to run it in one command and watch three
transactions happen in order. `wallet_api` is the one that shows what a deployment looks like.

The fan-out and dispatch jobs are driven by hand here, through `deliver_pending_events`, so the
steps happen at points the reader can see rather than whenever a background thread got round to
it. In a service they are two distributed jobs and nobody calls them.
"""

from __future__ import annotations

from dataclasses import dataclass

from ekbatan import (
    ActionExecutor,
    ActionRegistry,
    DatabaseRegistry,
    RepositoryRegistry,
    ShardingConfig,
)
from ekbatan.clock import Clock, SystemClock
from ekbatan.events import (
    EventDispatch,
    EventFanout,
    EventHandlerRegistry,
)
from ekbatan.migrations import migrate_registry

from .actions import (
    CompleteTransferAction,
    InitiateTransferAction,
    OpenWalletAction,
    RefundTransferAction,
)
from .handlers import TransferFailedEventHandler, TransferInitiatedEventHandler
from .repository import TransferStepRepository, WalletRepository
from .schema import EVENT_NOTIFICATIONS, EVENTS, metadata


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


def build(url: str, *, clock: Clock | None = None) -> SagaService:
    running = clock or SystemClock()
    registry = DatabaseRegistry.from_config(ShardingConfig.single(url))
    migrate_registry(registry, metadata)

    wallets = WalletRepository(registry, clock=running)
    steps = TransferStepRepository(registry, clock=running)

    executor = ActionExecutor(
        namespace="example.wallet_saga",
        registry=registry,
        repositories=(
            RepositoryRegistry.builder()
            .with_model_repository(wallets)
            .with_entity_repository(steps)
            .build()
        ),
        actions=(
            ActionRegistry.builder()
            .with_action(OpenWalletAction())
            .with_action(InitiateTransferAction(wallets))
            .with_action(CompleteTransferAction(wallets))
            .with_action(RefundTransferAction(wallets))
            .build()
        ),
        events=EVENTS,
        clock=running,
    )

    handlers = EventHandlerRegistry(
        [TransferInitiatedEventHandler(executor), TransferFailedEventHandler(executor)]
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
