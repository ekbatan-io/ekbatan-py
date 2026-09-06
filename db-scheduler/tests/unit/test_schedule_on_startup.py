"""`ScheduleOnceOnStartup` and `ScheduleRecurringOnStartup`, from `ScheduleRecurringOnStartupTest`.

The interesting one is `ScheduleRecurringOnStartup`. It is not "insert if absent": the schedule
in the code can change between deploys while the row in the table does not, and this is what
reconciles them - carefully, because it runs on every instance in the cluster at boot, next to
a scheduler that is already polling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest

from db_scheduler.clock import SettableClock
from db_scheduler.task.helper import (
    ScheduleOnceOnStartup,
    ScheduleRecurringOnStartup,
    Tasks,
    difference_greater_than,
)
from db_scheduler.task.schedule import Daily, DisabledSchedule, FixedDelay

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
EVERY_HOUR = FixedDelay.of(timedelta(hours=1))
AT_EIGHT = Daily(time(8, 0), zone=UTC)


@dataclass
class FakeScheduledExecution:
    """Stands in for `ScheduledExecution` until the client layer lands."""

    execution_time: datetime


class FakeSchedulerClient:
    def __init__(self, existing: FakeScheduledExecution | None = None) -> None:
        self.existing = existing
        self.scheduled: list[tuple[str, datetime]] = []
        self.scheduled_data: list[Any] = []
        self.rescheduled: list[tuple[str, datetime]] = []
        self.cancelled: list[str] = []
        self.cancel_raises = False

    def schedule_if_not_exists(self, instance: Any, execution_time: datetime) -> bool:
        self.scheduled.append((instance.id, execution_time))
        self.scheduled_data.append(instance.data)
        return True

    def get_scheduled_execution(self, instance: Any) -> Any:
        return self.existing

    def reschedule(self, instance: Any, new_execution_time: datetime) -> None:
        self.rescheduled.append((instance.id, new_execution_time))

    def cancel(self, instance: Any) -> None:
        if self.cancel_raises:
            raise RuntimeError("someone else got there first")
        self.cancelled.append(instance.id)


def a_task() -> Any:
    return Tasks.recurring("t", EVERY_HOUR).execute(lambda i, c: None)


class TestScheduleOnceOnStartup:
    def test_it_schedules_the_named_instance_at_now_by_default(self) -> None:
        client = FakeSchedulerClient()

        ScheduleOnceOnStartup[Any]("the-one").apply(client, SettableClock(NOW), a_task())

        assert client.scheduled == [("the-one", NOW)]
        assert client.scheduled_data == [None]

    def test_it_carries_data(self) -> None:
        client = FakeSchedulerClient()

        ScheduleOnceOnStartup[Any]("the-one", {"n": 1}).apply(
            client, SettableClock(NOW), a_task()
        )

        assert client.scheduled_data == [{"n": 1}]

    def test_the_first_time_is_a_function_of_now(self) -> None:
        client = FakeSchedulerClient()

        ScheduleOnceOnStartup[Any](
            "the-one", None, lambda now: now + timedelta(days=1)
        ).apply(client, SettableClock(NOW), a_task())

        assert client.scheduled == [("the-one", NOW + timedelta(days=1))]

    def test_if_not_exists_is_what_it_calls(self) -> None:
        """Every instance in the cluster runs this at boot. The row already being there is the
        normal case, not an error."""
        client = FakeSchedulerClient(existing=FakeScheduledExecution(NOW))

        ScheduleOnceOnStartup[Any]("the-one").apply(client, SettableClock(NOW), a_task())

        assert client.scheduled == [("the-one", NOW)]


class TestScheduleRecurringOnStartupWithNoExistingRow:
    def test_it_creates_the_initial_execution(self) -> None:
        client = FakeSchedulerClient()

        ScheduleRecurringOnStartup[Any]("recurring", None, EVERY_HOUR).apply(
            client, SettableClock(NOW), a_task()
        )

        assert client.scheduled == [("recurring", NOW)], "a fixed delay starts immediately"

    def test_a_daily_schedule_starts_at_its_next_time(self) -> None:
        client = FakeSchedulerClient()

        ScheduleRecurringOnStartup[Any]("recurring", None, AT_EIGHT).apply(
            client, SettableClock(NOW), a_task()
        )

        assert client.scheduled == [("recurring", datetime(2026, 1, 2, 8, 0, tzinfo=UTC))]

    def test_it_carries_initial_data(self) -> None:
        client = FakeSchedulerClient()

        ScheduleRecurringOnStartup[Any]("recurring", {"n": 1}, EVERY_HOUR).apply(
            client, SettableClock(NOW), a_task()
        )

        assert client.scheduled_data == [{"n": 1}]


class TestScheduleRecurringOnStartupWhenDisabled:
    def test_it_removes_an_existing_execution(self) -> None:
        """A task switched off in configuration should stop running, not keep its last row."""
        client = FakeSchedulerClient(existing=FakeScheduledExecution(NOW + timedelta(hours=1)))

        ScheduleRecurringOnStartup[Any]("recurring", None, DisabledSchedule()).apply(
            client, SettableClock(NOW), a_task()
        )

        assert client.cancelled == ["recurring"]
        assert client.scheduled == []

    def test_it_creates_nothing_when_there_is_nothing(self) -> None:
        client = FakeSchedulerClient()

        ScheduleRecurringOnStartup[Any]("recurring", None, DisabledSchedule()).apply(
            client, SettableClock(NOW), a_task()
        )

        assert client.scheduled == []
        assert client.cancelled == []

    def test_a_failed_cancel_is_survivable(self) -> None:
        """Another instance may have removed it already, or it may be executing right now.
        Neither is a reason to fail the boot."""
        client = FakeSchedulerClient(existing=FakeScheduledExecution(NOW + timedelta(hours=1)))
        client.cancel_raises = True

        ScheduleRecurringOnStartup[Any]("recurring", None, DisabledSchedule()).apply(
            client, SettableClock(NOW), a_task()
        )

        assert client.scheduled == []


class TestScheduleRecurringOnStartupWithAnExistingRow:
    def test_an_execution_due_within_ten_seconds_is_left_alone(self) -> None:
        """It is about to be picked. Touching it only races the scheduler that is picking it."""
        client = FakeSchedulerClient(existing=FakeScheduledExecution(NOW + timedelta(seconds=5)))

        ScheduleRecurringOnStartup[Any]("recurring", None, AT_EIGHT).apply(
            client, SettableClock(NOW), a_task()
        )

        assert client.rescheduled == []

    def test_a_deterministic_schedule_that_moved_reschedules_the_row(self) -> None:
        """Cron and daily are deterministic: if the answer changed, the schedule was edited."""
        client = FakeSchedulerClient(existing=FakeScheduledExecution(NOW + timedelta(days=3)))

        ScheduleRecurringOnStartup[Any]("recurring", None, AT_EIGHT).apply(
            client, SettableClock(NOW), a_task()
        )

        assert client.rescheduled == [("recurring", datetime(2026, 1, 2, 8, 0, tzinfo=UTC))]

    def test_a_deterministic_schedule_that_agrees_changes_nothing(self) -> None:
        client = FakeSchedulerClient(
            existing=FakeScheduledExecution(datetime(2026, 1, 2, 8, 0, tzinfo=UTC))
        )

        ScheduleRecurringOnStartup[Any]("recurring", None, AT_EIGHT).apply(
            client, SettableClock(NOW), a_task()
        )

        assert client.rescheduled == []

    def test_a_second_of_drift_is_not_a_changed_schedule(self) -> None:
        """Database timestamp precision, not an edit."""
        client = FakeSchedulerClient(
            existing=FakeScheduledExecution(
                datetime(2026, 1, 2, 8, 0, 0, 500_000, tzinfo=UTC)
            )
        )

        ScheduleRecurringOnStartup[Any]("recurring", None, AT_EIGHT).apply(
            client, SettableClock(NOW), a_task()
        )

        assert client.rescheduled == []

    def test_a_non_deterministic_schedule_only_ever_moves_earlier(self) -> None:
        """A fixed delay shortened from a day to an hour should take effect now."""
        client = FakeSchedulerClient(existing=FakeScheduledExecution(NOW + timedelta(days=1)))

        ScheduleRecurringOnStartup[Any]("recurring", None, EVERY_HOUR).apply(
            client, SettableClock(NOW), a_task()
        )

        assert client.rescheduled == [("recurring", NOW + timedelta(hours=1))]

    def test_a_non_deterministic_schedule_never_moves_later(self) -> None:
        """The reason db-scheduler gives, and it is a good one: a service that restarts more
        often than its fixed delay would push the execution away every boot and never run."""
        client = FakeSchedulerClient(existing=FakeScheduledExecution(NOW + timedelta(minutes=30)))

        ScheduleRecurringOnStartup[Any]("recurring", None, EVERY_HOUR).apply(
            client, SettableClock(NOW), a_task()
        )

        assert client.rescheduled == []

    def test_rescheduling_leaves_the_data_alone(self) -> None:
        """Intentionally without data - an updated schedule must not overwrite what the running
        task has been keeping in the row."""
        client = FakeSchedulerClient(existing=FakeScheduledExecution(NOW + timedelta(days=3)))

        ScheduleRecurringOnStartup[Any]("recurring", {"n": 1}, AT_EIGHT).apply(
            client, SettableClock(NOW), a_task()
        )

        assert client.scheduled_data == []


class TestDifferenceGreaterThan:
    @pytest.mark.parametrize(
        ("apart", "delta", "expected"),
        [
            (timedelta(seconds=2), timedelta(seconds=1), True),
            (timedelta(seconds=1), timedelta(seconds=1), False),
            (timedelta(milliseconds=500), timedelta(seconds=1), False),
            (timedelta(seconds=-2), timedelta(seconds=1), True),
        ],
    )
    def test_it_is_absolute(
        self, apart: timedelta, delta: timedelta, expected: bool
    ) -> None:
        assert difference_greater_than(NOW, NOW + apart, delta) is expected
