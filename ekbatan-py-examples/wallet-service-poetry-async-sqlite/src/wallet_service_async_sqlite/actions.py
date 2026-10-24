"""Units of work, on `ekbatan.aio`: each `perform` awaits.

Each one is short on purpose. Read one and you should be able to say what it does to the
business without knowing anything about this framework: no transaction, no flush, no publish,
no outbox.

Each action's parameters are its nested `Params`, written to the outbox as JSON when it runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from ekbatan import Clock, ShardIdentifier
from ekbatan.aio import AsyncAction
from ekbatan.ids import ShardedId

from .model import Currency, Notification, NotificationKind, Wallet
from .repository import WalletRepository


class WalletCreateAction(AsyncAction["WalletCreateAction.Params", Wallet]):
    """The only place a shard is *chosen*. Every other action takes a wallet id and is routed
    by it, so business logic never names a database."""

    GLOBAL_SHARD = ShardIdentifier(0, 0)
    MEXICO_SHARD = ShardIdentifier(1, 0)

    @dataclass(frozen=True, kw_only=True)
    class Params:
        #: "MX" puts the wallet on the Mexico shard; anything else, or nothing, on the global one.
        country_code: str | None = None
        owner_id: UUID
        #: Plain text, as the outbox's JSON writes it: "EUR". The action turns it into a
        #: `Currency`, which refuses anything but three capital letters.
        currency: str
        initial_balance: Decimal

    async def perform(self, principal: str | None, params: Params) -> Wallet:
        wallet = Wallet.create_wallet(
            self._resolve_shard(params.country_code),
            params.owner_id,
            Currency(params.currency),
            params.initial_balance,
            self.clock.instant(),
        ).build()
        return self.plan.add(wallet)

    def _resolve_shard(self, country_code: str | None) -> ShardIdentifier:
        if (country_code or "").upper() == "MX":
            return self.MEXICO_SHARD
        return self.GLOBAL_SHARD


class WalletDepositMoneyAction(AsyncAction["WalletDepositMoneyAction.Params", Wallet]):
    """Updates the wallet's balance and emits a `WalletMoneyDepositedEvent`.

    The wallet id is shard-aware, so `wallet_repository.get(...)` and `plan.update(...)` both
    route to the wallet's own database. This stays a single-shard action however many shards
    the application has.
    """

    @dataclass(frozen=True, kw_only=True)
    class Params:
        wallet_id: ShardedId[Wallet]
        amount: Decimal
        #: Not part of the event. A delivery address has no business inside a money-deposited
        #: event, so it travels as an action parameter and the handler reads it from the envelope.
        recipient: str

    def __init__(self, clock: Clock, wallet_repository: WalletRepository) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    async def perform(self, principal: str | None, params: Params) -> Wallet:
        wallet = await self.wallet_repository.get(params.wallet_id)
        updated = wallet.deposit(params.amount)
        return self.plan.update(updated)


class WalletCloseAction(AsyncAction["WalletCloseAction.Params", Wallet]):
    @dataclass(frozen=True, kw_only=True)
    class Params:
        wallet_id: ShardedId[Wallet]

    def __init__(self, clock: Clock, wallet_repository: WalletRepository) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    async def perform(self, principal: str | None, params: Params) -> Wallet:
        wallet = await self.wallet_repository.get(params.wallet_id)
        closed = wallet.close()
        return self.plan.update(closed)


class CreateNotificationAction(AsyncAction["CreateNotificationAction.Params", Notification]):
    """Writes one notification row. Called by the event handler, so the notification is made in
    an action of its own - its own transaction, retried on its own if the handler fails part
    way. Emits no events (a notification is an entity, not a model); the outbox still gets a
    sentinel row for the invocation itself."""

    @dataclass(frozen=True, kw_only=True)
    class Params:
        wallet_id: UUID
        kind: NotificationKind
        recipient: str
        message: str

    async def perform(self, principal: str | None, params: Params) -> Notification:
        notification = Notification.create_notification(
            params.wallet_id, params.kind, params.recipient, params.message
        ).build()
        return self.plan.add(notification)
