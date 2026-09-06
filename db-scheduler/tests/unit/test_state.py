"""`SchedulerState`, `HeartbeatConfig`, `HeartbeatState`, `CurrentlyExecuting`
and `ExecutionOperations`.

The heartbeat is how a cluster tells "still working" from "the instance holding this died". Get
it wrong in one direction and dead rows sit picked for ever; wrong in the other and two
instances run the same work.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from db_scheduler.clock import SettableClock
from db_scheduler.event import SchedulerListeners
from db_scheduler.state import (
    CurrentlyExecuting,
    HeartbeatConfig,
    HeartbeatState,
    SettableSchedulerState,
)
from db_scheduler.task import (
    Execution,
    ExecutionComplete,
    SchedulableTaskInstance,
    TaskInstance,
    TaskInstanceId,
)
from db_scheduler.task.operations import ExecutionOperations

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
EVERY_5S = HeartbeatConfig(
    heartbeat_interval=timedelta(seconds=5),
    missed_heartbeats_limit=6,
    max_age_before_considered_dead=timedelta(seconds=30),
)


def an_execution(**kwargs: Any) -> Execution:
    return Execution(NOW, TaskInstance[Any]("a-task", "an-id"), **kwargs)


class TestSchedulerState:
    def test_it_starts_stopped_and_unpaused(self) -> None:
        state = SettableSchedulerState()

        assert (state.is_started, state.is_paused, state.is_shutting_down) == (
            False,
            False,
            False,
        )

    def test_it_can_be_started_paused_and_shut_down(self) -> None:
        state = SettableSchedulerState()

        state.set_started()
        state.set_paused(True)
        state.set_is_shutting_down()

        assert (state.is_started, state.is_paused, state.is_shutting_down) == (
            True,
            True,
            True,
        )

    def test_pausing_says_so_once(self, caplog: pytest.LogCaptureFixture) -> None:
        """Twice would be noise, and in a cluster the line is how an operator confirms the
        pause took."""
        state = SettableSchedulerState()

        with caplog.at_level(logging.INFO, logger="db_scheduler.state"):
            state.set_paused(True)
            state.set_paused(True)

        assert caplog.text.count("Scheduler is paused.") == 1

    def test_resuming_says_so_too(self, caplog: pytest.LogCaptureFixture) -> None:
        state = SettableSchedulerState()
        state.set_paused(True)

        with caplog.at_level(logging.INFO, logger="db_scheduler.state"):
            state.set_paused(False)

        assert "Scheduler is resumed." in caplog.text


class TestHeartbeatState:
    def test_a_fresh_one_is_not_stale(self) -> None:
        assert HeartbeatState(SettableClock(NOW), NOW, EVERY_5S).has_stale_heartbeat is False

    def test_one_missed_beat_within_the_grace_period_is_not_stale(self) -> None:
        """A beat is late once a quarter of its interval has passed on top of it - here 5s plus
        1.25s - so a poll that ran a little slow is not a reason to sound an alarm."""
        clock = SettableClock(NOW)
        state = HeartbeatState(clock, NOW, EVERY_5S)

        clock.set(NOW + timedelta(seconds=6))

        assert state.has_stale_heartbeat is False

    def test_past_the_grace_period_it_is_stale(self) -> None:
        clock = SettableClock(NOW)
        state = HeartbeatState(clock, NOW, EVERY_5S)

        clock.set(NOW + timedelta(seconds=7))

        assert state.has_stale_heartbeat is True

    def test_the_grace_period_is_capped(self) -> None:
        """A quarter of a ten-minute interval would be two and a half minutes of patience."""
        long_interval = HeartbeatConfig(timedelta(minutes=10), 6, timedelta(hours=1))
        clock = SettableClock(NOW)
        state = HeartbeatState(clock, NOW, long_interval)

        clock.set(NOW + timedelta(minutes=10, seconds=11))

        assert state.has_stale_heartbeat is True

    def test_a_single_failure_makes_it_stale_immediately(self) -> None:
        """Time has not passed, but a *failed* write is already evidence the row may be gone."""
        state = HeartbeatState(SettableClock(NOW), NOW, EVERY_5S)

        state.heartbeat(False, NOW)

        assert state.has_stale_heartbeat is True
        assert state.failed_heartbeats == 1

    def test_a_success_clears_the_failures(self) -> None:
        clock = SettableClock(NOW)
        state = HeartbeatState(clock, NOW, EVERY_5S)
        state.heartbeat(False, NOW)
        state.heartbeat(False, NOW + timedelta(seconds=5))

        state.heartbeat(True, NOW + timedelta(seconds=10))

        assert state.failed_heartbeats == 0
        clock.set(NOW + timedelta(seconds=11))
        assert state.has_stale_heartbeat is False

    def test_fraction_dead_reaches_one_at_the_dead_threshold(self) -> None:
        """What a long-running handler polls to decide whether to give up: at 1.0 another
        instance is entitled to take the row, and continuing means two of them running it."""
        clock = SettableClock(NOW)
        state = HeartbeatState(clock, NOW, EVERY_5S)

        assert state.fraction_dead == 0.0
        clock.set(NOW + timedelta(seconds=15))
        assert state.fraction_dead == 0.5
        clock.set(NOW + timedelta(seconds=30))
        assert state.fraction_dead == 1.0

    def test_describe_names_the_counters(self) -> None:
        state = HeartbeatState(SettableClock(NOW), NOW, EVERY_5S)
        state.heartbeat(False, NOW)

        assert "failuresSinceLastSuccess=1" in state.describe()
        assert "missedHeartbeatsLimit=6" in state.describe()


class TestCurrentlyExecuting:
    def test_it_times_the_run(self) -> None:
        clock = SettableClock(NOW)
        running = CurrentlyExecuting(an_execution(), clock, EVERY_5S)

        clock.set(NOW + timedelta(seconds=30))

        assert running.duration == timedelta(seconds=30)
        assert running.start_time == NOW

    def test_it_exposes_the_execution_and_its_instance(self) -> None:
        execution = an_execution()
        running = CurrentlyExecuting(execution, SettableClock(NOW), EVERY_5S)

        assert running.execution is execution
        assert running.task_instance.id == "an-id"

    def test_heartbeats_reach_the_state(self) -> None:
        running = CurrentlyExecuting(an_execution(), SettableClock(NOW), EVERY_5S)

        running.heartbeat(False, NOW)

        assert running.heartbeat_state.failed_heartbeats == 1


class FakeRepository:
    def __init__(self) -> None:
        self.removed: list[Execution] = []
        self.rescheduled: list[dict[str, Any]] = []
        self.replaced: list[Any] = []

    def remove(self, execution: Execution) -> None:
        self.removed.append(execution)

    def reschedule(self, execution: Execution, next_execution_time: datetime, **kwargs: Any) -> bool:
        self.rescheduled.append({"time": next_execution_time, **kwargs})
        return True

    def replace(self, to_be_replaced: Execution, new_instance: Any) -> datetime:
        self.replaced.append(new_instance)
        moment: datetime = new_instance.execution_time
        return moment


class RecordingListeners(SchedulerListeners):
    def __init__(self) -> None:
        super().__init__()
        self.scheduled: list[tuple[TaskInstanceId, datetime]] = []

    def on_execution_scheduled(
        self, task_instance_id: TaskInstanceId, execution_time: datetime
    ) -> None:
        self.scheduled.append((task_instance_id, execution_time))


def operations_for(
    execution: Execution, repository: FakeRepository, listeners: RecordingListeners
) -> ExecutionOperations[Any]:
    return ExecutionOperations[Any](repository, listeners, execution, SettableClock(NOW))


class TestExecutionOperations:
    def test_remove_and_stop_are_the_same_thing(self) -> None:
        """The row is what would run it again, so stopping means removing it."""
        repository, listeners = FakeRepository(), RecordingListeners()
        execution = an_execution()

        operations_for(execution, repository, listeners).stop()

        assert repository.removed == [execution]

    def test_a_successful_reschedule_resets_the_failure_count(self) -> None:
        repository, listeners = FakeRepository(), RecordingListeners()
        execution = an_execution(consecutive_failures=4, last_failure=NOW - timedelta(days=1))
        completed = ExecutionComplete.success(execution, NOW, NOW)

        operations_for(execution, repository, listeners).reschedule(
            completed, NOW + timedelta(hours=1)
        )

        [written] = repository.rescheduled
        assert written["consecutive_failures"] == 0
        assert written["last_success"] == NOW
        assert written["last_failure"] == NOW - timedelta(days=1), "left where it was"

    def test_a_failed_reschedule_increments_it(self) -> None:
        """And leaves `last_success` alone - the pair is what the backoff handlers and the
        failing-task report read."""
        repository, listeners = FakeRepository(), RecordingListeners()
        execution = an_execution(consecutive_failures=4, last_success=NOW - timedelta(days=1))
        completed = ExecutionComplete.failure(execution, NOW, NOW, RuntimeError("boom"))

        operations_for(execution, repository, listeners).reschedule(
            completed, NOW + timedelta(hours=1)
        )

        [written] = repository.rescheduled
        assert written["consecutive_failures"] == 5
        assert written["last_failure"] == NOW
        assert written["last_success"] == NOW - timedelta(days=1)

    def test_new_data_is_carried_through(self) -> None:
        repository, listeners = FakeRepository(), RecordingListeners()
        execution = an_execution()

        operations_for(execution, repository, listeners).reschedule(
            ExecutionComplete.success(execution, NOW, NOW), NOW + timedelta(hours=1), {"n": 1}
        )

        assert repository.rescheduled[0]["new_data"] == {"n": 1}

    def test_no_new_data_means_the_column_is_left_alone(self) -> None:
        repository, listeners = FakeRepository(), RecordingListeners()
        execution = an_execution()

        operations_for(execution, repository, listeners).reschedule(
            ExecutionComplete.success(execution, NOW, NOW), NOW + timedelta(hours=1)
        )

        assert "new_data" not in repository.rescheduled[0]

    def test_rescheduling_hints_that_new_work_exists(self) -> None:
        """Which is what lets a scheduler that has just gone to sleep wake for work it created
        itself, instead of waiting out the poll interval."""
        repository, listeners = FakeRepository(), RecordingListeners()
        execution = an_execution()

        operations_for(execution, repository, listeners).reschedule(
            ExecutionComplete.success(execution, NOW, NOW), NOW + timedelta(hours=1)
        )

        assert listeners.scheduled == [
            (TaskInstanceId("a-task", "an-id"), NOW + timedelta(hours=1))
        ]

    def test_removing_and_scheduling_new_replaces_the_row(self) -> None:
        """One statement, so a crash in between cannot lose the work: the row names the old
        task or the new one, never neither."""
        repository, listeners = FakeRepository(), RecordingListeners()
        execution = an_execution()
        successor = SchedulableTaskInstance(
            TaskInstance[Any]("next-task", "an-id"), NOW + timedelta(minutes=5)
        )

        operations_for(execution, repository, listeners).remove_and_schedule_new(successor)

        assert repository.removed == []
        assert repository.replaced[0].task_name == "next-task"
        assert listeners.scheduled == [
            (TaskInstanceId("next-task", "an-id"), NOW + timedelta(minutes=5))
        ]
