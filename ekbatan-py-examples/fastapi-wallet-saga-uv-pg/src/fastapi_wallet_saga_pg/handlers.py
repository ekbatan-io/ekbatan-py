"""What chains one step to the next.

There is no saga coordinator here, and that is the design rather than an omission. Each handler
knows exactly one thing: given *this* event, run *that* action. Adding a fourth step means
writing a fifth event and a third handler, and changing nothing that already works.

Both handlers read `envelope.event` and nothing else. The events carry the whole transfer, so
neither needs the producing action's parameters, another row, or any knowledge of where the
other wallet lives.
"""

from __future__ import annotations

from ekbatan import ActionExecutor
from ekbatan.events import EventEnvelope
from ekbatan.ids import ShardedId

from .actions import (
    CompleteTransferAction,
    CompleteTransferParams,
    RefundTransferAction,
    RefundTransferParams,
)
from .model import TransferFailedEvent, TransferInitiatedEvent, Wallet


class TransferInitiatedEventHandler:
    """Step one committed; run step two."""

    name = "transfer-initiated"
    event_type = TransferInitiatedEvent

    def __init__(self, executor: ActionExecutor) -> None:
        self.executor = executor

    def handle(self, envelope: EventEnvelope[TransferInitiatedEvent]) -> None:
        event = envelope.event
        self.executor.execute(
            CompleteTransferAction,
            CompleteTransferParams(
                transfer_id=event.transfer_id,
                from_wallet_id=ShardedId[Wallet](event.from_wallet_id),
                to_wallet_id=ShardedId[Wallet](event.to_wallet_id),
                amount=event.amount,
                currency=event.currency,
            ),
        )


class TransferFailedEventHandler:
    """Step two could not finish; compensate.

    Subscribed to `TransferFailedEvent`, which `CompleteTransferAction` emitted on the
    *source* wallet -
    so this handler is triggered by a business outcome that was committed like any other,
    rather than by an exception that would have been retried forever.
    """

    name = "transfer-failed"
    event_type = TransferFailedEvent

    def __init__(self, executor: ActionExecutor) -> None:
        self.executor = executor

    def handle(self, envelope: EventEnvelope[TransferFailedEvent]) -> None:
        event = envelope.event
        self.executor.execute(
            RefundTransferAction,
            RefundTransferParams(
                transfer_id=event.transfer_id,
                from_wallet_id=ShardedId[Wallet](event.from_wallet_id),
                to_wallet_id=ShardedId[Wallet](event.to_wallet_id),
                amount=event.amount,
                reason=event.reason,
            ),
        )
