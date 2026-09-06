"""`ScheduledExecution`, `ScheduledExecutionsFilter`, `ExecutionTimeAndId`, `TaskSummary`,
`ScheduledTaskInstance` - the client-facing view of a row and the query that lists them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from db_scheduler.clock import SettableClock
from db_scheduler.exceptions import DataClassMismatchException, MissingRawDataException
from db_scheduler.scheduled_execution import (
    ExecutionTimeAndId,
    ScheduledExecution,
    ScheduledExecutionsFilter,
    TaskSummary,
)
from db_scheduler.task import (
    Execution,
    SchedulableTaskInstance,
    ScheduledTaskInstance,
    TaskInstance,
    TaskInstanceId,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def an_execution(data: Any = None, **kwargs: Any) -> Execution:
    return Execution(NOW, TaskInstance[Any]("a-task", "an-id", data), **kwargs)


class TestScheduledTaskInstance:
    def test_it_pairs_an_instance_with_a_decided_time(self) -> None:
        scheduled = ScheduledTaskInstance(TaskInstance[Any]("t", "i"), NOW)

        assert (scheduled.task_name, scheduled.id) == ("t", "i")
        assert scheduled.execution_time == NOW
        assert scheduled.to_id() == TaskInstanceId("t", "i")

    def test_fix_execution_time_reads_the_clock_once(self) -> None:
        """Which is the point: a batch of a thousand rows shares one instant instead of taking
        a thousand slightly different ones."""
        clock = SettableClock(NOW)
        schedulable = SchedulableTaskInstance(
            TaskInstance[Any]("t", "i"), lambda now: now + timedelta(hours=1)
        )

        assert ScheduledTaskInstance.fix_execution_time(
            schedulable, clock
        ).execution_time == NOW + timedelta(hours=1)

    def test_equality_and_repr(self) -> None:
        one = ScheduledTaskInstance(TaskInstance[Any]("t", "i"), NOW)
        assert one == ScheduledTaskInstance(TaskInstance[Any]("t", "i"), NOW)
        assert one != ScheduledTaskInstance(TaskInstance[Any]("t", "i"), NOW + timedelta(days=1))
        assert "ScheduledTaskInstance" in repr(one)


class TestScheduledExecution:
    def test_it_exposes_the_row(self) -> None:
        execution = an_execution(
            last_success=NOW - timedelta(days=1),
            last_failure=NOW - timedelta(hours=1),
            consecutive_failures=2,
            picked=True,
            picked_by="worker-1",
        )
        seen = ScheduledExecution[Any](None, execution)

        assert seen.task_instance == TaskInstanceId("a-task", "an-id")
        assert seen.execution_time == NOW
        assert seen.last_success == NOW - timedelta(days=1)
        assert seen.last_failure == NOW - timedelta(hours=1)
        assert seen.consecutive_failures == 2
        assert seen.is_picked is True
        assert seen.picked_by == "worker-1"

    def test_typed_data_comes_back_typed(self) -> None:
        assert ScheduledExecution(dict, an_execution({"n": 1})).data == {"n": 1}

    def test_no_data_is_no_data(self) -> None:
        assert ScheduledExecution(dict, an_execution()).data is None

    def test_data_of_the_wrong_type_is_refused(self) -> None:
        """The message says to use raw_data instead, because the usual cause is a listing that
        had no way to know the type."""
        with pytest.raises(DataClassMismatchException, match="raw_data"):
            _ = ScheduledExecution(dict, an_execution("a string")).data

    def test_raw_data_when_the_bytes_were_never_deserialized(self) -> None:
        assert ScheduledExecution(dict, an_execution(b"{}")).raw_data == b"{}"
        assert ScheduledExecution(dict, an_execution(b"{}")).has_raw_data is True

    def test_raw_data_is_refused_once_the_data_is_typed(self) -> None:
        typed = ScheduledExecution(dict, an_execution({"n": 1}))

        assert typed.has_raw_data is False
        with pytest.raises(MissingRawDataException, match="typed data"):
            _ = typed.raw_data

    def test_equality_is_the_execution(self) -> None:
        assert ScheduledExecution(dict, an_execution()) == ScheduledExecution(
            dict, an_execution()
        )
        assert "ScheduledExecution" in repr(ScheduledExecution(dict, an_execution()))


class TestExecutionTimeAndId:
    def test_it_round_trips_through_its_encoded_form(self) -> None:
        cursor = ExecutionTimeAndId(NOW, "an-id")

        assert ExecutionTimeAndId.from_encoded_string(cursor.to_encoded_string()) == cursor

    def test_microseconds_survive(self) -> None:
        """Two rows 300 microseconds apart are two pages apart; losing that repeats a row."""
        cursor = ExecutionTimeAndId(NOW.replace(microsecond=123_456), "an-id")

        assert ExecutionTimeAndId.from_encoded_string(cursor.to_encoded_string()) == cursor

    def test_an_id_with_a_colon_in_it_survives(self) -> None:
        cursor = ExecutionTimeAndId(NOW, "customer:42:retry")

        assert ExecutionTimeAndId.from_encoded_string(cursor.to_encoded_string()) == cursor

    def test_the_encoded_form_is_opaque_and_url_safe_enough_to_hand_out(self) -> None:
        encoded = ExecutionTimeAndId(NOW, "an-id").to_encoded_string()

        assert "an-id" not in encoded
        assert encoded.isascii()

    def test_from_an_execution(self) -> None:
        seen = ScheduledExecution[Any](None, an_execution())

        assert ExecutionTimeAndId.from_execution(seen) == ExecutionTimeAndId(NOW, "an-id")

    @pytest.mark.parametrize("bad", ["not base64!", "", "YWJj"])
    def test_nonsense_is_refused(self, bad: str) -> None:
        with pytest.raises(ValueError, match="Invalid execution time and id"):
            ExecutionTimeAndId.from_encoded_string(bad)


class TestScheduledExecutionsFilter:
    def test_all_includes_unresolved(self) -> None:
        """A monitoring view wants them: they are exactly the rows nobody is running."""
        assert ScheduledExecutionsFilter.all().include_unresolved is True

    def test_only_resolved_does_not(self) -> None:
        assert ScheduledExecutionsFilter.only_resolved().include_unresolved is False

    def test_picked_is_unset_until_asked_for(self) -> None:
        assert ScheduledExecutionsFilter.all().picked_value is None
        assert ScheduledExecutionsFilter.all().with_picked(True).picked_value is True
        assert ScheduledExecutionsFilter.all().with_picked(False).picked_value is False

    def test_a_limit_must_be_positive(self) -> None:
        assert ScheduledExecutionsFilter.all().limit(10).limit_value == 10
        for bad in (0, -1):
            with pytest.raises(ValueError, match="must be positive"):
                ScheduledExecutionsFilter.all().limit(bad)

    def test_the_cursor_goes_either_way(self) -> None:
        cursor = ExecutionTimeAndId(NOW, "an-id")
        filtered = ScheduledExecutionsFilter.all().after(cursor).before(cursor)

        assert filtered.after_execution == cursor
        assert filtered.before_execution == cursor

    def test_it_chains(self) -> None:
        filtered = (
            ScheduledExecutionsFilter.only_resolved()
            .with_picked(False)
            .limit(5)
            .after(ExecutionTimeAndId(NOW, "an-id"))
        )

        assert (filtered.picked_value, filtered.limit_value) == (False, 5)
        assert "ScheduledExecutionsFilter" in repr(filtered)


class TestTaskSummary:
    def test_it_carries_the_counts_an_operations_page_needs(self) -> None:
        summary = TaskSummary(
            task_name="a-task",
            instance_count=10,
            running_count=2,
            failing_count=1,
            scheduled_count=7,
            earliest_execution_time=NOW,
            latest_last_success=NOW - timedelta(hours=1),
            latest_last_failure=None,
            max_consecutive_failures=3,
        )

        assert summary.task_name == "a-task"
        assert summary.instance_count == 10
        assert summary.max_consecutive_failures == 3
        assert summary == TaskSummary(
            "a-task", 10, 2, 1, 7, NOW, NOW - timedelta(hours=1), None, 3
        )
