"""Repositories, on `ekbatan.aio`: every read awaits.

A repository is where a query that means something to the business lives. The framework's own
reads - by id, by predicate, counting, existence - are inherited, so what is left here is the
handful of questions this application actually asks. The mapping is written, and typed by the
table's record, which schemagen writes into `schema.py`.
"""

from __future__ import annotations

from uuid import UUID

from ekbatan.aio import AsyncDatabaseRegistry, AsyncEntityRepository, AsyncModelRepository
from ekbatan.domain import GenericState
from ekbatan.ids import Id

from .model import TransferStep, TransferStepName, Wallet, WalletState
from .schema import TRANSFER_STEPS, WALLETS, TransferStepsRecord, WalletsRecord


class WalletRepository(AsyncModelRepository[Wallet, WalletsRecord, Id[Wallet]]):
    def __init__(self, database_registry: AsyncDatabaseRegistry) -> None:
        super().__init__(Wallet, WALLETS, database_registry)

    async def find_all_by_owner_id(self, owner_id: UUID) -> list[Wallet]:
        return await self.find_where(WALLETS.c.owner_id == owner_id)

    def to_record(self, wallet: Wallet) -> WalletsRecord:
        return WalletsRecord(
            id=wallet.id.value,
            version=wallet.version,
            state=wallet.state.name,
            owner_id=wallet.owner_id,
            currency=wallet.currency,
            balance=wallet.balance,
            created_date=wallet.created_date,
            # Never None on a constructed model - `Model.__post_init__` falls it back to
            # `created_date` - while the column is NOT NULL.
            updated_date=wallet.updated_date or wallet.created_date,
        )

    def from_record(self, record: WalletsRecord) -> Wallet:
        return Wallet(
            id=Id[Wallet](record.column_id()),
            version=record.column_version(),
            state=WalletState[record.column_state()],
            owner_id=record.column_owner_id(),
            currency=record.column_currency(),
            balance=record.column_balance(),
            created_date=record.column_created_date(),
            updated_date=record.column_updated_date(),
        )


class TransferStepRepository(
    AsyncEntityRepository[TransferStep, TransferStepsRecord, Id[TransferStep]]
):
    def __init__(self, database_registry: AsyncDatabaseRegistry) -> None:
        super().__init__(TransferStep, TRANSFER_STEPS, database_registry)

    async def exists_step(self, transfer_id: UUID, step: TransferStepName) -> bool:
        return await self.exists_where(
            TRANSFER_STEPS.c.transfer_id == transfer_id, TRANSFER_STEPS.c.step == step
        )

    def to_record(self, step: TransferStep) -> TransferStepsRecord:
        return TransferStepsRecord(
            id=step.id.value,
            version=step.version,
            state=step.state.name,
            transfer_id=step.transfer_id,
            step=step.step,
        )

    def from_record(self, record: TransferStepsRecord) -> TransferStep:
        return TransferStep(
            id=Id[TransferStep](record.column_id()),
            version=record.column_version(),
            state=GenericState[record.column_state()],
            transfer_id=record.column_transfer_id(),
            step=record.column_step(),
        )
