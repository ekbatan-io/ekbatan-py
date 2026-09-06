"""Everything this library raises on purpose.

A port of `com.github.kagkarlsson.scheduler.exceptions`, one class per Java file. The messages
are Java's, verbatim where they say something an operator needs - `DataClassMismatchException`
in particular explains what to do about it, and a paraphrase would lose that.

Java's checked/unchecked pairs collapse into one class each, since Python has no checked
exceptions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .task import Execution

__all__ = [
    "DataClassMismatchException",
    "DbSchedulerException",
    "ExecutionException",
    "FailedToScheduleBatchException",
    "MissingRawDataException",
    "SerializationException",
    "TaskInstanceCurrentlyExecutingException",
    "TaskInstanceException",
    "TaskInstanceNotFoundException",
]

#: What every instance-scoped message ends with, so a log line names the row it is about.
TASK_NAME_INSTANCE_MESSAGE_PART = " (task name: {}, instance id: {})"


class DbSchedulerException(Exception):
    """Base for everything raised on purpose.

    Abstract in Java. Left instantiable here because the repository raises it directly in a
    handful of places where Java has nothing more specific either.
    """


class SerializationException(DbSchedulerException):
    """Task data could not be written or read back."""


class DataClassMismatchException(DbSchedulerException):
    """The data came back as a different type than the task expects.

    Usually means the execution was fetched without knowledge of the task's data types, so the
    bytes were never deserialized - `raw_data` is then how to get at them.
    """

    def __init__(self, expected_class: type[Any], actual_class: type[Any]) -> None:
        super().__init__(
            "Task data mismatch. If actual data-class is bytes, it might have been fetched "
            "without knowledge of task-data types, and is thus not deserialized. Use raw_data "
            f"to get non-deserialized data in that case. Expected class : {expected_class}, "
            f"actual : {actual_class}"
        )
        self.expected_class = expected_class
        self.actual_class = actual_class


class MissingRawDataException(DbSchedulerException):
    """The other direction: the execution has typed data, so there are no raw bytes to hand back."""

    def __init__(self, data_class: type[Any]) -> None:
        super().__init__(
            "Scheduled execution has typed data, use data to read the deserialized object. "
            f"Data-class : {data_class}"
        )
        self.data_class = data_class


class FailedToScheduleBatchException(DbSchedulerException):
    """A batch could not be scheduled, and none of it was written."""


class TaskInstanceException(DbSchedulerException):
    """Something about one instance, named."""

    def __init__(self, message: str, task_name: str, instance_id: str) -> None:
        super().__init__(message + TASK_NAME_INSTANCE_MESSAGE_PART.format(task_name, instance_id))
        self.task_name = task_name
        self.instance_id = instance_id


class TaskInstanceNotFoundException(TaskInstanceException):
    """An instance a caller required is not in the table."""

    def __init__(self, task_name: str, instance_id: str) -> None:
        super().__init__(
            "Failed to perform action on task because it was not found.", task_name, instance_id
        )


class TaskInstanceCurrentlyExecutingException(TaskInstanceException):
    """It is running right now, so this cannot be done to it."""

    def __init__(self, task_name: str, instance_id: str) -> None:
        super().__init__(
            "Cannot use SchedulerClient to modify a task-instance that is currently executing. "
            "If this occurs often, consider only modifying executions not picked that has the "
            "next execution-time a certain time into the future.",
            task_name,
            instance_id,
        )


class ExecutionException(TaskInstanceException):
    """Something about one execution - the instance, plus the version it was at.

    The version is the point: an optimistic-lock failure is only interpretable if you know which
    version the loser was holding.
    """

    def __init__(
        self,
        message: str,
        execution: Execution | None = None,
        *,
        task_name: str | None = None,
        instance_id: str | None = None,
        version: int | None = None,
    ) -> None:
        if execution is not None:
            task_name = execution.task_name
            instance_id = execution.id
            version = execution.version
        if task_name is None or instance_id is None or version is None:
            raise TypeError(
                "ExecutionException needs either an execution or all of task_name, "
                "instance_id and version"
            )
        super().__init__(message, task_name, instance_id)
        self.version = version
