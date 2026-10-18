"""The domain.

Read `Wallet.deposit` first. It is the whole argument for the framework: a few lines, one of
which says what happened, and no mention anywhere of a transaction, a flush, a publish, or an
outbox.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from ekbatan import Entity, GenericState, Model, ModelEvent, ShardedId
from ekbatan.errors import ValidationError
from ekbatan.ids import Id

from .builders import WalletBuilder


@dataclass(frozen=True)
class Currency:
    """A three-letter currency code, such as EUR. Checked once, here, so a wallet never holds
    "eur" or "EURO".

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


@dataclass(frozen=True)
class EmailAddress:
    """Where an alert goes. Only the shape is checked - something, an @, something.

    The `recipient` column is plain text; a forced type in `pyproject.toml` picks it by name and
    `EmailAddressConverter`, in `converters.py`, turns it into this.
    """

    address: str

    def __post_init__(self) -> None:
        name, at, domain = self.address.rpartition("@")
        if not (name and at and domain) or any(c.isspace() for c in self.address):
            raise ValidationError(f"not an email address: {self.address!r}")

    def __str__(self) -> str:
        return self.address


class WalletState(Enum):
    OPENED = "OPENED"
    CLOSED = "CLOSED"
    DELETED = "DELETED"


@dataclass(frozen=True, kw_only=True)
class WalletOpenedEvent(ModelEvent["Wallet"]):
    owner_id: UUID
    currency: str
    opening_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class MoneyDepositedEvent(ModelEvent["Wallet"]):
    amount: Decimal
    new_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class MoneyWithdrawnEvent(ModelEvent["Wallet"]):
    amount: Decimal
    new_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class WalletClosedEvent(ModelEvent["Wallet"]):
    pass


@dataclass(frozen=True, kw_only=True)
class WalletDeletedEvent(ModelEvent["Wallet"]):
    """Deleting is a domain event like any other.

    The row survives - every read stops seeing it, but it is still the other side of every
    event already published about this wallet. What leaves is the wallet, not the history.
    """


@dataclass(frozen=True, kw_only=True)
class Wallet(Model[ShardedId["Wallet"], WalletState, WalletBuilder]):
    owner_id: UUID
    currency: Currency
    balance: Decimal

    @classmethod
    def open(
        cls,
        *,
        owner_id: UUID,
        currency: Currency,
        balance: Decimal,
        id: ShardedId[Wallet] | None = None,
        created_date: datetime,
    ) -> Wallet:
        wallet_id = id or ShardedId[Wallet].random()
        return (
            WalletBuilder()
            .id(wallet_id)
            .state(WalletState.OPENED)
            .owner_id(owner_id)
            .currency(currency)
            .balance(balance)
            .with_initial_version()
            .created_date(created_date)
            .with_event(
                WalletOpenedEvent(
                    model_id=str(wallet_id),
                    owner_id=owner_id,
                    # The event carries the plain code: what a consumer reads is "EUR", the
                    # same text the column holds.
                    currency=currency.code,
                    opening_balance=balance,
                )
            )
            .build()
        )

    def deposit(self, amount: Decimal) -> Wallet:
        if amount <= 0:
            raise ValidationError(f"deposit amount must be positive, got {amount}")
        new_balance = self.balance + amount
        return (
            self.copy()
            .with_event(
                MoneyDepositedEvent(model_id=str(self.id), amount=amount, new_balance=new_balance)
            )
            .balance(new_balance)
            .build()
        )

    def withdraw(self, amount: Decimal) -> Wallet:
        if amount <= 0:
            raise ValidationError(f"withdrawal amount must be positive, got {amount}")
        if amount > self.balance:
            raise ValidationError(f"cannot withdraw {amount}: balance is {self.balance}")
        new_balance = self.balance - amount
        return (
            self.copy()
            .with_event(
                MoneyWithdrawnEvent(model_id=str(self.id), amount=amount, new_balance=new_balance)
            )
            .balance(new_balance)
            .build()
        )

    def close(self) -> Wallet:
        # Closing a closed wallet is a legitimate call that changes nothing, so it must emit
        # nothing - the smallest case where "no events" and "no change" have to agree.
        if self.state is WalletState.CLOSED:
            return self
        return (
            self.copy()
            .with_event(WalletClosedEvent(model_id=str(self.id)))
            .state(WalletState.CLOSED)
            .build()
        )

    def delete(self) -> Wallet:
        """Soft delete: the row stays, every read stops seeing it.

        Written here rather than inherited, because what deletion means belongs to the model.
        The framework supplies only the convention - a state named DELETED - and the filter
        that hides it from every read.
        """
        if self.state is WalletState.DELETED:
            return self
        return (
            self.copy()
            .with_event(WalletDeletedEvent(model_id=str(self.id)))
            .state(WalletState.DELETED)
            .build()
        )


@dataclass(frozen=True, kw_only=True)
class Alert(Entity[Id["Alert"]]):
    """An entity: the persistence layer tracks it, the event stream does not care.

    Writing one emits nothing - and the unit of work that wrote it is still recorded, because
    the audit trail is of units of work rather than of events.
    """

    wallet_id: ShardedId[Wallet]
    recipient: EmailAddress
    message: str

    @classmethod
    def create(
        cls, *, wallet_id: ShardedId[Wallet], recipient: EmailAddress, message: str
    ) -> Alert:
        return cls(
            id=Id[Alert].random(),
            state=GenericState.ACTIVE,
            version=1,
            wallet_id=wallet_id,
            recipient=recipient,
            message=message,
        )
