"""Distributed jobs: once per slot, across the cluster.

The coordination itself - claiming a row, heartbeating it, reclaiming a dead one, counting
failures - belongs to db-scheduler and is tested there, against four databases and with real
concurrency. What is Ekbatan's, and what these cover, is the layer on top: turning a
``DistributedJob`` into a recurring task, refusing a registry that cannot work, and confirming
after ``start()`` that the jobs are actually registered.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from db_scheduler.clock import SettableClock
from db_scheduler.task import ExecutionContext
from db_scheduler.task.schedule import DisabledSchedule, FixedDelay, Schedule
from sqlalchemy import func, select

from ekbatan.distributedjobs import DistributedJob, JobRegistry, job_registry
from ekbatan.errors import ValidationError
from fixtures.actions import CreateWalletAction, CreateWalletParams
from fixtures.jobs import CountWalletsJob, PayStipendJob
from fixtures.schema import SCHEDULED_TASKS

#: Where the scheduler's clock starts. Any fixed instant will do; a fixed one keeps the tests
#: from depending on when they run.
SCHEDULER_EPOCH = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def wallet(executor):
    return executor.execute(
        CreateWalletAction(),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("0.00")),
    )


def a_registry(registry, clock, jobs) -> JobRegistry:
    """A registry with the threads and the clock taken out, so a test says when things run.

    The scheduler's clock is its own - db-scheduler's `SettableClock`, which decides when a slot
    is due - and is separate from Ekbatan's, which stamps rows. Two concerns, two clocks; the
    tests below advance the scheduler's with `tick`.
    """
    return (
        job_registry()
        .engine(registry.default.primary)
        .with_jobs(jobs)
        .poll_interval(timedelta(seconds=1))
        .build_manual(SettableClock(SCHEDULER_EPOCH))
    )


class TestRunningJobs:
    def test_a_job_runs_and_reschedules(self, registry, executor, wallets, clock, wallet):
        job = PayStipendJob(executor, wallets)
        jobs = a_registry(registry, clock, [job])

        jobs.start()
        jobs.scheduler.run_any_due_executions()

        assert job.runs == 1
        assert wallets.get(wallet.id).balance == Decimal("1.0000")

    def test_a_slot_that_is_not_due_yet_does_not_run(
        self, registry, executor, wallets, clock, wallet
    ):
        job = PayStipendJob(executor, wallets)
        jobs = a_registry(registry, clock, [job])
        jobs.start()

        jobs.scheduler.run_any_due_executions()
        jobs.scheduler.run_any_due_executions()
        assert job.runs == 1, "the next slot is a day away"

        jobs.scheduler.tick(timedelta(days=1))
        jobs.scheduler.run_any_due_executions()
        assert job.runs == 2, "a recurring job must actually recur"

    def test_one_row_per_job_however_many_instances_register_it(
        self, registry, executor, wallets, clock, wallet
    ):
        """A rolling deploy starts every instance within the same second, and each registers the
        same jobs. One job, one row - the row is what stops five servers running it."""
        for _ in range(4):
            a_registry(registry, clock, [PayStipendJob(executor, wallets)]).start()

        with registry.default.connection(readonly=True) as conn:
            rows = conn.execute(select(func.count()).select_from(SCHEDULED_TASKS)).scalar_one()

        assert rows == 1

    def test_work_done_by_a_job_is_audited_like_any_other(
        self, registry, executor, wallets, clock, wallet, outbox
    ):
        jobs = a_registry(registry, clock, [PayStipendJob(executor, wallets)])
        jobs.start()
        jobs.scheduler.run_any_due_executions()

        deposits = [r for r in outbox() if r["event_type"] == "WalletMoneyDepositedEvent"]
        assert len(deposits) == 1
        assert deposits[0]["action_name"] == "DepositMoneyAction", (
            "a job is a caller, not a special case; a consumer cannot tell and need not"
        )
        assert deposits[0]["namespace"] == "example.wallet"

    def test_a_failing_job_is_rescheduled_rather_than_dropped(self, registry, clock):
        class AlwaysFails(DistributedJob):
            runs = 0

            def name(self) -> str:
                return "always-fails"

            def schedule(self) -> Schedule:
                return FixedDelay.of(timedelta(minutes=5))

            def execute(self, ctx: ExecutionContext) -> None:
                AlwaysFails.runs += 1
                raise RuntimeError("nope")

        jobs = a_registry(registry, clock, [AlwaysFails()])
        jobs.start()

        jobs.scheduler.run_any_due_executions()
        jobs.scheduler.tick(timedelta(minutes=6))
        jobs.scheduler.run_any_due_executions()

        assert AlwaysFails.runs == 2, "a failure reschedules rather than stopping the job"
        with registry.default.connection(readonly=True) as conn:
            row = conn.execute(select(SCHEDULED_TASKS)).one()
        assert row.consecutive_failures == 2
        assert row.last_failure is not None
        assert row.last_success is None
        assert row.picked is False, "a failed run releases the slot"

    def test_the_job_is_told_its_own_heartbeat_standing(self, registry, clock):
        """What lets a job whose work cannot be made idempotent stop before a takeover rather
        than racing it."""
        seen: list[float] = []

        class Reads(DistributedJob):
            def name(self) -> str:
                return "reads-its-health"

            def schedule(self) -> Schedule:
                return FixedDelay.of(timedelta(minutes=5))

            def execute(self, ctx: ExecutionContext) -> None:
                seen.append(ctx.currently_executing.heartbeat_state.fraction_dead)

        jobs = a_registry(registry, clock, [Reads()])
        jobs.start()
        jobs.scheduler.run_any_due_executions()

        assert seen == [0.0], "just heartbeated, so nowhere near dead"


class TestDisabledJobs:
    def test_a_disabled_schedule_registers_no_row_and_never_runs(
        self, registry, executor, wallets, clock, wallet
    ):
        """How a job is switched off for an environment where the work is someone else's -
        without deleting the code. There is no separate `enabled` flag, in either language: the
        schedule already says it."""

        class Dormant(DistributedJob):
            runs = 0

            def name(self) -> str:
                return "dormant"

            def schedule(self) -> Schedule:
                return DisabledSchedule()

            def execute(self, ctx: ExecutionContext) -> None:
                Dormant.runs += 1

        jobs = a_registry(registry, clock, [Dormant()])
        jobs.start()
        jobs.scheduler.run_any_due_executions()

        assert Dormant.runs == 0
        with registry.default.connection(readonly=True) as conn:
            rows = conn.execute(select(func.count()).select_from(SCHEDULED_TASKS)).scalar_one()
        assert rows == 0, "db-scheduler deliberately writes no execution for a disabled schedule"

    def test_and_start_does_not_mistake_that_for_a_failure(
        self, registry, executor, wallets, clock
    ):
        """`start()` insists every job got a row - except the disabled ones, whose absence is
        correct rather than a symptom."""

        class Dormant(DistributedJob):
            def name(self) -> str:
                return "dormant"

            def schedule(self) -> Schedule:
                return DisabledSchedule()

            def execute(self, ctx: ExecutionContext) -> None: ...

        jobs = a_registry(registry, clock, [Dormant(), CountWalletsJob(wallets)])

        jobs.start()  # must not raise


class TestVerifyingRegistration:
    """Registering a job is a write, and db-scheduler catches, logs and continues when one
    fails - so `start()` returns normally whether every row was written or none were. A missing
    table, or a database it cannot write to, would otherwise mean the application boots, health
    checks pass, the scheduler polls an empty table, and no job ever runs. Not late - never."""

    def test_it_refuses_to_report_success_when_a_job_has_no_row(self):
        """The scheduler started, but the row is not there. Driven through a stand-in because
        arranging a write that fails while the read succeeds is not something a test can stage
        honestly against a real database."""

        class SchedulerThatRegisteredNothing:
            def start(self) -> None: ...

            def get_scheduled_executions_for_task(self, name, data_class=None, filter=None):
                return []

        jobs = JobRegistry(
            SchedulerThatRegisteredNothing(),  # type: ignore[arg-type]
            ["wallet-stipend"],
            ["wallet-stipend"],
        )

        with pytest.raises(ValidationError, match="were not registered"):
            jobs.start()

        with pytest.raises(ValidationError, match="wallet-stipend"):
            jobs.start()

    def test_and_says_so_when_it_cannot_even_check(self, registry, executor, wallets, clock):
        """A dropped table takes the verification query down with it. Reported as "could not
        verify" rather than as a missing job, because those are different problems."""
        jobs = a_registry(registry, clock, [PayStipendJob(executor, wallets)])
        jobs.start()

        SCHEDULED_TASKS.drop(registry.default.primary)
        try:
            with pytest.raises(ValidationError, match="could not verify"):
                jobs.start()
        finally:
            SCHEDULED_TASKS.create(registry.default.primary)

    def test_a_healthy_start_says_nothing(self, registry, executor, wallets, clock, wallet):
        a_registry(registry, clock, [PayStipendJob(executor, wallets)]).start()  # must not raise


