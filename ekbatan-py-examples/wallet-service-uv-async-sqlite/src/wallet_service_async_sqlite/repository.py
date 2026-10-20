"""Repositories, on `ekbatan.aio`.

A repository is where a query that means something to the business lives. The framework's own
reads - by id, by predicate, counting, existence - are inherited, so what is left here is the
handful of questions this application actually asks.

**All async.** Every read and write is awaited. `to_record` and `from_record` stay plain
functions - mapping waits on nothing - and every repository implements both, typed by the
table's record (`WalletsRecord`, written by hand in `schema.py`); the base refuses one that
does not.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, func, select

from ekbatan import ShardIdentifier
from ekbatan.aio import AsyncDatabaseRegistry, AsyncEntityRepository, AsyncModelRepository
from ekbatan.clock import Clock
from ekbatan.domain import GenericState
from ekbatan.errors import ValidationError
from ekbatan.ids import Id, ShardedId
from ekbatan.sharding import EmbeddedBitsShardingStrategy

from .model import Alert, Wallet, WalletState
from .schema import ALERTS, WALLETS, AlertsRecord, WalletsRecord


class WalletRepository(AsyncModelRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
    """Wallets, awaited, routed by the shard packed into each id.

    A model repository, because a wallet's changes are events. Saying so is what lets the
    framework check that this table can hold the timestamps a model carries.
    """

    def __init__(self, registry: AsyncDatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(
            Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
        )

    def to_record(self, wallet: Wallet) -> WalletsRecord:
        """Every column, named.

        Two of the eight are conversions rather than copies, and each is a decision about how
        the value is stored: a typed id goes in as its `uuid`, and an enum as its `name` rather
        than its value. The timestamps go in exactly as they are - the clock gives UTC with no
        zone attached, and that is what the column holds.
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
        """And back.

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
        """Every wallet an owner has, on whichever databases they live.

        A predicate says nothing about where its rows are, so this asks all of them - at once.
        Deleted rows are excluded here as everywhere - that filter is applied once, in the
        framework, rather than remembered at each call site.
        """
        return await self.find_where(WALLETS.c.owner_id == owner_id)

    async def find_open_below(self, threshold: Decimal) -> list[Wallet]:
        """Open wallets running low - what a top-up job would ask for."""
        return await self.find_where(
            WALLETS.c.state == WalletState.OPENED.name,
            WALLETS.c.balance < threshold,
        )

    async def find_on_shard(self, shard: ShardIdentifier) -> list[Wallet]:
        """Everything on one database - for a migration, or a per-shard report."""
        return await self.all(shard=shard)

    async def exists_on_shard(self, shard: ShardIdentifier, wallet_id: ShardedId[Wallet]) -> bool:
        """Whether a particular database holds this row, rather than wherever its id says.

        Worth asking during a rebalance, when the two can legitimately disagree.
        """
        async with self.readonly_db(shard=shard) as conn:
            found = await conn.execute(select(WALLETS.c.id).where(self.id_matches(wallet_id)))
            return found.first() is not None

    async def erase(self, wallet_id: ShardedId[Wallet]) -> bool:
        """Remove a wallet for real, and only if it holds nothing.

        The inherited `delete` is a soft delete - it moves the row to DELETED and every read
        stops seeing it, which is what an application almost always wants, because the row is
        still the other side of every event already published about it. This is the other
        kind, written out because the rule it enforces is this application's: a wallet with
        money in it is not something to erase.

        `tx_db_else_db` rather than `db`, so this joins a transaction if the caller has one
        open - erasing a wallet and writing the audit row of why usually belong together.
        """
        held = await self.find(wallet_id)
        if held is None:
            return False
        if held.balance != 0:
            raise ValidationError(
                f"wallet {wallet_id} still holds {held.balance} {held.currency}; move the "
                f"balance off it before erasing, or use delete() to close it and keep the row"
            )
        async with self.tx_db_else_db(key=wallet_id) as conn:
            erased = await conn.execute(delete(WALLETS).where(self.id_matches(wallet_id)))
            return erased.rowcount == 1

    async def total_balance(self) -> Decimal:
        """The sum across every shard - a query of this repository's own.

        `readonly_dbs()` hands over each shard's replica in turn; a report can be a second out
        of date. A shard holding nothing contributes zero rather than a null. The
        `state != DELETED` filter is spelled out because this is *your* statement: the
        inherited reads apply it for you, one you build carries only what you put in it.
        """
        query = select(func.coalesce(func.sum(WALLETS.c.balance), 0)).where(
            WALLETS.c.state != WalletState.DELETED.name
        )
        total = Decimal(0)
        async for _, conn in self.readonly_dbs():
            total += Decimal(str((await conn.execute(query)).scalar_one()))
        return total


class AlertRepository(AsyncEntityRepository[Alert, AlertsRecord, Id[Alert]]):
    """Alerts, awaited. Not sharded - they are looked up by wallet, not by their own id.

    An entity repository, because writing one is not news: no events, and no timestamps for
    the table to hold.
    """

    def to_record(self, alert: Alert) -> AlertsRecord:
        """Six columns, and no timestamps - an entity carries none."""
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

    def __init__(self, registry: AsyncDatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(Alert, ALERTS, registry, clock=clock)

    async def find_for_wallet(self, wallet_id: ShardedId[Wallet]) -> list[Alert]:
        return await self.find_where(ALERTS.c.wallet_id == wallet_id.value)
