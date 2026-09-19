"""The domain.

Read `Wallet.deposit` first. It is the whole argument for the framework: a few lines, one of
which says what happened, and no mention anywhere of a transaction, a flush, a publish, or an
outbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from ekbatan import Entity, GenericState, Model, ModelEvent, ShardedId
from ekbatan.errors import ValidationError
from ekbatan.ids import Id

from .builders import WalletBuilder


class WalletId(ShardedId["Wallet"]):
    """A wallet's identity, and the database its row lives in.

    Named rather than spelled `ShardedId[Wallet]` so the distinction survives into the running
    program: `WalletId(u) == AlertId(u)` is False with no type checker involved.
    """


class AlertId(Id["Alert"]):
    pass


class WalletState(Enum):
    OPENED = "OPENED"
    CLOSED = "CLOSED"
    DELETED = "DELETED"


@dataclass(frozen=True, kw_only=True)
class WalletOpenedEvent(ModelEvent):
    owner_id: UUID
    currency: str
    opening_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class MoneyDepositedEvent(ModelEvent):
    amount: Decimal
    new_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class MoneyWithdrawnEvent(ModelEvent):
    amount: Decimal
    new_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class WalletClosedEvent(ModelEvent):
    pass


@dataclass(frozen=True, kw_only=True)
class WalletDeletedEvent(ModelEvent):
    """Deleting is a domain event like any other.

    The row survives - every read stops seeing it, but it is still the other side of every
    event already published about this wallet. What leaves is the wallet, not the history.
    """


@dataclass(frozen=True, kw_only=True)
class Wallet(Model[WalletId, WalletState, WalletBuilder]):
    owner_id: UUID
    currency: str
    balance: Decimal

    @classmethod
    def open(
        cls,
        *,
        owner_id: UUID,
        currency: str,
        balance: Decimal,
        id: WalletId | None = None,
        created_date: datetime,
    ) -> Wallet:
        wallet_id = id or WalletId.random()
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
                WalletOpenedEvent(owner_id=owner_id, currency=currency, opening_balance=balance)
            )
            .build()
        )

    def deposit(self, amount: Decimal) -> Wallet:
        if amount <= 0:
            raise ValidationError(f"deposit amount must be positive, got {amount}")
        new_balance = self.balance + amount
        return (
            self.copy()
            .with_event(MoneyDepositedEvent(amount=amount, new_balance=new_balance))
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
            .with_event(MoneyWithdrawnEvent(amount=amount, new_balance=new_balance))
            .balance(new_balance)
            .build()
        )

    def close(self) -> Wallet:
        # Closing a closed wallet is a legitimate call that changes nothing, so it must emit
        # nothing - the smallest case where "no events" and "no change" have to agree.
        if self.state is WalletState.CLOSED:
            return self
        return self.copy().with_event(WalletClosedEvent()).state(WalletState.CLOSED).build()

    def delete(self) -> Wallet:
        """Soft delete: the row stays, every read stops seeing it.

        Written here rather than inherited, because what deletion means belongs to the model.
        The framework supplies only the convention - a state named DELETED - and the filter
        that hides it from every read.
        """
        if self.state is WalletState.DELETED:
            return self
        return self.copy().with_event(WalletDeletedEvent()).state(WalletState.DELETED).build()


@dataclass(frozen=True, kw_only=True)
class Alert(Entity[AlertId]):
    """An entity: the persistence layer tracks it, the event stream does not care.

    Writing one emits nothing - and the unit of work that wrote it is still recorded, because
    the audit trail is of units of work rather than of events.
    """

    wallet_id: WalletId
    recipient: str
    message: str

    @classmethod
    def create(cls, *, wallet_id: WalletId, recipient: str, message: str) -> Alert:
        return cls(
            id=AlertId.random(),
            state=GenericState.ACTIVE,
            wallet_id=wallet_id,
            recipient=recipient,
            message=message,
        )
