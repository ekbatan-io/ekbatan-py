"""Repositories.

A repository is where a query that means something to the business lives. The framework's own
reads - by id, by predicate, counting, existence - are inherited, so what is left here is the
handful of questions this application actually asks.

**The mapping is written, as Java's is.** `AbstractRepository` declares `fromRecord` and
`toRecord` abstract there, so every repository implements both; here they are `to_row` and
`from_row` and the base refuses until they exist.

Where the names line up - which is the usual case, because a dataclass already knows its own
fields - that is two lines saying so. It was the default until `EKB-0217`, and being the default
is what made it magic: rename a column without renaming the field and the mapping silently
stopped matching, where Java gets a compile error.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Row, delete, func, select

from ekbatan import DatabaseRegistry, EntityRepository, ModelRepository, ShardIdentifier
from ekbatan.clock import Clock, to_storage
from ekbatan.domain import GenericState
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

        The one question the inherited reads cannot answer, so it drops to SQL. `readonly_dbs()`
        is **the replicas**, and that is the deliberate choice here: a total is a report, and a
        report can be a second out of date. A read that had to be current would use `dbs()`.
        Note also that a shard holding nothing contributes zero rather than a null.

        The `state != DELETED` filter is spelled out because this is *your* statement. The
        inherited reads apply it for you; one you build yourself carries only what you put in
        it, exactly as the Java repository's `.and(WALLETS.STATE.ne(...))` does.
        """
        query = select(func.coalesce(func.sum(WALLETS.c.balance), 0)).where(
            WALLETS.c.state != WalletState.DELETED.name
        )
        return sum(
            (Decimal(str(conn.execute(query).scalar_one())) for _, conn in self.readonly_dbs()),
            Decimal(0),
        )


class AlertRepository(EntityRepository[Alert, AlertId]):
    """Alerts. Not sharded - they are looked up by wallet, not by their own id.

    An EntityRepository, because writing one is not news: no events, and no timestamps for the
    table to hold.
    """

    def to_row(self, alert: Alert) -> dict[str, Any]:
        """Six columns, and no timestamps - an entity carries none."""
        return {
            "id": alert.id.value,
            "version": alert.version,
            "state": alert.state.name,
            "wallet_id": alert.wallet_id.value,
            "recipient": alert.recipient,
            "message": alert.message,
        }

    def from_row(self, row: Row[Any] | Mapping[str, Any]) -> Alert:
        values = row._mapping if isinstance(row, Row) else row
        return Alert(
            id=AlertId(values["id"]),
            version=values["version"],
            state=GenericState[values["state"]],
            wallet_id=WalletId(values["wallet_id"]),
            recipient=values["recipient"],
            message=values["message"],
        )

    def __init__(self, registry: DatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(Alert, ALERTS, registry, clock=clock)

    def find_for_wallet(self, wallet_id: WalletId) -> list[Alert]:
        return self.find_where(ALERTS.c.wallet_id == wallet_id.value)
