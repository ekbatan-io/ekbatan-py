"""Repositories, awaited. Small here, because the saga's interesting parts are elsewhere.

**All async.** The requests and the handlers both run on `ekbatan.aio`, so every repository here
is an async one: every read and write is awaited. The handlers' wallets and steps go through the
same classes, built over the handlers' own registry. `to_row` and `from_row` stay plain
functions - mapping waits on nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import Row, select

from ekbatan.aio import AsyncDatabaseRegistry, AsyncEntityRepository, AsyncModelRepository
from ekbatan.clock import Clock
from ekbatan.domain import GenericState
from ekbatan.ids import Id, ShardedId
from ekbatan.sharding import EmbeddedBitsShardingStrategy

from .model import StepName, TransferStep, Wallet, WalletState
from .schema import TRANSFER_STEPS, WALLETS


class WalletRepository(AsyncModelRepository[Wallet, ShardedId[Wallet]]):
    """Wallets, awaited, routed by the shard packed into each id."""

    def __init__(self, registry: AsyncDatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(
            Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
        )

    def to_row(self, wallet: Wallet) -> dict[str, Any]:
        """Every column, named - the counterpart to Java's `toRecord`.

        Two of the eight are conversions rather than copies, and each is a decision about how
        the value is stored: a typed id goes in as its `uuid`, and an enum as its `name` rather
        than its value. The timestamps go in exactly as they are - the clock gives UTC with no
        zone attached, and that is what the column holds.

        `currency` goes in as the `Currency` it is. The column's converter in `schema.py`
        turns it into text, and turns the text back into a `Currency` on every read.
        """
        return {
            "id": wallet.id.value,
            "version": wallet.version,
            "state": wallet.state.name,
            "owner_id": wallet.owner_id,
            "currency": wallet.currency,
            "balance": wallet.balance,
            "created_date": wallet.created_date,
            # `Model.__post_init__` falls this back to `created_date`, so it is never None on a
            # constructed model - the declared type keeps the `| None` because a builder may
            # leave it unset. The column is NOT NULL.
            "updated_date": wallet.updated_date or wallet.created_date,
        }

    def from_row(self, row: Row[Any] | Mapping[str, Any]) -> Wallet:
        """And back - the counterpart to Java's `fromRecord`.

        A plain mapping rather than a row is how an insert without `RETURNING` rebuilds what it
        just wrote, on a dialect that has none.
        """
        values = row._mapping if isinstance(row, Row) else row
        return Wallet(
            id=ShardedId[Wallet](values["id"]),
            version=values["version"],
            state=WalletState[values["state"]],
            owner_id=values["owner_id"],
            currency=values["currency"],
            balance=values["balance"],
            created_date=values["created_date"],
            updated_date=values["updated_date"],
        )


class TransferStepRepository(AsyncEntityRepository[TransferStep, Id[TransferStep]]):
    """The idempotency markers.

    Nothing in the saga reads these - the unique constraint does the work, and a read would
    lose the race it exists to win. They are queryable so an operator can ask what happened to
    a transfer, and so the tests can show that a replay wrote nothing.
    """

    def to_row(self, step: TransferStep) -> dict[str, Any]:
        """Five columns. `step` goes in as the `StepName` it is: the column's converter in
        `schema.py` writes its name, and reads a `StepName` back. `state` is still written by
        hand, because the framework reads that column as text."""
        return {
            "id": step.id.value,
            "version": step.version,
            "state": step.state.name,
            "transfer_id": step.transfer_id,
            "step": step.step,
        }

    def from_row(self, row: Row[Any] | Mapping[str, Any]) -> TransferStep:
        values = row._mapping if isinstance(row, Row) else row
        return TransferStep(
            id=Id[TransferStep](values["id"]),
            version=values["version"],
            state=GenericState[values["state"]],
            transfer_id=values["transfer_id"],
            step=values["step"],
        )

    def __init__(self, registry: AsyncDatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(TransferStep, TRANSFER_STEPS, registry, clock=clock)

    async def steps_of(self, transfer_id: UUID) -> list[StepName]:
        """Which steps of this transfer have run, in the order they were written.

        `db()`, not `readonly_db()`: this backs `GET /transfers/{id}`, which a client polls
        immediately after the 202 - so a replica's lag would report a step as not-yet-run when
        it has committed, which is exactly the lie the 202 exists to avoid telling.

        `conn.rows(...)` rather than `conn.execute(...)`, because these rows become objects.
        The mapper is a lambda rather than `from_row`: this takes one column, not a whole row
        into a `TransferStep` - and the column's converter has already made it a `StepName`.
        """
        statement = select(TRANSFER_STEPS.c.step).where(TRANSFER_STEPS.c.transfer_id == transfer_id)
        async with self.db() as conn:
            return (await conn.rows(statement)).fetch(lambda row: row.step)
