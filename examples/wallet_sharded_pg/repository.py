"""One repository over every shard.

`EmbeddedBitsShardingStrategy` is the whole configuration: it reads the coordinate out of the
id. Everything else - which connection, which database, which outbox - follows from that, and
no method below mentions a shard unless it is deliberately asking about one.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select

from ekbatan import DatabaseRegistry, ModelRepository, ShardIdentifier
from ekbatan.clock import Clock
from ekbatan.sharding import EmbeddedBitsShardingStrategy

from .model import Wallet, WalletId
from .schema import WALLETS


class WalletRepository(ModelRepository[Wallet, WalletId]):
    def __init__(self, registry: DatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(
            Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
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
        questions about rows rather than about databases."""
        query = select(func.coalesce(func.sum(WALLETS.c.balance), 0)).where(*self._live())
        return {
            shard: Decimal(str(conn.execute(query).scalar_one()))
            for shard, conn in self.readonly_dbs()
        }
