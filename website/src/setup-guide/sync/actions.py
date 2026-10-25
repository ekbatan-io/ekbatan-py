"""Units of work.

Each one is short on purpose. Read one and you should be able to say what it does to the
business without knowing anything about this framework: no transaction, no flush, no publish,
no outbox. Each action's parameters are its nested `Params`, written to the outbox as JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from ekbatan import Action, Clock
from ekbatan.ids import Id

from .model import Currency, Notification, NotificationKind, Wallet
from .repository import WalletRepository


class WalletCreateAction(Action["WalletCreateAction.Params", Wallet]):
    @dataclass(frozen=True, kw_only=True)
    class Params:
        owner_id: UUID
        #: Plain text, as the outbox's JSON writes it: "EUR".
        currency: str
        initial_balance: Decimal

    def perform(self, principal: str | None, params: Params) -> Wallet:
        wallet = Wallet.create_wallet(
            params.owner_id, Currency(params.currency), params.initial_balance, self.clock.instant()
        ).build()
        return self.plan.add(wallet)


class WalletDepositMoneyAction(Action["WalletDepositMoneyAction.Params", Wallet]):
    @dataclass(frozen=True, kw_only=True)
    class Params:
        wallet_id: Id[Wallet]
        amount: Decimal
        #: Not part of the event: the handler reads it from the envelope's action parameters.
        recipient: str

    def __init__(self, clock: Clock, wallet_repository: WalletRepository) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    def perform(self, principal: str | None, params: Params) -> Wallet:
        wallet = self.wallet_repository.get_by_id(params.wallet_id.value)
        updated = wallet.deposit(params.amount)
        return self.plan.update(updated)


class CreateNotificationAction(Action["CreateNotificationAction.Params", Notification]):
    """What the event handler runs: one notification row, in an action of its own. Emits no
    events - a notification is an entity - and is still recorded, as one sentinel row."""

    @dataclass(frozen=True, kw_only=True)
    class Params:
        wallet_id: UUID
        kind: NotificationKind
        recipient: str
        message: str

    def perform(self, principal: str | None, params: Params) -> Notification:
        notification = Notification.create_notification(
            params.wallet_id, params.kind, params.recipient, params.message
        ).build()
        return self.plan.add(notification)
