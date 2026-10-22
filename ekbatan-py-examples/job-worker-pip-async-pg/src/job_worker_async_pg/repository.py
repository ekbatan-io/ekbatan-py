"""Repositories, awaited.

A repository is where a query that means something to the business lives. The framework's own
reads - by id, by predicate, counting, existence - are inherited, so what is left here is the
handful of questions this worker actually asks.

**All async.** The jobs' work and the event handler run on `ekbatan.aio`, on the worker loop, so
every repository here is an async one. `to_record` and `from_record` stay plain functions -
mapping waits on nothing.

**The mapping is written, as Java's is.** `AbstractRepository` declares `fromRecord` and
`toRecord` abstract there, so every repository implements both; here they are `to_record` and
`from_record`, typed by the table's record (`WalletsRecord`, generated beside `WALLETS`), and
the base refuses until they exist.

Where the names line up - which is the usual case, because a dataclass already knows its own
fields - that is two lines saying so. It was the default until `EKB-0217`, and being the default
is what made it magic: rename a column without renaming the field and the mapping silently
stopped matching, where Java gets a compile error.
"""

from __future__ import annotations

from decimal import Decimal

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
        return WalletsRecord(
            id=wallet.id.value,
            version=wallet.version,
            state=wallet.state.name,
            owner_id=wallet.owner_id,
            currency=wallet.currency,
            balance=wallet.balance,
            created_date=wallet.created_date,
            # `Model.__post_init__` falls this back to `created_date`, so it is never None on a
            # constructed model - the declared type keeps the `| None` because a builder may
            # leave it unset. The column is NOT NULL.
            updated_date=wallet.updated_date or wallet.created_date,
        )

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

    async def find_open_below(self, threshold: Decimal) -> list[Wallet]:
        """Open wallets running low - what a top-up job would ask for."""
        return await self.find_where(
            WALLETS.c.state == WalletState.OPENED.name,
            WALLETS.c.balance < threshold,
        )


class AlertRepository(AsyncEntityRepository[Alert, AlertsRecord, Id[Alert]]):
    """Alerts, awaited. Not sharded - they are looked up by wallet, not by their own id."""

    def to_record(self, alert: Alert) -> AlertsRecord:
        """Six columns, and no timestamps - an entity carries none. `recipient` is an
        `EmailAddress` both ways; its converter in `schema.py` does the text."""
        return AlertsRecord(
            id=alert.id.value,
            version=alert.version,
            state=alert.state.name,
            wallet_id=alert.wallet_id.value,
            recipient=alert.recipient,
            message=alert.message,
        )

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
