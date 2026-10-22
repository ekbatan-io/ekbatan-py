"""Repositories for the suite, with the mapping written out.

Java's `AbstractRepository` declares `fromRecord` and `toRecord` abstract, so every repository
there implements both - including the one its own tests use, `DummyRepository`. The parity
ledger recorded that this port needed no counterpart "because mapping is by field name". That
was true, and it was the one piece of magic left in the read and write paths: rename a column
without renaming the field and the mapping silently stopped matching.

Since `EKB-0217` the mapping is written, here as everywhere. Note what writing it makes visible:
three of the eight wallet columns are conversions rather than copies, `updated_date` is declared
`datetime | None` against a NOT NULL column, and `aliases` is a list that only reaches the
database because a JSON column takes it.
"""

from __future__ import annotations

from ekbatan.aio import AbstractAsyncRepository, AsyncEntityRepository, AsyncModelRepository
from ekbatan.domain import GenericState
from ekbatan.ids import Id, ShardedId
from ekbatan.repository import AbstractRepository, EntityRepository, ModelRepository
from modes import twin

from .domain import Notification, Wallet, WalletState
from .schema import NotificationsRecord, WalletsRecord


class _WalletRows:
    """The wallet mapping, shared by the three repository kinds the suite builds."""

    def to_record(self, wallet: Wallet) -> WalletsRecord:
        return WalletsRecord(
            id=wallet.id.value,
            version=wallet.version,
            state=wallet.state.name,
            owner_id=wallet.owner_id,
            currency=wallet.currency,
            balance=wallet.balance,
            # A list, and it only survives the round trip because the column is JSON. A write
            # path binding from the runtime class rather than the declared type would discard
            # it, which is exactly what this fixture exists to catch.
            aliases=wallet.aliases,
            # Nullable on purpose, and nothing may default it: a batch in which every row
            # leaves it null is what exposes an untyped NULL bind.
            reward_points=wallet.reward_points,
            created_date=wallet.created_date,
            # `Model.__post_init__` falls this back to `created_date`, so it is never None on a
            # constructed model - the declared type keeps the `| None` because a builder may
            # leave it unset.
            updated_date=wallet.updated_date or wallet.created_date,
        )

    def from_record(self, record: WalletsRecord) -> Wallet:
        return Wallet(
            id=ShardedId[Wallet](record["id"]),
            version=record["version"],
            state=WalletState[record["state"]],
            owner_id=record["owner_id"],
            currency=record["currency"],
            balance=record["balance"],
            aliases=record["aliases"] or [],
            reward_points=record["reward_points"],
            created_date=record["created_date"],
            updated_date=record["updated_date"],
        )


class _NotificationRows:
    """An entity: no timestamps, because it is state rather than history."""

    def to_record(self, note: Notification) -> NotificationsRecord:
        return NotificationsRecord(
            id=note.id.value,
            version=note.version,
            state=note.state.name,
            wallet_id=note.wallet_id.value,
            kind=note.kind,
            recipient=note.recipient,
            message=note.message,
        )

    def from_record(self, record: NotificationsRecord) -> Notification:
        return Notification(
            id=Id[Notification](record["id"]),
            version=record["version"],
            state=GenericState[record["state"]],
            wallet_id=ShardedId[Wallet](record["wallet_id"]),
            kind=record["kind"],
            recipient=record["recipient"],
            message=record["message"],
        )


class WalletRepository(_WalletRows, AbstractRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
    """Java's `DummyRepository`: a repository that exists so the suite has one."""


class NotificationRepository(
    _NotificationRows, AbstractRepository[Notification, NotificationsRecord, Id[Notification]]
):
    pass


class WalletModelRepository(_WalletRows, ModelRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
    """For the checks that a `ModelRepository` refuses a table with no timestamps."""


class NotificationModelRepository(
    _NotificationRows, ModelRepository[Notification, NotificationsRecord, Id[Notification]]
):
    pass


class WalletEntityRepository(
    _WalletRows, EntityRepository[Wallet, WalletsRecord, ShardedId[Wallet]]
):
    pass


class NotificationEntityRepository(
    _NotificationRows, EntityRepository[Notification, NotificationsRecord, Id[Notification]]
):
    pass


# ---------------------------------------------------------------- the same, on ekbatan.aio
#
# The same mapping on the async bases. `modes.twin` files each as its sync class's twin, so a
# test module that runs both ways (`MODES`) builds these in its async run.


@twin(WalletRepository)
class AsyncWalletRepository(
    _WalletRows, AbstractAsyncRepository[Wallet, WalletsRecord, ShardedId[Wallet]]
):
    pass


@twin(NotificationRepository)
class AsyncNotificationRepository(
    _NotificationRows, AbstractAsyncRepository[Notification, NotificationsRecord, Id[Notification]]
):
    pass


@twin(WalletModelRepository)
class AsyncWalletModelRepository(
    _WalletRows, AsyncModelRepository[Wallet, WalletsRecord, ShardedId[Wallet]]
):
    pass


@twin(NotificationModelRepository)
class AsyncNotificationModelRepository(
    _NotificationRows, AsyncModelRepository[Notification, NotificationsRecord, Id[Notification]]
):
    pass


@twin(WalletEntityRepository)
class AsyncWalletEntityRepository(
    _WalletRows, AsyncEntityRepository[Wallet, WalletsRecord, ShardedId[Wallet]]
):
    pass


@twin(NotificationEntityRepository)
class AsyncNotificationEntityRepository(
    _NotificationRows, AsyncEntityRepository[Notification, NotificationsRecord, Id[Notification]]
):
    pass
