"""Repositories, on `ekbatan.aio`: every read awaits.

A repository is where a query that means something to the business lives. The framework's own
reads - by id, by predicate, counting, existence - are inherited, so what is left here is the
handful of questions this application asks.

**The mapping is written, and typed.** Every repository implements `to_record` and
`from_record`. Both are typed by the table's record - `WalletsRecord`, which schemagen writes into
`schema.py` beside `WALLETS` - so mypy says so when a column is left out, misspelt, or given the
wrong type.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from ekbatan.aio import AsyncDatabaseRegistry, AsyncEntityRepository, AsyncModelRepository
from ekbatan.ids import Id

from .model import Notification, NotificationKind, NotificationState, Wallet, WalletState
from .schema import NOTIFICATIONS, WALLETS, NotificationsRecord, WalletsRecord


class WalletRepository(AsyncModelRepository[Wallet, WalletsRecord, Id[Wallet]]):
    def __init__(self, database_registry: AsyncDatabaseRegistry) -> None:
        super().__init__(Wallet, WALLETS, database_registry)

    async def find_open_with_balance_below(self, threshold: Decimal) -> list[Wallet]:
        return await self.find_where(
            WALLETS.c.balance < threshold, WALLETS.c.state == WalletState.OPENED.name
        )

    def to_record(self, wallet: Wallet) -> WalletsRecord:
        """Every column, named - the model as one row. `currency` goes in as the `Currency` it is:
        the column's converter in `schema.py` turns it into text and back."""
        return WalletsRecord(
            id=wallet.id.value,
            version=wallet.version,
            state=wallet.state.name,
            owner_id=wallet.owner_id,
            currency=wallet.currency,
            balance=wallet.balance,
            created_date=wallet.created_date,
            # Never None on a constructed model - `Model.__post_init__` falls it back to
            # `created_date` - while the column is NOT NULL.
            updated_date=wallet.updated_date or wallet.created_date,
        )

    def from_record(self, record: WalletsRecord) -> Wallet:
        return Wallet(
            id=Id[Wallet](record.column_id()),
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
    def __init__(self, database_registry: AsyncDatabaseRegistry) -> None:
        super().__init__(Notification, NOTIFICATIONS, database_registry)

    async def find_all_by_wallet_id(self, wallet_id: UUID) -> list[Notification]:
        return await self.find_where(NOTIFICATIONS.c.wallet_id == wallet_id)

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
