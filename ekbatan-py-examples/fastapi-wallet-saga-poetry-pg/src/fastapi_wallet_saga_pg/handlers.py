"""The saga's other two steps, each started by the event the step before it committed.

No coordinator holds the saga's state: each step is a unit of work of its own, and the outbox
and the step markers carry it from one to the next - at least once, which the markers make safe.
"""

from __future__ import annotations

import logging

from ekbatan import ActionExecutor
from ekbatan.events import EventEnvelope, EventHandler
from ekbatan.ids import Id

from .actions import CompleteTransferAction, RefundTransferAction
from .model import TransferFailedEvent, TransferInitiatedEvent, Wallet

LOG = logging.getLogger(__name__)


class TransferInitiatedEventHandler(EventHandler[TransferInitiatedEvent]):
    """Step one committed: run step two."""

    def __init__(self, executor: ActionExecutor) -> None:
        self.executor = executor

    def name(self) -> str:
        # Cluster-stable; stored in `event_notifications.handler_name`.
        return "transfer-initiated-handler"

    def event_type(self) -> type[TransferInitiatedEvent]:
        return TransferInitiatedEvent

    def handle(self, envelope: EventEnvelope[TransferInitiatedEvent]) -> None:
        event = envelope.event
        LOG.info("Transfer %s initiated; invoking CompleteTransferAction", event.transfer_id)
        self.executor.execute(
            CompleteTransferAction,
            CompleteTransferAction.Params(
                transfer_id=event.transfer_id,
                from_wallet_id=Id[Wallet].of(event.from_wallet_id),
                to_wallet_id=Id[Wallet].of(event.to_wallet_id),
                amount=event.amount,
            ),
            principal="transfer-saga",
        )


class TransferFailedEventHandler(EventHandler[TransferFailedEvent]):
    """Step two could not credit the destination: refund the source."""

    def __init__(self, executor: ActionExecutor) -> None:
        self.executor = executor

    def name(self) -> str:
        return "transfer-failed-handler"

    def event_type(self) -> type[TransferFailedEvent]:
        return TransferFailedEvent

    def handle(self, envelope: EventEnvelope[TransferFailedEvent]) -> None:
        event = envelope.event
        LOG.info(
            "Transfer %s failed (%s); invoking RefundTransferAction",
            event.transfer_id,
            event.reason,
        )
        self.executor.execute(
            RefundTransferAction,
            RefundTransferAction.Params(
                transfer_id=event.transfer_id,
                from_wallet_id=Id[Wallet].of(event.from_wallet_id),
                amount=event.amount,
            ),
            principal="transfer-saga",
        )
