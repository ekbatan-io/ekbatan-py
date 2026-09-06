"""Everything this library raises on purpose.

A port of `com.github.kagkarlsson.scheduler.exceptions`. Java's checked/unchecked pairs collapse
into one class each, since Python has no checked exceptions.
"""

from __future__ import annotations

__all__ = [
    "DataClassMismatchException",
    "DbSchedulerException",
    "ExecutionException",
    "FailedToScheduleBatchException",
    "SerializationException",
    "TaskInstanceCurrentlyExecutingException",
    "TaskInstanceException",
    "TaskInstanceNotFoundException",
]


class DbSchedulerException(Exception):
    """Base for everything raised on purpose."""


class SerializationException(DbSchedulerException):
    """Task data could not be written or read back."""


class DataClassMismatchException(SerializationException):
    """The data came back as a different type than the task expects."""

    def __init__(self, expected: type, actual: type) -> None:
        super().__init__(
            f"task data is a {actual.__name__}, but the task declares {expected.__name__}. "
            f"The row was probably written by a different version of this task."
        )
        self.expected = expected
        self.actual = actual


class TaskInstanceException(DbSchedulerException):
    """Something about one instance, named."""

    def __init__(self, message: str, task_name: str, instance_id: str) -> None:
        super().__init__(f"{message} (task={task_name}, id={instance_id})")
        self.task_name = task_name
        self.instance_id = instance_id


class TaskInstanceNotFoundException(TaskInstanceException):
    """An instance a caller required is not in the table."""

    def __init__(self, task_name: str, instance_id: str) -> None:
        super().__init__("no such task instance", task_name, instance_id)


class TaskInstanceCurrentlyExecutingException(TaskInstanceException):
    """It is running right now, so this cannot be done to it."""

    def __init__(self, task_name: str, instance_id: str) -> None:
        super().__init__("the task instance is currently executing", task_name, instance_id)


class ExecutionException(DbSchedulerException):
    """A run raised, and the scheduler is reporting it rather than swallowing it."""


class FailedToScheduleBatchException(DbSchedulerException):
    """A batch could not be scheduled, and none of it was written."""
