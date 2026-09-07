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

from db_scheduler.task import ExecutionContext
from db_scheduler.task.schedule import FixedDelay, Schedule

from ekbatan import ActionExecutor
from ekbatan.distributedjobs import DistributedJob

from .actions import DepositMoney, DepositParams
from .model import WalletState
from .repository import WalletRepository


class PayStipend(DistributedJob):
    """Deposits into every open wallet, once a day, from one instance.

    Nothing about being a job makes the work special: it invokes the same unit of work an HTTP
    request would, so the audit trail is identical and a consumer downstream cannot tell.
    """

    def __init__(
        self,
        executor: ActionExecutor,
        wallets: WalletRepository,
        amount: Decimal = Decimal("1.00"),
    ) -> None:
        self.executor = executor
        self.wallets = wallets
        self.amount = amount

    def name(self) -> str:
        """The primary key of this job's row, and how every instance recognises it as the same
        job. Stable across restarts, or each boot writes a fresh row and nothing ever recurs."""
        return "wallet-stipend"

    def schedule(self) -> Schedule:
        return FixedDelay.of(timedelta(days=1))

    def execute(self, ctx: ExecutionContext) -> None:
        for wallet in self.wallets.all():
            if wallet.state is WalletState.OPENED:
                self.executor.execute(
                    DepositMoney,
                    DepositParams(
                        wallet_id=wallet.id,
                        amount=self.amount,
                        recipient="stipend@example.com",
                    ),
                )
