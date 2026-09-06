"""`SchedulerClient` against a real table, on every dialect the run asked for.

The client is what an application server uses when it only ever *creates* work - no scheduler,
no threads, just rows. Which is why these run against the database rather than a fake: the
refusals it makes are about what is in the table.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db_scheduler.client import (
    ScheduleOptions,
    SchedulerClientName,
    StandardSchedulerClient,
    WhenExists,
)
from db_scheduler.clock import SettableClock
from db_scheduler.event import SchedulerListeners
from db_scheduler.exceptions import (
    DbSchedulerException,
    TaskInstanceCurrentlyExecutingException,
    TaskInstanceNotFoundException,
)
from db_scheduler.scheduled_execution import ScheduledExecutionsFilter
from db_scheduler.task import (
    SchedulableTaskInstance,
    TaskInstance,
    TaskInstanceId,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class RecordingListeners(SchedulerListeners):
    def __init__(self) -> None:
        super().__init__()
        self.scheduled: list[tuple[str, datetime]] = []

    def on_execution_scheduled(
        self, task_instance_id: TaskInstanceId, execution_time: datetime
    ) -> None:
        self.scheduled.append((task_instance_id.id, execution_time))


@pytest.fixture
def listeners() -> RecordingListeners:
    return RecordingListeners()


@pytest.fixture
def client(
    repository: Any, listeners: RecordingListeners, clock: SettableClock
) -> StandardSchedulerClient:
    return StandardSchedulerClient(repository, listeners, clock)


def an_instance(id: str = "an-id", data: Any = None) -> TaskInstance[Any]:
    return TaskInstance[Any]("a-task", id, data)


class TestScheduling:
    def test_it_writes_a_row(self, client: StandardSchedulerClient, repository: Any) -> None:
        assert client.schedule_if_not_exists(an_instance(), NOW) is True

        [found] = repository.get_scheduled_executions()
        assert found.id == "an-id"
        assert found.execution_time == NOW

    def test_a_schedulable_instance_carries_its_own_time(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        schedulable = SchedulableTaskInstance(
            an_instance(), lambda now: now + timedelta(hours=1)
        )

        client.schedule_if_not_exists(schedulable)

        [found] = repository.get_scheduled_executions()
        assert found.execution_time == NOW + timedelta(hours=1)

    def test_scheduling_the_same_instance_twice_is_declined(
        self, client: StandardSchedulerClient
    ) -> None:
        """Which is what makes it safe to run at boot on every instance of a cluster."""
        assert client.schedule_if_not_exists(an_instance(), NOW) is True
        assert client.schedule_if_not_exists(an_instance(), NOW) is False

    def test_it_says_what_it_scheduled(
        self, client: StandardSchedulerClient, listeners: RecordingListeners
    ) -> None:
        client.schedule_if_not_exists(an_instance(), NOW)

        assert listeners.scheduled == [("an-id", NOW)]

    def test_and_says_nothing_when_it_declined(
        self, client: StandardSchedulerClient, listeners: RecordingListeners
    ) -> None:
        client.schedule_if_not_exists(an_instance(), NOW)
        listeners.scheduled.clear()

        client.schedule_if_not_exists(an_instance(), NOW)

        assert listeners.scheduled == []

    def test_data_goes_with_it(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        client.schedule_if_not_exists(an_instance(data={"n": 1}), NOW)

        [found] = repository.get_scheduled_executions()
        assert found.task_instance.data == {"n": 1}


class TestScheduleOptions:
    def test_with_no_options_it_is_schedule_if_not_exists(
        self, client: StandardSchedulerClient
    ) -> None:
        assert client.schedule(an_instance(), NOW) is True
        assert client.schedule(an_instance(), NOW) is False

    def test_do_nothing_keeps_the_existing_time(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        client.schedule(an_instance(), NOW)

        scheduled = client.schedule(
            an_instance(), NOW + timedelta(days=1), ScheduleOptions.WHEN_EXISTS_DO_NOTHING
        )

        assert scheduled is False
        [found] = repository.get_scheduled_executions()
        assert found.execution_time == NOW

    def test_reschedule_moves_it(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        client.schedule(an_instance(), NOW)

        scheduled = client.schedule(
            an_instance(), NOW + timedelta(days=1), ScheduleOptions.WHEN_EXISTS_RESCHEDULE
        )

        assert scheduled is True
        [found] = repository.get_scheduled_executions()
        assert found.execution_time == NOW + timedelta(days=1)

    def test_reschedule_carries_the_new_data(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        client.schedule(an_instance(data={"n": 1}), NOW)

        client.schedule(
            an_instance(data={"n": 2}),
            NOW + timedelta(days=1),
            ScheduleOptions.WHEN_EXISTS_RESCHEDULE,
        )

        [found] = repository.get_scheduled_executions()
        assert found.task_instance.data == {"n": 2}

    def test_the_options_object_reads_back(self) -> None:
        assert ScheduleOptions.WHEN_EXISTS_DO_NOTHING.when_exists is WhenExists.DO_NOTHING
        assert ScheduleOptions.WHEN_EXISTS_RESCHEDULE.when_exists is WhenExists.RESCHEDULE
        assert ScheduleOptions.default_options().when_exists is None


class TestBatch:
    def test_a_batch_shares_one_execution_time(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        client.schedule_batch([an_instance(f"{n}") for n in range(3)], NOW)

        found = repository.get_scheduled_executions()
        assert sorted(e.id for e in found) == ["0", "1", "2"]
        assert {e.execution_time for e in found} == {NOW}

    def test_a_batch_of_schedulable_instances_keeps_its_own_times(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        client.schedule_batch(
            [
                SchedulableTaskInstance(an_instance("soon"), NOW),
                SchedulableTaskInstance(an_instance("later"), NOW + timedelta(days=1)),
            ]
        )

        by_id = {e.id: e.execution_time for e in repository.get_scheduled_executions()}
        assert by_id == {"soon": NOW, "later": NOW + timedelta(days=1)}

    def test_a_collision_fails_the_whole_batch(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        """A caller scheduling a batch means the batch. Writing a subset would leave them with
        no way to know which half went in."""
        client.schedule_if_not_exists(an_instance("1"), NOW)

        with pytest.raises(DbSchedulerException):
            client.schedule_batch([an_instance("1"), an_instance("2")], NOW)

        assert [e.id for e in repository.get_scheduled_executions()] == ["1"]

    def test_a_lazy_iterable_is_chunked(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        """A million rows is not one statement anybody wants to hold a lock for."""
        client.schedule_batch((an_instance(f"{n}") for n in range(250)), NOW)

        assert len(repository.get_scheduled_executions()) == 250

    def test_it_says_what_it_scheduled(
        self, client: StandardSchedulerClient, listeners: RecordingListeners
    ) -> None:
        client.schedule_batch([an_instance("1"), an_instance("2")], NOW)

        assert listeners.scheduled == [("1", NOW), ("2", NOW)]


class TestRescheduling:
    def test_it_moves_the_execution(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        client.schedule_if_not_exists(an_instance(), NOW)

        assert client.reschedule(TaskInstanceId("a-task", "an-id"), NOW + timedelta(days=1))

        [found] = repository.get_scheduled_executions()
        assert found.execution_time == NOW + timedelta(days=1)

    def test_it_resets_the_failure_history(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        """A caller rescheduling by hand is saying "try this again as if new"; five failures
        left behind would have the backoff handler wait an hour before the run they asked for."""
        client.schedule_if_not_exists(an_instance(), NOW)
        [execution] = repository.get_due(NOW, limit=10)
        repository.reschedule(
            execution, NOW, last_failure=NOW, consecutive_failures=5
        )

        client.reschedule(TaskInstanceId("a-task", "an-id"), NOW + timedelta(days=1))

        [found] = repository.get_scheduled_executions()
        assert found.consecutive_failures == 0
        assert found.last_failure is None

    def test_new_data_can_be_given(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        client.schedule_if_not_exists(an_instance(data={"n": 1}), NOW)

        client.reschedule(TaskInstanceId("a-task", "an-id"), NOW + timedelta(days=1), {"n": 2})

        [found] = repository.get_scheduled_executions()
        assert found.task_instance.data == {"n": 2}

    def test_a_schedulable_instance_says_it_all_at_once(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        client.schedule_if_not_exists(an_instance(), NOW)

        client.reschedule(
            SchedulableTaskInstance(an_instance(data={"n": 9}), NOW + timedelta(hours=2))
        )

        [found] = repository.get_scheduled_executions()
        assert found.execution_time == NOW + timedelta(hours=2)
        assert found.task_instance.data == {"n": 9}

    def test_rescheduling_something_that_is_not_there_says_so(
        self, client: StandardSchedulerClient
    ) -> None:
        with pytest.raises(TaskInstanceNotFoundException):
            client.reschedule(TaskInstanceId("a-task", "never-scheduled"), NOW)

    def test_rescheduling_something_that_is_running_is_refused(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        """It would be overwritten moments later by the instance running it, which finishes and
        writes its own outcome."""
        client.schedule_if_not_exists(an_instance(), NOW)
        [execution] = repository.get_due(NOW, limit=10)
        repository.pick(execution, NOW)

        with pytest.raises(TaskInstanceCurrentlyExecutingException):
            client.reschedule(TaskInstanceId("a-task", "an-id"), NOW + timedelta(days=1))

    def test_it_says_what_it_rescheduled(
        self, client: StandardSchedulerClient, listeners: RecordingListeners
    ) -> None:
        client.schedule_if_not_exists(an_instance(), NOW)
        listeners.scheduled.clear()

        client.reschedule(TaskInstanceId("a-task", "an-id"), NOW + timedelta(days=1))

        assert listeners.scheduled == [("an-id", NOW + timedelta(days=1))]


class TestCancelling:
    def test_it_removes_the_row(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        client.schedule_if_not_exists(an_instance(), NOW)

        client.cancel(TaskInstanceId("a-task", "an-id"))

        assert repository.get_scheduled_executions() == []

    def test_cancelling_something_that_is_not_there_says_so(
        self, client: StandardSchedulerClient
    ) -> None:
        with pytest.raises(TaskInstanceNotFoundException):
            client.cancel(TaskInstanceId("a-task", "never-scheduled"))

    def test_cancelling_something_that_is_running_is_refused(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        client.schedule_if_not_exists(an_instance(), NOW)
        [execution] = repository.get_due(NOW, limit=10)
        repository.pick(execution, NOW)

        with pytest.raises(TaskInstanceCurrentlyExecutingException):
            client.cancel(TaskInstanceId("a-task", "an-id"))


class TestReading:
    def test_one_execution(self, client: StandardSchedulerClient) -> None:
        client.schedule_if_not_exists(an_instance(data={"n": 1}), NOW)

        found = client.get_scheduled_execution(TaskInstanceId("a-task", "an-id"))

        assert found is not None
        assert found.task_instance == TaskInstanceId("a-task", "an-id")
        assert found.execution_time == NOW
        assert found.data == {"n": 1}
        assert found.is_picked is False

    def test_one_that_is_not_there(self, client: StandardSchedulerClient) -> None:
        assert client.get_scheduled_execution(TaskInstanceId("a-task", "nope")) is None

    def test_the_default_listing_leaves_out_what_is_running(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        """The default is "work still waiting", which is what a queue-depth view wants."""
        client.schedule_batch([an_instance("1"), an_instance("2")], NOW)
        [first, _] = repository.get_due(NOW, limit=10)
        repository.pick(first, NOW)

        assert [e.task_instance.id for e in client.get_scheduled_executions()] == ["2"]

    def test_a_filter_can_ask_for_everything(
        self, client: StandardSchedulerClient, repository: Any
    ) -> None:
        client.schedule_batch([an_instance("1"), an_instance("2")], NOW)
        [first, _] = repository.get_due(NOW, limit=10)
        repository.pick(first, NOW)

        found = client.get_scheduled_executions(ScheduledExecutionsFilter.all())

        assert [e.task_instance.id for e in found] == ["1", "2"]

    def test_by_task_name(self, client: StandardSchedulerClient) -> None:
        client.schedule_if_not_exists(TaskInstance[Any]("a", "1"), NOW)
        client.schedule_if_not_exists(TaskInstance[Any]("b", "2"), NOW)

        found = client.get_scheduled_executions_for_task("a")

        assert [e.task_instance.id for e in found] == ["1"]

    def test_a_data_class_can_be_named(self, client: StandardSchedulerClient) -> None:
        client.schedule_if_not_exists(an_instance(data={"n": 1}), NOW)

        [found] = client.get_scheduled_executions_for_task("a-task", dict)

        assert found.data == {"n": 1}

    def test_fetching_hands_them_over_one_at_a_time(
        self, client: StandardSchedulerClient
    ) -> None:
        """For a table with a million rows, which is what an export looks at."""
        client.schedule_batch([an_instance("1"), an_instance("2")], NOW)
        seen: list[str] = []

        client.fetch_scheduled_executions(lambda e: seen.append(e.task_instance.id))

        assert seen == ["1", "2"]

    def test_a_summary_by_task(self, client: StandardSchedulerClient) -> None:
        client.schedule_batch([an_instance("1"), an_instance("2")], NOW)

        [summary] = client.get_scheduled_executions_summary_by_task()

        assert summary.task_name == "a-task"
        assert summary.instance_count == 2


class TestSchedulerClientName:
    def test_it_names_itself(self) -> None:
        """A client picks nothing, so a row picked by one is immediately identifiable as a bug."""
        assert SchedulerClientName().name == "SchedulerClient"
