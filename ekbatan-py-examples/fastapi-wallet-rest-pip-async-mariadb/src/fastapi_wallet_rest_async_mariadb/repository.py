"""Repositories, on `ekbatan.aio`: every read awaits.

A repository is where a query that means something to the business lives. The framework's own
reads - by id, by predicate, counting, existence - are inherited, so what is left here is the
handful of questions this application actually asks.

**The mapping is written, and typed.** Every repository implements `to_record` and
`from_record`, and the base refuses to create one until both exist. Both are typed by the
table's record - `WalletsRecord`, which schemagen writes into `schema.py` beside `WALLETS` - so
mypy says so when a column is left out, misspelt, or given the wrong type.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from ekbatan import ShardIdentifier
from ekbatan.aio import AsyncDatabaseRegistry, AsyncEntityRepository, AsyncModelRepository
from ekbatan.ids import Id, ShardedId
from ekbatan.sharding import EmbeddedBitsShardingStrategy

from .model import Notification, NotificationKind, NotificationState, Wallet, WalletState
from .schema import NOTIFICATIONS, WALLETS, NotificationsRecord, WalletsRecord


class WalletRepository(AsyncModelRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
    """Wallets, routed by the shard packed into each id.

    A ModelRepository, because a wallet's changes are events. Saying so is what lets the
    framework check that this table can hold the timestamps a model carries.
    """

    def __init__(self, database_registry: AsyncDatabaseRegistry) -> None:
        super().__init__(
            Wallet, WALLETS, database_registry, sharding=EmbeddedBitsShardingStrategy()
        )

    async def find_all_by_owner_id(self, owner_id: UUID) -> list[Wallet]:
        """Every shard's replica, asked in turn: an owner's wallets can be on any of them."""
        return await self.find_all_where(WALLETS.c.owner_id == owner_id)

    async def find_all_on_shard(self, shard: ShardIdentifier) -> list[Wallet]:
        return await self.find_all(shard=shard)

    async def exists_on_shard(self, shard: ShardIdentifier, wallet_id: UUID) -> bool:
        return await self.exists_where(WALLETS.c.id == wallet_id, shard=shard)

    async def find_open_with_balance_below(self, threshold: Decimal) -> list[Wallet]:
        return await self.find_all_where(
            WALLETS.c.balance < threshold, WALLETS.c.state == WalletState.OPENED.name
        )

    def to_record(self, wallet: Wallet) -> WalletsRecord:
        """Every column, named - the model as one row.

        A typed id goes in as its `uuid` and an enum as its `name`. `currency` goes in as the
        `Currency` it is: the column's converter in `schema.py` turns it into text, and the text
        back into a `Currency` on every read.
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
        return Wallet(
            id=ShardedId[Wallet](record.column_id()),
            version=record.column_version(),
            state=WalletState[record.column_state()],
            owner_id=record.column_owner_id(),
            currency=record.column_currency(),
            balance=record.column_balance(),
            created_date=record.column_created_date(),
            updated_date=record.column_updated_date(),
        )


class NotificationRepository(
    AsyncEntityRepository[Notification, NotificationsRecord, Id[Notification]]
):
    """Notifications, on the default shard: no sharding strategy, so every one is written there
    and read from there, whichever shard its wallet is on.

    An EntityRepository, because writing one is not news: no events, and no timestamps for the
    table to hold.
    """

    def __init__(self, database_registry: AsyncDatabaseRegistry) -> None:
        super().__init__(Notification, NOTIFICATIONS, database_registry)

    async def find_all_by_wallet_id(self, wallet_id: UUID) -> list[Notification]:
        return await self.find_all_where(NOTIFICATIONS.c.wallet_id == wallet_id)

    def to_record(self, notification: Notification) -> NotificationsRecord:
        return NotificationsRecord(
            id=notification.id.value,
            version=notification.version,
            state=notification.state.name,
            wallet_id=notification.wallet_id,
            kind=notification.kind.name,
            recipient=notification.recipient,
            message=notification.message,
        )

    def from_record(self, record: NotificationsRecord) -> Notification:
        return Notification(
            id=Id[Notification](record.column_id()),
            version=record.column_version(),
            state=NotificationState[record.column_state()],
            wallet_id=record.column_wallet_id(),
            kind=NotificationKind[record.column_kind()],
            recipient=record.column_recipient(),
            message=record.column_message(),
        )