class TestRefusingARegistryThatCannotWork:
    def test_a_blank_name_is_refused(self, registry, clock):
        class Nameless(DistributedJob):
            def name(self) -> str:
                return "  "

            def schedule(self) -> Schedule:
                return FixedDelay.of(timedelta(minutes=1))

            def execute(self, ctx: ExecutionContext) -> None: ...

        with pytest.raises(ValidationError, match="blank name"):
            a_registry(registry, clock, [Nameless()])

    def test_two_jobs_with_one_name_are_refused(self, registry, executor, wallets, clock):
        """They would be the same row, so one would silently replace the other."""
        with pytest.raises(ValidationError, match="unique"):
            a_registry(
                registry,
                clock,
                [PayStipendJob(executor, wallets), PayStipendJob(executor, wallets)],
            )

    def test_a_null_schedule_is_refused(self, registry, clock):
        """Nothing downstream would catch it: db-scheduler stores the schedule without looking,
        dereferences it during start(), logs one line and continues - and that job is simply
        never written, while start() reports success and names it in the log."""

        class NoSchedule(DistributedJob):
            def name(self) -> str:
                return "no-schedule"

            def schedule(self) -> Schedule:
                return None  # type: ignore[return-value]

            def execute(self, ctx: ExecutionContext) -> None: ...

        with pytest.raises(ValidationError, match="null schedule"):
            a_registry(registry, clock, [NoSchedule()])

    def test_a_registry_with_no_jobs_is_refused(self, registry, clock):
        with pytest.raises(ValidationError, match="at least one"):
            a_registry(registry, clock, [])

    def test_a_registry_with_no_engine_is_refused(self, executor, wallets):
        with pytest.raises(ValidationError, match="engine is required"):
            job_registry().with_job(PayStipendJob(executor, wallets)).build()

    def test_a_none_job_is_refused_where_it_was_passed(self, registry, clock):
        """Rather than at build(), where it surfaced with no indication of which call put it
        in the list."""
        with pytest.raises(ValidationError, match="job cannot be None"):
            job_registry().with_job(None)  # type: ignore[arg-type]
        with pytest.raises(ValidationError, match="cannot contain a None"):
            job_registry().with_jobs([None])  # type: ignore[list-item]

    @pytest.mark.parametrize("bad", [timedelta(), timedelta(seconds=-1)])
    def test_an_interval_that_would_spin_is_refused(self, registry, executor, wallets, clock, bad):
        """A zero or negative interval makes db-scheduler skip its wait entirely and poll in a
        tight loop - around ten thousand queries a second, measured."""
        builder = (
            job_registry()
            .engine(registry.default.primary)
            .with_job(PayStipendJob(executor, wallets))
        )
        with pytest.raises(ValidationError, match="poll_interval must be positive"):
            builder.poll_interval(bad).build()
        with pytest.raises(ValidationError, match="heartbeat_interval must be positive"):
            builder.poll_interval(timedelta(seconds=1)).heartbeat_interval(bad).build()


