"""Repositories.

A repository is where a query that means something to the business lives. The framework's own
reads - by id, by predicate, counting, existence - are inherited, so what is left here is the
handful of questions this application actually asks.

**All async.** The HTTP requests and the event handler both run on `ekbatan.aio`, so every
repository here is an async one: every read and write is awaited. The handler's alerts go through
the same `AlertRepository`, built over the handler's own registry. `to_row` and `from_row` stay
plain functions - mapping waits on nothing.

**The mapping is written.** Every repository implements `to_row` and `from_row`, and the base
refuses to create one until both exist.

Where the names line up - which is the usual case, because a dataclass already knows its own
fields - that is two lines saying so. It was the default until `EKB-0217`, and being the default
is what made it magic: rename a column without renaming the field and the mapping silently
stopped matching.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Row, func, select

from ekbatan.aio import AsyncDatabaseRegistry, AsyncEntityRepository, AsyncModelRepository
from ekbatan.clock import Clock
from ekbatan.domain import GenericState
from ekbatan.ids import Id, ShardedId
from ekbatan.sharding import EmbeddedBitsShardingStrategy

from .model import Alert, Wallet, WalletState
from .schema import ALERTS, WALLETS


class WalletRepository(AsyncModelRepository[Wallet, ShardedId[Wallet]]):
    """Wallets, awaited, routed by the shard packed into each id."""

    def __init__(self, registry: AsyncDatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(
            Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
        )

    def to_row(self, wallet: Wallet) -> dict[str, Any]:
        """Every column, named - the model as one row.

        Two of the eight are conversions rather than copies, and each is a decision about how
        the value is stored: a typed id goes in as its `uuid`, and an enum as its `name` rather
        than its value. The timestamps go in exactly as they are - the clock gives UTC with no
        zone attached, and that is what the column holds.

        `currency` goes in as the `Currency` it is. The column's converter in `schema.py`
        turns it into text, and turns the text back into a `Currency` on every read.
        """
        return {
            "id": wallet.id.value,
            "version": wallet.version,
            "state": wallet.state.name,
            "owner_id": wallet.owner_id,
            "currency": wallet.currency,
            "balance": wallet.balance,
            "created_date": wallet.created_date,
            # `Model.__post_init__` falls this back to `created_date`, so it is never None on a
            # constructed model - the declared type keeps the `| None` because a builder may
            # leave it unset. The column is NOT NULL.
            "updated_date": wallet.updated_date or wallet.created_date,
        }

    def from_row(self, row: Row[Any] | Mapping[str, Any]) -> Wallet:
        """And back - one row as the model.

        A plain mapping rather than a row is how an insert without `RETURNING` rebuilds what it
        just wrote, on a dialect that has none.
        """
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


class AlertRepository(AsyncEntityRepository[Alert, Id[Alert]]):
    """Alerts, awaited - what the HTTP layer reads. Not sharded: looked up by wallet."""

    def to_row(self, alert: Alert) -> dict[str, Any]:
        """Six columns, and no timestamps - an entity carries none. `recipient` is an
        `EmailAddress` both ways; its converter in `schema.py` does the text."""
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

    def __init__(self, registry: AsyncDatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(Alert, ALERTS, registry, clock=clock)

    async def find_for_wallet(self, wallet_id: ShardedId[Wallet]) -> list[Alert]:
        return await self.find_where(ALERTS.c.wallet_id == wallet_id.value)
