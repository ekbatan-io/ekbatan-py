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

from sqlalchemy import delete, func, select

from ekbatan import DatabaseRegistry, EntityRepository, ModelRepository, ShardIdentifier
from ekbatan.clock import Clock
from ekbatan.errors import ValidationError
from ekbatan.sharding import EmbeddedBitsShardingStrategy

from .model import Alert, AlertId, Wallet, WalletId, WalletState
from .schema import ALERTS, WALLETS


class WalletRepository(ModelRepository[Wallet, WalletId]):
    """Wallets, routed by the shard packed into each id.

    A ModelRepository, because a wallet's changes are events. Saying so is what lets the
    framework check that this table can hold the timestamps a model carries.
    """

    def __init__(self, registry: DatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(
            Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
        )

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

    def erase(self, wallet_id: WalletId) -> bool:
        """Remove a wallet for real, and only if it holds nothing.

        The inherited `delete` is a soft delete - it moves the row to DELETED and every read
        stops seeing it, which is what an application almost always wants, because the row is
        still the other side of every event already published about it.

        This is the other kind, written out rather than inherited, because the rule it enforces
        is this application's and the framework cannot know it: a wallet with money in it is
        not something to erase, whatever the caller believes. An operator running a
        data-deletion request gets an answer, not a silent no-op, so the balance is checked
        first and the miss is distinguishable from the refusal.

        `tx_db_else_db` rather than `db`, so this joins a transaction if the caller has one
        open - erasing a wallet and writing the audit row of why usually belong together.
        """
        held = self.find(wallet_id)
        if held is None:
            return False
        if held.balance != 0:
            raise ValidationError(
                f"wallet {wallet_id} still holds {held.balance} {held.currency}; move the "
                f"balance off it before erasing, or use delete() to close it and keep the row"
            )
        with self.tx_db_else_db(key=wallet_id) as conn:
            return conn.execute(delete(WALLETS).where(self.id_matches(wallet_id))).rowcount == 1

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


class AlertRepository(EntityRepository[Alert, AlertId]):
    """Alerts. Not sharded - they are looked up by wallet, not by their own id.

    An EntityRepository, because writing one is not news: no events, and no timestamps for the
    table to hold.
    """

    def __init__(self, registry: DatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(Alert, ALERTS, registry, clock=clock)

    def find_for_wallet(self, wallet_id: WalletId) -> list[Alert]:
        return self.find_where(ALERTS.c.wallet_id == wallet_id.value)
