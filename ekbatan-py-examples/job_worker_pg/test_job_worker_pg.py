"""The worker, its two jobs, and the claim that makes a cluster safe.

    uv run pytest ekbatan-py-examples/job_worker_pg

Java's `WalletJobsIntegrationTest` covers the same ground. This runs on SQLite with the
scheduler's clock in the test's hands, so the slots happen where the assertions are rather than
whenever a timer fired.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from db_scheduler.clock import SettableClock
from sqlalchemy import func, select, text

from ekbatan import (
    ActionExecutor,
    ActionRegistry,
    DatabaseRegistry,
    RepositoryRegistry,
    ShardingConfig,
)
from ekbatan.clock import SystemClock
from ekbatan.distributedjobs import job_registry
from ekbatan.events import (
    EventFanoutJob,
    EventHandlerRegistry,
    EventHandlingJob,
    HandlingConfig,
    LocalEventHandlerConfig,
)
from ekbatan.migrations import migrate_registry

from .actions import (
    CloseWalletAction,
    DepositMoneyAction,
    OpenWalletAction,
    OpenWalletParams,
    RaiseAlertAction,
)
from .handlers import MoneyDepositedEventHandler
from .jobs import PayStipendJob, WalletReportJob
from .model import Wallet
from .repository import AlertRepository, WalletRepository
from .schema import EVENT_NOTIFICATIONS, EVENTS, SCHEDULED_TASKS, metadata

EPOCH = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class Harness:
    """The worker, with the scheduler's threads and clock taken out.

    `build()` in `app.py` returns the real thing - a scheduler with a thread pool, polling on a
    timer - which is what you want in production and not what you want in a test. This is the
    same wiring through `build_manual`, so a test says when a slot runs.
    """

    def __init__(self, url: str) -> None:
        running = SystemClock()
        self.registry = DatabaseRegistry.from_config(ShardingConfig.single(url))
        migrate_registry(self.registry, metadata)

        self.wallets = WalletRepository(self.registry, clock=running)
        self.alerts = AlertRepository(self.registry, clock=running)
        self.executor = ActionExecutor(
            namespace="example.job_worker_pg",
            registry=self.registry,
            repositories=(
                RepositoryRegistry.builder()
                .with_model_repository(self.wallets)
                .with_entity_repository(self.alerts)
                .build()
            ),
            actions=(
                ActionRegistry.builder()
                .with_action(OpenWalletAction(running))
                .with_action(DepositMoneyAction(self.wallets, running))
                .with_action(CloseWalletAction(self.wallets, running))
                .with_action(RaiseAlertAction(running))
                .build()
            ),
            events=EVENTS,
            clock=running,
        )
        handlers = EventHandlerRegistry([MoneyDepositedEventHandler(self.executor)])
        config = LocalEventHandlerConfig(handling=HandlingConfig(enabled=True))

        self.stipend = PayStipendJob(self.executor, self.wallets)
        self.report = WalletReportJob(self.wallets)
        self.clock = SettableClock(EPOCH)
        self.fanout_job = EventFanoutJob(
            self.registry,
            handlers,
            events=EVENTS,
            notifications=EVENT_NOTIFICATIONS,
            config=config,
            clock=running,
        )
        self.handling_job = EventHandlingJob(
            self.registry,
            handlers,
            notifications=EVENT_NOTIFICATIONS,
            config=config,
            clock=running,
        )

        # The two event jobs are built but **not scheduled**, which is how Java's own
        # integration tests use them: `fanoutJob.drainOneRound()`, never a scheduler. Each is
        # one execution that runs for the life of the process, so scheduling one here would
        # never return - the manual scheduler runs everything on the calling thread. In the
        # worker itself they are registered like any other job and loop on their own threads.
        self.jobs = (
            job_registry()
            .engine(self.registry.default.primary)
            .with_jobs([self.stipend, self.report])
            .build_manual(self.clock)
        )

    def tick(self, seconds: int = 3) -> None:
        """Move the scheduler's clock on, run whatever that made due, then drain the events.

        The event pipeline is drained directly rather than scheduled, for the reason given
        where the two registries are built: each event job is one execution that never returns.
        In the running worker they loop on their own threads and none of this applies.
        """
        self.clock.set(self.clock.now() + timedelta(seconds=seconds))
        self.jobs.scheduler.run_any_due_executions()
        self.fanout_job.fanout.drain_once()
        self.handling_job.dispatch.drain_once()

    def close(self) -> None:
        self.registry.close()


@pytest.fixture(scope="module")
def database() -> Iterator[str]:
    from testcontainers.postgres import PostgresContainer

    # PostgreSQL, because the directory says so. Java's counterpart is `-pg` too, and the
    # reason is the same: running once per slot across a cluster is the thing this shows, and
    # a cluster does not share a SQLite file.
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        yield container.get_connection_url()


@pytest.fixture
def worker(database: str) -> Iterator[Harness]:
    built = Harness(database)
    # One container for the module, so every test starts from an empty database rather than
    # from whatever the last one left. `scheduled_tasks` goes too: each test builds its own
    # registry and `start()` writes the rows again, and a row left behind from the previous
    # test carries its `execution_time` with it.
    with built.registry.default.connection() as conn:
        for table in ("event_notifications", "events", "alerts", "wallets", "scheduled_tasks"):
            conn.execute(text(f"TRUNCATE TABLE {table}"))
    try:
        built.jobs.start()
        yield built
    finally:
        built.close()


def a_wallet(worker: Harness, balance: str = "0.00") -> Wallet:
    return worker.executor.execute(
        OpenWalletAction,
        OpenWalletParams(owner_id=uuid4(), currency="EUR", opening_balance=Decimal(balance)),
    )


def test_every_job_has_exactly_one_row_however_many_workers_run(worker: Harness) -> None:
    """The whole basis of running once per slot across a cluster: one row per job, and a second
    worker registering the same jobs adds none.

    Two here, not the worker's four: this harness schedules the stipend and the report, and
    drains the two event jobs directly, as Java's integration tests do. `app.build()` registers
    all four, and `test_the_worker_registers_all_four_jobs` is where that is asserted."""
    with worker.registry.default.connection(readonly=True) as conn:
        rows = conn.execute(select(func.count()).select_from(SCHEDULED_TASKS)).scalar_one()

    assert rows == 2, "stipend and report"


def test_the_worker_registers_all_four_jobs(worker: Harness) -> None:
    """What the running worker does: the event jobs are scheduled like any other, and claimed
    from the same table as the stipend. Registered and not ticked - each is one execution that
    runs for the life of the process, so there is nothing here that would return."""
    all_four = (
        job_registry()
        .engine(worker.registry.default.primary)
        .with_jobs([worker.fanout_job, worker.handling_job, worker.stipend, worker.report])
        .build_manual(SettableClock(EPOCH))
    )
    all_four.start()

    with worker.registry.default.connection(readonly=True) as conn:
        names = {r[0] for r in conn.execute(select(SCHEDULED_TASKS.c.task_name))}

    assert len(names) == 4, f"four jobs, four rows - got {sorted(names)}"


def test_the_stipend_job_tops_up_a_low_wallet(worker: Harness) -> None:
    wallet = a_wallet(worker, "0.00")

    worker.tick()

    assert worker.wallets.get(wallet.id).balance == Decimal("10.0000")


def test_and_stops_once_the_wallet_is_above_the_threshold(worker: Harness) -> None:
    """Idempotent by construction: the query selects wallets *below* the threshold, so a repeat
    of the slot finds nothing to do. A job that deposited unconditionally would double-pay on
    the replay that at-least-once delivery makes inevitable."""
    wallet = a_wallet(worker, "95.00")

    worker.tick()
    first = worker.wallets.get(wallet.id).balance
    for _ in range(3):
        worker.tick()

    assert first == Decimal("105.0000")
    assert worker.wallets.get(wallet.id).balance == first, "already above the threshold"


def test_a_job_is_an_ordinary_caller_and_the_audit_cannot_tell(worker: Harness) -> None:
    """The point of routing a job's work through an action: the outbox row it produces is
    indistinguishable from one a request produced."""
    a_wallet(worker, "0.00")

    worker.tick()

    with worker.registry.default.connection(readonly=True) as conn:
        deposits = conn.execute(
            select(EVENTS.c.action_name, EVENTS.c.namespace).where(
                EVENTS.c.event_type == "MoneyDepositedEvent"
            )
        ).all()

    assert len(deposits) == 1
    assert deposits[0].action_name == "DepositMoneyAction"
    assert deposits[0].namespace == "example.job_worker_pg"


def test_the_job_to_handler_chain_runs_inside_one_worker(worker: Harness) -> None:
    """Job -> action -> outbox -> fan-out -> handler -> another action, with no HTTP anywhere
    and no second process. The fan-out and handling jobs are claimed from the same table as the
    stipend, so this is four scheduled slots cooperating."""
    wallet = a_wallet(worker, "0.00")

    for _ in range(4):
        worker.tick()

    alerts = worker.alerts.find_for_wallet(wallet.id)
    assert alerts, "the deposit event reached the handler, which wrote an alert"
    assert alerts[0].recipient == "stipend@example.com", "read off the job's own parameters"


def test_the_read_only_job_writes_nothing(worker: Harness) -> None:
    """The other shape a job comes in. It invokes no action, so it produces no outbox row -
    which is correct, because nothing happened."""
    a_wallet(worker, "500.00")

    with worker.registry.default.connection(readonly=True) as conn:
        before = conn.execute(select(func.count()).select_from(EVENTS)).scalar_one()

    worker.tick(10)

    assert worker.report.last is not None, "the report ran"
    count, total = worker.report.last
    assert count == 1
    assert total == Decimal("500.0000")

    with worker.registry.default.connection(readonly=True) as conn:
        after = conn.execute(select(func.count()).select_from(EVENTS)).scalar_one()
    assert after == before, "a read-only job writes no events"
