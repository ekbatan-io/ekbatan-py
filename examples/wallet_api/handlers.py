"""Reacting to your own events, in the same process.

No broker, no connector, no second runtime. The unit of work commits, fan-out turns each
committed event into work, and dispatch runs it - at least once, which is the only guarantee
anything on this side of a database can honestly make.

A handler reads its envelope and nothing else. That is what would let this one move into a
broker consumer later without its body changing.
"""

from __future__ import annotations

from ekbatan import ActionExecutor
from ekbatan.events import EventEnvelope

from .actions import RaiseAlert, RaiseAlertParams
from .model import MoneyDeposited, WalletId


class NotifyOnDeposit:
    """Tells the owner money arrived, by starting another unit of work."""

    name = "wallet-deposit-alert"
    event_type = MoneyDeposited

    def __init__(self, executor: ActionExecutor) -> None:
        self.executor = executor

    def handle(self, envelope: EventEnvelope[MoneyDeposited]) -> None:
        self.executor.execute(
            RaiseAlert,
            RaiseAlertParams(
                wallet_id=WalletId.of(envelope.model_id or ""),
                # Off the producing action's parameters, not the event. The event says what
                # happened to the wallet; where to send a message about it is this handler's
                # business and nobody else's.
                recipient=str(envelope.action_params.get("recipient", "")),
                message=f"{envelope.event.amount} received, balance {envelope.event.new_balance}",
            ),
        )
