"""Units of work, on `ekbatan.aio`.

Each one is short on purpose. Read one and you should be able to say what it does to the
business without knowing anything about this framework: no transaction, no flush, no publish,
no outbox. The one difference from a sync action is that `perform` is a coroutine and awaits
its reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from ekbatan import Clock
from ekbatan.aio import AsyncAction
from ekbatan.ids import ShardedId

from .model import Alert, Wallet
from .repository import WalletRepository


@dataclass(frozen=True, kw_only=True)
class OpenWalletParams:
    owner_id: UUID
    currency: str
    opening_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class DepositParams:
    wallet_id: ShardedId[Wallet]
    amount: Decimal
    #: Not part of the event. A delivery address has no business inside a money-deposited
    #: event, so it travels as an action parameter and the handler reads it from the envelope.
    recipient: str


@dataclass(frozen=True, kw_only=True)
class WalletIdParams:
    wallet_id: ShardedId[Wallet]


@dataclass(frozen=True, kw_only=True)
class RaiseAlertParams:
    wallet_id: ShardedId[Wallet]
    recipient: str
    message: str


class OpenWalletAction(AsyncAction[OpenWalletParams, Wallet]):
    async def perform(self, principal: str | None, params: OpenWalletParams) -> Wallet:
        return self.plan.add(
            Wallet.open(
                owner_id=params.owner_id,
                currency=params.currency,
                balance=params.opening_balance,
                created_date=self.clock.instant(),
            )
        )


class DepositMoneyAction(AsyncAction[DepositParams, Wallet]):
    def __init__(self, wallets: WalletRepository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    async def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        wallet = await self.wallets.get(params.wallet_id)
        return self.plan.update(wallet.deposit(params.amount))


class CloseWalletAction(AsyncAction[WalletIdParams, Wallet]):
    def __init__(self, wallets: WalletRepository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    async def perform(self, principal: str | None, params: WalletIdParams) -> Wallet:
        wallet = await self.wallets.get(params.wallet_id)
        closed = wallet.close()
        # Already closed: nothing to stage, and therefore nothing to emit. The invocation is
        # still recorded, as one sentinel row.
        return self.plan.update(closed) if closed is not wallet else wallet


class RaiseAlertAction(AsyncAction[RaiseAlertParams, Alert]):
    """Writes an entity. Emits nothing, and is still audited."""

    async def perform(self, principal: str | None, params: RaiseAlertParams) -> Alert:
        return self.plan.add(
            Alert.create(
                wallet_id=params.wallet_id,
                recipient=params.recipient,
                message=params.message,
            )
        )
