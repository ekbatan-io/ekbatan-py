"""Repositories.

A repository is where a query that means something to the business lives. The framework's own
reads - by id, by predicate, counting, existence - are inherited, so what is left here is the
handful of questions this application actually asks.

Note what is *not* here: no `from_row`, no `to_row`. The Java counterpart writes both, listing
every column twice, because jOOQ hands it a generated record and something has to bridge the
two. Here the mapping is by field name and the dataclass already knows its fields, so the
bridge is only worth writing when the names differ.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select

from ekbatan import DatabaseRegistry, Repository, ShardIdentifier
from ekbatan.clock import Clock
from ekbatan.sharding import EmbeddedBitsSharding

from .model import Alert, AlertId, Wallet, WalletId, WalletState
from .schema import ALERTS, WALLETS


class WalletRepository(Repository[Wallet, WalletId]):
    """Wallets, routed by the shard packed into each id."""

    def __init__(self, registry: DatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(Wallet, WALLETS, registry, sharding=EmbeddedBitsSharding(), clock=clock)

    def find_all_by_owner(self, owner_id: UUID) -> list[Wallet]:
        """Every wallet an owner has, on whichever databases they live.

        A predicate says nothing about where its rows are, so this asks all of them. Deleted
        rows are excluded here as everywhere - that filter is applied once, in the framework,
        rather than remembered at each call site.
        """
        return self.find_where(WALLETS.c.owner_id == owner_id)

    def find_open_below(self, threshold: Decimal) -> list[Wallet]:
        """Open wallets running low - what a top-up job would ask for."""
        return self.find_where(
            WALLETS.c.state == WalletState.OPENED.name,
            WALLETS.c.balance < threshold,
        )

    def find_on_shard(self, shard: ShardIdentifier) -> list[Wallet]:
        """Everything on one database - for a migration, or a per-shard report."""
        return self.all(shard=shard)

    def exists_on_shard(self, shard: ShardIdentifier, wallet_id: WalletId) -> bool:
        """Whether a particular database holds this row, rather than wherever its id says.

        Worth asking during a rebalance, when the two can legitimately disagree.
        """
        with self.readonly_db(shard=shard) as conn:
            return (
                conn.execute(select(WALLETS.c.id).where(self.id_matches(wallet_id))).first()
                is not None
            )

    def total_balance(self) -> Decimal:
        """The sum across every shard.

        The one question the inherited reads cannot answer, so it drops to SQL - which is what
        `readonly_dbs()` is for. Note it goes to the readers, and that a shard holding nothing
        contributes zero rather than a null.
        """
        query = select(func.coalesce(func.sum(WALLETS.c.balance), 0)).where(*self._live())
        return sum(
            (Decimal(str(conn.execute(query).scalar_one())) for _, conn in self.readonly_dbs()),
            Decimal(0),
        )


class AlertRepository(Repository[Alert, AlertId]):
    """Alerts. Not sharded - they are looked up by wallet, not by their own id."""

    def __init__(self, registry: DatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(Alert, ALERTS, registry, clock=clock)

    def find_for_wallet(self, wallet_id: WalletId) -> list[Alert]:
        return self.find_where(ALERTS.c.wallet_id == wallet_id.value)
