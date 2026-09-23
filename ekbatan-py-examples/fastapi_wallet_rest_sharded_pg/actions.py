"""Units of work, over a layout rather than a database.

`OpenWalletAction` is the only place a shard is *chosen*. Every other action here takes an id and is
routed by it, which is the property worth protecting: business logic that named a database
would have to be edited every time the layout changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from ekbatan import Action, Clock, ShardIdentifier
from ekbatan.ids import ShardedId

from .model import Wallet
from .repository import WalletRepository

#: Where each country's wallets live. The one piece of routing policy this application owns -
#: everything downstream reads the coordinate off the id instead.
GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)
BY_COUNTRY = {"MX": MEXICO}


@dataclass(frozen=True, kw_only=True)
class OpenWalletParams:
    owner_id: UUID
    currency: str
    opening_balance: Decimal
    #: Decides the shard, and nothing else. An unknown country goes to the global shard.
    country: str = "GL"


@dataclass(frozen=True, kw_only=True)
class DepositParams:
    wallet_id: ShardedId[Wallet]
    amount: Decimal


@dataclass(frozen=True, kw_only=True)
class TransferParams:
    from_wallet_id: ShardedId[Wallet]
    to_wallet_id: ShardedId[Wallet]
    amount: Decimal


class OpenWalletAction(Action[OpenWalletParams, Wallet]):
    def perform(self, principal: str | None, params: OpenWalletParams) -> Wallet:
        shard = BY_COUNTRY.get(params.country, GLOBAL)
        return self.plan.add(
            Wallet.open(
                id=ShardedId[Wallet].generate(shard),
                owner_id=params.owner_id,
                currency=params.currency,
                balance=params.opening_balance,
                created_date=self.clock.now(),
            )
        )


class DepositMoneyAction(Action[DepositParams, Wallet]):
    """Says nothing about shards. The id routes it."""

    def __init__(self, wallets: WalletRepository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        wallet = self.wallets.get(params.wallet_id)
        return self.plan.update(wallet.deposit(params.amount))


class TransferWithinShardAction(Action[TransferParams, tuple[Wallet, Wallet]]):
    """Two wallets, one transaction - and only legal when both are on the same database.

    Reaching a second shard raises `CrossShardError` rather than committing each half, because
    there is no two-phase commit here and committing separately would turn "all or nothing"
    into a lie. Moving money *between* shards is a saga; see the `fastapi_wallet_saga_pg`
    example.
    """

    def __init__(self, wallets: WalletRepository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    def perform(self, principal: str | None, params: TransferParams) -> tuple[Wallet, Wallet]:
        source = self.wallets.get(params.from_wallet_id)
        destination = self.wallets.get(params.to_wallet_id)
        return (
            self.plan.update(source.withdraw(params.amount)),
            self.plan.update(destination.deposit(params.amount)),
        )
