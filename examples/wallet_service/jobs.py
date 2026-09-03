"""Work on a schedule, once across the cluster.

Five instances of this service all want to pay the daily stipend; exactly one may. Coordination
is in the database rather than in each process.

A slot can still run twice - if an instance stops writing heartbeats, another treats the
execution as dead and runs it again while the first may still be part way through. So the work
here is written to be harmless when repeated.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from ekbatan import ActionExecutor, Repository
from ekbatan.jobs import DistributedJob, Every, JobContext, Schedule

from .actions import DepositMoney, DepositParams
from .model import Wallet, WalletId, WalletState


class PayStipend(DistributedJob):
    """Deposits into every open wallet, once a day, from one instance.

    Nothing about being a job makes the work special: it invokes the same unit of work an HTTP
    request would, so the audit trail is identical and a consumer downstream cannot tell.
    """

    name = "wallet-stipend"

    def __init__(
        self,
        executor: ActionExecutor,
        wallets: Repository[Wallet, WalletId],
        amount: Decimal = Decimal("1.00"),
    ) -> None:
        self.executor = executor
        self.wallets = wallets
        self.amount = amount

    @property
    def schedule(self) -> Schedule:
        return Every(timedelta(days=1))

    def run(self, context: JobContext) -> None:
        for wallet in self.wallets.all():
            if wallet.state is WalletState.OPENED:
                self.executor.execute(
                    DepositMoney(self.wallets),
                    DepositParams(
                        wallet_id=wallet.id,
                        amount=self.amount,
                        recipient="stipend@example.com",
                    ),
                )
