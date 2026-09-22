"""One repository over every shard.

`EmbeddedBitsShardingStrategy` is the whole configuration: it reads the coordinate out of the
id. Everything else - which connection, which database, which outbox - follows from that, and
no method below mentions a shard unless it is deliberately asking about one.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Row, func, select

from ekbatan import DatabaseRegistry, ModelRepository, ShardIdentifier
from ekbatan.clock import Clock, to_storage
from ekbatan.sharding import EmbeddedBitsShardingStrategy

from .model import Wallet, WalletId, WalletState
from .schema import WALLETS


class WalletRepository(ModelRepository[Wallet, WalletId]):
    def __init__(self, registry: DatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(
            Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
        )

    def to_row(self, wallet: Wallet) -> dict[str, Any]:
        """Every column, named - the counterpart to Java's `toRecord`.

        Three of the eight are conversions rather than copies, and each is a decision about how
        the value is stored: a typed id goes in as its `uuid`, an enum as its `name` rather
        than its value, and a timestamp through `to_storage`, which is what drops the zone.
        """
        return {
            "id": wallet.id.value,
            "version": wallet.version,
            "state": wallet.state.name,
            "owner_id": wallet.owner_id,
            "currency": wallet.currency,
            "balance": wallet.balance,
            "created_date": to_storage(wallet.created_date),
            # `Model.__post_init__` falls this back to `created_date`, so it is never None on a
            # constructed model - the declared type keeps the `| None` because a builder may
            # leave it unset. The column is NOT NULL.
            "updated_date": to_storage(wallet.updated_date or wallet.created_date),
        }

    def from_row(self, row: Row[Any] | Mapping[str, Any]) -> Wallet:
        """And back - the counterpart to Java's `fromRecord`.

        A plain mapping rather than a row is how an insert without `RETURNING` rebuilds what it
        just wrote, on a dialect that has none.
        """
        values = row._mapping if isinstance(row, Row) else row
        return Wallet(
            id=WalletId(values["id"]),
            version=values["version"],
            state=WalletState[values["state"]],
            owner_id=values["owner_id"],
            currency=values["currency"],
            balance=values["balance"],
            created_date=values["created_date"],
            updated_date=values["updated_date"],
        )

    def find_all_by_owner(self, owner_id: UUID) -> list[Wallet]:
        """A predicate says nothing about where its rows are, so this asks every shard.

        The cost is real and worth seeing: it is one query per database. A question you ask
        often should be answerable from one shard - which usually means choosing the shard key
        so that it is.
        """
        return self.find_where(WALLETS.c.owner_id == owner_id)

    def on_shard(self, shard: ShardIdentifier) -> list[Wallet]:
        """Everything on one database - for a per-region report, or a rebalance."""
        return self.all(shard=shard)

    def total_per_shard(self) -> dict[ShardIdentifier, Decimal]:
        """What each database holds. Drops to SQL, because the inherited reads answer
        questions about rows rather than about databases.

        `readonly_dbs()` is the replicas - fine for an operator's view of how the shards are
        filling up, and the wrong choice for anything a caller acts on.

        No `state != DELETED` filter here, unlike the other examples, because this
        application's `WalletState` declares no DELETED and no row can ever be in it. A
        statement you write yourself carries exactly what you put in it - which is the point,
        and cuts both ways: the inherited reads apply that filter for you, and this one does
        not apply it for you either.
        """
        query = select(func.coalesce(func.sum(WALLETS.c.balance), 0))
        return {
            shard: Decimal(str(conn.execute(query).scalar_one()))
            for shard, conn in self.readonly_dbs()
        }
