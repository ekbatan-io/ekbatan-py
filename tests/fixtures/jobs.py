"""The wallet application's scheduled work."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from db_scheduler.task import ExecutionContext
from db_scheduler.task.schedule import FixedDelay, Schedule

from ekbatan import ActionExecutor, Repository
from ekbatan.distributedjobs import DistributedJob

from .actions import DepositMoneyAction, DepositParams
from .domain import Wallet, WalletState


class PayStipendJob(DistributedJob):
    """Deposits into every open wallet, once a day, from one instance in the cluster.

    Nothing about being a job makes its work special: it invokes the same unit of work an HTTP
    request would, so the audit trail is identical and a consumer downstream cannot tell.
    """

    def __init__(
        self,
        executor: ActionExecutor,
        wallets: Repository[Wallet, object],
        amount: Decimal = Decimal("1.00"),
    ) -> None:
        self.executor = executor
        self.wallets = wallets
        self.amount = amount
        self.runs = 0

    def name(self) -> str:
        return "wallet-stipend"

    def schedule(self) -> Schedule:
        return FixedDelay.of(timedelta(days=1))

    def execute(self, ctx: ExecutionContext) -> None:
        self.runs += 1
        for wallet in self.wallets.all():
            if wallet.state is WalletState.OPENED:
                self.executor.execute(
                    DepositMoneyAction(self.wallets),
                    DepositParams(
                        wallet_id=wallet.id, amount=self.amount, recipient="stipend@example.com"
                    ),
                )


class CountWalletsJob(DistributedJob):
    """Reads and writes nothing. The shape of a reporting job."""

    def __init__(self, wallets: Repository[Wallet, object]) -> None:
        self.wallets = wallets
        self.last_count = 0

    def name(self) -> str:
        return "wallet-report"

    def schedule(self) -> Schedule:
        return FixedDelay.of(timedelta(hours=1))

    def execute(self, ctx: ExecutionContext) -> None:
        self.last_count = self.wallets.count()
