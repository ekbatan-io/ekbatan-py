"""Repositories, awaited. Small here, because the saga's interesting parts are elsewhere.

**All async.** The requests and the handlers both run on `ekbatan.aio`, so every repository here
is an async one: every read and write is awaited. The handlers' wallets and steps go through the
same classes, built over the handlers' own registry. `to_record` and `from_record` stay plain
functions - mapping waits on nothing.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from ekbatan.aio import AsyncDatabaseRegistry, AsyncEntityRepository, AsyncModelRepository
from ekbatan.clock import Clock
from ekbatan.domain import GenericState
from ekbatan.ids import Id, ShardedId
from ekbatan.sharding import EmbeddedBitsShardingStrategy

from .model import StepName, TransferStep, Wallet, WalletState
from .schema import TRANSFER_STEPS, WALLETS, TransferStepsRecord, WalletsRecord


class WalletRepository(AsyncModelRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
    """Wallets, awaited, routed by the shard packed into each id."""

    def __init__(self, registry: AsyncDatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(
            Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
        )

    def to_record(self, wallet: Wallet) -> WalletsRecord:
        """Every column, named - the counterpart to Java's `toRecord`, typed by the table's record.

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
        """And back - the counterpart to Java's `fromRecord`.

        The framework hands it the row read by column name - or, where an insert has no
        `RETURNING`, the record `to_record` just wrote.
        """
        return Wallet(
            id=ShardedId[Wallet](record["id"]),
            version=record["version"],
            state=WalletState[record["state"]],
            owner_id=record["owner_id"],
            currency=record["currency"],
            balance=record["balance"],
            created_date=record["created_date"],
            updated_date=record["updated_date"],
        )


class TransferStepRepository(
    AsyncEntityRepository[TransferStep, TransferStepsRecord, Id[TransferStep]]
):
    """The idempotency markers.

    Nothing in the saga reads these - the unique constraint does the work, and a read would
    lose the race it exists to win. They are queryable so an operator can ask what happened to
    a transfer, and so the tests can show that a replay wrote nothing.
    """

    def to_record(self, step: TransferStep) -> TransferStepsRecord:
        """Five columns. `step` goes in as the `StepName` it is: the column's converter in
        `schema.py` writes its name, and reads a `StepName` back. `state` is still written by
        hand, because the framework reads that column as text."""
        return TransferStepsRecord(
            id=step.id.value,
            version=step.version,
            state=step.state.name,
            transfer_id=step.transfer_id,
            step=step.step,
        )

    def from_record(self, record: TransferStepsRecord) -> TransferStep:
        return TransferStep(
            id=Id[TransferStep](record["id"]),
            version=record["version"],
            state=GenericState[record["state"]],
            transfer_id=record["transfer_id"],
            step=record["step"],
        )

    def __init__(self, registry: AsyncDatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(TransferStep, TRANSFER_STEPS, registry, clock=clock)

    async def steps_of(self, transfer_id: UUID) -> list[StepName]:
        """Which steps of this transfer have run, in the order they were written.

        `db()`, not `readonly_db()`: this backs `GET /transfers/{id}`, which a client polls
        immediately after the 202 - so a replica's lag would report a step as not-yet-run when
        it has committed, which is exactly the lie the 202 exists to avoid telling.

        `conn.rows(...)` rather than `conn.execute(...)`, because these rows become objects.
        The mapper is a lambda rather than `from_record`: this takes one column, not a whole row
        into a `TransferStep` - and the column's converter has already made it a `StepName`.
        """
        statement = select(TRANSFER_STEPS.c.step).where(TRANSFER_STEPS.c.transfer_id == transfer_id)
        async with self.db() as conn:
            return (await conn.rows(statement)).fetch(lambda row: row["step"])
