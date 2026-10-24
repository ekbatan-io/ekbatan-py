"""The domain: a wallet, and the notification a deposit leaves behind.

Read `Wallet.deposit` first. It is the whole argument for the framework: a few lines, one of
which says what happened, and no mention anywhere of a transaction, a flush, a publish, or an
outbox.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from uuid import UUID

from ekbatan import Entity, Model, ModelEvent
from ekbatan.errors import ValidationError
from ekbatan.ids import Id
from ekbatan.time import Instant

from .builders import NotificationBuilder, WalletBuilder


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


class NotificationKind(Enum):
    WALLET_CREATED = "WALLET_CREATED"
    MONEY_DEPOSITED = "MONEY_DEPOSITED"
    WALLET_CLOSED = "WALLET_CLOSED"


class NotificationState(Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    DELETED = "DELETED"


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
class Wallet(Model[Id["Wallet"], WalletState, WalletBuilder]):
    owner_id: UUID
    currency: Currency
    balance: Decimal

    def validate(self) -> None:
        # No rule beyond the fields' types: each of the three is required, and ekbatan checks
        # that before this runs.
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


@dataclass(frozen=True, kw_only=True)
class Notification(Entity[Id["Notification"], NotificationState, NotificationBuilder]):
    """An entity: the persistence layer tracks it, the event stream does not care.

    Writing one emits nothing - and the unit of work that wrote it is still recorded, because
    the audit trail is of units of work rather than of events.
    """

    wallet_id: UUID
    kind: NotificationKind
    recipient: str
    message: str

    def validate(self) -> None:
        if not self.recipient.strip():
            raise ValidationError("recipient cannot be blank")
        if not self.message.strip():
            raise ValidationError("message cannot be blank")

    @staticmethod
    def create_notification(
        wallet_id: UUID, kind: NotificationKind, recipient: str, message: str
    ) -> NotificationBuilder:
        return (
            NotificationBuilder()
            .id(Id[Notification].random())
            .state(NotificationState.PENDING)
            .wallet_id(wallet_id)
            .kind(kind)
            .recipient(recipient)
            .message(message)
            .with_initial_version()
        )

    def mark_sent(self) -> Notification:
        return self.copy().state(NotificationState.SENT).build()

    def mark_failed(self) -> Notification:
        return self.copy().state(NotificationState.FAILED).build()
