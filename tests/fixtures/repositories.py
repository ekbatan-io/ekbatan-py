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

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Row

from ekbatan.clock import to_storage
from ekbatan.domain import GenericState
from ekbatan.ids import Id, ShardedId
from ekbatan.repository import EntityRepository, ModelRepository, Repository

from .domain import Notification, Wallet, WalletState


class _WalletRows:
    """The wallet mapping, shared by the three repository kinds the suite builds."""

    def to_row(self, wallet: Wallet) -> dict[str, Any]:
        return {
            "id": wallet.id.value,
            "version": wallet.version,
            "state": wallet.state.name,
            "owner_id": wallet.owner_id,
            "currency": wallet.currency,
            "balance": wallet.balance,
            # A list, and it only survives the round trip because the column is JSON. A write
            # path binding from the runtime class rather than the declared type would discard
            # it, which is exactly what this fixture exists to catch.
            "aliases": wallet.aliases,
            # Nullable on purpose, and nothing may default it: a batch in which every row
            # leaves it null is what exposes an untyped NULL bind.
            "reward_points": wallet.reward_points,
            "created_date": to_storage(wallet.created_date),
            # `Model.__post_init__` falls this back to `created_date`, so it is never None on a
            # constructed model - the declared type keeps the `| None` because a builder may
            # leave it unset.
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
            aliases=values["aliases"] or [],
            reward_points=values["reward_points"],
            created_date=values["created_date"],
            updated_date=values["updated_date"],
        )


class _NotificationRows:
    """An entity: no timestamps, because it is state rather than history."""

    def to_row(self, note: Notification) -> dict[str, Any]:
        return {
            "id": note.id.value,
            "version": note.version,
            "state": note.state.name,
            "wallet_id": note.wallet_id.value,
            "kind": note.kind,
            "recipient": note.recipient,
            "message": note.message,
        }

    def from_row(self, row: Row[Any] | Mapping[str, Any]) -> Notification:
        values = row._mapping if isinstance(row, Row) else row
        return Notification(
            id=Id[Notification](values["id"]),
            version=values["version"],
            state=GenericState[values["state"]],
            wallet_id=ShardedId[Wallet](values["wallet_id"]),
            kind=values["kind"],
            recipient=values["recipient"],
            message=values["message"],
        )


class WalletRepository(_WalletRows, Repository[Wallet, ShardedId[Wallet]]):
    """Java's `DummyRepository`: a repository that exists so the suite has one."""


class NotificationRepository(_NotificationRows, Repository[Notification, Id[Notification]]):
    pass


class WalletModelRepository(_WalletRows, ModelRepository[Wallet, ShardedId[Wallet]]):
    """For the checks that a `ModelRepository` refuses a table with no timestamps."""


class NotificationModelRepository(
    _NotificationRows, ModelRepository[Notification, Id[Notification]]
):
    pass


class WalletEntityRepository(_WalletRows, EntityRepository[Wallet, ShardedId[Wallet]]):
    pass


class NotificationEntityRepository(
    _NotificationRows, EntityRepository[Notification, Id[Notification]]
):
    pass
