"""Reacting to your own events, in the same process - on a thread, as handlers run.

No broker, no connector, no second runtime. The unit of work commits, fan-out turns each
committed event into work, and dispatch runs it - at least once, which is the only guarantee
anything on this side of a database can honestly make.

The HTTP requests are async; event handlers are not, and do not need to be: dispatch runs a
batch of them at once on its own threads, away from the event loop. So the handler's unit of
work is a sync `Action`, run by a sync `ActionExecutor` over a sync registry built from the
same `ekbatan.yaml`. A handler reads its envelope and nothing else, which is what would let it
move into a broker consumer later without its body changing.
"""

from __future__ import annotations

from dataclasses import dataclass

from ekbatan import Action, ActionExecutor
from ekbatan.events import EventEnvelope
from ekbatan.ids import ShardedId

from .model import Alert, EmailAddress, MoneyDepositedEvent, Wallet


@dataclass(frozen=True, kw_only=True)
class RaiseAlertParams:
    wallet_id: ShardedId[Wallet]
    recipient: str
    message: str


class RaiseAlertAction(Action[RaiseAlertParams, Alert]):
    """Writes an entity. Emits nothing, and is still audited."""

    def perform(self, principal: str | None, params: RaiseAlertParams) -> Alert:
        return self.plan.add(
            Alert.create(
                wallet_id=params.wallet_id,
                recipient=EmailAddress(params.recipient),
                message=params.message,
            )
        )


class MoneyDepositedEventHandler:
    """Tells the owner money arrived, by starting another unit of work."""

    name = "wallet-deposit-alert"
    event_type = MoneyDepositedEvent

    def __init__(self, executor: ActionExecutor) -> None:
        self.executor = executor

    def handle(self, envelope: EventEnvelope[MoneyDepositedEvent]) -> None:
        self.executor.execute(
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
