"""The wallet domain.

Read ``deposit`` first. It is the whole argument for the framework: five lines, one of which
says what happened, and no mention anywhere of a transaction, a flush, a publish, or an outbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from uuid import UUID

from ekbatan.domain import Entity, GenericState, Model, ModelEvent
from ekbatan.errors import ValidationError


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
class Wallet(Model[UUID, WalletState]):
    owner_id: UUID
    currency: str
    balance: Decimal

    @classmethod
    def open(cls, *, id: UUID, owner_id: UUID, currency: str, balance: Decimal) -> Wallet:
        return cls(
            id=id,
            state=WalletState.OPENED,
            owner_id=owner_id,
            currency=currency,
            balance=balance,
            events=(WalletCreated(owner_id=owner_id, currency=currency, initial_balance=balance),),
        )

    def deposit(self, amount: Decimal) -> Wallet:
        if amount <= 0:
            raise ValidationError(f"deposit amount must be positive, got {amount}")
        new_balance = self.balance + amount
        return self.evolve(
            balance=new_balance,
            emit=WalletMoneyDeposited(amount=amount, new_balance=new_balance),
        )

    def withdraw(self, amount: Decimal) -> Wallet:
        if amount <= 0:
            raise ValidationError(f"withdrawal amount must be positive, got {amount}")
        if amount > self.balance:
            raise ValidationError(f"cannot withdraw {amount}: balance is {self.balance}")
        new_balance = self.balance - amount
        return self.evolve(
            balance=new_balance,
            emit=WalletMoneyWithdrawn(amount=amount, new_balance=new_balance),
        )

    def close(self) -> Wallet:
        # Closing a closed wallet is a legitimate call that changes nothing. It must therefore
        # emit nothing - the smallest case where "no events" and "no change" have to agree.
        if self.state is WalletState.CLOSED:
            return self
        return self.evolve(state=WalletState.CLOSED, emit=WalletClosed())


@dataclass(frozen=True, kw_only=True)
class Notification(Entity[UUID]):
    """An entity: the persistence layer tracks it, the event stream does not care.

    No timestamps, because an entity is state rather than history - and nothing here is worth
    telling the rest of the system about, which is exactly what makes it an entity.
    """

    wallet_id: UUID
    kind: str
    recipient: str
    message: str | None = None

    @classmethod
    def create(
        cls, *, id: UUID, wallet_id: UUID, kind: str, recipient: str, message: str | None = None
    ) -> Notification:
        return cls(
            id=id,
            state=GenericState.ACTIVE,
            wallet_id=wallet_id,
            kind=kind,
            recipient=recipient,
            message=message,
        )
