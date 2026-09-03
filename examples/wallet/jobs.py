"""The wallet application's scheduled work."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from ekbatan import ActionExecutor, Repository
from ekbatan.jobs import DistributedJob, Every, JobContext, Schedule

from .actions import DepositMoney, DepositParams
from .model import Wallet, WalletState


class PayStipend(DistributedJob):
    """Deposits into every open wallet, once a day, from one instance in the cluster.

    Nothing about being a job makes its work special: it invokes the same unit of work an HTTP
    request would, so the audit trail is identical and a consumer downstream cannot tell.
    """

    def __init__(
        self,
        executor: ActionExecutor,
        wallets: Repository[Wallet, UUID],
        amount: Decimal = Decimal("1.00"),
    ) -> None:
        self.executor = executor
        self.wallets = wallets
        self.amount = amount
        self.runs = 0

    name = "wallet-stipend"

    @property
    def schedule(self) -> Schedule:
        return Every(timedelta(days=1))

    def run(self, context: JobContext) -> None:
        self.runs += 1
        for wallet in self.wallets.all():
            if wallet.state is WalletState.OPENED:
                self.executor.execute(
                    DepositMoney(self.wallets),
                    DepositParams(
                        wallet_id=wallet.id, amount=self.amount, recipient="stipend@example.com"
                    ),
                )


class CountWallets(DistributedJob):
    """Reads and writes nothing. The shape of a reporting job."""

    def __init__(self, wallets: Repository[Wallet, UUID]) -> None:
        self.wallets = wallets
        self.last_count = 0

    name = "wallet-report"
    schedule = Every(timedelta(hours=1))

    def run(self, context: JobContext) -> None:
        self.last_count = self.wallets.count()
