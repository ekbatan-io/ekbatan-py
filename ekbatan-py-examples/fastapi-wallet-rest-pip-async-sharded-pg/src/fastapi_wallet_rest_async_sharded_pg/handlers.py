"""Reacting to your own events, in the same process - after the deposit has committed.

The deposit commits on its wallet's shard; fan-out, which reads every shard, turns the event into
a delivery on that same shard; dispatch runs this handler. It is `async def`, so dispatch runs
it on its handler loop - one event loop on a thread of its own - where it awaits an
`AsyncActionExecutor` of its own. That executor and its registry belong to the handler loop:
they are built for the handler and closed on that loop at shutdown (`app.py`). Nothing here
knows how many shards there are.
"""

from __future__ import annotations

from ekbatan.aio import AsyncActionExecutor
from ekbatan.events import EventEnvelope
from ekbatan.ids import ShardedId

from .actions import RaiseAlertAction, RaiseAlertParams
from .model import MoneyDepositedEvent, Wallet


class MoneyDepositedEventHandler:
    """Tells the owner money arrived, by starting another unit of work - awaited."""

    #: Cluster-stable: stored in `event_notifications.handler_name`.
    name = "wallet-deposit-alert"
    event_type = MoneyDepositedEvent

    def __init__(self, executor: AsyncActionExecutor) -> None:
        self.executor = executor

    async def handle(self, envelope: EventEnvelope[MoneyDepositedEvent]) -> None:
        await self.executor.execute(
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
