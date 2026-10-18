"""One wallet, and the id that says which database it lives in.

The whole of sharding, in this framework, is this: the shard is packed into the identifier, so
anyone holding an id can be routed without a lookup table, without a tenant column, and without
asking anything. `ShardedId[Wallet].generate(shard)` mints an id that carries its coordinate;
every read and write after that follows it.

The bit layout is a cross-language contract. A Java service and a Python service minting ids
for the same layout produce ids each can route, which is the point of fixing it rather than
letting each port choose.
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
class Wallet(Model[ShardedId["Wallet"], WalletState, WalletBuilder]):
    owner_id: UUID
    currency: Currency
    balance: Decimal

    @classmethod
    def open(
        cls,
        *,
        id: ShardedId[Wallet],
        owner_id: UUID,
        currency: Currency,
        balance: Decimal,
        created_date: datetime,
    ) -> Wallet:
        """Takes its id rather than minting one, because *which shard* is a decision the
        caller makes - usually from something the business knows, like a country."""
        return (
            WalletBuilder()
            .id(id)
            .state(WalletState.OPENED)
            .owner_id(owner_id)
            .currency(currency)
            .balance(balance)
            .with_initial_version()
            .created_date(created_date)
            .with_event(
                WalletOpenedEvent(
                    model_id=str(id),
                    owner_id=owner_id,
                    # The event carries the plain code: what a consumer reads is "EUR", the
                    # same text the column holds.
                    currency=currency.code,
                    opening_balance=balance,
                )
            )
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


@dataclass(frozen=True, kw_only=True)
class Alert(Entity[Id["Alert"]]):
    """What the deposit handler writes - Java's `Notification`.

    Its id carries no shard, so every alert lives on the default shard whichever shard its wallet
    is on, as Java's `Id.random(Notification.class)` does. An entity: writing one emits nothing.
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
