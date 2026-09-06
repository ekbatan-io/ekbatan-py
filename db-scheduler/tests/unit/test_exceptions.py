"""One test class per exception file, because the messages are the API.

An operator reads these in a log at three in the morning; "Task data mismatch" that then says
what to do about it is worth more than a tidy class hierarchy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from db_scheduler.exceptions import (
    DataClassMismatchException,
    DbSchedulerException,
    ExecutionException,
    FailedToScheduleBatchException,
    MissingRawDataException,
    SerializationException,
    TaskInstanceCurrentlyExecutingException,
    TaskInstanceException,
    TaskInstanceNotFoundException,
)
from db_scheduler.task import Execution, TaskInstance


class TestTheHierarchy:
    @pytest.mark.parametrize(
        "kind",
        [
            SerializationException,
            DataClassMismatchException,
            MissingRawDataException,
            FailedToScheduleBatchException,
            TaskInstanceException,
            TaskInstanceNotFoundException,
            TaskInstanceCurrentlyExecutingException,
            ExecutionException,
        ],
    )
    def test_everything_is_a_db_scheduler_exception(self, kind: type) -> None:
        """So one `except` catches the library and nothing else."""
        assert issubclass(kind, DbSchedulerException)

    def test_the_instance_scoped_ones_share_a_base(self) -> None:
        for kind in (
            TaskInstanceNotFoundException,
            TaskInstanceCurrentlyExecutingException,
            ExecutionException,
        ):
            assert issubclass(kind, TaskInstanceException)

    def test_a_data_class_mismatch_is_not_a_serialization_failure(self) -> None:
        """It deserialized fine; it is the wrong shape. Java keeps them apart, and so does this."""
        assert not issubclass(DataClassMismatchException, SerializationException)


class TestTaskInstanceException:
    def test_the_message_names_the_row(self) -> None:
        raised = TaskInstanceException("could not do the thing", "a-task", "an-id")

        assert str(raised) == "could not do the thing (task name: a-task, instance id: an-id)"
        assert raised.task_name == "a-task"
        assert raised.instance_id == "an-id"

    def test_not_found(self) -> None:
        raised = TaskInstanceNotFoundException("a-task", "an-id")

        assert "Failed to perform action on task because it was not found." in str(raised)
        assert "(task name: a-task, instance id: an-id)" in str(raised)

    def test_currently_executing_says_what_to_do_about_it(self) -> None:
        raised = TaskInstanceCurrentlyExecutingException("a-task", "an-id")

        assert "currently executing" in str(raised)
        assert "certain time into the future" in str(raised)


class TestExecutionException:
    def test_it_takes_an_execution_and_keeps_its_version(self) -> None:
        execution = Execution(
            execution_time=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            task_instance=TaskInstance[Any]("a-task", "an-id"),
            version=7,
        )

        raised = ExecutionException("lost the race", execution)

        assert raised.version == 7
        assert raised.task_name == "a-task"
        assert raised.instance_id == "an-id"
        assert str(raised) == "lost the race (task name: a-task, instance id: an-id)"

    def test_it_can_be_given_the_parts_instead(self) -> None:
        raised = ExecutionException("x", task_name="t", instance_id="i", version=3)

        assert (raised.task_name, raised.instance_id, raised.version) == ("t", "i", 3)

    def test_it_needs_one_or_the_other(self) -> None:
        with pytest.raises(TypeError, match="either an execution"):
            ExecutionException("x")


class TestDataShapeExceptions:
    def test_a_mismatch_says_what_to_use_instead(self) -> None:
        raised = DataClassMismatchException(dict, bytes)

        assert "Task data mismatch" in str(raised)
        assert "raw_data" in str(raised)
        assert raised.expected_class is dict
        assert raised.actual_class is bytes

    def test_missing_raw_data_points_the_other_way(self) -> None:
        raised = MissingRawDataException(dict)

        assert "typed data" in str(raised)
        assert raised.data_class is dict
