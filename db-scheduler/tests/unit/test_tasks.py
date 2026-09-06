"""The three kinds of task, and the builder that makes them.

From `OneTimeTaskTest`, `RecurringTaskTest`, `CustomTaskTest`, `AbstractTaskTest` and
`ScheduleRecurringOnStartupTest`. The distinction between the kinds is entirely in what they do
when a run completes, so that is what these assert.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest

from db_scheduler.clock import SettableClock
from db_scheduler.task import (
    Execution,
    ExecutionComplete,
    HasTaskName,
    Priority,
    TaskDescriptor,
    TaskInstance,
    TaskInstanceId,
)
from db_scheduler.task.handlers import (
    CancelDeadExecution,
    OnCompleteRemove,
    OnCompleteReschedule,
    OnFailureRescheduleUsingTaskDataSchedule,
    OnFailureRetryLater,
    ReviveDeadExecution,
)
from db_scheduler.task.helper import (
    ExecutionContext,
    PlainScheduleAndData,
    RecurringTask,
    Tasks,
)
from db_scheduler.task.schedule import Daily, FixedDelay

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
EVERY_10S = FixedDelay.of_seconds(10)
AT_EIGHT = Daily(time(8, 0), zone=UTC)


def a_completion() -> ExecutionComplete:
    return ExecutionComplete.simulated_success(NOW)


class FakeExecutionOperations:
    """Records what a completion handler decided, instead of touching a database."""

    def __init__(self) -> None:
        self.removed = False
        self.rescheduled_to: datetime | None = None
        self.rescheduled_with_data: Any = None
        self.replaced_with: Any = None

    def stop(self) -> None:
        self.removed = True

    def remove(self) -> None:
        self.removed = True

    def reschedule(
        self, completion: ExecutionComplete, next_time: datetime, new_data: Any = None
    ) -> None:
        self.rescheduled_to = next_time
        self.rescheduled_with_data = new_data

    def remove_and_schedule_new(self, schedulable: Any) -> None:
        # Strict, for the reason given in test_handlers.py: a lenient fake is how a handler
        # that hands over the wrong type reaches production.
        from db_scheduler.task import SchedulableInstance

        if not isinstance(schedulable, SchedulableInstance):
            raise TypeError(
                f"remove_and_schedule_new needs a SchedulableInstance, got "
                f"{type(schedulable).__name__}"
            )
        self.replaced_with = schedulable


class FakeSchedulerClient:
    """The four calls scheduling-on-startup makes, recorded rather than executed."""

    def __init__(self, existing: Any = None) -> None:
        self.existing = existing
        self.scheduled: list[tuple[str, datetime]] = []
        self.scheduled_data: list[Any] = []
        self.rescheduled: list[tuple[str, datetime]] = []
        self.cancelled: list[str] = []

    def schedule_if_not_exists(self, instance: Any, execution_time: datetime) -> bool:
        self.scheduled.append((instance.id, execution_time))
        self.scheduled_data.append(instance.data)
        return True

    def get_scheduled_execution(self, instance: Any) -> Any:
        return self.existing

    def reschedule(self, instance: Any, new_execution_time: datetime) -> None:
        self.rescheduled.append((instance.id, new_execution_time))

    def cancel(self, instance: Any) -> None:
        self.cancelled.append(instance.id)


def a_context() -> ExecutionContext:
    return ExecutionContext(execution=Execution(NOW, TaskInstance[Any]("t", "i")))


# ------------------------------------------------------------------ one-time


class TestOneTimeTask:
    def test_it_removes_itself_when_it_completes(self) -> None:
        """A one-time task is done when it is done; the row is what would make it run again."""
        ran: list[str] = []
        task = Tasks.one_time("send-welcome-email").execute(lambda inst, ctx: ran.append(inst.id))

        handler = task.execute(task.instance("abc"), a_context())

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

        handler = task.execute(task.instance(RecurringTask.INSTANCE), a_context())

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
        client = FakeSchedulerClient()

        task.on_startup(client, SettableClock(NOW))

        assert client.scheduled == [(RecurringTask.INSTANCE, NOW)], (
            "a fixed delay starts immediately"
        )

    def test_initial_data_reaches_the_scheduled_instance(self) -> None:
        task = Tasks.recurring("t", EVERY_10S).initial_data({"n": 1}).execute(lambda i, c: None)
        client = FakeSchedulerClient()

        task.on_startup(client, SettableClock(NOW))

        assert client.scheduled_data == [{"n": 1}]

    def test_the_single_instance_has_a_name(self) -> None:
        task = Tasks.recurring("t", EVERY_10S).execute(lambda i, c: None)
        assert task.default_task_instance == TaskInstanceId.of("t", "recurring")

    def test_do_not_schedule_on_startup_does_exactly_that(self) -> None:
        """Not recommended - nothing then keeps the row in step with a changed schedule."""
        task = (
            Tasks.recurring("t", EVERY_10S)
            .do_not_schedule_on_startup()
            .execute(lambda i, c: None)
        )
        client = FakeSchedulerClient()

        task.on_startup(client, SettableClock(NOW))

        assert client.scheduled == []

    def test_execute_stateful_carries_data_to_the_next_run(self) -> None:
        """State between runs without a table of its own: what the body returns is written into
        `task_data` as part of the same reschedule."""
        task = Tasks.recurring("counter", EVERY_10S).execute_stateful(
            lambda inst, ctx: {"n": (inst.data or {"n": 0})["n"] + 1}
        )

        handler = task.execute(task.instance(RecurringTask.INSTANCE, {"n": 4}), a_context())

        assert isinstance(handler, OnCompleteReschedule)
        assert handler.new_data == {"n": 5}


# ------------------------------------------------------------------ custom


class TestCustomTask:
    def test_the_handler_decides_what_happens_next(self) -> None:
        """A custom task returns its own CompletionHandler, which is the whole point of it."""
        decided = OnCompleteReschedule[Any](EVERY_10S)
        task = Tasks.custom("t").execute(lambda inst, ctx: decided)

        assert task.execute(task.instance("i"), a_context()) is decided

    def test_its_default_execution_time_can_be_set(self) -> None:
        task = (
            Tasks.custom("t")
            .default_execution_time(lambda now: now + timedelta(hours=1))
            .execute(lambda i, c: OnCompleteRemove[Any]())
        )
        assert task.schedulable_instance("i").next_execution_time(NOW) == NOW + timedelta(hours=1)

    def test_it_can_schedule_one_execution_on_startup(self) -> None:
        task = (
            Tasks.custom("t")
            .schedule_on_startup("the-one", {"n": 1})
            .execute(lambda i, c: OnCompleteRemove[Any]())
        )
        client = FakeSchedulerClient()

        task.on_startup(client, SettableClock(NOW))

        assert client.scheduled == [("the-one", NOW)]
        assert client.scheduled_data == [{"n": 1}]

    def test_schedule_on_startup_can_take_a_schedule(self) -> None:
        task = (
            Tasks.custom("t")
            .schedule_on_startup("the-one", None, Daily(time(8, 0), zone=UTC))
            .execute(lambda i, c: OnCompleteRemove[Any]())
        )
        client = FakeSchedulerClient()

        task.on_startup(client, SettableClock(NOW))

        assert client.scheduled == [("the-one", datetime(2026, 1, 2, 8, 0, tzinfo=UTC))]

    def test_nothing_happens_on_startup_when_none_was_asked_for(self) -> None:
        task = Tasks.custom("t").execute(lambda i, c: OnCompleteRemove[Any]())
        client = FakeSchedulerClient()

        task.on_startup(client, SettableClock(NOW))

        assert client.scheduled == []


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


# ------------------------------------------------------------------ persistent schedule


class TestRecurringTaskWithPersistentSchedule:
    """The schedule lives in the row, so changing when a task runs is an UPDATE, not a deploy."""

    def test_it_reschedules_by_the_schedule_in_its_data(self) -> None:
        task = Tasks.recurring_with_persistent_schedule("t").execute(lambda i, c: None)
        data = PlainScheduleAndData(EVERY_10S)

        handler = task.execute(task.instance("i", data), a_context())
        operations = FakeExecutionOperations()
        handler.complete(a_completion(), operations)

        assert operations.rescheduled_to == NOW + timedelta(seconds=10)

    def test_execute_stateful_reschedules_by_the_new_data_s_schedule(self) -> None:
        every_minute = FixedDelay.of_seconds(60)
        task = Tasks.recurring_with_persistent_schedule("t").execute_stateful(
            lambda i, c: PlainScheduleAndData(every_minute, {"n": 1})
        )

        handler = task.execute(task.instance("i", PlainScheduleAndData(EVERY_10S)), a_context())
        operations = FakeExecutionOperations()
        handler.complete(a_completion(), operations)

        assert operations.rescheduled_to == NOW + timedelta(seconds=60)
        assert operations.rescheduled_with_data == PlainScheduleAndData(every_minute, {"n": 1})

    def test_it_cannot_be_instantiated_without_data(self) -> None:
        """The data is the schedule. Without it there is nothing to schedule by."""
        task = Tasks.recurring_with_persistent_schedule("t").execute(lambda i, c: None)

        with pytest.raises(NotImplementedError, match="without 'data'"):
            task.instance("i")
        with pytest.raises(NotImplementedError, match="without 'data'"):
            task.schedulable_instance("i")

    def test_its_schedulable_instance_starts_at_the_data_s_first_time(self) -> None:
        task = Tasks.recurring_with_persistent_schedule("t").execute(lambda i, c: None)

        schedulable = task.schedulable_instance("i", PlainScheduleAndData(AT_EIGHT))

        assert schedulable.next_execution_time(NOW) == datetime(2026, 1, 2, 8, 0, tzinfo=UTC)

    def test_a_failure_also_goes_by_the_data_s_schedule(self) -> None:
        task = Tasks.recurring_with_persistent_schedule("t").execute(lambda i, c: None)
        assert isinstance(task.failure_handler, OnFailureRescheduleUsingTaskDataSchedule)

    def test_its_default_priority_matches_a_recurring_task(self) -> None:
        task = Tasks.recurring_with_persistent_schedule("t").execute(lambda i, c: None)
        assert task.default_priority == Priority.HIGH

    def test_it_defaults_to_the_shipped_schedule_type(self) -> None:
        """Java requires the data class; Python can supply the obvious one. Without it the
        repository has nothing to deserialize into, hands back a plain dict, and the task can
        never reschedule itself."""
        task = Tasks.recurring_with_persistent_schedule("t").execute(lambda i, c: None)

        assert task.data_class is PlainScheduleAndData

    def test_a_custom_schedule_type_is_kept(self) -> None:
        class MySchedule(PlainScheduleAndData):
            pass

        task = Tasks.recurring_with_persistent_schedule("t", MySchedule).execute(
            lambda i, c: None
        )

        assert task.data_class is MySchedule

    def test_data_that_lost_its_type_says_what_to_do(self) -> None:
        """What a row read back without a data class looks like: a plain dict. The old code
        asserted - which explains nothing and vanishes under `python -O`."""
        task = Tasks.recurring_with_persistent_schedule("t").execute(lambda i, c: None)
        # A row read back without a data class deserializes to a plain dict, not a schedule.
        lost_its_type = task.instance("i", {"schedule": "...", "data": None})

        handler = task.execute(lost_its_type, a_context())

        with pytest.raises(ValueError, match="must be a ScheduleAndData"):
            handler.complete(a_completion(), FakeExecutionOperations())

    def test_data_that_is_not_a_schedule_is_refused(self) -> None:
        task = Tasks.recurring_with_persistent_schedule("t").execute(lambda i, c: None)

        with pytest.raises(ValueError, match="ScheduleAndData"):
            task.schedulable_instance("i", {"n": 1})


# ------------------------------------------------------------------ descriptors


class TestTaskDescriptor:
    def test_a_task_can_be_declared_from_a_descriptor(self) -> None:
        """So a caller can schedule work for a task that lives in another service."""
        descriptor = TaskDescriptor.of("remote-task", dict)

        task = Tasks.one_time(descriptor).execute(lambda i, c: None)

        assert task.name == "remote-task"
        assert task.data_class is dict

    def test_the_data_class_reaches_the_task(self) -> None:
        task = Tasks.one_time("t", dict).execute(lambda i, c: None)
        assert task.data_class is dict

    def test_a_descriptor_builds_instances_and_ids(self) -> None:
        descriptor = TaskDescriptor.of("remote-task")

        assert descriptor.instance("abc").build().task_and_instance == "remote-task_abc"
        assert descriptor.instance_id("abc") == TaskInstanceId.of("remote-task", "abc")

    def test_it_is_a_has_task_name(self) -> None:
        assert isinstance(TaskDescriptor.of("t"), HasTaskName)
