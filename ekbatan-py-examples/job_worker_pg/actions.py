"""Units of work.

Each one is short on purpose. Read one and you should be able to say what it does to the
business without knowing anything about this framework: no transaction, no flush, no publish,
no outbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from ekbatan import Action

from .model import Alert, Wallet, WalletId
from .repository import WalletRepository


@dataclass(frozen=True, kw_only=True)
class OpenWalletParams:
    owner_id: UUID
    currency: str
    opening_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class DepositParams:
    wallet_id: WalletId
    amount: Decimal
    #: Not part of the event. A delivery address has no business inside a money-deposited
    #: event, so it travels as an action parameter and the handler reads it from the envelope.
    recipient: str


@dataclass(frozen=True, kw_only=True)
class WalletIdParams:
    wallet_id: WalletId


@dataclass(frozen=True, kw_only=True)
class RaiseAlertParams:
    wallet_id: WalletId
    recipient: str
    message: str


class OpenWalletAction(Action[OpenWalletParams, Wallet]):
    def perform(self, params: OpenWalletParams) -> Wallet:
        return self.plan.add(
            Wallet.open(
                owner_id=params.owner_id,
                currency=params.currency,
                balance=params.opening_balance,
            )
        )


class DepositMoneyAction(Action[DepositParams, Wallet]):
    def __init__(self, wallets: WalletRepository) -> None:
        self.wallets = wallets

    def perform(self, params: DepositParams) -> Wallet:
        wallet = self.wallets.get(params.wallet_id)
        return self.plan.update(wallet.deposit(params.amount))


class CloseWalletAction(Action[WalletIdParams, Wallet]):
    def __init__(self, wallets: WalletRepository) -> None:
        self.wallets = wallets

    def perform(self, params: WalletIdParams) -> Wallet:
        wallet = self.wallets.get(params.wallet_id)
        closed = wallet.close()
        # Already closed: nothing to stage, and therefore nothing to emit. The invocation is
        # still recorded, as one sentinel row.
        return self.plan.update(closed) if closed is not wallet else wallet


class RaiseAlertAction(Action[RaiseAlertParams, Alert]):
    """Writes an entity. Emits nothing, and is still audited."""

    def perform(self, params: RaiseAlertParams) -> Alert:
        return self.plan.add(
            Alert.create(
                wallet_id=params.wallet_id,
                recipient=params.recipient,
                message=params.message,
            )
        )
