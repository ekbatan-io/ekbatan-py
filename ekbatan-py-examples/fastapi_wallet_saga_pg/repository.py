"""Repositories. Small here, because the saga's interesting parts are elsewhere."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import Row, select

from ekbatan import DatabaseRegistry, EntityRepository, ModelRepository
from ekbatan.clock import Clock, to_storage
from ekbatan.domain import GenericState
from ekbatan.sharding import EmbeddedBitsShardingStrategy

from .model import StepName, TransferStep, TransferStepId, Wallet, WalletId, WalletState
from .schema import TRANSFER_STEPS, WALLETS


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


class TransferStepRepository(EntityRepository[TransferStep, TransferStepId]):
    """The idempotency markers.

    Nothing in the saga reads these - the unique constraint does the work, and a read would
    lose the race it exists to win. They are queryable so an operator can ask what happened to
    a transfer, and so the tests can show that a replay wrote nothing.
    """

    def to_row(self, step: TransferStep) -> dict[str, Any]:
        """Five columns. `step` is an enum, so it goes in by name like any other."""
        return {
            "id": step.id.value,
            "version": step.version,
            "state": step.state.name,
            "transfer_id": step.transfer_id,
            "step": step.step.name,
        }

    def from_row(self, row: Row[Any] | Mapping[str, Any]) -> TransferStep:
        values = row._mapping if isinstance(row, Row) else row
        return TransferStep(
            id=TransferStepId(values["id"]),
            version=values["version"],
            state=GenericState[values["state"]],
            transfer_id=values["transfer_id"],
            step=StepName[values["step"]],
        )

    def __init__(self, registry: DatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(TransferStep, TRANSFER_STEPS, registry, clock=clock)

    def steps_of(self, transfer_id: UUID) -> list[StepName]:
        """Which steps of this transfer have run, in the order they were written.

        `db()`, not `readonly_db()`, and the distinction matters here more than anywhere else in
        these examples. This backs `GET /transfers/{id}`, which a client polls immediately after
        the 202 - so a replica's lag would report a step as not-yet-run when it has committed,
        which is exactly the lie the 202 exists to avoid telling.

        `conn.rows(...)` rather than `conn.execute(...)`, because these rows become objects.
        The mapper is a lambda rather than `from_row`: this projects one column into an enum,
        not a whole row into a `TransferStep`.
        """
        statement = select(TRANSFER_STEPS.c.step).where(TRANSFER_STEPS.c.transfer_id == transfer_id)
        with self.db() as conn:
            return conn.rows(statement).fetch(lambda row: StepName(row.step))
