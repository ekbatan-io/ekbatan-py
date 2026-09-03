"""The wallet domain.

Read ``deposit`` first. It is the whole argument for the framework: a few lines, one of which
says what happened, and no mention anywhere of a transaction, a flush, a publish, or an outbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from uuid import UUID

from ekbatan.domain import Builder, Entity, GenericState, Model, ModelEvent
from ekbatan.errors import ValidationError
from ekbatan.ids import Id


class WalletState(Enum):
    OPENED = "OPENED"
    CLOSED = "CLOSED"
    DELETED = "DELETED"


@dataclass(frozen=True, kw_only=True)
class WalletCreated(ModelEvent):
    owner_id: UUID
    currency: str
    initial_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class WalletMoneyDeposited(ModelEvent):
    amount: Decimal
    new_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class WalletMoneyWithdrawn(ModelEvent):
    amount: Decimal
    new_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class WalletClosed(ModelEvent):
    pass


@dataclass(frozen=True, kw_only=True)
class Wallet(Model[Id["Wallet"], WalletState]):
    owner_id: UUID
    currency: str
    balance: Decimal

    @classmethod
    def open(cls, *, id: Id[Wallet], owner_id: UUID, currency: str, balance: Decimal) -> Wallet:
        return (
            Builder(cls)
            .id(id)
            .state(WalletState.OPENED)
            .owner_id(owner_id)
            .currency(currency)
            .balance(balance)
            .with_initial_version()
            .with_event(
                WalletCreated(owner_id=owner_id, currency=currency, initial_balance=balance)
            )
            .build()
        )

    # There is no copy() to write. Java declares it abstract and every model implements the
    # same six lines - hand each field back to the builder - because Java cannot reconstruct a
    # subclass it does not know the shape of. Here copy() is inherited and reads the fields
    # off the dataclass, so a model that gains a field gains it everywhere at once instead of
    # in one place and not the other.

    def deposit(self, amount: Decimal) -> Wallet:
        if amount <= 0:
            raise ValidationError(f"deposit amount must be positive, got {amount}")
        new_balance = self.balance + amount
        return (
            self.copy()
            .with_event(WalletMoneyDeposited(amount=amount, new_balance=new_balance))
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
            .with_event(WalletMoneyWithdrawn(amount=amount, new_balance=new_balance))
            .balance(new_balance)
            .build()
        )

    def close(self) -> Wallet:
        # Closing a closed wallet is a legitimate call that changes nothing. It must therefore
        # emit nothing - the smallest case where "no events" and "no change" have to agree.
        if self.state is WalletState.CLOSED:
            return self
        return self.copy().with_event(WalletClosed()).state(WalletState.CLOSED).build()


@dataclass(frozen=True, kw_only=True)
class Notification(Entity[Id["Notification"]]):
    """An entity: the persistence layer tracks it, the event stream does not care.

    No timestamps, because an entity is state rather than history - and nothing here is worth
    telling the rest of the system about, which is exactly what makes it an entity.
    """

    wallet_id: Id[Wallet]
    kind: str
    recipient: str
    message: str | None = None

    @classmethod
    def create(
        cls,
        *,
        id: Id[Notification],
        wallet_id: Id[Wallet],
        kind: str,
        recipient: str,
        message: str | None = None,
    ) -> Notification:
        return cls(
            id=id,
            state=GenericState.ACTIVE,
            wallet_id=wallet_id,
            kind=kind,
            recipient=recipient,
            message=message,
        )
