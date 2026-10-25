"""Work on a schedule, once across the cluster - with the work on `ekbatan.aio`.

Three worker processes running this app share one `scheduled_tasks` row per job, so each job
runs once per slot, not three times. Distributed jobs are sync: db-scheduler calls `execute` on
one of its worker threads, and each job here hands its work to the worker loop and waits for it.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from db_scheduler.task import ExecutionContext
from db_scheduler.task.schedule import FixedDelay, Schedule

from ekbatan.aio import AsyncActionExecutor
from ekbatan.aio.locks import AsyncKeyedLockProvider
from ekbatan.distributedjobs import DistributedJob
from ekbatan.events import HandlerLoop

from .actions import WalletDepositMoneyAction
from .repository import WalletRepository

LOG = logging.getLogger(__name__)


class WalletStipendJob(DistributedJob):
    """Tops up under-funded wallets - a job invoking an action, awaited.

    Every slot it reads the open wallets below `THRESHOLD` and, for each, awaits
    `WalletDepositMoneyAction`: the action opens its own transaction, applies the deposit, writes
    a `WalletMoneyDepositedEvent` to the outbox and commits. The event handler then picks each
    event up - `WalletMoneyDepositedEventHandler`, then `CreateNotificationAction`, a row in
    `notifications` - the same chain a request drives.

    A failed deposit is logged and skipped, so one bad row does not halt the pass.
    """

    #: Wallets below this balance get a top-up on each run.
    THRESHOLD = Decimal("100.00")
    #: What each such wallet is given per run.
    STIPEND = Decimal("10.00")

    def __init__(
        self,
        loop: HandlerLoop,
        executor: AsyncActionExecutor,
        lock_provider: AsyncKeyedLockProvider,
        wallet_repository: WalletRepository,
    ) -> None:
        #: The worker loop: where the executor, the lock provider and the repository were built
        #: to be used.
        self.loop = loop
        self.executor = executor
        self.lock_provider = lock_provider
        self.wallet_repository = wallet_repository

    def name(self) -> str:
        # Cluster-wide unique: the primary key of this job's row in scheduled_tasks, and how
        # every instance recognises it as the same job.
        return "wallet-stipend"

    def schedule(self) -> Schedule:
        return FixedDelay.of_seconds(2)

    def execute(self, ctx: ExecutionContext) -> None:
        # On db-scheduler's thread: hand the work to the worker loop and wait for it.
        self.loop.run(self.top_up())

    async def top_up(self) -> None:
        under_funded = await self.wallet_repository.find_open_with_balance_below(self.THRESHOLD)
        if not under_funded:
            return
        LOG.info("WalletStipendJob: depositing %s to %d wallet(s)", self.STIPEND, len(under_funded))
        for wallet in under_funded:
            try:
                async with self.lock_provider.acquire(
                    f"wallet:{wallet.id.value}", timedelta(seconds=10)
                ):
                    await self.executor.execute(
                        WalletDepositMoneyAction,
                        WalletDepositMoneyAction.Params(
                            wallet_id=wallet.id,
                            amount=self.STIPEND,
                            recipient="stipend@system.local",
                        ),
                        principal="wallet-stipend-job",
                    )
            except Exception:
                LOG.warning(
                    "WalletStipendJob: deposit failed for wallet %s - skipping",
                    wallet.id.value,
                    exc_info=True,
                )


class WalletReportJob(DistributedJob):
    """A read-only report: counts the wallets and sums their balances, and logs it. No action,
    no writes - the scheduled query an ops dashboard or a metrics exporter is."""

    def __init__(self, loop: HandlerLoop, wallet_repository: WalletRepository) -> None:
        self.loop = loop
        self.wallet_repository = wallet_repository

    def name(self) -> str:
        return "wallet-report"

    def schedule(self) -> Schedule:
        return FixedDelay.of_seconds(5)

    def execute(self, ctx: ExecutionContext) -> None:
        self.loop.run(self.report())

    async def report(self) -> None:
        wallets = await self.wallet_repository.find_all()
        total = sum((wallet.balance for wallet in wallets), Decimal(0))
        LOG.info("WalletReportJob: %d wallets, total balance = %s", len(wallets), total)
