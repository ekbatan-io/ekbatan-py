"""Reacting to your own events, in the same process.

No broker, no connector, no second runtime. The unit of work commits, fan-out turns each
committed event into work, and dispatch runs it - at least once, which is the only guarantee
anything on this side of a database can honestly make.

A handler runs later than the action that emitted its event, never inside it: fan-out and
dispatch poll on their own threads. This one is `async def`, so dispatch runs it on its handler
loop - here the worker loop, the same one the jobs' work runs on - where it awaits an
`AsyncActionExecutor`. A handler reads its envelope and nothing else, which is what would let it
move into a broker consumer later without its body changing.
"""

from __future__ import annotations

from ekbatan.aio import AsyncActionExecutor
from ekbatan.events import EventEnvelope
from ekbatan.ids import ShardedId

from .actions import RaiseAlertAction, RaiseAlertParams
from .model import MoneyDepositedEvent, Wallet


class MoneyDepositedEventHandler:
    """Tells the owner money arrived, by starting another unit of work - awaited."""

    name = "wallet-deposit-alert"
    event_type = MoneyDepositedEvent

    def __init__(self, executor: AsyncActionExecutor) -> None:
        self.executor = executor

    async def handle(self, envelope: EventEnvelope[MoneyDepositedEvent]) -> None:
        await self.executor.execute(
            RaiseAlertAction,
            RaiseAlertParams(
                wallet_id=ShardedId[Wallet].of(envelope.model_id or ""),
                # Off the producing action's parameters, not the event. The event says what
                # happened to the wallet; where to send a message about it is this handler's
                # business and nobody else's.
                recipient=str(envelope.action_params.get("recipient", "")),
                message=f"{envelope.event.amount} received, balance {envelope.event.new_balance}",
            ),
        )
