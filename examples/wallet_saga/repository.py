"""Repositories. Small here, because the saga's interesting parts are elsewhere."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from ekbatan import DatabaseRegistry, EntityRepository, ModelRepository
from ekbatan.clock import Clock
from ekbatan.sharding import EmbeddedBitsShardingStrategy

from .model import StepName, TransferStep, TransferStepId, Wallet, WalletId
from .schema import TRANSFER_STEPS, WALLETS


class WalletRepository(ModelRepository[Wallet, WalletId]):
    def __init__(self, registry: DatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(
            Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
        )


class TransferStepRepository(EntityRepository[TransferStep, TransferStepId]):
    """The idempotency markers.

    Nothing in the saga reads these - the unique constraint does the work, and a read would
    lose the race it exists to win. They are queryable so an operator can ask what happened to
    a transfer, and so the tests can show that a replay wrote nothing.
    """

    def __init__(self, registry: DatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(TransferStep, TRANSFER_STEPS, registry, clock=clock)

    def steps_of(self, transfer_id: UUID) -> list[StepName]:
        """Which steps of this transfer have run, in the order they were written."""
        with self.readonly_db() as conn:
            rows = conn.execute(
                select(TRANSFER_STEPS.c.step).where(TRANSFER_STEPS.c.transfer_id == transfer_id)
            ).scalars()
            return [StepName(value) for value in rows]
