"""`TaskInstance` and its Builder, ported whole.

Covers every public member of TaskInstance.java, SchedulableTaskInstance.java,
ScheduleAndData.java and PlainScheduleAndData.java - the four files this one is about.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from db_scheduler.task import (
    Priority,
    SchedulableTaskInstance,
    TaskInstance,
    TaskInstanceId,
)
from db_scheduler.task.helper import PlainScheduleAndData, ScheduleAndData
from db_scheduler.task.schedule import Daily, FixedDelay

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class TestTaskInstance:
    def test_the_three_constructors(self) -> None:
        assert TaskInstance[Any]("t", "i").data is None
        assert TaskInstance[Any]("t", "i", {"n": 1}).data == {"n": 1}
        assert TaskInstance[Any]("t", "i", data_supplier=lambda: 7).data == 7

    def test_task_and_instance(self) -> None:
        assert TaskInstance[Any]("t", "i").task_and_instance == "t_i"

    def test_the_default_priority_is_medium(self) -> None:
        assert TaskInstance[Any]("t", "i").priority == Priority.MEDIUM

    def test_equality_covers_name_id_and_priority(self) -> None:
        assert TaskInstance[Any]("t", "i") == TaskInstance[Any]("t", "i")
        assert TaskInstance[Any]("t", "i") != TaskInstance[Any]("t", "j")
        assert TaskInstance[Any]("t", "i") != TaskInstance[Any]("u", "i")
        assert TaskInstance[Any]("t", "i", priority=Priority.LOW) != TaskInstance[Any]("t", "i")

    def test_equality_ignores_the_data(self) -> None:
        """Two rows with the same key are the same row, whatever they carry."""
        assert TaskInstance[Any]("t", "i", {"n": 1}) == TaskInstance[Any]("t", "i", {"n": 2})

    def test_hashing_matches_equality(self) -> None:
        assert len({TaskInstance[Any]("t", "i"), TaskInstance[Any]("t", "i")}) == 1

    def test_the_data_supplier_is_not_called_until_the_data_is_read(self) -> None:
        """A poll that fetches a hundred rows and runs three should deserialize three."""
        calls: list[int] = []

        def supply() -> int:
            calls.append(1)
            return 42

        instance = TaskInstance[int]("t", "i", data_supplier=supply)
        assert calls == []
        assert instance.data == 42
        assert calls == [1]

    def test_passing_both_data_and_a_supplier_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            TaskInstance[Any]("t", "i", {"n": 1}, data_supplier=lambda: 2)

    def test_repr_names_the_parts(self) -> None:
        assert "task=t" in repr(TaskInstance[Any]("t", "i"))
        assert "id=i" in repr(TaskInstance[Any]("t", "i"))

    def test_to_id(self) -> None:
        assert TaskInstance[Any]("t", "i").to_id() == TaskInstanceId("t", "i")


class TestTaskInstanceBuilder:
    def test_it_builds_with_data_and_priority(self) -> None:
        built = TaskInstance.Builder[Any]("t", "i").data({"n": 1}).priority(Priority.HIGH).build()

        assert built.task_name == "t"
        assert built.id == "i"
        assert built.data == {"n": 1}
        assert built.priority == Priority.HIGH

    def test_a_supplier_can_be_given_instead(self) -> None:
        built = TaskInstance.Builder[Any]("t", "i").data_supplier(lambda: 9).build()
        assert built.data == 9

    def test_scheduled_to_fixes_the_time(self) -> None:
        schedulable = TaskInstance.Builder[Any]("t", "i").scheduled_to(NOW)

        assert schedulable.task_instance.id == "i"
        assert schedulable.next_execution_time(NOW + timedelta(days=1)) == NOW

    def test_scheduled_according_to_data_reads_the_schedule_off_the_data(self) -> None:
        """A recurring task whose schedule lives in its own row, so it can be changed at
        runtime without a deploy."""
        data = PlainScheduleAndData(Daily(datetime(2026, 1, 1, 8, 0).time(), zone=UTC))

        schedulable = TaskInstance.Builder[Any]("t", "i").data(data).scheduled_according_to_data()

        assert schedulable.next_execution_time(NOW) == datetime(2026, 1, 2, 8, 0, tzinfo=UTC)

    def test_scheduled_according_to_data_refuses_data_that_has_no_schedule(self) -> None:
        with pytest.raises(ValueError, match="ScheduleAndData"):
            TaskInstance.Builder[Any]("t", "i").data({"n": 1}).scheduled_according_to_data()


class TestScheduleAndData:
    def test_of_builds_a_plain_one(self) -> None:
        schedule = FixedDelay.of_seconds(10)
        both = ScheduleAndData.of(schedule, {"n": 1})

        assert both.schedule == schedule
        assert both.data == {"n": 1}

    def test_a_schedule_with_no_data(self) -> None:
        assert PlainScheduleAndData(FixedDelay.of_seconds(1)).data is None

    def test_equality_and_repr(self) -> None:
        one = PlainScheduleAndData(FixedDelay.of_seconds(10), {"n": 1})
        assert one == PlainScheduleAndData(FixedDelay.of_seconds(10), {"n": 1})
        assert one != PlainScheduleAndData(FixedDelay.of_seconds(11), {"n": 1})
        assert "PlainScheduleAndData" in repr(one)


class TestSchedulableTaskInstance:
    def test_it_takes_a_fixed_instant(self) -> None:
        made = SchedulableTaskInstance(TaskInstance[Any]("t", "i"), NOW)
        assert made.next_execution_time(NOW + timedelta(days=1)) == NOW

    def test_it_takes_a_function_of_now(self) -> None:
        made = SchedulableTaskInstance(
            TaskInstance[Any]("t", "i"), lambda now: now + timedelta(hours=1)
        )
        assert made.next_execution_time(NOW) == NOW + timedelta(hours=1)

    def test_it_forwards_the_name_and_id(self) -> None:
        made = SchedulableTaskInstance(TaskInstance[Any]("t", "i"), NOW)
        assert (made.task_name, made.id) == ("t", "i")
