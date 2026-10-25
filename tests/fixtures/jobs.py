"""The wallet application's scheduled work."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from db_scheduler.task import ExecutionContext
from db_scheduler.task.schedule import FixedDelay, Schedule

from ekbatan import AbstractRepository, ActionExecutor
from ekbatan.distributedjobs import DistributedJob

from .actions import (
    WalletDepositMoneyAction,
)
from .domain import Wallet, WalletState


class WalletStipendJob(DistributedJob):
    """Deposits into every open wallet, once a day, from one instance in the cluster.

    Nothing about being a job makes its work special: it invokes the same unit of work an HTTP
    request would, so the audit trail is identical and a consumer downstream cannot tell.
    """

    def __init__(
        self,
        executor: ActionExecutor,
        wallet_repository: AbstractRepository[Wallet, object],
        amount: Decimal = Decimal("1.00"),
    ) -> None:
        self.executor = executor
        self.wallet_repository = wallet_repository
        self.amount = amount
        self.runs = 0

    def name(self) -> str:
        return "wallet-stipend"

    def schedule(self) -> Schedule:
        return FixedDelay.of(timedelta(days=1))

    def execute(self, ctx: ExecutionContext) -> None:
        self.runs += 1
        for wallet in self.wallet_repository.find_all():
            if wallet.state is WalletState.OPENED:
                self.executor.execute(
                    WalletDepositMoneyAction,
                    WalletDepositMoneyAction.Params(
                        wallet_id=wallet.id, amount=self.amount, recipient="stipend@example.com"
                    ),
                )


class WalletReportJob(DistributedJob):
    """Reads and writes nothing. The shape of a reporting job."""

    def __init__(self, wallet_repository: AbstractRepository[Wallet, object]) -> None:
        self.wallet_repository = wallet_repository
        self.last_count = 0

    def name(self) -> str:
        return "wallet-report"

    def schedule(self) -> Schedule:
        return FixedDelay.of(timedelta(hours=1))

    def execute(self, ctx: ExecutionContext) -> None:
        self.last_count = self.wallet_repository.count()