class CountingJob(DistributedJob):
    """Java's `CountingJob`. Counts its own executions and does nothing else."""

    def __init__(self, name: str, schedule: Schedule) -> None:
        self._name = name
        self._schedule = schedule
        self.count = 0

    def name(self) -> str:
        return self._name

    def schedule(self) -> Schedule:
        return self._schedule

    def execute(self, ctx: ExecutionContext) -> None:
        self.count += 1


class TestOneSlotRunsOnce:
    """Java's `multipleInstances_runJobOncePerSlot`.

    Java starts two real schedulers, sleeps two seconds and bounds the total. Here both
    instances are manual, so the claim is asserted rather than sampled: whichever one polls
    first takes the row, and the second finds nothing due. No sleeping, and no bound to widen
    when CI is slow.
    """

    def test_two_instances_and_one_due_slot_produce_one_execution(self, registry, clock):
        name = f"counting-{uuid4()}"
        first = CountingJob(name, FixedDelay(timedelta(seconds=60)))
        second = CountingJob(name, FixedDelay(timedelta(seconds=60)))

        a = a_registry(registry, clock, [first])
        b = a_registry(registry, clock, [second])
        a.start()
        b.start()

        a.scheduler.run_any_due_executions()
        b.scheduler.run_any_due_executions()

        assert (first.count, second.count) == (1, 0), (
            "the row is claimed atomically, so the slot belongs to exactly one instance"
        )

    def test_and_the_slot_after_it_is_claimable_by_the_other(self, registry, clock):
        """The claim releases. A lock that stayed with the first instance would pass the test
        above and still leave the second one idle."""
        name = f"counting-{uuid4()}"
        first, second = (
            CountingJob(name, FixedDelay(timedelta(seconds=1))),
            CountingJob(name, FixedDelay(timedelta(seconds=1))),
        )
        a = a_registry(registry, clock, [first])
        b = a_registry(registry, clock, [second])
        a.start()
        b.start()

        a.scheduler.run_any_due_executions()
        a.scheduler.clock.set(SCHEDULER_EPOCH + timedelta(seconds=5))
        b.scheduler.clock.set(SCHEDULER_EPOCH + timedelta(seconds=5))
        b.scheduler.run_any_due_executions()

        assert (first.count, second.count) == (1, 1)


