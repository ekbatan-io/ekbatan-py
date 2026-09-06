"""What is scheduled, and what happened when it ran.

The model mirrors db-scheduler's `com.github.kagkarlsson.scheduler.task` package, name for
name, because these are the values that end up in a `scheduled_tasks` row - and a row written
by one port has to be legible to the other.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, Self

__all__ = [
    "Execution",
    "ExecutionComplete",
    "ExecutionContext",
    "HasTaskName",
    "NextExecutionTime",
    "OnStartup",
    "Priority",
    "Result",
    "SchedulableInstance",
    "SchedulableTaskInstance",
    "ScheduledTaskInstance",
    "SimpleTaskName",
    "StateReturningExecutionHandler",
    "TaskDescriptor",
    "TaskInstance",
    "TaskInstanceId",
    "VoidExecutionHandler",
]

#: db-scheduler's `NextExecutionTime`: given the current time, when should this run?
#:
#: Java declares it as a one-method interface so a lambda can satisfy it. In Python a callable
#: already is that, so the name survives as an alias rather than as a class - there is nothing
#: for the class to add. `NextExecutionTime.from(...)` was an adapter for the same reason and
#: has no counterpart either.
type NextExecutionTime = Callable[[datetime], datetime]


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
        data_supplier: Callable[[], T | None] | None = None,
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

    class Builder[U = Any]:
        """Builds one instance, and can schedule it in the same breath.

        `scheduled_to` and `scheduled_according_to_data` are why this exists rather than a
        constructor call: they hand back a `SchedulableInstance`, which is an instance plus a
        time, and is what the client's `schedule(...)` actually wants.
        """

        __slots__ = ("_data_supplier", "_id", "_priority", "_task_name")

        def __init__(self, task_name: str, id: str) -> None:
            self._task_name = task_name
            self._id = id
            self._data_supplier: Callable[[], U | None] = lambda: None
            self._priority = Priority.MEDIUM

        def data_supplier(self, data_supplier: Callable[[], U]) -> Self:
            self._data_supplier = data_supplier
            return self

        def data(self, data: U) -> Self:
            self._data_supplier = lambda: data
            return self

        def priority(self, priority: int) -> Self:
            self._priority = priority
            return self

        def build(self) -> TaskInstance[U]:
            return TaskInstance[U](
                self._task_name,
                self._id,
                data_supplier=self._data_supplier,
                priority=self._priority,
            )

        def scheduled_to(self, execution_time: datetime) -> SchedulableInstance[U]:
            return SchedulableTaskInstance[U](self.build(), execution_time)

        def scheduled_according_to_data(self) -> SchedulableInstance[U]:
            """Schedule it by the schedule stored in its own data.

            The point of a persistent schedule: the row carries the cron, so changing when a
            task runs is a write, not a deploy. Which is also why this can fail - the data has
            to actually carry one.
            """
            from .helper import ScheduleAndData

            data = self._data_supplier()
            if not isinstance(data, ScheduleAndData):
                raise ValueError(
                    "To be able to use method 'scheduled_according_to_data()', the data class "
                    "must implement the ScheduleAndData interface and contain a Schedule"
                )
            schedule = data.schedule
            return SchedulableTaskInstance[U](self.build(), schedule.initial_execution_time)


class SchedulableInstance[T = Any](ABC):
    """An instance, plus when it should first run.

    The time is a function of "now" and not a fixed instant, because a task registered at boot
    must not carry a time computed when the object was built - which, for a container that
    constructs eagerly, could be minutes earlier.

    An interface in Java, with `SchedulableTaskInstance` as the only implementation shipped; the
    split is kept because callers are typed against this one and tests substitute their own.
    """

    __slots__ = ()

    @staticmethod
    def of[U = Any](
        task_instance: TaskInstance[U], execution_time: datetime | NextExecutionTime
    ) -> SchedulableInstance[U]:
        return SchedulableTaskInstance[U](task_instance, execution_time)

    @property
    @abstractmethod
    def task_instance(self) -> TaskInstance[T]: ...

    @abstractmethod
    def next_execution_time(self, current_time: datetime) -> datetime: ...

    @property
    def task_name(self) -> str:
        return self.task_instance.task_name

    @property
    def id(self) -> str:
        return self.task_instance.id

    class Builder[U = Any]:
        """Builds an instance and schedules it, without a `build()` in between.

        Java's `SchedulableInstance.Builder` has no `build()` either - `scheduledTo` is the only
        exit, because an instance with no time is not what this builder is for.
        """

        __slots__ = ("_data_supplier", "_id", "_priority", "_task_name")

        def __init__(self, task_name: str, id: str) -> None:
            self._task_name = task_name
            self._id = id
            self._data_supplier: Callable[[], U | None] = lambda: None
            self._priority = Priority.MEDIUM

        def data_supplier(self, data_supplier: Callable[[], U]) -> Self:
            self._data_supplier = data_supplier
            return self

        def data(self, data: U) -> Self:
            self._data_supplier = lambda: data
            return self

        def priority(self, priority: int) -> Self:
            self._priority = priority
            return self

        def scheduled_to(self, execution_time: datetime) -> SchedulableInstance[U]:
            instance = TaskInstance[U](
                self._task_name,
                self._id,
                data_supplier=self._data_supplier,
                priority=self._priority,
            )
            return SchedulableTaskInstance[U](instance, execution_time)


class SchedulableTaskInstance[T = Any](SchedulableInstance[T]):
    """The one implementation: an instance and a time, either fixed or computed."""

    __slots__ = ("_execution_time", "_task_instance")

    def __init__(
        self, task_instance: TaskInstance[T], execution_time: datetime | NextExecutionTime
    ) -> None:
        self._task_instance = task_instance
        self._execution_time: NextExecutionTime = (
            execution_time
            if callable(execution_time)
            else (lambda _ignored, fixed=execution_time: fixed)  # type: ignore[misc]
        )

    @property
    def task_instance(self) -> TaskInstance[T]:
        return self._task_instance

    def next_execution_time(self, current_time: datetime) -> datetime:
        return self._execution_time(current_time)

    def __repr__(self) -> str:
        return f"SchedulableTaskInstance({self._task_instance!r})"


class ScheduledTaskInstance:
    """An instance whose execution time has been decided.

    The difference from `SchedulableInstance` is the whole reason it exists: that one holds a
    *function* of now, this one holds an instant. Everything written to the table goes through
    here, so that a batch of a thousand rows shares one reading of the clock instead of taking
    a thousand slightly different ones.
    """

    __slots__ = ("execution_time", "task_instance")

    def __init__(self, task_instance: TaskInstance[Any], execution_time: datetime) -> None:
        self.task_instance = task_instance
        self.execution_time = execution_time

    @classmethod
    def fix_execution_time(
        cls, schedulable_instance: SchedulableInstance[Any], clock: Any
    ) -> ScheduledTaskInstance:
        return cls(
            schedulable_instance.task_instance,
            schedulable_instance.next_execution_time(clock.now()),
        )

    @property
    def task_name(self) -> str:
        return self.task_instance.task_name

    @property
    def id(self) -> str:
        return self.task_instance.id

    def to_id(self) -> TaskInstanceId:
        return self.task_instance.to_id()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ScheduledTaskInstance):
            return NotImplemented
        return (
            self.task_instance == other.task_instance
            and self.execution_time == other.execution_time
        )

    def __hash__(self) -> int:
        return hash((self.task_instance, self.execution_time))

    def __repr__(self) -> str:
        return (
            f"ScheduledTaskInstance(task={self.task_name}, id={self.id}, "
            f"execution_time={self.execution_time.isoformat()})"
        )


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


# ------------------------------------------------------------------ what a running task sees


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """What a running task is told about its own execution.

    `scheduler_client` is here so a handler can schedule *other* work - the point db-scheduler
    makes in its own javadoc is that it cannot be used to change the current execution, which is
    the executor's business.
    """

    execution: Execution
    scheduler_state: Any = None
    scheduler_client: Any = None
    currently_executing: Any = None


# ------------------------------------------------------------------ naming


class HasTaskName(ABC):
    """Anything that knows which task it belongs to."""

    __slots__ = ()

    @property
    @abstractmethod
    def task_name(self) -> str: ...

    @staticmethod
    def of(name: str) -> HasTaskName:
        return SimpleTaskName(name)


class SimpleTaskName(HasTaskName):
    """A name and nothing else. What `HasTaskName.of` hands back."""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def task_name(self) -> str:
        return self._name

    def __repr__(self) -> str:
        return f"SimpleTaskName({self._name!r})"


class TaskDescriptor[T = Any](HasTaskName):
    """A task's name and the type of its data, without the task itself.

    It lets a caller schedule work for a task that lives in another service or another module -
    the scheduler only needs the name to write the row, and the data class to read it back.
    """

    __slots__ = ()

    @staticmethod
    def of[U = Any](name: str, data_class: type[U] | None = None) -> TaskDescriptor[U]:
        return SimpleTaskDescriptor[U](name, data_class)

    @property
    @abstractmethod
    def data_class(self) -> type[T] | None: ...

    def instance(self, id: str) -> TaskInstance.Builder[T]:
        return TaskInstance.Builder[T](self.task_name, id)

    def instance_id(self, id: str) -> TaskInstanceId:
        return TaskInstanceId.of(self.task_name, id)


class SimpleTaskDescriptor[T = Any](TaskDescriptor[T]):
    __slots__ = ("_data_class", "_name")

    def __init__(self, name: str, data_class: type[T] | None = None) -> None:
        self._name = name
        self._data_class = data_class

    @property
    def task_name(self) -> str:
        return self._name

    @property
    def data_class(self) -> type[T] | None:
        return self._data_class

    def __repr__(self) -> str:
        return f"SimpleTaskDescriptor({self._name!r}, {self._data_class})"


class OnStartup(Protocol):
    """A task that puts itself in the table when the scheduler starts."""

    def on_startup(self, scheduler: Any, clock: Any) -> None: ...


#: What a task body does with no return value - db-scheduler's `VoidExecutionHandler`. The
#: completion handler is then the task kind's own (remove, or reschedule).
type VoidExecutionHandler[T] = Callable[[TaskInstance[T], ExecutionContext], None]

#: A recurring body that returns the data the *next* execution should carry - db-scheduler's
#: `StateReturningExecutionHandler`. This is how a recurring task keeps state between runs
#: without a table of its own.
type StateReturningExecutionHandler[T] = Callable[[TaskInstance[T], ExecutionContext], T]


def __getattr__(name: str) -> Any:
    """`Task`, `AbstractTask` and the task kinds, under their Java import path.

    In Java these live in this package alongside `FailureHandler`. Here the handlers are a
    module of their own, so a task class defined here would import a module that imports this
    one. Resolving the name on demand gives `from db_scheduler.task import AbstractTask` without
    the cycle; `db_scheduler.task.helper` remains where they are defined.
    """
    forwarded = {
        "AbstractTask": "helper",
        "CustomTask": "helper",
        "ExecutionHandler": "handlers",
        "OneTimeTask": "helper",
        "RecurringTask": "helper",
        "RecurringTaskWithPersistentSchedule": "helper",
        "Task": "helper",
        "Tasks": "helper",
    }
    module = forwarded.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(f".{module}", __name__), name)
