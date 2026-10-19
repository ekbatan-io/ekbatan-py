"""Two jobs, and the two shapes a job comes in - with their work async.

`PayStipendJob` writes, through the same actions an HTTP request would use. `WalletReportJob`
only reads. Both run **at most once per slot across the whole cluster** - three replicas of this
worker share one row in `scheduled_tasks`, and the row is what makes that true. Nothing in
either class arranges it.

**What is async here, and what is not.** A distributed job is always sync: db-scheduler claims
its row and calls `execute` on one of its worker threads, and heartbeats it from another. That
does not change. What changes is the *work*: each job's body is a coroutine, run with
`loop.run(...)` on the worker loop - one event loop on a thread of its own, a `HandlerLoop`,
the same kind dispatch runs async handlers on - where it awaits an `AsyncActionExecutor` and
async repositories. The job thread waits for it, so the claim, the heartbeat, a failure and its
retry are db-scheduler's exactly as for a sync job: whatever the coroutine raises, `execute`
raises.

The rule every job body has to obey is at the bottom of `pay`: **a slot can run twice.** If
an instance stops writing heartbeats - because it died, or stalled, or briefly lost the
database - another treats the execution as dead and runs it again, while the first may still be
part-way through. The claim protects the row, not your method.
"""

from __future__ import annotations

import logging
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

LOG = logging.getLogger("examples.job_worker_async_pg")


class PayStipendJob(DistributedJob):
    """Tops up every wallet running low, by invoking an ordinary action - awaited.

    Nothing about being a job makes the work special. It goes through `DepositMoneyAction` like
    any other caller, so the outbox row it writes is indistinguishable from one a request
    produced - `action_name` says `DepositMoneyAction` either way, and a consumer downstream
    cannot tell and does not need to.
    """

    def __init__(
        self,
        loop: HandlerLoop,
        executor: AsyncActionExecutor,
        wallets: WalletRepository,
        *,
        threshold: Decimal = Decimal("100.00"),
        amount: Decimal = Decimal("10.00"),
        every: timedelta = timedelta(seconds=2),
    ) -> None:
        #: The worker loop: where `executor` and `wallets` were built to be used.
        self.loop = loop
        self.executor = executor
        self.wallets = wallets
        self.threshold = threshold
        self.amount = amount
        self.every = every

    def name(self) -> str:
        """The primary key of this job's row, and how every instance recognises it as the same
        job. It must be **stable across restarts**: a name built from a uuid or today's date
        writes a fresh row every boot, orphans the old one, and the job never actually recurs.
        """
        return "wallet-stipend"

    def schedule(self) -> Schedule:
        return FixedDelay(self.every)

    def execute(self, ctx: ExecutionContext) -> None:
        # On db-scheduler's thread: hand the work to the worker loop and wait for it.
        self.loop.run(self.pay())

    async def pay(self) -> None:
        # Idempotent by construction rather than by promise: the query selects wallets *below*
        # the threshold, so a repeat of this slot finds the ones it already topped up are no
        # longer below it. A job that deposited unconditionally would double-pay on a replay.
        low = await self.wallets.find_open_below(self.threshold)
        for wallet in low:
            if wallet.state is not WalletState.OPENED:
                continue
            await self.executor.execute(
                DepositMoneyAction,
                DepositParams(
                    wallet_id=wallet.id,
                    amount=self.amount,
                    recipient="stipend@example.com",
                ),
            )
        LOG.info("stipend paid into %d wallet(s)", len(low))


class WalletReportJob(DistributedJob):
    """A scheduled query: counts and sums, writes nothing - awaited.

    The other shape a job comes in, and the more common one - an ops dashboard, a metrics
    exporter, a freshness probe. It invokes no action and so produces no outbox row, which is
    correct: nothing happened.
    """

    def __init__(
        self,
        loop: HandlerLoop,
        wallets: WalletRepository,
        *,
        every: timedelta = timedelta(seconds=5),
    ) -> None:
        self.loop = loop
        self.wallets = wallets
        self.every = every
        #: What the last run saw. Here so a test can assert the job ran without reading a log.
        self.last: tuple[int, Decimal] | None = None

    def name(self) -> str:
        return "wallet-report"

    def schedule(self) -> Schedule:
        return FixedDelay(self.every)

    def execute(self, ctx: ExecutionContext) -> None:
        self.loop.run(self.report())

    async def report(self) -> None:
        wallets = await self.wallets.all()
        total = sum((w.balance for w in wallets), Decimal(0))
        self.last = (len(wallets), total)
        LOG.info("report: %d wallets, total balance %s", len(wallets), total)
