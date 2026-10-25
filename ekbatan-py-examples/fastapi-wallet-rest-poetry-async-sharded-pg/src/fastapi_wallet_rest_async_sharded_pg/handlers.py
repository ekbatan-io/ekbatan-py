"""Reacting to your own events, in the same process.

No broker, no connector, no second runtime. The unit of work commits, fan-out turns each
committed event into work, and dispatch runs it - at least once, which is the only guarantee
anything on this side of a database can honestly make.
"""

from __future__ import annotations

import logging
from uuid import UUID

from ekbatan.aio import AsyncActionExecutor
from ekbatan.events import AsyncEventHandler, EventEnvelope

from .actions import CreateNotificationAction
from .model import NotificationKind, WalletMoneyDepositedEvent

LOG = logging.getLogger(__name__)


class WalletMoneyDepositedEventHandler(AsyncEventHandler[WalletMoneyDepositedEvent]):
    """Reacts to a committed `WalletMoneyDepositedEvent` by creating a notification row.

    The handler runs in-process after the deposit commits, on dispatch's handler loop, then
    awaits another action. The
    recipient is not part of the event - that would put notification routing inside a domain
    event - so it is read back from the envelope's `action_params`, the parameters of the
    action that produced the event.
    """

    def __init__(self, executor: AsyncActionExecutor) -> None:
        self.executor = executor

    def name(self) -> str:
        # Cluster-stable; stored in `event_notifications.handler_name`.
        return "wallet-money-deposited-notification"

    def event_type(self) -> type[WalletMoneyDepositedEvent]:
        return WalletMoneyDepositedEvent

    async def handle(self, envelope: EventEnvelope[WalletMoneyDepositedEvent]) -> None:
        wallet_id = UUID(envelope.event.model_id)
        recipient = str(envelope.action_params["recipient"])
        message = (
            f"Deposit of {envelope.event.amount} received. "
            f"New balance: {envelope.event.new_balance}."
        )

        LOG.info("Creating notification for wallet=%s recipient=%s", wallet_id, recipient)

        await self.executor.execute(
            "wallet-handler",
            CreateNotificationAction,
            CreateNotificationAction.Params(
                wallet_id=wallet_id,
                kind=NotificationKind.MONEY_DEPOSITED,
                recipient=recipient,
                message=message,
            ),
        )
