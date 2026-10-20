"""One repository over every shard, awaited.

`EmbeddedBitsShardingStrategy` is the whole configuration: it reads the coordinate out of the
id. Everything else - which connection, which database, which outbox - follows from that, and
no method below mentions a shard unless it is deliberately asking about one.

**All async.** Every read and write is awaited. A read that names no shard asks every shard
**at the same time** - `find_where`, `all`, `count` - so it costs about one shard's round trip
rather than one per shard. `to_record` and `from_record` stay plain functions - mapping waits on
nothing.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select

from ekbatan import ShardIdentifier
from ekbatan.aio import AsyncDatabaseRegistry, AsyncEntityRepository, AsyncModelRepository
from ekbatan.clock import Clock
from ekbatan.domain import GenericState
from ekbatan.ids import Id, ShardedId
from ekbatan.sharding import EmbeddedBitsShardingStrategy

from .model import Alert, Wallet, WalletState
from .schema import ALERTS, WALLETS, AlertsRecord, WalletsRecord


class WalletRepository(AsyncModelRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
    """Wallets, awaited, routed by the shard packed into each id."""

    def __init__(self, registry: AsyncDatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(
            Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
        )

    def to_record(self, wallet: Wallet) -> WalletsRecord:
        """Every column, named - the counterpart to Java's `toRecord`, typed by the table's record.

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

    def from_record(self, record: WalletsRecord) -> Wallet:
        """And back - the counterpart to Java's `fromRecord`.

        The framework hands it the row read by column name - or, where an insert has no
        `RETURNING`, the record `to_record` just wrote.
        """
        return Wallet(
            id=ShardedId[Wallet](record["id"]),
            version=record["version"],
            state=WalletState[record["state"]],
            owner_id=record["owner_id"],
            currency=record["currency"],
            balance=record["balance"],
            created_date=record["created_date"],
            updated_date=record["updated_date"],
        )

    async def find_all_by_owner(self, owner_id: UUID) -> list[Wallet]:
        """A predicate says nothing about where its rows are, so this asks every shard - all at
        once, on `ekbatan.aio`.

        Still one query per database: a question you ask often should be answerable from one
        shard - which usually means choosing the shard key so that it is.
        """
        return await self.find_where(WALLETS.c.owner_id == owner_id)

    async def on_shard(self, shard: ShardIdentifier) -> list[Wallet]:
        """Everything on one database - for a per-region report, or a rebalance."""
        return await self.all(shard=shard)

    async def total_per_shard(self) -> dict[ShardIdentifier, Decimal]:
        """What each database holds. Drops to SQL, because the inherited reads answer
        questions about rows rather than about databases.

        Each shard's replica is asked at the same time, in a `TaskGroup`, as the inherited
        reads ask theirs: `readonly_dbs()` would visit them one after another. All or nothing:
        if one shard fails, the others are cancelled and its error is raised.

        No `state != DELETED` filter here, because this application's `WalletState` declares no
        DELETED and no row can ever be in it. A statement you write yourself carries exactly
        what you put in it - the inherited reads apply that filter for you, and this one does
        not apply it for you either.
        """
        query = select(func.coalesce(func.sum(WALLETS.c.balance), 0))

        async def total(shard: ShardIdentifier) -> Decimal:
            async with self.readonly_db(shard=shard) as conn:
                return Decimal(str((await conn.execute(query)).scalar_one()))

        async with asyncio.TaskGroup() as group:
            asked = {shard: group.create_task(total(shard)) for shard in self.registry.shards}
        return {shard: task.result() for shard, task in asked.items()}


class AlertRepository(AsyncEntityRepository[Alert, AlertsRecord, Id[Alert]]):
    """Alerts. Their ids carry no shard, so they are all on the default shard, and are looked up
    by wallet."""

    def __init__(self, registry: AsyncDatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(Alert, ALERTS, registry, clock=clock)

    def to_record(self, alert: Alert) -> AlertsRecord:
        """`recipient` is an `EmailAddress` both ways; its converter in `schema.py` does the
        text."""
        return {
            "id": alert.id.value,
            "version": alert.version,
            "state": alert.state.name,
            "wallet_id": alert.wallet_id.value,
            "recipient": alert.recipient,
            "message": alert.message,
        }

    def from_record(self, record: AlertsRecord) -> Alert:
        return Alert(
            id=Id[Alert](record["id"]),
            version=record["version"],
            state=GenericState[record["state"]],
            wallet_id=ShardedId[Wallet](record["wallet_id"]),
            recipient=record["recipient"],
            message=record["message"],
        )

    async def find_for_wallet(self, wallet_id: ShardedId[Wallet]) -> list[Alert]:
        return await self.find_where(ALERTS.c.wallet_id == wallet_id.value)
