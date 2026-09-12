"""The wallet application's units of work.

Each is short on purpose. Read one and you should be able to say what it does to the business
without knowing anything about this framework - no transaction, no flush, no publish, no
outbox. That is the bar these are here to demonstrate.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from ekbatan import Action, Repository
from ekbatan.errors import ValidationError

from .domain import Notification, NotificationId, Wallet, WalletId


@dataclass(frozen=True, kw_only=True)
class CreateWalletParams:
    owner_id: UUID
    currency: str
    initial_balance: Decimal
    wallet_id: WalletId | None = None


@dataclass(frozen=True, kw_only=True)
class DepositParams:
    wallet_id: WalletId
    amount: Decimal
    recipient: str


@dataclass(frozen=True, kw_only=True)
class TransferParams:
    from_wallet_id: WalletId
    to_wallet_id: WalletId
    amount: Decimal


@dataclass(frozen=True, kw_only=True)
class WalletIdParams:
    wallet_id: WalletId


@dataclass(frozen=True, kw_only=True)
class NotifyParams:
    wallet_id: WalletId
    kind: str
    recipient: str
    message: str | None = None


class CreateWalletAction(Action[CreateWalletParams, Wallet]):
    def perform(self, params: CreateWalletParams) -> Wallet:
        return self.plan.add(
            Wallet.open(
                id=params.wallet_id or WalletId.random(),
                owner_id=params.owner_id,
                currency=params.currency,
                balance=params.initial_balance,
            )
        )


class DepositMoneyAction(Action[DepositParams, Wallet]):
    def __init__(self, wallets: Repository[Wallet, WalletId]) -> None:
        self.wallets = wallets

    def perform(self, params: DepositParams) -> Wallet:
        wallet = self.wallets.get(params.wallet_id)
        return self.plan.update(wallet.deposit(params.amount))


class CloseWalletAction(Action[WalletIdParams, Wallet]):
    def __init__(self, wallets: Repository[Wallet, WalletId]) -> None:
        self.wallets = wallets

    def perform(self, params: WalletIdParams) -> Wallet:
        wallet = self.wallets.get(params.wallet_id)
        closed = wallet.close()
        # An already-closed wallet comes back unchanged, so there is nothing to stage - and
        # therefore no event. The invocation is still recorded.
        return self.plan.update(closed) if closed is not wallet else wallet


class TransferMoneyAction(Action[TransferParams, tuple[Wallet, Wallet]]):
    """Two models, one transaction. Both balances move or neither does, and nothing in here
    arranges that."""

    def __init__(self, wallets: Repository[Wallet, WalletId]) -> None:
        self.wallets = wallets

    def perform(self, params: TransferParams) -> tuple[Wallet, Wallet]:
        source = self.wallets.get(params.from_wallet_id)
        destination = self.wallets.get(params.to_wallet_id)
        if source.currency != destination.currency:
            raise ValidationError(
                f"cannot transfer {source.currency} into a {destination.currency} wallet"
            )
        return (
            self.plan.update(source.withdraw(params.amount)),
            self.plan.update(destination.deposit(params.amount)),
        )


class NotifyAction(Action[NotifyParams, Notification]):
    """Writes an entity. Emits nothing - and is still recorded, as one sentinel row."""

    def perform(self, params: NotifyParams) -> Notification:
        return self.plan.add(
            Notification.create(
                id=NotificationId.random(),
                wallet_id=params.wallet_id,
                kind=params.kind,
                recipient=params.recipient,
                message=params.message,
            )
        )


class DepositThenFailAction(Action[DepositParams, Wallet]):
    """Stages a real change and then raises.

    No business has one. It exists because "staging is not writing" cannot be observed any
    other way.
    """

    def __init__(self, wallets: Repository[Wallet, WalletId]) -> None:
        self.wallets = wallets

    def perform(self, params: DepositParams) -> Wallet:
        wallet = self.wallets.get(params.wallet_id)
        self.plan.update(wallet.deposit(params.amount))
        raise ValidationError("changed my mind")
