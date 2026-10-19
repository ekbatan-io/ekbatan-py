"""An execution as a *caller* sees it, and how to ask for a page of them.

`ScheduledExecution` is the client-facing view of a row: typed data, or the raw bytes when the
fetch had no way to know the type. `ScheduledExecutionsFilter` is the query - picked or not,
resolved or not, and a cursor - and `ExecutionTimeAndId` is that cursor, encoded so it can go in
a URL.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Self

from .exceptions import DataClassMismatchException, MissingRawDataException
from .task import Execution, TaskInstanceId

__all__ = [
    "ExecutionTimeAndId",
    "ScheduledExecution",
    "ScheduledExecutionsFilter",
    "TaskSummary",
]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_NAIVE_EPOCH = datetime(1970, 1, 1)

#: `<epoch seconds>,<nanos>:<task instance id>`, before base64. The id may contain anything,
#: including colons, so only the first two fields are delimited.
_ENCODED = re.compile(r"(\d+),(\d+):(.*)", re.DOTALL)


@dataclass(frozen=True, slots=True)
class ExecutionTimeAndId:
    """Where a page of scheduled executions left off.

    Time *and* id, because execution times are not unique - two rows due at the same instant
    would make a time-only cursor either skip one or repeat it. Encoded to an opaque string so
    it can be handed to a caller and given back without them picking it apart.
    """

    execution_time: datetime
    task_instance_id: str

    def __post_init__(self) -> None:
        if self.execution_time is None:
            raise ValueError("Execution time cannot be null")
        if self.task_instance_id is None:
            raise ValueError("Task instance ID cannot be null")

    @classmethod
    def from_execution(cls, execution: ScheduledExecution[Any]) -> Self:
        return cls(execution.execution_time, execution.task_instance.id)

    def to_encoded_string(self) -> str:
        # Seconds since the epoch, counted in the value's own terms - no zone moved.
        epoch = _NAIVE_EPOCH if self.execution_time.tzinfo is None else _EPOCH
        moment = self.execution_time - epoch
        seconds = moment.days * 86_400 + moment.seconds
        nanos = moment.microseconds * 1000
        return base64.b64encode(
            f"{seconds},{nanos}:{self.task_instance_id}".encode()
        ).decode("ascii")

    @classmethod
    def from_encoded_string(cls, encoded: str) -> Self:
        try:
            decoded = base64.b64decode(encoded, validate=True).decode()
            found = _ENCODED.fullmatch(decoded)
            if found is None:
                raise ValueError("Invalid execution time and id format")
            seconds, nanos, task_id = found.groups()
            # Naive UTC, as the default clock gives and a zone-less column holds.
            moment = _NAIVE_EPOCH + timedelta(
                seconds=int(seconds), microseconds=int(nanos) // 1000
            )
            return cls(moment, task_id)
        except Exception as exc:
            raise ValueError(f"Invalid execution time and id: {encoded}") from exc


class ScheduledExecution[T = Any]:
    """A row, as the client sees it.

    `data` and `raw_data` are the two ways to read the payload, and exactly one of them works at
    a time. Which one depends on whether the fetch knew the task's data class: a listing across
    all tasks does not, so it hands back bytes and says so, rather than guessing at a type.
    """

    __slots__ = ("_data_class", "_execution")

    def __init__(self, data_class: type[T] | None, execution: Execution) -> None:
        self._data_class = data_class
        self._execution = execution

    @property
    def task_instance(self) -> TaskInstanceId:
        return self._execution.task_instance.to_id()

    @property
    def execution_time(self) -> datetime:
        return self._execution.execution_time

    @property
    def data(self) -> T | None:
        data = self._execution.task_instance.data
        if data is None:
            return None
        if self._data_class is None or isinstance(data, self._data_class):
            return data  # type: ignore[no-any-return]
        raise DataClassMismatchException(self._data_class, type(data))

    @property
    def has_raw_data(self) -> bool:
        data = self._execution.task_instance.data
        return data is None or isinstance(data, bytes | bytearray | memoryview)

    @property
    def raw_data(self) -> bytes | None:
        if not self.has_raw_data:
            raise MissingRawDataException(self._data_class or object)
        raw = self._execution.task_instance.data
        return bytes(raw) if raw is not None else None

    @property
    def last_success(self) -> datetime | None:
        return self._execution.last_success

    @property
    def last_failure(self) -> datetime | None:
        return self._execution.last_failure

    @property
    def consecutive_failures(self) -> int:
        return self._execution.consecutive_failures

    @property
    def is_picked(self) -> bool:
        return self._execution.picked

    @property
    def picked_by(self) -> str | None:
        return self._execution.picked_by

    @property
    def execution(self) -> Execution:
        return self._execution

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ScheduledExecution):
            return NotImplemented
        return self._execution == other._execution

    def __hash__(self) -> int:
        return hash(self._execution)

    def __repr__(self) -> str:
        return f"ScheduledExecution{{execution={self._execution!r}}}"


class ScheduledExecutionsFilter:
    """Which scheduled executions to list, and how many.

    `include_unresolved` is the one worth understanding: rows for task names this instance has
    no task for. A monitoring view wants them - they are exactly the rows nobody is running -
    while anything that intends to *act* on what it reads does not.
    """

    __slots__ = (
        "_after_execution",
        "_before_execution",
        "_include_unresolved",
        "_limit",
        "_picked_value",
    )

    def __init__(self) -> None:
        self._picked_value: bool | None = None
        self._include_unresolved = False
        self._after_execution: ExecutionTimeAndId | None = None
        self._before_execution: ExecutionTimeAndId | None = None
        self._limit: int | None = None

    @staticmethod
    def all() -> ScheduledExecutionsFilter:
        return ScheduledExecutionsFilter().with_include_unresolved(True)

    @staticmethod
    def only_resolved() -> ScheduledExecutionsFilter:
        return ScheduledExecutionsFilter().with_include_unresolved(False)

    def with_picked(self, picked_value: bool) -> ScheduledExecutionsFilter:
        self._picked_value = picked_value
        return self

    def with_include_unresolved(self, include_unresolved: bool) -> ScheduledExecutionsFilter:
        self._include_unresolved = include_unresolved
        return self

    def limit(self, limit: int) -> ScheduledExecutionsFilter:
        if limit <= 0:
            raise ValueError(f"Limit must be positive, was: {limit}")
        self._limit = limit
        return self

    def after(self, execution: ExecutionTimeAndId) -> ScheduledExecutionsFilter:
        if execution is None:
            raise ValueError("execution cannot be None")
        self._after_execution = execution
        return self

    def before(self, execution: ExecutionTimeAndId) -> ScheduledExecutionsFilter:
        if execution is None:
            raise ValueError("execution cannot be None")
        self._before_execution = execution
        return self

    @property
    def picked_value(self) -> bool | None:
        return self._picked_value

    @property
    def include_unresolved(self) -> bool:
        return self._include_unresolved

    @property
    def after_execution(self) -> ExecutionTimeAndId | None:
        return self._after_execution

    @property
    def before_execution(self) -> ExecutionTimeAndId | None:
        return self._before_execution

    @property
    def limit_value(self) -> int | None:
        return self._limit

    def __repr__(self) -> str:
        return (
            f"ScheduledExecutionsFilter(picked={self._picked_value}, "
            f"include_unresolved={self._include_unresolved}, limit={self._limit})"
        )


@dataclass(frozen=True, slots=True)
class TaskSummary:
    """One row per task name: how many, how they are doing, and since when.

    What an operations page is built from - `failing_count` and `max_consecutive_failures`
    together say whether a task is having a bad minute or has been broken since Tuesday.
    """

    task_name: str
    instance_count: int
    running_count: int
    failing_count: int
    scheduled_count: int
    earliest_execution_time: datetime | None
    latest_last_success: datetime | None
    latest_last_failure: datetime | None
    max_consecutive_failures: int
