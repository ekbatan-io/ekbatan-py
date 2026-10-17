"""Two jobs, and the two shapes a job comes in.

`PayStipendJob` writes, through the same actions an HTTP request would use. `WalletReportJob`
only reads. Both run **at most once per slot across the whole cluster** - three replicas of this
worker share one row in `scheduled_tasks`, and the row is what makes that true. Nothing in
either class arranges it.

The rule every job body has to obey is at the bottom of `execute`: **a slot can run twice.** If
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

from ekbatan import ActionExecutor
from ekbatan.distributedjobs import DistributedJob

from .actions import DepositMoneyAction, DepositParams
from .model import WalletState
from .repository import WalletRepository

LOG = logging.getLogger("examples.job_worker_pg")


class PayStipendJob(DistributedJob):
    """Tops up every wallet running low, by invoking an ordinary action.

    Nothing about being a job makes the work special. It goes through `DepositMoneyAction` like
    any other caller, so the outbox row it writes is indistinguishable from one a request
    produced - `action_name` says `DepositMoneyAction` either way, and a consumer downstream
    cannot tell and does not need to.
    """

    def __init__(
        self,
        executor: ActionExecutor,
        wallets: WalletRepository,
        *,
        threshold: Decimal = Decimal("100.00"),
        amount: Decimal = Decimal("10.00"),
        every: timedelta = timedelta(seconds=2),
    ) -> None:
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
        # Idempotent by construction rather than by promise: the query selects wallets *below*
        # the threshold, so a repeat of this slot finds the ones it already topped up are no
        # longer below it. A job that deposited unconditionally would double-pay on a replay.
        low = self.wallets.find_open_below(self.threshold)
        for wallet in low:
            if wallet.state is not WalletState.OPENED:
                continue
            self.executor.execute(
                DepositMoneyAction,
                DepositParams(
                    wallet_id=wallet.id,
                    amount=self.amount,
                    recipient="stipend@example.com",
                ),
            )
        LOG.info("stipend paid into %d wallet(s)", len(low))


class WalletReportJob(DistributedJob):
    """A scheduled query: counts and sums, writes nothing.

    The other shape a job comes in, and the more common one - an ops dashboard, a metrics
    exporter, a freshness probe. It invokes no action and so produces no outbox row, which is
    correct: nothing happened.
    """

    def __init__(self, wallets: WalletRepository, *, every: timedelta = timedelta(seconds=5)):
        self.wallets = wallets
        self.every = every
        #: What the last run saw. Here so a test can assert the job ran without reading a log.
        self.last: tuple[int, Decimal] | None = None

    def name(self) -> str:
        return "wallet-report"

    def schedule(self) -> Schedule:
        return FixedDelay(self.every)

    def execute(self, ctx: ExecutionContext) -> None:
        wallets = self.wallets.all()
        total = sum((w.balance for w in wallets), Decimal(0))
        self.last = (len(wallets), total)
        LOG.info("report: %d wallets, total balance %s", len(wallets), total)
