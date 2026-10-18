"""Units of work.

Each one is short on purpose. Read one and you should be able to say what it does to the
business without knowing anything about this framework: no transaction, no flush, no publish,
no outbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from ekbatan import Action, Clock
from ekbatan.ids import ShardedId

from .model import Alert, Currency, EmailAddress, Wallet
from .repository import WalletRepository


@dataclass(frozen=True, kw_only=True)
class OpenWalletParams:
    owner_id: UUID
    #: Plain text, as are all parameters: they are written to the outbox as JSON. The action
    #: turns it into a `Currency`, which refuses anything but three capital letters.
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


class OpenWalletAction(Action[OpenWalletParams, Wallet]):
    def perform(self, principal: str | None, params: OpenWalletParams) -> Wallet:
        return self.plan.add(
            Wallet.open(
                owner_id=params.owner_id,
                currency=Currency(params.currency),
                balance=params.opening_balance,
                created_date=self.clock.now(),
            )
        )


class DepositMoneyAction(Action[DepositParams, Wallet]):
    def __init__(self, wallets: WalletRepository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        # The address is checked now, so a deposit whose alert could never be written is
        # refused here rather than failing in the handler later, over and over.
        EmailAddress(params.recipient)
        wallet = self.wallets.get(params.wallet_id)
        return self.plan.update(wallet.deposit(params.amount))


class CloseWalletAction(Action[WalletIdParams, Wallet]):
    def __init__(self, wallets: WalletRepository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    def perform(self, principal: str | None, params: WalletIdParams) -> Wallet:
        wallet = self.wallets.get(params.wallet_id)
        closed = wallet.close()
        # Already closed: nothing to stage, and therefore nothing to emit. The invocation is
        # still recorded, as one sentinel row.
        return self.plan.update(closed) if closed is not wallet else wallet


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
