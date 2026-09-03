"""Distributed jobs: once per slot, across the cluster."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from ekbatan.errors import ConnectivityError, ValidationError
from ekbatan.jobs import DistributedJob, Every, JobContext, JobRegistry
from fixtures.actions import CreateWallet, CreateWalletParams
from fixtures.jobs import CountWallets, PayStipend
from fixtures.schema import SCHEDULED_TASKS


def a_registry(registry, clock, jobs, instance: str) -> JobRegistry:
    return JobRegistry(
        registry,
        jobs,
        table=SCHEDULED_TASKS,
        clock=clock,
        instance=instance,
        heartbeat=timedelta(seconds=1),
        missed_heartbeats=5,
    )


@pytest.fixture
def wallet(executor):
    return executor.execute(
        CreateWallet(),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("0.00")),
    )


def test_a_slot_runs_once_however_many_instances_are_up(registry, executor, wallets, clock, wallet):
    stipends = [PayStipend(executor, wallets) for _ in range(5)]
    schedulers = [
        a_registry(registry, clock, [job], f"instance-{i}") for i, job in enumerate(stipends)
    ]
    for scheduler in schedulers:
        scheduler.start()

    ran = sum(scheduler.run_due_once() for scheduler in schedulers)

    assert ran == 1, "five instances, one slot"
    assert sum(job.runs for job in stipends) == 1
    assert wallets.get(wallet.id).balance == Decimal("1.0000")


def test_many_slots_each_run_once(registry, executor, wallets, clock, wallet):
    stipends = [PayStipend(executor, wallets) for _ in range(3)]
    schedulers = [a_registry(registry, clock, [j], f"instance-{i}") for i, j in enumerate(stipends)]
    schedulers[0].start()

    for _ in range(4):
        for scheduler in schedulers:
            scheduler.run_due_once()
        clock.advance(timedelta(days=1))

    assert sum(job.runs for job in stipends) == 4, "a recurring job must actually recur"
    assert wallets.get(wallet.id).balance == Decimal("4.0000")


def test_a_slot_that_is_not_due_yet_does_not_run(registry, executor, wallets, clock, wallet):
    job = PayStipend(executor, wallets)
    scheduler = a_registry(registry, clock, [job], "only")
    scheduler.start()

    assert scheduler.run_due_once() == 1
    assert scheduler.run_due_once() == 0, "the next slot is a day away"

    clock.advance(timedelta(days=1))
    assert scheduler.run_due_once() == 1


def test_work_done_by_a_job_is_audited_like_any_other(
    registry, executor, wallets, clock, wallet, outbox
):
    scheduler = a_registry(registry, clock, [PayStipend(executor, wallets)], "only")
    scheduler.start()
    scheduler.run_due_once()

    deposits = [r for r in outbox() if r["event_type"] == "WalletMoneyDeposited"]
    assert len(deposits) == 1
    assert deposits[0]["action_name"] == "DepositMoney", (
        "a job is a caller, not a special case; a consumer cannot tell and need not"
    )
    assert deposits[0]["namespace"] == "example.wallet"


def test_a_disabled_job_is_registered_but_never_runs(registry, executor, wallets, clock, wallet):
    job = PayStipend(executor, wallets)
    type(job).enabled = property(lambda self: False)
    try:
        scheduler = a_registry(registry, clock, [job], "only")
        scheduler.start()
        scheduler.run_due_once()
        assert job.runs == 0
        assert wallets.get(wallet.id).balance == Decimal("0.0000")
    finally:
        del type(job).enabled


def test_a_stalled_instance_is_taken_over(registry, executor, wallets, clock, wallet):
    stalled = a_registry(registry, clock, [PayStipend(executor, wallets)], "stalled")
    stalled.start()
    assert stalled._claim("wallet-stipend") is True

    healthy_job = PayStipend(executor, wallets)
    healthy = a_registry(registry, clock, [healthy_job], "healthy")
    assert healthy._claim("wallet-stipend") is False, "someone is on it and still breathing"

    clock.advance(timedelta(seconds=30))  # past heartbeat x missed_heartbeats
    assert healthy._claim("wallet-stipend") is True, "silence means the execution is dead"


def test_a_failing_job_is_counted_and_rescheduled(registry, clock):
    class AlwaysFails(DistributedJob):
        name = "always-fails"
        schedule = Every(timedelta(minutes=5))
        runs = 0

        def run(self, context: JobContext) -> None:
            AlwaysFails.runs += 1
            self.seen = context.consecutive_failures
            raise RuntimeError("nope")

    job = AlwaysFails()
    scheduler = a_registry(registry, clock, [job], "only")
    scheduler.start()

    scheduler.run_due_once()
    clock.advance(timedelta(minutes=6))
    scheduler.run_due_once()

    assert AlwaysFails.runs == 2, "a failure reschedules rather than stopping the job"
    assert job.seen == 1, "the job is told how many times it has failed in a row"

    with registry.default.connection(readonly=True) as conn:
        row = conn.execute(select(SCHEDULED_TASKS)).one()
    assert row.consecutive_failures == 2
    assert row.last_failure is not None
    assert row.last_success is None
    assert row.picked is False, "a failed run releases the slot"


def test_a_job_without_a_stable_name_is_refused(registry, clock):
    class Nameless(DistributedJob):
        name = "  "
        schedule = Every(timedelta(minutes=1))

        def run(self, context: JobContext) -> None: ...

    with pytest.raises(ValidationError, match="blank name"):
        a_registry(registry, clock, [Nameless()], "only")


def test_two_jobs_with_one_name_are_refused(registry, executor, wallets, clock):
    with pytest.raises(ValidationError, match="both called"):
        a_registry(
            registry, clock, [PayStipend(executor, wallets), PayStipend(executor, wallets)], "only"
        )


def test_a_scheduler_that_cannot_register_fails_loudly(registry, executor, wallets, clock):
    from sqlalchemy import text

    with registry.default.transaction() as conn:
        conn.execute(text("DROP TABLE scheduled_tasks"))

    scheduler = a_registry(registry, clock, [PayStipend(executor, wallets)], "only")
    with pytest.raises(ConnectivityError, match="could not register"):
        scheduler.start()


def test_a_read_only_job_writes_nothing(registry, executor, wallets, clock, wallet, outbox):
    counter = CountWallets(wallets)
    scheduler = a_registry(registry, clock, [counter], "only")
    scheduler.start()
    before = len(outbox())

    scheduler.run_due_once()

    assert counter.last_count == 1
    assert len(outbox()) == before, "a job that stages nothing writes nothing"


def test_a_missed_run_skips_its_slots_rather_than_catching_up(
    registry, executor, wallets, clock, wallet
):
    job = PayStipend(executor, wallets)
    scheduler = a_registry(registry, clock, [job], "only")
    scheduler.start()
    scheduler.run_due_once()

    clock.advance(timedelta(days=4))  # the service was down for four days
    assert scheduler.run_due_once() == 1
    assert scheduler.run_due_once() == 0, "four missed slots must not become four runs at breakfast"
    assert job.runs == 2
