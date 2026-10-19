"""Work on a schedule, once across the cluster - with the work awaited.

Five instances of this service all want to pay the daily stipend; exactly one may. Coordination
is in the database rather than in each process.

A slot can still run twice - if an instance stops writing heartbeats, another treats the
execution as dead and runs it again while the first may still be part way through. So the work
here is written to be harmless when repeated.

**The job is sync, its work is not.** db-scheduler claims the job's row and calls `execute` on
one of its worker threads, as for any job. `execute` hands the work - a coroutine - to a
`HandlerLoop` with `loop.run(...)` and waits for it; there it awaits an `AsyncActionExecutor` and
an async repository built for that loop. Whatever the coroutine raises, `execute` raises, so a
failure and its retry are db-scheduler's exactly as for a sync job.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from db_scheduler.task import ExecutionContext
from db_scheduler.task.schedule import FixedDelay, Schedule

from ekbatan.aio import AsyncActionExecutor
from ekbatan.distributedjobs import DistributedJob
from ekbatan.events import HandlerLoop

from .actions import DepositMoneyAction, DepositParams
from .model import WalletState
from .repository import WalletRepository


class PayStipendJob(DistributedJob):
    """Deposits into every open wallet, once a day, from one instance.

    Nothing about being a job makes the work special: it invokes the same unit of work an HTTP
    request would, so the audit trail is identical and a consumer downstream cannot tell.
    """

    def __init__(
        self,
        loop: HandlerLoop,
        executor: AsyncActionExecutor,
        wallets: WalletRepository,
        amount: Decimal = Decimal("1.00"),
    ) -> None:
        #: The loop `executor` and `wallets` were built to be used on.
        self.loop = loop
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
        # On db-scheduler's thread: hand the work to the loop and wait for it.
        self.loop.run(self.pay())

    async def pay(self) -> None:
        for wallet in await self.wallets.all():
            if wallet.state is WalletState.OPENED:
                await self.executor.execute(
                    DepositMoneyAction,
                    DepositParams(
                        wallet_id=wallet.id,
                        amount=self.amount,
                        recipient="stipend@example.com",
                    ),
                )
