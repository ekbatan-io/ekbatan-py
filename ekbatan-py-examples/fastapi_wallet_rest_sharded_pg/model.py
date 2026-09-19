"""One wallet, and the id that says which database it lives in.

The whole of sharding, in this framework, is this: the shard is packed into the identifier, so
anyone holding an id can be routed without a lookup table, without a tenant column, and without
asking anything. `WalletId.random(shard)` mints an id that carries its coordinate; every read
and write after that follows it.

The bit layout is a cross-language contract. A Java service and a Python service minting ids
for the same layout produce ids each can route, which is the point of fixing it rather than
letting each port choose.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from ekbatan import Model, ModelEvent, ShardedId
from ekbatan.errors import ValidationError

if TYPE_CHECKING:
    from .builders import WalletBuilder


class WalletId(ShardedId["Wallet"]):
    """Carries the shard in its own bits.

    `WalletId.generate(MEXICO)` mints one that lands in Mexico; `WalletId.random()` mints one
    on the default shard, because an id with no shard named belongs somewhere rather than
    nowhere.
    """


class WalletState(Enum):
    OPENED = "OPENED"
    CLOSED = "CLOSED"


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
class Wallet(Model[WalletId, WalletState]):
    owner_id: UUID
    currency: str
    balance: Decimal

    def copy(self) -> WalletBuilder:
        from .builders import WalletBuilder

        return WalletBuilder().copy_base(self)

    @classmethod
    def open(
        cls,
        *,
        id: WalletId,
        owner_id: UUID,
        currency: str,
        balance: Decimal,
        created_date: datetime,
    ) -> Wallet:
        """Takes its id rather than minting one, because *which shard* is a decision the
        caller makes - usually from something the business knows, like a country."""
        from .builders import WalletBuilder

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
                WalletOpenedEvent(owner_id=owner_id, currency=currency, opening_balance=balance)
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
            .with_event(MoneyWithdrawnEvent(amount=amount, new_balance=new_balance))
            .balance(new_balance)
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
