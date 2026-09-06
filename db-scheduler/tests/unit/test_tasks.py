"""The three kinds of task, and the builder that makes them.

From `OneTimeTaskTest`, `RecurringTaskTest`, `CustomTaskTest`, `AbstractTaskTest` and
`ScheduleRecurringOnStartupTest`. The distinction between the kinds is entirely in what they do
when a run completes, so that is what these assert.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from db_scheduler.clock import SettableClock
from db_scheduler.task import Execution, Priority, TaskInstance
from db_scheduler.task.handlers import (
    CancelDeadExecution,
    OnCompleteRemove,
    OnCompleteReschedule,
    OnFailureRetryLater,
    ReviveDeadExecution,
)
from db_scheduler.task.helper import (
    ExecutionContext,
    RecurringTask,
    Tasks,
)
from db_scheduler.task.schedule import FixedDelay

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
EVERY_10S = FixedDelay.of_seconds(10)


def a_context() -> ExecutionContext:
    return ExecutionContext(
        execution=Execution(NOW, TaskInstance[Any]("t", "i")),
        clock=SettableClock(NOW),
    )


# ------------------------------------------------------------------ one-time


class TestOneTimeTask:
    def test_it_removes_itself_when_it_completes(self) -> None:
        """A one-time task is done when it is done; the row is what would make it run again."""
        ran: list[str] = []
        task = Tasks.one_time("send-welcome-email").execute(lambda inst, ctx: ran.append(inst.id))

        handler = task.execute_task(task.instance("abc"), a_context())

        assert ran == ["abc"]
        assert isinstance(handler, OnCompleteRemove)

    def test_its_default_priority_is_medium(self) -> None:
        assert Tasks.one_time("t").execute(lambda i, c: None).default_priority == Priority.MEDIUM

    def test_it_retries_after_five_minutes_by_default(self) -> None:
        task = Tasks.one_time("t").execute(lambda i, c: None)
        assert isinstance(task.failure_handler, OnFailureRetryLater)
        assert task.failure_handler.sleep_duration == timedelta(minutes=5)

    def test_it_revives_a_dead_execution_by_default(self) -> None:
        task = Tasks.one_time("t").execute(lambda i, c: None)
        assert isinstance(task.dead_execution_handler, ReviveDeadExecution)

    def test_its_schedulable_instance_runs_now(self) -> None:
        task = Tasks.one_time("t").execute(lambda i, c: None)
        assert task.schedulable_instance("abc").next_execution_time(NOW) == NOW

    def test_the_handlers_can_be_replaced(self) -> None:
        task = (
            Tasks.one_time("t")
            .on_failure_retry_later(timedelta(seconds=30))
            .on_dead_execution(CancelDeadExecution[Any]())
            .execute(lambda i, c: None)
        )
        assert isinstance(task.failure_handler, OnFailureRetryLater)
        assert task.failure_handler.sleep_duration == timedelta(seconds=30)
        assert isinstance(task.dead_execution_handler, CancelDeadExecution)


# ------------------------------------------------------------------ recurring


class TestRecurringTask:
    def test_it_reschedules_itself_when_it_completes(self) -> None:
        ran: list[str] = []
        task = Tasks.recurring("nightly-report", EVERY_10S).execute(
            lambda inst, ctx: ran.append(inst.id)
        )

        handler = task.execute_task(task.instance(RecurringTask.INSTANCE), a_context())

        assert ran == [RecurringTask.INSTANCE]
        assert isinstance(handler, OnCompleteReschedule)
        assert handler.schedule == EVERY_10S

    def test_its_single_instance_is_called_recurring(self) -> None:
        """One row per recurring task, cluster-wide. The id is the constant that makes five
        servers recognise it as the same task rather than five of them."""
        assert RecurringTask.INSTANCE == "recurring"

    def test_its_default_priority_is_high(self) -> None:
        """A recurring task that is due has waited for its slot; a one-time task has not."""
        task = Tasks.recurring("t", EVERY_10S).execute(lambda i, c: None)
        assert task.default_priority == Priority.HIGH

    def test_a_failure_leaves_the_schedule_alone(self) -> None:
        """The next run is the next scheduled one, not a hurried retry - otherwise a task that
        fails every time turns into a hot loop."""
        from db_scheduler.task.handlers import OnFailureReschedule

        task = Tasks.recurring("t", EVERY_10S).execute(lambda i, c: None)
        assert isinstance(task.failure_handler, OnFailureReschedule)
        assert task.failure_handler.schedule == EVERY_10S

    def test_it_schedules_itself_on_startup(self) -> None:
        task = Tasks.recurring("t", EVERY_10S).execute(lambda i, c: None)
        assert task.on_startup is not None

        scheduled: list[tuple[str, datetime]] = []

        class FakeClient:
            def schedule_if_not_exists(self, instance: Any, execution_time: datetime) -> bool:
                scheduled.append((instance.id, execution_time))
                return True

        task.on_startup(FakeClient(), SettableClock(NOW))

        assert scheduled == [(RecurringTask.INSTANCE, NOW)], "a fixed delay starts immediately"

    def test_initial_data_reaches_the_scheduled_instance(self) -> None:
        task = Tasks.recurring("t", EVERY_10S, initial_data={"n": 1}).execute(lambda i, c: None)
        captured: list[Any] = []

        class FakeClient:
            def schedule_if_not_exists(self, instance: Any, execution_time: datetime) -> bool:
                captured.append(instance.data)
                return True

        assert task.on_startup is not None
        task.on_startup(FakeClient(), SettableClock(NOW))
        assert captured == [{"n": 1}]


# ------------------------------------------------------------------ custom


class TestCustomTask:
    def test_the_handler_decides_what_happens_next(self) -> None:
        """A custom task returns its own CompletionHandler, which is the whole point of it."""
        decided = OnCompleteReschedule[Any](EVERY_10S)
        task = Tasks.custom("t").execute(lambda inst, ctx: decided)

        assert task.execute_task(task.instance("i"), a_context()) is decided

    def test_its_default_execution_time_can_be_set(self) -> None:
        task = Tasks.custom("t").execute(
            lambda i, c: OnCompleteRemove[Any](),
            default_execution_time=lambda now: now + timedelta(hours=1),
        )
        assert task.schedulable_instance("i").next_execution_time(NOW) == NOW + timedelta(hours=1)


# ------------------------------------------------------------------ shared


class TestTaskBasics:
    def test_a_task_makes_instances_of_itself(self) -> None:
        task = Tasks.one_time("my-task").execute(lambda i, c: None)

        assert task.instance("abc").task_name == "my-task"
        assert task.instance("abc").id == "abc"
        assert task.instance("abc", {"x": 1}).data == {"x": 1}
        assert task.instance_id("abc").task_name == "my-task"

    def test_an_instance_inherits_the_task_default_priority(self) -> None:
        recurring = Tasks.recurring("r", EVERY_10S).execute(lambda i, c: None)
        assert recurring.instance("i").priority == Priority.HIGH

    def test_a_priority_can_be_set_per_instance(self) -> None:
        task = Tasks.one_time("t").execute(lambda i, c: None)
        assert task.instance_builder("i").priority(Priority.LOW).build().priority == Priority.LOW

    def test_a_blank_name_is_refused(self) -> None:
        """The name is the primary key half of every row this task ever writes."""
        for blank in ("", "   "):
            with pytest.raises(ValueError, match="name"):
                Tasks.one_time(blank).execute(lambda i, c: None)

    def test_two_tasks_are_equal_when_they_have_the_same_name(self) -> None:
        assert Tasks.one_time("t").execute(lambda i, c: None) == Tasks.one_time("t").execute(
            lambda i, c: None
        )
