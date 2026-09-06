"""What is scheduled, and what happened when it ran.

The model mirrors db-scheduler's `com.github.kagkarlsson.scheduler.task` package, name for
name, because these are the values that end up in a `scheduled_tasks` row - and a row written
by one port has to be legible to the other.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Self

__all__ = [
    "Execution",
    "ExecutionComplete",
    "Priority",
    "Result",
    "TaskInstance",
    "TaskInstanceId",
]


class Priority:
    """Where a task sits when several are due at once. Higher runs first."""

    HIGH = 90
    MEDIUM = 50
    LOW = 10


@dataclass(frozen=True, slots=True)
class TaskInstanceId:
    """What names one row: the task, and which instance of it."""

    task_name: str
    id: str

    @classmethod
    def of(cls, task_name: str, id: str) -> Self:
        return cls(task_name, id)

    def __str__(self) -> str:
        return f"{self.task_name}_{self.id}"


class TaskInstance[T]:
    """One instance of a task, with the data it was scheduled with.

    The data is held behind a supplier, as it is in Java, so that a caller who never reads it
    never pays to deserialize it - which for a batch of a thousand due executions is the
    difference between one round trip and a thousand deserializations.
    """

    __slots__ = ("_data_supplier", "id", "priority", "task_name")

    def __init__(
        self,
        task_name: str,
        id: str,
        data: T | None = None,
        *,
        priority: int = Priority.MEDIUM,
        data_supplier: Callable[[], T] | None = None,
    ) -> None:
        if data is not None and data_supplier is not None:
            raise ValueError("pass data or data_supplier, not both")
        self.task_name = task_name
        self.id = id
        self.priority = priority
        self._data_supplier: Callable[[], T | None] = (
            data_supplier if data_supplier is not None else (lambda: data)
        )

    @property
    def data(self) -> T | None:
        return self._data_supplier()

    @property
    def task_and_instance(self) -> str:
        return f"{self.task_name}_{self.id}"

    def to_id(self) -> TaskInstanceId:
        return TaskInstanceId(self.task_name, self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TaskInstance):
            return NotImplemented
        return (
            self.task_name == other.task_name
            and self.id == other.id
            and self.priority == other.priority
        )

    def __hash__(self) -> int:
        return hash((self.task_name, self.id, self.priority))

    def __repr__(self) -> str:
        return f"TaskInstance(task={self.task_name}, id={self.id}, priority={self.priority})"


@dataclass(slots=True)
class Execution:
    """A row of `scheduled_tasks`, in memory.

    `version` is the optimistic lock every state change turns on: picking, rescheduling and
    heartbeating all carry `WHERE version = ?`, which is what makes exactly one instance in the
    cluster win a slot.
    """

    execution_time: datetime
    task_instance: TaskInstance[Any]
    picked: bool = False
    picked_by: str | None = None
    last_success: datetime | None = None
    last_failure: datetime | None = None
    consecutive_failures: int = 0
    last_heartbeat: datetime | None = None
    version: int = 1

    @property
    def task_name(self) -> str:
        return self.task_instance.task_name

    @property
    def id(self) -> str:
        return self.task_instance.id

    def to_id(self) -> TaskInstanceId:
        return self.task_instance.to_id()

    def update_to_picked(self, picked_by: str, last_heartbeat: datetime) -> Execution:
        """The row as it now is, after the database accepted the pick.

        The version moves because the UPDATE moved it; holding the old one would make every
        later statement on this execution miss.
        """
        return Execution(
            execution_time=self.execution_time,
            task_instance=self.task_instance,
            picked=True,
            picked_by=picked_by,
            last_success=self.last_success,
            last_failure=self.last_failure,
            consecutive_failures=self.consecutive_failures,
            last_heartbeat=last_heartbeat,
            version=self.version + 1,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Execution):
            return NotImplemented
        return (
            self.execution_time == other.execution_time
            and self.task_instance == other.task_instance
        )

    def __hash__(self) -> int:
        return hash((self.execution_time, self.task_instance))

    def __repr__(self) -> str:
        return (
            f"Execution(task={self.task_name}, id={self.id}, "
            f"execution_time={self.execution_time.isoformat()}, picked={self.picked}, "
            f"version={self.version})"
        )


class Result(enum.Enum):
    OK = "OK"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ExecutionComplete:
    """How a run ended, and what a schedule is asked in order to pick the next time."""

    execution: Execution
    time_started: datetime
    time_done: datetime
    result: Result
    cause: BaseException | None = None

    def __post_init__(self) -> None:
        if self.result is Result.OK and self.cause is not None:
            raise ValueError("Result 'OK' should never have a cause.")

    @classmethod
    def success(cls, execution: Execution, started: datetime, done: datetime) -> ExecutionComplete:
        return cls(execution, started, done, Result.OK, None)

    @classmethod
    def failure(
        cls,
        execution: Execution,
        started: datetime,
        done: datetime,
        cause: BaseException | None,
    ) -> ExecutionComplete:
        return cls(execution, started, done, Result.FAILED, cause)

    @classmethod
    def simulated_success(cls, done: datetime) -> ExecutionComplete:
        """A completion that never happened, so a schedule can be asked for a first time.

        Every schedule answers "when next, given this finished at T". Getting the *first* time
        therefore needs a completion to point at, and this is it - the same trick, and the same
        name, as the Java implementation's `simulatedSuccess`.
        """
        placeholder = TaskInstance[Any]("non-existing-task", "non-existing-id")
        execution = Execution(
            execution_time=done,
            task_instance=placeholder,
            picked=False,
            picked_by="simulated-picked-by",
            last_success=done,
            version=1,
        )
        return cls(execution, done - timedelta(seconds=1), done, Result.OK, None)

    @property
    def duration(self) -> timedelta:
        return self.time_done - self.time_started


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)


_ = field  # re-exported for submodules that build dataclasses on this model
