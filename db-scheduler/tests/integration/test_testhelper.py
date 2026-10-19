"""`ManualScheduler` - the scheduler with the threads and the clock taken out.

These are also the clearest demonstration of what the helper is *for*: an hour's retry backoff
asserted in microseconds, and a dead execution produced without killing anything.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db_scheduler.clock import SettableClock
from db_scheduler.scheduled_execution import ScheduledExecutionsFilter
from db_scheduler.task import TaskInstance
from db_scheduler.task.handlers import CancelDeadExecution
from db_scheduler.task.helper import Tasks
from db_scheduler.task.schedule import Daily, DisabledSchedule, FixedDelay
from db_scheduler.testhelper import ManualSchedulerBuilder, create_manual_scheduler

NOW = datetime(2026, 1, 1, 12, 0)


@pytest.fixture
def clock() -> SettableClock:
    return SettableClock(NOW)


@pytest.fixture
def manual(engine: Any, table: Any, clock: SettableClock) -> ManualSchedulerBuilder:
    return create_manual_scheduler(engine).table_name(table.name).clock(clock)


class TestRunningOnDemand:
    def test_nothing_runs_until_it_is_asked_to(
        self, manual: ManualSchedulerBuilder
    ) -> None:
        """Which is the point: a test that fails does so at its assertion, not in a worker."""
        ran: list[str] = []
        task = Tasks.one_time("a-task").execute(lambda instance, context: ran.append(instance.id))
        scheduler = manual.add_tasks(task).start()

        scheduler.schedule_if_not_exists(task.instance("an-id"), NOW)
        assert ran == []

        scheduler.run_any_due_executions()
        assert ran == ["an-id"]

    def test_work_in_the_future_is_not_due_yet(
        self, manual: ManualSchedulerBuilder
    ) -> None:
        ran: list[str] = []
        task = Tasks.one_time("a-task").execute(lambda instance, context: ran.append(instance.id))
        scheduler = manual.add_tasks(task).start()
        scheduler.schedule_if_not_exists(task.instance("an-id"), NOW + timedelta(hours=1))

        scheduler.run_any_due_executions()
        assert ran == []

        scheduler.tick(timedelta(hours=1))
        scheduler.run_any_due_executions()
        assert ran == ["an-id"]

    def test_the_clock_can_be_set_outright(self, manual: ManualSchedulerBuilder) -> None:
        scheduler = manual.start()

        scheduler.set_time(datetime(2030, 6, 1, tzinfo=UTC))

        assert scheduler.clock.now() == datetime(2030, 6, 1, tzinfo=UTC)

    def test_it_runs_on_the_calling_thread(self, manual: ManualSchedulerBuilder) -> None:
        """So the assertion on the next line sees the result, with nothing to wait for."""
        import threading

        seen: list[int] = []
        task = Tasks.one_time("a-task").execute(
            lambda instance, context: seen.append(threading.get_ident())
        )
        scheduler = manual.add_tasks(task).start()
        scheduler.schedule_if_not_exists(task.instance("an-id"), NOW)

        scheduler.run_any_due_executions()

        assert seen == [threading.get_ident()]


class TestRetryBackoff:
    def test_an_hour_of_backoff_asserted_without_waiting_an_hour(
        self, manual: ManualSchedulerBuilder
    ) -> None:
        attempts: list[datetime] = []

        def explode(instance: Any, context: Any) -> None:
            attempts.append(scheduler.clock.now())
            raise RuntimeError("boom")

        task = (
            Tasks.one_time("a-task")
            .on_failure_retry_later(timedelta(hours=1))
            .execute(explode)
        )
        scheduler = manual.add_tasks(task).start()
        scheduler.schedule_if_not_exists(task.instance("an-id"), NOW)

        scheduler.run_any_due_executions()
        assert attempts == [NOW]

        scheduler.run_any_due_executions()
        assert attempts == [NOW], "still backing off"

        scheduler.tick(timedelta(hours=1))
        scheduler.run_any_due_executions()
        assert attempts == [NOW, NOW + timedelta(hours=1)]


class TestRecurring:
    def test_a_recurring_task_schedules_itself_at_start(
        self, manual: ManualSchedulerBuilder
    ) -> None:
        task = Tasks.recurring("a-task", FixedDelay.of(timedelta(hours=1))).execute(
            lambda instance, context: None
        )
        scheduler = manual.add_start_tasks(task).start()

        [found] = scheduler.get_scheduled_executions()
        assert found.task_instance.id == "recurring"

    def test_and_reschedules_itself_after_each_run(
        self, manual: ManualSchedulerBuilder
    ) -> None:
        runs: list[datetime] = []
        task = Tasks.recurring("a-task", FixedDelay.of(timedelta(hours=1))).execute(
            lambda instance, context: runs.append(scheduler.clock.now())
        )
        scheduler = manual.add_start_tasks(task).start()

        scheduler.run_any_due_executions()
        scheduler.tick(timedelta(hours=1))
        scheduler.run_any_due_executions()
        scheduler.tick(timedelta(hours=1))
        scheduler.run_any_due_executions()

        assert runs == [NOW, NOW + timedelta(hours=1), NOW + timedelta(hours=2)]


class TestDeadExecutions:
    def test_a_dead_execution_is_produced_without_killing_anything(
        self, manual: ManualSchedulerBuilder, clock: SettableClock
    ) -> None:
        ran: list[str] = []
        task = Tasks.one_time("a-task").execute(lambda instance, context: ran.append(instance.id))
        scheduler = manual.add_tasks(task).start()
        repository = scheduler.scheduler_task_repository

        scheduler.schedule_if_not_exists(task.instance("an-id"), NOW)
        [execution] = repository.get_due(NOW, limit=10)
        repository.pick(execution, NOW)

        # Far enough past the heartbeat window that no living instance could still hold it.
        scheduler.tick(timedelta(hours=1))
        scheduler.run_dead_execution_detection()
        scheduler.run_any_due_executions()

        assert ran == ["an-id"], "revived and run by this instance"

    def test_a_task_that_refuses_revival_has_its_row_dropped(
        self, manual: ManualSchedulerBuilder
    ) -> None:
        task = (
            Tasks.one_time("a-task")
            .on_dead_execution(CancelDeadExecution())
            .execute(lambda instance, context: None)
        )
        scheduler = manual.add_tasks(task).start()
        repository = scheduler.scheduler_task_repository

        scheduler.schedule_if_not_exists(task.instance("an-id"), NOW)
        [execution] = repository.get_due(NOW, limit=10)
        repository.pick(execution, NOW)

        scheduler.tick(timedelta(hours=1))
        scheduler.run_dead_execution_detection()

        assert scheduler.get_scheduled_executions(ScheduledExecutionsFilter.all()) == []


class TestBuilder:
    def test_it_insists_on_a_settable_clock(self, engine: Any, table: Any) -> None:
        """A real clock would make every one of these tests a race."""
        from db_scheduler.clock import SystemClock

        with pytest.raises(TypeError, match="SettableClock"):
            create_manual_scheduler(engine).clock(SystemClock())

    def test_tasks_can_be_given_at_creation(self, engine: Any, table: Any) -> None:
        task = Tasks.one_time("a-task").execute(lambda instance, context: None)

        builder = create_manual_scheduler(engine, task).table_name(table.name)

        assert builder.known_tasks == [task]

    def test_starting_returns_a_started_scheduler(
        self, manual: ManualSchedulerBuilder
    ) -> None:
        scheduler = manual.start()

        assert scheduler.scheduler_state.is_started is True

    def test_the_scheduler_is_named_manual(self, manual: ManualSchedulerBuilder) -> None:
        """So a row picked during a test is identifiable as one."""
        scheduler = manual.start()

        assert scheduler.scheduler_task_repository.scheduler_name == "manual"

    def test_it_is_a_client_too(self, manual: ManualSchedulerBuilder) -> None:
        scheduler = manual.start()

        scheduler.schedule_if_not_exists(TaskInstance[Any]("a-task", "an-id"), NOW)

        assert len(scheduler.get_scheduled_executions(ScheduledExecutionsFilter.all())) == 1


class TestARedeployThatChangesTheSchedule:
    """A recurring task's schedule lives in the code but its next execution lives in the table,
    and a deploy can change one without the other. Reconciling them is what
    `ScheduleRecurringOnStartup` does, and until now it had only ever been tested against fakes.

    Each "deploy" below is a fresh scheduler over the same table, which is what a restart is.
    """

    def _deploy(self, engine: Any, table: Any, schedule: Any) -> datetime | None:
        task = Tasks.recurring("nightly", schedule).execute(lambda instance, context: None)
        scheduler = (
            create_manual_scheduler(engine)
            .table_name(table.name)
            .clock(SettableClock(NOW))
            .add_start_tasks(task)
            .start()
        )
        rows = scheduler.get_scheduled_executions(ScheduledExecutionsFilter.all())
        return rows[0].execution_time if rows else None

    def test_an_unchanged_schedule_leaves_the_row_alone(self, engine: Any, table: Any) -> None:
        first = self._deploy(engine, table, Daily(time(8, 0), zone=UTC))

        assert self._deploy(engine, table, Daily(time(8, 0), zone=UTC)) == first

    def test_a_changed_deterministic_schedule_moves_the_row(
        self, engine: Any, table: Any
    ) -> None:
        """Cron and daily are deterministic: a different answer means somebody edited the code,
        so the row follows."""
        self._deploy(engine, table, Daily(time(8, 0), zone=UTC))

        moved = self._deploy(engine, table, Daily(time(9, 0), zone=UTC))

        assert moved == datetime(2026, 1, 2, 9, 0, tzinfo=NOW.tzinfo), "in the kind the table holds"

    def test_a_non_deterministic_schedule_never_pushes_the_row_later(
        self, engine: Any, table: Any
    ) -> None:
        """The rule that stops a frequently-restarting service from never running anything: a
        fixed delay recomputed at every boot would walk the execution into the future forever.
        """
        before = self._deploy(engine, table, Daily(time(9, 0), zone=UTC))

        after = self._deploy(engine, table, FixedDelay.of(timedelta(days=7)))

        assert after == before, "a fixed delay pushed the execution further away"

    def test_but_it_does_move_the_row_earlier(self, engine: Any, table: Any) -> None:
        """Shortening the delay should take effect, or the change would be ignored."""
        before = self._deploy(engine, table, Daily(time(9, 0), zone=UTC))

        after = self._deploy(engine, table, FixedDelay.of(timedelta(seconds=30)))

        assert after is not None and before is not None and after < before
        assert after == NOW + timedelta(seconds=30)

    def test_disabling_the_schedule_removes_the_row(self, engine: Any, table: Any) -> None:
        """So a task can be switched off in configuration without deleting it by hand."""
        self._deploy(engine, table, Daily(time(8, 0), zone=UTC))

        assert self._deploy(engine, table, DisabledSchedule()) is None

    def test_and_a_disabled_schedule_creates_nothing(self, engine: Any, table: Any) -> None:
        assert self._deploy(engine, table, DisabledSchedule()) is None
