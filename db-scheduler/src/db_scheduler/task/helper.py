"""The three kinds of task, and the builder that makes them.

A port of `com.github.kagkarlsson.scheduler.task.helper`. What separates the kinds is entirely
what they do when a run completes:

    one-time    removes the row - it is done
    recurring   reschedules the row to the schedule's next time
    custom      returns whatever handler the body decided on

Java expresses the body by subclassing and overriding `executeOnce`. Here it is a callable
passed to `execute(...)`, which is the same thing without the ceremony - and it is why these are
concrete classes rather than abstract ones.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from ..clock import Clock
from . import (
    Execution,
    Priority,
    SchedulableInstance,
    SchedulableTaskInstance,
    TaskInstance,
    TaskInstanceId,
)
from .handlers import (
    CompletionHandler,
    DeadExecutionHandler,
    FailureHandler,
    OnCompleteRemove,
    OnCompleteReschedule,
    OnFailureReschedule,
    OnFailureRetryLater,
    ReviveDeadExecution,
)
from .schedule import PersistentCronSchedule, Schedule

__all__ = [
    "DEFAULT_RETRY_INTERVAL",
    "AbstractTask",
    "CustomTask",
    "ExecutionContext",
    "OneTimeTask",
    "PlainScheduleAndData",
    "RecurringTask",
    "SchedulableInstance",
    "SchedulableTaskInstance",
    "ScheduleAndData",
    "Task",
    "Tasks",
]

LOG = logging.getLogger("db_scheduler.task")

#: What a one-time task waits before trying again, unless told otherwise.
DEFAULT_RETRY_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """What a running task is told about its own execution."""

    execution: Execution
    clock: Clock

    @property
    def scheduler_state(self) -> Any:
        return getattr(self, "_scheduler_state", None)


class ScheduleAndData(ABC):
    """Data that carries its own schedule.

    This is what makes a schedule persistent: the cron lives in the row rather than in the code,
    so changing when a task runs is an UPDATE, not a deploy. `RecurringTaskWithPersistentSchedule`
    and `TaskInstance.Builder.scheduled_according_to_data` both look for this.

    Nominal, not structural - an abstract base rather than a Protocol - because Java's check is
    `instanceof`, and a class that merely happens to have a `schedule` attribute is not making
    the same promise.
    """

    __slots__ = ()

    @property
    @abstractmethod
    def schedule(self) -> Schedule: ...

    @property
    @abstractmethod
    def data(self) -> Any: ...

    @staticmethod
    def of(schedule: Schedule, data: Any = None) -> PlainScheduleAndData:
        return PlainScheduleAndData(schedule, data)


class PlainScheduleAndData(ScheduleAndData):
    """The obvious implementation: a schedule, and optionally something alongside it."""

    __slots__ = ("_data", "_schedule")

    def __init__(self, schedule: Schedule, data: Any = None) -> None:
        self._schedule = schedule
        self._data = data

    @property
    def schedule(self) -> Schedule:
        return self._schedule

    @property
    def data(self) -> Any:
        return self._data

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PlainScheduleAndData):
            return NotImplemented
        return self._schedule == other._schedule and self._data == other._data

    def __hash__(self) -> int:
        return hash((self._schedule, self._data))

    def __repr__(self) -> str:
        return f"{type(self).__name__}{{schedule={self._schedule}, data={self._data}}}"


# `PersistentCronSchedule` lives in `task.schedule` and is a `ScheduleAndData`, which lives
# here. Java lets the two packages import each other; Python does not, so the relationship is
# declared from this side, where both names are already in scope. `isinstance` answers the same
# either way, which is all `scheduled_according_to_data()` asks.
ScheduleAndData.register(PersistentCronSchedule)


class OnStartupLike(Protocol):
    """A task that puts itself in the table when the scheduler starts."""

    def __call__(self, client: Any, clock: Clock) -> None: ...


class AbstractTask[T]:
    """What every task has: a name, the two handlers, and a default priority."""

    __slots__ = (
        "dead_execution_handler",
        "default_priority",
        "failure_handler",
        "name",
        "on_startup",
    )

    def __init__(
        self,
        name: str,
        failure_handler: FailureHandler[T],
        dead_execution_handler: DeadExecutionHandler[T],
        default_priority: int = Priority.MEDIUM,
    ) -> None:
        if not name or not name.strip():
            # The name is half the primary key of every row this task will ever write, and how
            # every instance in the cluster recognises it as the same task.
            raise ValueError("a task name cannot be blank")
        self.name = name
        self.failure_handler = failure_handler
        self.dead_execution_handler = dead_execution_handler
        self.default_priority = default_priority
        self.on_startup: OnStartupLike | None = None

    # -- making instances -------------------------------------------------------------

    def instance(self, id: str, data: T | None = None) -> TaskInstance[T]:
        builder = self.instance_builder(id)
        return (builder if data is None else builder.data(data)).build()

    def instance_builder(self, id: str) -> TaskInstance.Builder[T]:
        return TaskInstance.Builder[T](self.name, id).priority(self.default_priority)

    def instance_id(self, id: str) -> TaskInstanceId:
        return TaskInstanceId.of(self.name, id)

    def schedulable_instance(self, id: str, data: T | None = None) -> SchedulableInstance[T]:
        raise NotImplementedError

    # -- running ----------------------------------------------------------------------

    def execute_task(
        self, instance: TaskInstance[T], context: ExecutionContext
    ) -> CompletionHandler[T]:
        raise NotImplementedError

    def __eq__(self, other: object) -> bool:
        return isinstance(other, AbstractTask) and other.name == self.name

    def __hash__(self) -> int:
        return hash(self.name)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r})"


#: What the scheduler needs of a task. Kept as an alias of the base rather than a second
#: Protocol, because every task here goes through AbstractTask and a parallel interface would
#: be a name to maintain for nobody.
Task = AbstractTask


class OneTimeTask[T](AbstractTask[T]):
    """Runs once and removes itself."""

    DEFAULT_PRIORITY = Priority.MEDIUM

    __slots__ = ("_body",)

    def __init__(
        self,
        name: str,
        body: Callable[[TaskInstance[T], ExecutionContext], None],
        failure_handler: FailureHandler[T] | None = None,
        dead_execution_handler: DeadExecutionHandler[T] | None = None,
        default_priority: int = DEFAULT_PRIORITY,
    ) -> None:
        super().__init__(
            name,
            failure_handler or OnFailureRetryLater[T](DEFAULT_RETRY_INTERVAL),
            dead_execution_handler or ReviveDeadExecution[T](),
            default_priority,
        )
        self._body = body

    def schedulable_instance(self, id: str, data: T | None = None) -> SchedulableInstance[T]:
        return SchedulableTaskInstance(self.instance(id, data), lambda now: now)

    def execute_task(
        self, instance: TaskInstance[T], context: ExecutionContext
    ) -> CompletionHandler[T]:
        self._body(instance, context)
        return OnCompleteRemove[T]()


class RecurringTask[T](AbstractTask[T]):
    """Runs on a schedule, for ever, as one row shared by the whole cluster."""

    #: The id of that one row. Constant, because it is what makes five servers agree they are
    #: looking at the same task rather than five of them.
    INSTANCE = "recurring"
    DEFAULT_PRIORITY = Priority.HIGH

    __slots__ = ("_body", "_initial_data", "schedule")

    def __init__(
        self,
        name: str,
        schedule: Schedule,
        body: Callable[[TaskInstance[T], ExecutionContext], None],
        failure_handler: FailureHandler[T] | None = None,
        dead_execution_handler: DeadExecutionHandler[T] | None = None,
        default_priority: int = DEFAULT_PRIORITY,
        initial_data: T | None = None,
    ) -> None:
        super().__init__(
            name,
            failure_handler or OnFailureReschedule[T](schedule),
            dead_execution_handler or ReviveDeadExecution[T](),
            default_priority,
        )
        self.schedule = schedule
        self._body = body
        self._initial_data = initial_data
        self.on_startup = self._schedule_on_startup

    def _schedule_on_startup(self, client: Any, clock: Clock) -> None:
        """Put the single row in the table if nobody has yet.

        `schedule_if_not_exists`, not `schedule`: every instance in the cluster runs this at
        boot, and the row already being there is the normal case.
        """
        instance = self.instance(self.INSTANCE, self._initial_data)
        client.schedule_if_not_exists(instance, self.schedule.initial_execution_time(clock.now()))

    def schedulable_instance(self, id: str, data: T | None = None) -> SchedulableInstance[T]:
        # `TaskInstance`, not `self.instance(...)`: Java builds this one without the task's
        # default priority, so a recurring instance scheduled this way is MEDIUM even though the
        # task is HIGH. Reproduced rather than corrected - priority is persisted, and a Java and
        # a Python instance scheduling the same task must write the same row.
        return SchedulableTaskInstance(
            TaskInstance[T](self.name, id, data), self.schedule.initial_execution_time
        )

    def execute_task(
        self, instance: TaskInstance[T], context: ExecutionContext
    ) -> CompletionHandler[T]:
        self._body(instance, context)
        return OnCompleteReschedule[T](self.schedule)


class CustomTask[T](AbstractTask[T]):
    """Decides for itself what happens after each run."""

    DEFAULT_PRIORITY = Priority.MEDIUM

    __slots__ = ("_body", "_default_execution_time")

    def __init__(
        self,
        name: str,
        body: Callable[[TaskInstance[T], ExecutionContext], CompletionHandler[T]],
        failure_handler: FailureHandler[T] | None = None,
        dead_execution_handler: DeadExecutionHandler[T] | None = None,
        default_priority: int = DEFAULT_PRIORITY,
        default_execution_time: Callable[[datetime], datetime] | None = None,
    ) -> None:
        super().__init__(
            name,
            failure_handler or OnFailureRetryLater[T](DEFAULT_RETRY_INTERVAL),
            dead_execution_handler or ReviveDeadExecution[T](),
            default_priority,
        )
        self._body = body
        self._default_execution_time = default_execution_time or (lambda now: now)

    def schedulable_instance(self, id: str, data: T | None = None) -> SchedulableInstance[T]:
        return SchedulableTaskInstance(self.instance(id, data), self._default_execution_time)

    def execute_task(
        self, instance: TaskInstance[T], context: ExecutionContext
    ) -> CompletionHandler[T]:
        return self._body(instance, context)


# ------------------------------------------------------------------ builders


class _OneTimeTaskBuilder[T]:
    __slots__ = ("_dead", "_failure", "_name", "_priority")

    def __init__(self, name: str) -> None:
        self._name = name
        self._failure: FailureHandler[T] | None = None
        self._dead: DeadExecutionHandler[T] | None = None
        self._priority = OneTimeTask.DEFAULT_PRIORITY

    def on_failure_retry_later(self, delay: timedelta) -> _OneTimeTaskBuilder[T]:
        self._failure = OnFailureRetryLater[T](delay)
        return self

    def on_failure(self, handler: FailureHandler[T]) -> _OneTimeTaskBuilder[T]:
        self._failure = handler
        return self

    def on_dead_execution(self, handler: DeadExecutionHandler[T]) -> _OneTimeTaskBuilder[T]:
        self._dead = handler
        return self

    def priority(self, priority: int) -> _OneTimeTaskBuilder[T]:
        self._priority = priority
        return self

    def execute(self, body: Callable[[TaskInstance[T], ExecutionContext], None]) -> OneTimeTask[T]:
        return OneTimeTask[T](self._name, body, self._failure, self._dead, self._priority)


class _RecurringTaskBuilder[T]:
    __slots__ = ("_dead", "_failure", "_initial_data", "_name", "_priority", "_schedule")

    def __init__(self, name: str, schedule: Schedule, initial_data: T | None = None) -> None:
        self._name = name
        self._schedule = schedule
        self._initial_data = initial_data
        self._failure: FailureHandler[T] | None = None
        self._dead: DeadExecutionHandler[T] | None = None
        self._priority = RecurringTask.DEFAULT_PRIORITY

    def on_failure_reschedule(self) -> _RecurringTaskBuilder[T]:
        self._failure = OnFailureReschedule[T](self._schedule)
        return self

    def on_failure(self, handler: FailureHandler[T]) -> _RecurringTaskBuilder[T]:
        self._failure = handler
        return self

    def on_dead_execution(self, handler: DeadExecutionHandler[T]) -> _RecurringTaskBuilder[T]:
        self._dead = handler
        return self

    def priority(self, priority: int) -> _RecurringTaskBuilder[T]:
        self._priority = priority
        return self

    def execute(
        self, body: Callable[[TaskInstance[T], ExecutionContext], None]
    ) -> RecurringTask[T]:
        return RecurringTask[T](
            self._name,
            self._schedule,
            body,
            self._failure,
            self._dead,
            self._priority,
            self._initial_data,
        )


class _CustomTaskBuilder[T]:
    __slots__ = ("_dead", "_failure", "_name", "_priority")

    def __init__(self, name: str) -> None:
        self._name = name
        self._failure: FailureHandler[T] | None = None
        self._dead: DeadExecutionHandler[T] | None = None
        self._priority = CustomTask.DEFAULT_PRIORITY

    def on_failure(self, handler: FailureHandler[T]) -> _CustomTaskBuilder[T]:
        self._failure = handler
        return self

    def on_dead_execution(self, handler: DeadExecutionHandler[T]) -> _CustomTaskBuilder[T]:
        self._dead = handler
        return self

    def priority(self, priority: int) -> _CustomTaskBuilder[T]:
        self._priority = priority
        return self

    def execute(
        self,
        body: Callable[[TaskInstance[T], ExecutionContext], CompletionHandler[T]],
        default_execution_time: Callable[[datetime], datetime] | None = None,
    ) -> CustomTask[T]:
        return CustomTask[T](
            self._name, body, self._failure, self._dead, self._priority, default_execution_time
        )


class Tasks:
    """The factory, matching db-scheduler's `Tasks`."""

    DEFAULT_RETRY_INTERVAL = DEFAULT_RETRY_INTERVAL

    @staticmethod
    def one_time[T = Any](name: str) -> _OneTimeTaskBuilder[T]:
        return _OneTimeTaskBuilder[T](name)

    @staticmethod
    def recurring[T = Any](
        name: str, schedule: Schedule, initial_data: T | None = None
    ) -> _RecurringTaskBuilder[T]:
        return _RecurringTaskBuilder[T](name, schedule, initial_data)

    @staticmethod
    def custom[T = Any](name: str) -> _CustomTaskBuilder[T]:
        return _CustomTaskBuilder[T](name)
