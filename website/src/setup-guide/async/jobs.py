"""Work on a schedule, once across the cluster - with the work on `ekbatan.aio`.

Distributed jobs are sync: db-scheduler calls `execute` on one of its threads. The job hands its
work to the handler loop - where the executor and the repository it awaits were built to be
used - and waits for it.
"""

from __future__ import annotations

from decimal import Decimal

from db_scheduler.task import ExecutionContext
from db_scheduler.task.schedule import FixedDelay, Schedule

from ekbatan.aio import AsyncActionExecutor
from ekbatan.distributedjobs import DistributedJob
from ekbatan.events import HandlerLoop

from .actions import WalletDepositMoneyAction
from .repository import WalletRepository


class WalletStipendJob(DistributedJob):
    """Every two seconds, each open wallet below `THRESHOLD` gets `STIPEND` - through
    `WalletDepositMoneyAction`, like any other caller, so its deposits are events like any
    other."""

    THRESHOLD = Decimal("100.00")
    STIPEND = Decimal("10.00")

    def __init__(
        self,
        loop: HandlerLoop,
        executor: AsyncActionExecutor,
        wallet_repository: WalletRepository,
    ) -> None:
        self.loop = loop
        self.executor = executor
        self.wallet_repository = wallet_repository

    def name(self) -> str:
        # Cluster-wide unique: the primary key of this job's row in scheduled_tasks.
        return "wallet-stipend"

    def schedule(self) -> Schedule:
        return FixedDelay.of_seconds(2)

    def execute(self, ctx: ExecutionContext) -> None:
        self.loop.run(self.top_up())

    async def top_up(self) -> None:
        for wallet in await self.wallet_repository.find_open_with_balance_below(self.THRESHOLD):
            await self.executor.execute(
                WalletDepositMoneyAction,
                WalletDepositMoneyAction.Params(
                    wallet_id=wallet.id, amount=self.STIPEND, recipient="stipend@system.local"
                ),
                principal="wallet-stipend-job",
            )
