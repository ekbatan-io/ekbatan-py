"""Repositories.

A repository is where a query that means something to the business lives. The framework's own
reads - by id, by predicate, counting, existence - are inherited, so what is left here is the
handful of questions this application actually asks.

**The mapping is written, and typed.** Every repository implements `to_record` and
`from_record`, and the base refuses to create one until both exist. Both are typed by the
table's record - `WalletsRecord`, which schemagen writes into `schema.py` beside `WALLETS` - so
mypy says so when a column is left out, misspelt, or given the wrong type.

Where the names line up - which is the usual case, because a dataclass already knows its own
fields - that is two lines saying so. It was the default until `EKB-0217`, and being the default
is what made it magic: rename a column without renaming the field and the mapping silently
stopped matching.
"""

from __future__ import annotations

from ekbatan import DatabaseRegistry, EntityRepository, ModelRepository
from ekbatan.clock import Clock
from ekbatan.domain import GenericState
from ekbatan.ids import Id, ShardedId
from ekbatan.sharding import EmbeddedBitsShardingStrategy

from .model import Alert, Wallet, WalletState
from .schema import ALERTS, WALLETS, AlertsRecord, WalletsRecord


class WalletRepository(ModelRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
    """Wallets, routed by the shard packed into each id.

    A ModelRepository, because a wallet's changes are events. Saying so is what lets the
    framework check that this table can hold the timestamps a model carries.
    """

    def __init__(self, registry: DatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(
            Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
        )

    def to_record(self, wallet: Wallet) -> WalletsRecord:
        """Every column, named - the model as one row.

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
        """And back - one row as the model.

        The framework hands it the row read by column name - or, where an insert has no
        `RETURNING`, the record `to_record` just wrote.
        """
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


class AlertRepository(EntityRepository[Alert, AlertsRecord, Id[Alert]]):
    """Alerts. Not sharded - they are looked up by wallet, not by their own id.

    An EntityRepository, because writing one is not news: no events, and no timestamps for the
    table to hold.
    """

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
            id=Id[Alert](record.column_id()),
            version=record.column_version(),
            state=GenericState[record.column_state()],
            wallet_id=ShardedId[Wallet](record.column_wallet_id()),
            recipient=record.column_recipient(),
            message=record.column_message(),
        )

    def __init__(self, registry: DatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(Alert, ALERTS, registry, clock=clock)

    def find_for_wallet(self, wallet_id: ShardedId[Wallet]) -> list[Alert]:
        return self.find_where(ALERTS.c.wallet_id == wallet_id.value)
