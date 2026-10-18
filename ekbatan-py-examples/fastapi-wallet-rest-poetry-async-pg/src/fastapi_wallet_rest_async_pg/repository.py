"""Repositories.

A repository is where a query that means something to the business lives. The framework's own
reads - by id, by predicate, counting, existence - are inherited, so what is left here is the
handful of questions this application actually asks.

**Two kinds, one mapping.** The HTTP requests run on `ekbatan.aio`, so their repositories are
the async ones: every read and write is awaited. The event handler runs on a thread, as handlers
do, so the one repository it writes through is the sync one. `to_row` and `from_row` are plain
functions either way, so each mapping is written once, in a small class both kinds take it
from.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Row, func, select

from ekbatan import DatabaseRegistry, EntityRepository
from ekbatan.aio import AsyncDatabaseRegistry, AsyncEntityRepository, AsyncModelRepository
from ekbatan.clock import Clock, to_storage
from ekbatan.domain import GenericState
from ekbatan.ids import Id, ShardedId
from ekbatan.sharding import EmbeddedBitsShardingStrategy

from .model import Alert, Wallet, WalletState
from .schema import ALERTS, WALLETS


class WalletRows:
    """The wallet as one row, and back. Plain functions: nothing here waits on anything."""

    def to_row(self, wallet: Wallet) -> dict[str, Any]:
        """Every column, named. A typed id goes in as its `uuid`, an enum as its `name`, a
        timestamp through `to_storage`, which drops the zone - the column holds UTC wall-clock
        time. `currency` goes in as the `Currency` it is; the column's converter does the text.
        """
        return {
            "id": wallet.id.value,
            "version": wallet.version,
            "state": wallet.state.name,
            "owner_id": wallet.owner_id,
            "currency": wallet.currency,
            "balance": wallet.balance,
            "created_date": to_storage(wallet.created_date),
            # `Model.__post_init__` falls this back to `created_date`, so it is never
            # None on a constructed model - the declared type keeps the `| None`
            # because a builder may leave it unset. The column is NOT NULL.
            "updated_date": to_storage(wallet.updated_date or wallet.created_date),
        }

    def from_row(self, row: Row[Any] | Mapping[str, Any]) -> Wallet:
        values = row._mapping if isinstance(row, Row) else row
        return Wallet(
            id=ShardedId[Wallet](values["id"]),
            version=values["version"],
            state=WalletState[values["state"]],
            owner_id=values["owner_id"],
            currency=values["currency"],
            balance=values["balance"],
            created_date=values["created_date"],
            updated_date=values["updated_date"],
        )


class AlertRows:
    """The alert as one row, and back: six columns, no timestamps - an entity carries none."""

    def to_row(self, alert: Alert) -> dict[str, Any]:
        return {
            "id": alert.id.value,
            "version": alert.version,
            "state": alert.state.name,
            "wallet_id": alert.wallet_id.value,
            "recipient": alert.recipient,
            "message": alert.message,
        }

    def from_row(self, row: Row[Any] | Mapping[str, Any]) -> Alert:
        values = row._mapping if isinstance(row, Row) else row
        return Alert(
            id=Id[Alert](values["id"]),
            version=values["version"],
            state=GenericState[values["state"]],
            wallet_id=ShardedId[Wallet](values["wallet_id"]),
            recipient=values["recipient"],
            message=values["message"],
        )


class WalletRepository(WalletRows, AsyncModelRepository[Wallet, ShardedId[Wallet]]):
    """Wallets, awaited, routed by the shard packed into each id."""

    def __init__(self, registry: AsyncDatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(
            Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
        )

    async def find_all_by_owner(self, owner_id: UUID) -> list[Wallet]:
        """Every wallet an owner has. A predicate says nothing about where its rows are, so
        this asks every database - all at once, on `ekbatan.aio`."""
        return await self.find_where(WALLETS.c.owner_id == owner_id)

    async def total_balance(self) -> Decimal:
        """The sum across every shard - a query of this repository's own.

        `readonly_dbs()` hands over each shard's replica in turn; a report can be a second out
        of date. The `state != DELETED` filter is spelled out because this is *your* statement:
        the inherited reads apply it for you, one you build carries only what you put in it.
        """
        query = select(func.coalesce(func.sum(WALLETS.c.balance), 0)).where(
            WALLETS.c.state != WalletState.DELETED.name
        )
        total = Decimal(0)
        async for _, conn in self.readonly_dbs():
            total += Decimal(str((await conn.execute(query)).scalar_one()))
        return total


class AlertRepository(AlertRows, AsyncEntityRepository[Alert, Id[Alert]]):
    """Alerts, awaited - what the HTTP layer reads. Not sharded: looked up by wallet."""

    def __init__(self, registry: AsyncDatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(Alert, ALERTS, registry, clock=clock)

    async def find_for_wallet(self, wallet_id: ShardedId[Wallet]) -> list[Alert]:
        return await self.find_where(ALERTS.c.wallet_id == wallet_id.value)


class HandlerAlertRepository(AlertRows, EntityRepository[Alert, Id[Alert]]):
    """Alerts, written by the event handler - which runs on a thread, so this one is sync."""

    def __init__(self, registry: DatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(Alert, ALERTS, registry, clock=clock)
