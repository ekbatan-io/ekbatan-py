"""The domain: a wallet whose transfers to another wallet run as a saga, and the marker that makes
each later step of the saga run once.

A transfer is three units of work, each its own transaction: the source is debited and a
`TransferInitiatedEvent` is emitted; a handler then credits the destination, or - when it cannot
- marks the transfer failed on the source; a second handler refunds the source. Each of those
steps is a method below that produces the new wallet and its event in one place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from uuid import UUID

from ekbatan import Entity, GenericState, Model, ModelEvent
from ekbatan.errors import ValidationError
from ekbatan.ids import Id
from ekbatan.time import Instant

from .builders import TransferStepBuilder, WalletBuilder


@dataclass(frozen=True)
class Currency:
    """A three-letter currency code, such as EUR - the wallet's `java.util.Currency`. Python has
    no such class, so this is the example's own, checked once, here.

    The `currency` column is plain text. `schema.py` hands back a `Currency` because of a forced
    type in `pyproject.toml`: every `CHAR(3)` column goes through `CurrencyConverter`, in
    `converters.py`.
    """

    code: str

    def __post_init__(self) -> None:
        if not re.fullmatch("[A-Z]{3}", self.code):
            raise ValidationError(
                f"a currency is three capital letters, such as EUR: {self.code!r}"
            )

    def __str__(self) -> str:
        return self.code


class WalletState(Enum):
    OPENED = "OPENED"
    CLOSED = "CLOSED"
    DELETED = "DELETED"


class TransferStepName(Enum):
    COMPLETE_TRANSFER = "COMPLETE_TRANSFER"
    REFUND_TRANSFER = "REFUND_TRANSFER"


@dataclass(frozen=True, kw_only=True)
class WalletCreatedEvent(ModelEvent["Wallet"]):
    owner_id: UUID
    #: The plain code: what a consumer reads is "EUR", the same text the column holds.
    currency: str
    initial_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class WalletMoneyDepositedEvent(ModelEvent["Wallet"]):
    amount: Decimal
    new_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class WalletClosedEvent(ModelEvent["Wallet"]):
    pass


@dataclass(frozen=True, kw_only=True)
class TransferInitiatedEvent(ModelEvent["Wallet"]):
    """On the source wallet: the debit, and everything the next step needs."""

    transfer_id: UUID
    from_wallet_id: UUID
    to_wallet_id: UUID
    amount: Decimal
    new_source_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class TransferCompletedEvent(ModelEvent["Wallet"]):
    """On the destination wallet: the credit."""

    transfer_id: UUID
    from_wallet_id: UUID
    to_wallet_id: UUID
    amount: Decimal
    new_destination_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class TransferFailedEvent(ModelEvent["Wallet"]):
    """On the source wallet: the destination could not receive, and why."""

    transfer_id: UUID
    from_wallet_id: UUID
    to_wallet_id: UUID
    amount: Decimal
    reason: str


@dataclass(frozen=True, kw_only=True)
class TransferRefundedEvent(ModelEvent["Wallet"]):
    """On the source wallet: the money back, which closes the saga."""

    transfer_id: UUID
    from_wallet_id: UUID
    amount: Decimal
    new_source_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class Wallet(Model[Id["Wallet"], WalletState, WalletBuilder]):
    owner_id: UUID
    currency: Currency
    balance: Decimal

    def validate(self) -> None:
        # No rule beyond the fields' types: each of the three is required, and ekbatan checks
        # that before this runs, as Java's constructor checks them for null.
        pass

    @staticmethod
    def create_wallet(
        owner_id: UUID, currency: Currency, initial_balance: Decimal, created_date: Instant
    ) -> WalletBuilder:
        wallet_id = Id[Wallet].random()
        return (
            WalletBuilder()
            .id(wallet_id)
            .state(WalletState.OPENED)
            .owner_id(owner_id)
            .currency(currency)
            .balance(initial_balance)
            .created_date(created_date)
            .with_initial_version()
            .with_event(
                WalletCreatedEvent(
                    model_id=str(wallet_id),
                    owner_id=owner_id,
                    currency=currency.code,
                    initial_balance=initial_balance,
                )
            )
        )

    def deposit(self, amount: Decimal) -> Wallet:
        if amount <= 0:
            raise ValidationError("Deposit amount must be positive")
        new_balance = self.balance + amount
        return (
            self.copy()
            .with_event(
                WalletMoneyDepositedEvent(
                    model_id=str(self.id), amount=amount, new_balance=new_balance
                )
            )
            .balance(new_balance)
            .build()
        )

    def close(self) -> Wallet:
        if self.state is WalletState.CLOSED:
            return self
        return (
            self.copy()
            .with_event(WalletClosedEvent(model_id=str(self.id)))
            .state(WalletState.CLOSED)
            .build()
        )

    # -- the saga's steps, each producing the new wallet and its event in one place ------------

    def initiate_transfer_out(
        self, transfer_id: UUID, to_wallet_id: Id[Wallet], amount: Decimal
    ) -> Wallet:
        """Step one: debit this, the source wallet. The action that calls this still has to
        `plan.update` the result for the change to commit."""
        if self.state is not WalletState.OPENED:
            raise ValidationError("Source wallet must be OPEN to initiate a transfer")
        if self.balance < amount:
            raise ValidationError("Insufficient balance for transfer")
        new_balance = self.balance - amount
        return (
            self.copy()
            .with_event(
                TransferInitiatedEvent(
                    model_id=str(self.id),
                    transfer_id=transfer_id,
                    from_wallet_id=self.id.value,
                    to_wallet_id=to_wallet_id.value,
                    amount=amount,
                    new_source_balance=new_balance,
                )
            )
            .balance(new_balance)
            .build()
        )

    def complete_transfer_in(
        self, transfer_id: UUID, from_wallet_id: Id[Wallet], amount: Decimal
    ) -> Wallet:
        """Step two, the happy path: credit this, the destination wallet."""
        if self.state is not WalletState.OPENED:
            raise ValidationError("Destination wallet must be OPEN to receive a transfer")
        new_balance = self.balance + amount
        return (
            self.copy()
            .with_event(
                TransferCompletedEvent(
                    model_id=str(self.id),
                    transfer_id=transfer_id,
                    from_wallet_id=from_wallet_id.value,
                    to_wallet_id=self.id.value,
                    amount=amount,
                    new_destination_balance=new_balance,
                )
            )
            .balance(new_balance)
            .build()
        )

    def mark_transfer_failed(
        self, transfer_id: UUID, to_wallet_id: Id[Wallet], amount: Decimal, reason: str
    ) -> Wallet:
        """Step two, the failure path: record on this, the *source* wallet, that the transfer
        cannot complete. No balance changes; the update still moves the version and
        `updated_date`, so the row writes and the event reaches the outbox."""
        return (
            self.copy()
            .with_event(
                TransferFailedEvent(
                    model_id=str(self.id),
                    transfer_id=transfer_id,
                    from_wallet_id=self.id.value,
                    to_wallet_id=to_wallet_id.value,
                    amount=amount,
                    reason=reason,
                )
            )
            .build()
        )

    def refund_transfer(self, transfer_id: UUID, amount: Decimal) -> Wallet:
        """The compensation: credit this, the source wallet, back."""
        new_balance = self.balance + amount
        return (
            self.copy()
            .with_event(
                TransferRefundedEvent(
                    model_id=str(self.id),
                    transfer_id=transfer_id,
                    from_wallet_id=self.id.value,
                    amount=amount,
                    new_source_balance=new_balance,
                )
            )
            .balance(new_balance)
            .build()
        )


@dataclass(frozen=True, kw_only=True)
class TransferStep(Entity[Id["TransferStep"], GenericState, TransferStepBuilder]):
    """A saga step that has run, by transfer: written in the same transaction as the step's own
    change, so a replayed delivery of the same event finds it and does nothing."""

    transfer_id: UUID
    step: TransferStepName

    def validate(self) -> None:
        # No rule beyond the fields' types: both are required.
        pass

    @staticmethod
    def create(transfer_id: UUID, step: TransferStepName) -> TransferStepBuilder:
        return (
            TransferStepBuilder()
            .id(Id[TransferStep].random())
            .state(GenericState.ACTIVE)
            .transfer_id(transfer_id)
            .step(step)
            .with_initial_version()
        )
