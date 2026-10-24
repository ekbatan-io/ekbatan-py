"""Work on a schedule, once across the cluster.

Every instance of the service registers the job; db-scheduler runs each slot on one of them,
coordinated through the `scheduled_tasks` table.
"""

from __future__ import annotations

from decimal import Decimal

from db_scheduler.task import ExecutionContext
from db_scheduler.task.schedule import FixedDelay, Schedule

from ekbatan import ActionExecutor
from ekbatan.distributedjobs import DistributedJob

from .actions import WalletDepositMoneyAction
from .repository import WalletRepository


class WalletStipendJob(DistributedJob):
    """Every two seconds, each open wallet below `THRESHOLD` gets `STIPEND` - through
    `WalletDepositMoneyAction`, like any other caller, so its deposits are events like any
    other."""

    THRESHOLD = Decimal("100.00")
    STIPEND = Decimal("10.00")

    def __init__(self, executor: ActionExecutor, wallet_repository: WalletRepository) -> None:
        self.executor = executor
        self.wallet_repository = wallet_repository

    def name(self) -> str:
        # Cluster-wide unique: the primary key of this job's row in scheduled_tasks.
        return "wallet-stipend"

    def schedule(self) -> Schedule:
        return FixedDelay.of_seconds(2)

    def execute(self, ctx: ExecutionContext) -> None:
        for wallet in self.wallet_repository.find_open_with_balance_below(self.THRESHOLD):
            self.executor.execute(
                WalletDepositMoneyAction,
                WalletDepositMoneyAction.Params(
                    wallet_id=wallet.id, amount=self.STIPEND, recipient="stipend@system.local"
                ),
                principal="wallet-stipend-job",
            )
