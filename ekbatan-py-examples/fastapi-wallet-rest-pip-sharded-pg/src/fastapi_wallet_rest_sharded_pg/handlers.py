"""Reacting to your own events, in the same process - Java's `WalletMoneyDepositedEventHandler`.

The deposit commits on its wallet's shard; fan-out, which reads every shard, turns the event into
a delivery on that same shard; the handling job runs this handler, which starts a unit of work of
its own. Nothing here knows how many shards there are.
"""

from __future__ import annotations

from ekbatan import ActionExecutor
from ekbatan.events import EventEnvelope
from ekbatan.ids import ShardedId

from .actions import RaiseAlertAction, RaiseAlertParams
from .model import MoneyDepositedEvent, Wallet


class MoneyDepositedEventHandler:
    """Tells the owner money arrived, by starting another unit of work."""

    #: Cluster-stable: stored in `event_notifications.handler_name`.
    name = "wallet-deposit-alert"
    event_type = MoneyDepositedEvent

    def __init__(self, executor: ActionExecutor) -> None:
        self.executor = executor

    def handle(self, envelope: EventEnvelope[MoneyDepositedEvent]) -> None:
        self.executor.execute(
            RaiseAlertAction,
            RaiseAlertParams(
                wallet_id=ShardedId[Wallet].of(envelope.model_id or ""),
                # Off the producing action's parameters, not the event: where to send a message
                # is this handler's business, not the wallet's.
                recipient=str(envelope.action_params.get("recipient", "")),
                message=f"Deposit of {envelope.event.amount} received. "
                f"New balance: {envelope.event.new_balance}.",
            ),
        )