class TestCustomizingTheScheduler:
    """Java's `customizeScheduler_runsLastAndCanOverrideEkbatanDefaults`."""

    def test_the_customizer_runs_and_its_override_wins(self, registry):
        """Applied *last* in build(), so anything set there beats the framework's own value.

        Java demonstrates this by timing: it sets a 10s poll, overrides it to 50ms, sleeps two
        seconds and counts executions. Read off the built scheduler instead - the override
        either took or it did not, and a two-second sleep proves the same thing more slowly and
        less reliably. The waiter's duration is what the poll loop actually sleeps on; Java
        reaches for the same value reflectively.
        """
        seen: list[object] = []

        built = (
            job_registry()
            .engine(registry.default.primary)
            .with_job(CountingJob(f"customize-{uuid4()}", FixedDelay(timedelta(seconds=60))))
            .poll_interval(timedelta(seconds=10))
            .heartbeat_interval(timedelta(seconds=1))
            .customize_scheduler(
                lambda builder: (
                    seen.append(builder),
                    builder.polling_interval(timedelta(milliseconds=50)),
                )
            )
            .build()
        )

        assert seen, "the customizer was never invoked"
        assert built.scheduler._execute_due_waiter.duration == timedelta(milliseconds=50), (
            "the framework's 10s should have been overwritten by the customizer's 50ms"
        )

    def test_without_a_customizer_the_builders_own_value_stands(self, registry):
        """The other half: the override is what changed it, not the plumbing."""
        built = (
            job_registry()
            .engine(registry.default.primary)
            .with_job(CountingJob(f"plain-{uuid4()}", FixedDelay(timedelta(seconds=60))))
            .poll_interval(timedelta(seconds=10))
            .build()
        )

        assert built.scheduler._execute_due_waiter.duration == timedelta(seconds=10)
