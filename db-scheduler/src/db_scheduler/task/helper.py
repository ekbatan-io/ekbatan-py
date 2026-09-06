"""The kinds of task, and the builders that make them.

A port of `com.github.kagkarlsson.scheduler.task.helper`, plus `Task` and `AbstractTask`, which
Java keeps in the parent package and which live here because the handlers they need are a
module of their own. `db_scheduler.task` forwards the names, so the Java import path works too.

What separates the kinds is entirely what happens when a run completes:

    one-time                        removes the row - it is done
    recurring                       reschedules to the schedule's next time
    recurring-with-persistent       reschedules to the schedule *in its own data*
    custom                          returns whatever handler the body decided on

Java expresses the body by subclassing and overriding `executeOnce`. Both work here: pass a
callable, or subclass and override the method. The builder passes a callable, which is what
`Tasks.oneTime(...).execute(...)` amounts to in Java once the anonymous class is stripped away.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Protocol

from ..clock import Clock
from . import (
    ExecutionComplete,
    ExecutionContext,
    HasTaskName,
    Priority,
    SchedulableInstance,
    SchedulableTaskInstance,
    StateReturningExecutionHandler,
    TaskInstance,
    TaskInstanceId,
    VoidExecutionHandler,
)
from .handlers import (
    CompletionHandler,
    DeadExecutionHandler,
    ExecutionHandler,
    FailureHandler,
    OnCompleteRemove,
    OnCompleteReschedule,
    OnFailureReschedule,
    OnFailureRescheduleUsingTaskDataSchedule,
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
    "OneTimeTaskBuilder",
    "PlainScheduleAndData",
    "RecurringTask",
    "RecurringTaskBuilder",
    "RecurringTaskWithPersistentSchedule",
    "RecurringTaskWithPersistentScheduleBuilder",
    "SchedulableInstance",
    "SchedulableTaskInstance",
    "ScheduleAndData",
    "ScheduleOnStartup",
    "ScheduleOnceOnStartup",
    "ScheduleRecurringOnStartup",
    "SchedulerClientLike",
    "Task",
    "TaskBuilder",
    "Tasks",
]

LOG = logging.getLogger("db_scheduler.task")

#: What a one-time task waits before trying again, unless told otherwise.
DEFAULT_RETRY_INTERVAL = timedelta(minutes=5)

#: How near "now" an existing execution has to be before schedule-on-startup leaves it alone.
#: Touching one that is about to run only races the scheduler that is about to pick it.
TOO_NEAR_NOW = timedelta(seconds=10)

#: How far a deterministic schedule's answer has to move before it counts as a changed schedule
#: rather than database timestamp precision.
SCHEDULE_CHANGE_DELTA = timedelta(seconds=1)


# ------------------------------------------------------------------ schedule-carrying data


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


# `PersistentCronSchedule` lives in `task.schedule` and is a `ScheduleAndData`, which lives here.
# Java lets the two packages import each other; Python does not, so the relationship is declared
# from this side, where both names are already in scope. `isinstance` answers the same either
# way, which is all `scheduled_according_to_data()` asks.
ScheduleAndData.register(PersistentCronSchedule)


# ------------------------------------------------------------------ the task interface


class Task[T = Any](HasTaskName, ABC):
    """What the scheduler needs of a task.

    An interface in Java. Kept as an abstract base here so `isinstance` means something and so
    the small defaults - `instance_id`, `default_priority` - have one home.
    """

    __slots__ = ()

    DEFAULT_PRIORITY = Priority.MEDIUM

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def data_class(self) -> type[T] | None: ...

    @property
    @abstractmethod
    def failure_handler(self) -> FailureHandler[T]: ...

    @property
    @abstractmethod
    def dead_execution_handler(self) -> DeadExecutionHandler[T]: ...

    @property
    def default_priority(self) -> int:
        return Task.DEFAULT_PRIORITY

    @property
    def task_name(self) -> str:
        return self.name

    @abstractmethod
    def instance(self, id: str, data: T | None = None) -> TaskInstance[T]: ...

    @abstractmethod
    def instance_builder(self, id: str) -> TaskInstance.Builder[T]: ...

    @abstractmethod
    def schedulable_instance(self, id: str, data: T | None = None) -> SchedulableInstance[T]: ...

    @abstractmethod
    def execute(
        self, task_instance: TaskInstance[T], execution_context: ExecutionContext
    ) -> CompletionHandler[T]: ...

    def instance_id(self, id: str) -> TaskInstanceId:
        return TaskInstanceId.of(self.name, id)


class AbstractTask[T = Any](Task[T]):
    """What every task has: a name, a data class, the two handlers, and a default priority."""

    __slots__ = (
        "_data_class",
        "_dead_execution_handler",
        "_default_priority",
        "_failure_handler",
        "_name",
    )

    def __init__(
        self,
        name: str,
        data_class: type[T] | None,
        failure_handler: FailureHandler[T],
        dead_execution_handler: DeadExecutionHandler[T],
        default_priority: int = Task.DEFAULT_PRIORITY,
    ) -> None:
        if not name or not name.strip():
            # The name is half the primary key of every row this task will ever write, and how
            # every instance in the cluster recognises it as the same task.
            raise ValueError("a task name cannot be blank")
        self._name = name
        self._data_class = data_class
        self._failure_handler = failure_handler
        self._dead_execution_handler = dead_execution_handler
        self._default_priority = default_priority

    @property
    def name(self) -> str:
        return self._name

    @property
    def data_class(self) -> type[T] | None:
        return self._data_class

    @property
    def failure_handler(self) -> FailureHandler[T]:
        return self._failure_handler

    @property
    def dead_execution_handler(self) -> DeadExecutionHandler[T]:
        return self._dead_execution_handler

    @property
    def default_priority(self) -> int:
        return self._default_priority

    def instance(self, id: str, data: T | None = None) -> TaskInstance[T]:
        builder = self.instance_builder(id)
        return (builder if data is None else builder.data(data)).build()

    def instance_builder(self, id: str) -> TaskInstance.Builder[T]:
        return TaskInstance.Builder[T](self._name, id).priority(self.default_priority)

    def schedulable_instance(self, id: str, data: T | None = None) -> SchedulableInstance[T]:
        raise NotImplementedError

    def execute(
        self, task_instance: TaskInstance[T], execution_context: ExecutionContext
    ) -> CompletionHandler[T]:
        raise NotImplementedError

    def on_startup(self, scheduler: SchedulerClientLike, clock: Clock) -> None:
        """Nothing, unless the kind of task says otherwise."""

    def __eq__(self, other: object) -> bool:
        return isinstance(other, AbstractTask) and other.name == self.name

    def __hash__(self) -> int:
        return hash(self.name)

    def __repr__(self) -> str:
        return f"Task name={self.name}"


# ------------------------------------------------------------------ scheduling on startup


class SchedulerClientLike(Protocol):
    """The part of `SchedulerClient` that scheduling-on-startup needs.

    Named as a protocol rather than typed against the real client because the client is a layer
    above this one, and a task must not have to import the scheduler to declare itself.
    """

    def schedule_if_not_exists(self, instance: Any, execution_time: datetime) -> bool: ...
    def get_scheduled_execution(self, instance: Any) -> Any: ...
    def reschedule(self, instance: Any, new_execution_time: datetime) -> None: ...
    def cancel(self, instance: Any) -> None: ...


class ScheduleOnStartup[T = Any](ABC):
    """What a task does to put itself in the table when the scheduler starts."""

    __slots__ = ()

    @abstractmethod
    def apply(self, scheduler: SchedulerClientLike, clock: Clock, task: Task[T]) -> None: ...


class ScheduleOnceOnStartup[T = Any](ScheduleOnStartup[T]):
    """Schedule one execution at boot, and only if it is not already there."""

    __slots__ = ("_data", "_first_execution_time", "_instance")

    def __init__(
        self,
        instance: str,
        data: T | None = None,
        first_execution_time: Callable[[datetime], datetime] | None = None,
    ) -> None:
        self._instance = instance
        self._data = data
        self._first_execution_time = first_execution_time or (lambda now: now)

    def apply(self, scheduler: SchedulerClientLike, clock: Clock, task: Task[T]) -> None:
        scheduler.schedule_if_not_exists(
            self._schedulable_instance(task), self._first_execution_time(clock.now())
        )

    def _schedulable_instance(self, task: Task[T]) -> TaskInstance[T]:
        if self._data is None:
            return task.instance(self._instance)
        return task.instance(self._instance, self._data)


class ScheduleRecurringOnStartup[T = Any](ScheduleOnStartup[T]):
    """Keep the single recurring row in step with the schedule the code declares.

    More than "insert if absent", because the schedule in the code can change between deploys
    while the row does not. The rules, and the reasons db-scheduler gives for them:

    - a schedule marked disabled removes the row, or declines to create one
    - an execution due within ten seconds is left alone, so this does not race the scheduler
      that is about to pick it
    - a *deterministic* schedule (cron, daily) that now answers differently means the schedule
      was edited, so the row moves
    - a *non-deterministic* one (fixed delay) only moves **earlier**. Moving it later on every
      boot would mean a service that restarts more often than its delay never runs at all
    """

    __slots__ = ("_data", "_instance", "_schedule")

    def __init__(self, instance: str, data: T | None, schedule: Schedule | None) -> None:
        self._instance = instance
        self._data = data
        self._schedule = schedule

    def apply(self, scheduler: SchedulerClientLike, clock: Clock, task: Task[T]) -> None:
        if self._schedule is None:
            return
        instance_without_data = task.instance(self._instance)
        preexisting_execution = scheduler.get_scheduled_execution(instance_without_data)

        if self._schedule.is_disabled:
            if preexisting_execution is not None:
                LOG.info(
                    "Task-instance '%s' has a Schedule that has been marked as disabled. "
                    "Removing existing execution with execution-time '%s'.",
                    instance_without_data,
                    preexisting_execution.execution_time,
                )
                self._try_cancel(scheduler, instance_without_data)
            else:
                LOG.info(
                    "Task-instance '%s' has a Schedule that has been marked as disabled. "
                    "Will not schedule a new execution",
                    instance_without_data,
                )
            return

        if preexisting_execution is not None:
            new_next_execution_time = self.check_for_new_execution_time(
                clock, instance_without_data, preexisting_execution
            )
            if new_next_execution_time is not None:
                # Deliberately without data: an updated schedule must not overwrite whatever
                # the running task has been keeping in the row.
                scheduler.reschedule(instance_without_data, new_next_execution_time)
            return

        initial_execution_time = self._schedule.initial_execution_time(clock.now())
        LOG.info(
            "Creating initial execution for task-instance '%s'. Next execution-time: %s",
            instance_without_data,
            initial_execution_time,
        )
        scheduler.schedule_if_not_exists(
            self._schedulable_instance(task), initial_execution_time
        )

    def _try_cancel(self, scheduler: SchedulerClientLike, instance: TaskInstance[T]) -> None:
        try:
            scheduler.cancel(instance)
        except Exception:
            LOG.warning(
                "Failed to cancel existing execution for a Task with a Schedule marked as "
                "disabled. May happen if another instance already did it, or if it is "
                "currently executing.",
                exc_info=True,
            )

    def check_for_new_execution_time(
        self, clock: Clock, instance_without_data: TaskInstance[T], preexisting_execution: Any
    ) -> datetime | None:
        assert self._schedule is not None
        preexisting_execution_time = preexisting_execution.execution_time
        now = clock.now()
        next_execution_time_relative_to_now = self._schedule.next_execution_time(
            ExecutionComplete.simulated_success(now)
        )

        if preexisting_execution_time < now + TOO_NEAR_NOW:
            LOG.debug(
                "Not checking if task-instance '%s' needs rescheduling as its execution-time is "
                "too near now().",
                instance_without_data,
            )
            return None

        if self._schedule.is_deterministic:
            potentially_new_execution_time = next_execution_time_relative_to_now
            if difference_greater_than(
                preexisting_execution_time,
                potentially_new_execution_time,
                SCHEDULE_CHANGE_DELTA,
            ):
                LOG.info(
                    "Rescheduling task-instance '%s' because deterministic Schedule seem to "
                    "have been updated. Previous execution-time: %s, new execution-time: %s",
                    instance_without_data,
                    preexisting_execution_time,
                    potentially_new_execution_time,
                )
                return potentially_new_execution_time
        elif preexisting_execution_time > next_execution_time_relative_to_now:
            LOG.info(
                "Rescheduling task-instance '%s' because non-deterministic Schedule seem to "
                "have been updated to a more frequent one. Previous execution-time: %s, new "
                "execution-time: %s",
                instance_without_data,
                preexisting_execution_time,
                next_execution_time_relative_to_now,
            )
            return next_execution_time_relative_to_now

        LOG.debug(
            "Task-instance '%s' is already scheduled, skipping schedule-on-startup.",
            instance_without_data,
        )
        return None

    def _schedulable_instance(self, task: Task[T]) -> TaskInstance[T]:
        if self._data is None:
            return task.instance(self._instance)
        return task.instance(self._instance, self._data)


def difference_greater_than(one: datetime, other: datetime, delta: timedelta) -> bool:
    return abs(other - one) > delta


# ------------------------------------------------------------------ the kinds of task


class OneTimeTask[T = Any](AbstractTask[T]):
    """Runs once and removes itself."""

    DEFAULT_PRIORITY = Priority.MEDIUM

    __slots__ = ("_execute_once",)

    def __init__(
        self,
        name: str,
        data_class: type[T] | None = None,
        failure_handler: FailureHandler[T] | None = None,
        dead_execution_handler: DeadExecutionHandler[T] | None = None,
        default_priority: int = DEFAULT_PRIORITY,
        execute_once: VoidExecutionHandler[T] | None = None,
    ) -> None:
        super().__init__(
            name,
            data_class,
            failure_handler or OnFailureRetryLater[T](DEFAULT_RETRY_INTERVAL),
            dead_execution_handler or ReviveDeadExecution[T](),
            default_priority,
        )
        self._execute_once = execute_once

    def schedulable_instance(self, id: str, data: T | None = None) -> SchedulableInstance[T]:
        builder = self.instance_builder(id)
        instance = (builder if data is None else builder.data(data)).build()
        return SchedulableTaskInstance[T](instance, lambda current_time: current_time)

    def execute_once(
        self, task_instance: TaskInstance[T], execution_context: ExecutionContext
    ) -> None:
        """The body. Override it, or pass one to the constructor."""
        if self._execute_once is None:
            raise NotImplementedError(
                f"{type(self).__name__} needs a body: pass execute_once=... or override it"
            )
        self._execute_once(task_instance, execution_context)

    def execute(
        self, task_instance: TaskInstance[T], execution_context: ExecutionContext
    ) -> CompletionHandler[T]:
        self.execute_once(task_instance, execution_context)
        return OnCompleteRemove[T]()

    def __repr__(self) -> str:
        return f"OneTimeTask name={self.name}"


class RecurringTask[T = Any](AbstractTask[T]):
    """Runs on a schedule, for ever, as one row shared by the whole cluster."""

    #: The id of that one row. Constant, because it is what makes five servers agree they are
    #: looking at the same task rather than five of them.
    INSTANCE = "recurring"
    DEFAULT_PRIORITY = Priority.HIGH

    __slots__ = ("_execute_recurringly", "_on_complete", "_schedule_on_startup", "schedule")

    def __init__(
        self,
        name: str,
        schedule: Schedule,
        data_class: type[T] | None = None,
        schedule_on_startup: ScheduleOnStartup[T] | None = None,
        failure_handler: FailureHandler[T] | None = None,
        dead_execution_handler: DeadExecutionHandler[T] | None = None,
        default_priority: int = DEFAULT_PRIORITY,
        execute_recurringly: VoidExecutionHandler[T] | None = None,
    ) -> None:
        super().__init__(
            name,
            data_class,
            failure_handler or OnFailureReschedule[T](schedule),
            dead_execution_handler or ReviveDeadExecution[T](),
            default_priority,
        )
        self.schedule = schedule
        self._on_complete = OnCompleteReschedule[T](schedule)
        self._schedule_on_startup = (
            schedule_on_startup
            if schedule_on_startup is not None
            else ScheduleRecurringOnStartup[T](self.INSTANCE, None, schedule)
        )
        self._execute_recurringly = execute_recurringly

    def schedulable_instance(self, id: str, data: T | None = None) -> SchedulableInstance[T]:
        # `TaskInstance`, not `self.instance(...)`: Java builds this one without the task's
        # default priority, so a recurring instance scheduled this way is MEDIUM even though the
        # task is HIGH. Reproduced rather than corrected - priority is persisted, and a Java and
        # a Python instance scheduling the same task must write the same row.
        return SchedulableTaskInstance[T](
            TaskInstance[T](self.name, id, data), self.schedule.initial_execution_time
        )

    def on_startup(self, scheduler: SchedulerClientLike, clock: Clock) -> None:
        if self._schedule_on_startup is not None:
            self._schedule_on_startup.apply(scheduler, clock, self)

    def execute_recurringly(
        self, task_instance: TaskInstance[T], execution_context: ExecutionContext
    ) -> None:
        """The body. Override it, or pass one to the constructor."""
        if self._execute_recurringly is None:
            raise NotImplementedError(
                f"{type(self).__name__} needs a body: pass execute_recurringly=... or override it"
            )
        self._execute_recurringly(task_instance, execution_context)

    def execute(
        self, task_instance: TaskInstance[T], execution_context: ExecutionContext
    ) -> CompletionHandler[T]:
        self.execute_recurringly(task_instance, execution_context)
        return self._on_complete

    @property
    def default_task_instance(self) -> TaskInstanceId:
        return TaskInstanceId.of(self.name, self.INSTANCE)

    def __repr__(self) -> str:
        return f"RecurringTask name={self.name}, onComplete={self._on_complete!r}"


class RecurringTaskWithPersistentSchedule[T = Any](AbstractTask[T]):
    """Recurring, but the schedule lives in the row.

    It cannot be instantiated without data, because the data *is* the schedule - which is why
    both no-data overloads raise here rather than picking something arbitrary.
    """

    DEFAULT_PRIORITY = RecurringTask.DEFAULT_PRIORITY

    __slots__ = ("_execute",)

    def __init__(
        self,
        name: str,
        data_class: type[T] | None = None,
        failure_handler: FailureHandler[T] | None = None,
        dead_execution_handler: DeadExecutionHandler[T] | None = None,
        default_priority: int = DEFAULT_PRIORITY,
        execute: ExecutionHandler[T] | None = None,
    ) -> None:
        super().__init__(
            name,
            data_class,
            failure_handler or OnFailureRescheduleUsingTaskDataSchedule[T](),
            dead_execution_handler or ReviveDeadExecution[T](),
            default_priority,
        )
        self._execute = execute

    def instance(self, id: str, data: T | None = None) -> TaskInstance[T]:
        if data is None:
            raise NotImplementedError(
                "Cannot instantiate a RecurringTaskWithPersistentSchedule without 'data' since "
                "that holds the schedule."
            )
        return self.instance_builder(id).data(data).build()

    def schedulable_instance(self, id: str, data: T | None = None) -> SchedulableInstance[T]:
        if data is None:
            raise NotImplementedError(
                "Cannot instantiate a RecurringTaskWithPersistentSchedule without 'data' since "
                "that holds the schedule."
            )
        if not isinstance(data, ScheduleAndData):
            raise ValueError(
                "RecurringTaskWithPersistentSchedule needs data that is a ScheduleAndData, but "
                f"it was {type(data).__name__}"
            )
        return SchedulableTaskInstance[T](
            self.instance_builder(id).data(data).build(), data.schedule.initial_execution_time
        )

    def execute(
        self, task_instance: TaskInstance[T], execution_context: ExecutionContext
    ) -> CompletionHandler[T]:
        if self._execute is None:
            raise NotImplementedError(
                f"{type(self).__name__} needs a body: pass execute=... or override it"
            )
        return self._execute(task_instance, execution_context)

    def __repr__(self) -> str:
        return f"RecurringTaskWithPersistentSchedule name={self.name}"


class CustomTask[T = Any](AbstractTask[T]):
    """Decides for itself what happens after each run."""

    DEFAULT_PRIORITY = Priority.MEDIUM

    __slots__ = ("_default_execution_time", "_execute", "_schedule_on_startup")

    def __init__(
        self,
        name: str,
        data_class: type[T] | None = None,
        schedule_on_startup: ScheduleOnStartup[T] | None = None,
        default_execution_time: Callable[[datetime], datetime] | None = None,
        failure_handler: FailureHandler[T] | None = None,
        dead_execution_handler: DeadExecutionHandler[T] | None = None,
        default_priority: int = DEFAULT_PRIORITY,
        execute: ExecutionHandler[T] | None = None,
    ) -> None:
        super().__init__(
            name,
            data_class,
            failure_handler or OnFailureRetryLater[T](DEFAULT_RETRY_INTERVAL),
            dead_execution_handler or ReviveDeadExecution[T](),
            default_priority,
        )
        self._schedule_on_startup = schedule_on_startup
        self._default_execution_time = default_execution_time or (lambda now: now)
        self._execute = execute

    def schedulable_instance(self, id: str, data: T | None = None) -> SchedulableInstance[T]:
        builder = self.instance_builder(id)
        instance = (builder if data is None else builder.data(data)).build()
        return SchedulableTaskInstance[T](instance, self._default_execution_time)

    def on_startup(self, scheduler: SchedulerClientLike, clock: Clock) -> None:
        if self._schedule_on_startup is not None:
            self._schedule_on_startup.apply(scheduler, clock, self)

    def execute(
        self, task_instance: TaskInstance[T], execution_context: ExecutionContext
    ) -> CompletionHandler[T]:
        if self._execute is None:
            raise NotImplementedError(
                f"{type(self).__name__} needs a body: pass execute=... or override it"
            )
        return self._execute(task_instance, execution_context)

    def __repr__(self) -> str:
        return f"CustomTask name={self.name}"


# ------------------------------------------------------------------ builders


class RecurringTaskBuilder[T = Any]:
    __slots__ = (
        "_data_class",
        "_default_priority",
        "_name",
        "_on_dead_execution",
        "_on_failure",
        "_schedule",
        "_schedule_on_startup",
    )

    def __init__(
        self, name: str, schedule: Schedule, data_class: type[T] | None = None
    ) -> None:
        self._name = name
        self._schedule = schedule
        self._data_class = data_class
        self._on_failure: FailureHandler[T] = OnFailureReschedule[T](schedule)
        self._on_dead_execution: DeadExecutionHandler[T] = ReviveDeadExecution[T]()
        self._schedule_on_startup: ScheduleOnStartup[T] = ScheduleRecurringOnStartup[T](
            RecurringTask.INSTANCE, None, schedule
        )
        self._default_priority = RecurringTask.DEFAULT_PRIORITY

    def on_failure_reschedule(self) -> RecurringTaskBuilder[T]:
        self._on_failure = OnFailureReschedule[T](self._schedule)
        return self

    def on_dead_execution_revive(self) -> RecurringTaskBuilder[T]:
        self._on_dead_execution = ReviveDeadExecution[T]()
        return self

    def on_failure(self, failure_handler: FailureHandler[T]) -> RecurringTaskBuilder[T]:
        self._on_failure = failure_handler
        return self

    def on_dead_execution(
        self, dead_execution_handler: DeadExecutionHandler[T]
    ) -> RecurringTaskBuilder[T]:
        self._on_dead_execution = dead_execution_handler
        return self

    def initial_data(self, initial_data: T) -> RecurringTaskBuilder[T]:
        self._schedule_on_startup = ScheduleRecurringOnStartup[T](
            RecurringTask.INSTANCE, initial_data, self._schedule
        )
        return self

    def default_priority(self, priority: int) -> RecurringTaskBuilder[T]:
        self._default_priority = priority
        return self

    def do_not_schedule_on_startup(self) -> RecurringTaskBuilder[T]:
        """Take over when the first execution is created.

        Not recommended - db-scheduler says so too - because nothing then keeps the row in step
        with a schedule that changes.
        """
        self._schedule_on_startup = _DoNotScheduleOnStartup[T]()
        return self

    def execute(self, execution_handler: VoidExecutionHandler[T]) -> RecurringTask[T]:
        return RecurringTask[T](
            self._name,
            self._schedule,
            self._data_class,
            self._schedule_on_startup,
            self._on_failure,
            self._on_dead_execution,
            self._default_priority,
            execution_handler,
        )

    def execute_stateful(
        self, execution_handler: StateReturningExecutionHandler[T]
    ) -> RecurringTask[T]:
        """A body that returns the data the *next* execution should carry.

        How a recurring task keeps state between runs without a table of its own: the return
        value is written into `task_data` as part of the same reschedule.
        """
        return _StatefulRecurringTask[T](
            self._name,
            self._schedule,
            self._data_class,
            self._schedule_on_startup,
            self._on_failure,
            self._on_dead_execution,
            self._default_priority,
            execution_handler,
        )


class _StatefulRecurringTask[T = Any](RecurringTask[T]):
    """A recurring task whose body returns the next execution's data.

    Java overrides `execute` in an anonymous subclass and leaves `executeRecurringly` as a
    never-called stub; this is that, with a name.
    """

    __slots__ = ("_state_handler",)

    def __init__(
        self,
        name: str,
        schedule: Schedule,
        data_class: type[T] | None,
        schedule_on_startup: ScheduleOnStartup[T] | None,
        failure_handler: FailureHandler[T] | None,
        dead_execution_handler: DeadExecutionHandler[T] | None,
        default_priority: int,
        state_handler: StateReturningExecutionHandler[T],
    ) -> None:
        super().__init__(
            name,
            schedule,
            data_class,
            schedule_on_startup,
            failure_handler,
            dead_execution_handler,
            default_priority,
        )
        self._state_handler = state_handler

    def execute(
        self, task_instance: TaskInstance[T], execution_context: ExecutionContext
    ) -> CompletionHandler[T]:
        next_data = self._state_handler(task_instance, execution_context)
        return OnCompleteReschedule[T](self.schedule, next_data)

    def execute_recurringly(
        self, task_instance: TaskInstance[T], execution_context: ExecutionContext
    ) -> None:
        """Never called - `execute` is overridden above."""


class _DoNotScheduleOnStartup[T = Any](ScheduleOnStartup[T]):
    """Java builds an anonymous `ScheduleRecurringOnStartup` whose `apply` does nothing."""

    __slots__ = ()

    def apply(self, scheduler: SchedulerClientLike, clock: Clock, task: Task[T]) -> None:
        """Nothing, deliberately."""


class RecurringTaskWithPersistentScheduleBuilder[T = Any]:
    __slots__ = ("_data_class", "_default_priority", "_name", "_on_dead_execution", "_on_failure")

    def __init__(self, name: str, data_class: type[T] | None = None) -> None:
        self._name = name
        self._data_class = data_class
        self._on_failure: FailureHandler[T] = OnFailureRescheduleUsingTaskDataSchedule[T]()
        self._on_dead_execution: DeadExecutionHandler[T] = ReviveDeadExecution[T]()
        self._default_priority = RecurringTaskWithPersistentSchedule.DEFAULT_PRIORITY

    def default_priority(self, priority: int) -> RecurringTaskWithPersistentScheduleBuilder[T]:
        self._default_priority = priority
        return self

    def on_failure(
        self, failure_handler: FailureHandler[T]
    ) -> RecurringTaskWithPersistentScheduleBuilder[T]:
        self._on_failure = failure_handler
        return self

    def on_dead_execution_revive(self) -> RecurringTaskWithPersistentScheduleBuilder[T]:
        self._on_dead_execution = ReviveDeadExecution[T]()
        return self

    def on_dead_execution(
        self, dead_execution_handler: DeadExecutionHandler[T]
    ) -> RecurringTaskWithPersistentScheduleBuilder[T]:
        self._on_dead_execution = dead_execution_handler
        return self

    def execute(
        self, execution_handler: VoidExecutionHandler[T]
    ) -> RecurringTaskWithPersistentSchedule[T]:
        def execute(
            task_instance: TaskInstance[T], execution_context: ExecutionContext
        ) -> CompletionHandler[T]:
            execution_handler(task_instance, execution_context)
            data = task_instance.data

            def on_complete(completion: ExecutionComplete, operations: Any) -> None:
                assert isinstance(data, ScheduleAndData)
                operations.reschedule(completion, data.schedule.next_execution_time(completion))

            return _CompletionHandlerFromCallable[T](on_complete)

        return self._build(execute)

    def execute_stateful(
        self, execution_handler: StateReturningExecutionHandler[T]
    ) -> RecurringTaskWithPersistentSchedule[T]:
        def execute(
            task_instance: TaskInstance[T], execution_context: ExecutionContext
        ) -> CompletionHandler[T]:
            next_data = execution_handler(task_instance, execution_context)

            def on_complete(completion: ExecutionComplete, operations: Any) -> None:
                assert isinstance(next_data, ScheduleAndData)
                operations.reschedule(
                    completion, next_data.schedule.next_execution_time(completion), next_data
                )

            return _CompletionHandlerFromCallable[T](on_complete)

        return self._build(execute)

    def _build(
        self, execute: ExecutionHandler[T]
    ) -> RecurringTaskWithPersistentSchedule[T]:
        return RecurringTaskWithPersistentSchedule[T](
            self._name,
            self._data_class,
            self._on_failure,
            self._on_dead_execution,
            self._default_priority,
            execute,
        )


class _CompletionHandlerFromCallable[T = Any]:
    """A `CompletionHandler` built from a function, as Java's lambdas are."""

    __slots__ = ("_complete",)

    def __init__(self, complete: Callable[[ExecutionComplete, Any], None]) -> None:
        self._complete = complete

    def complete(self, completion: ExecutionComplete, operations: Any) -> None:
        self._complete(completion, operations)


class OneTimeTaskBuilder[T = Any]:
    __slots__ = ("_data_class", "_default_priority", "_name", "_on_dead_execution", "_on_failure")

    def __init__(self, name: str, data_class: type[T] | None = None) -> None:
        self._name = name
        self._data_class = data_class
        self._on_dead_execution: DeadExecutionHandler[T] = ReviveDeadExecution[T]()
        self._on_failure: FailureHandler[T] = OnFailureRetryLater[T](DEFAULT_RETRY_INTERVAL)
        self._default_priority = OneTimeTask.DEFAULT_PRIORITY

    def on_failure_retry_later(
        self, delay: timedelta = DEFAULT_RETRY_INTERVAL
    ) -> OneTimeTaskBuilder[T]:
        """Java takes no argument here and always uses five minutes; the delay is accepted as
        an optional extra because there is no reason to build a whole handler to change it."""
        self._on_failure = OnFailureRetryLater[T](delay)
        return self

    def on_dead_execution_revive(self) -> OneTimeTaskBuilder[T]:
        self._on_dead_execution = ReviveDeadExecution[T]()
        return self

    def on_failure(self, failure_handler: FailureHandler[T]) -> OneTimeTaskBuilder[T]:
        self._on_failure = failure_handler
        return self

    def on_dead_execution(
        self, dead_execution_handler: DeadExecutionHandler[T]
    ) -> OneTimeTaskBuilder[T]:
        self._on_dead_execution = dead_execution_handler
        return self

    def default_priority(self, priority: int) -> OneTimeTaskBuilder[T]:
        self._default_priority = priority
        return self

    def execute(self, execution_handler: VoidExecutionHandler[T]) -> OneTimeTask[T]:
        return OneTimeTask[T](
            self._name,
            self._data_class,
            self._on_failure,
            self._on_dead_execution,
            self._default_priority,
            execution_handler,
        )


class TaskBuilder[T = Any]:
    """Builds a `CustomTask`. Java's name for it, which is why it is not `CustomTaskBuilder`."""

    __slots__ = (
        "_data_class",
        "_default_execution_time",
        "_default_priority",
        "_name",
        "_on_dead_execution",
        "_on_failure",
        "_on_startup",
    )

    def __init__(self, name: str, data_class: type[T] | None = None) -> None:
        self._name = name
        self._data_class = data_class
        self._on_dead_execution: DeadExecutionHandler[T] = ReviveDeadExecution[T]()
        self._on_failure: FailureHandler[T] = OnFailureRetryLater[T](DEFAULT_RETRY_INTERVAL)
        self._on_startup: ScheduleOnStartup[T] | None = None
        self._default_execution_time: Callable[[datetime], datetime] = lambda now: now
        self._default_priority = CustomTask.DEFAULT_PRIORITY

    def on_failure_reschedule(self, schedule: Schedule) -> TaskBuilder[T]:
        self._on_failure = OnFailureReschedule[T](schedule)
        return self

    def on_dead_execution_revive(self) -> TaskBuilder[T]:
        self._on_dead_execution = ReviveDeadExecution[T]()
        return self

    def on_failure(self, failure_handler: FailureHandler[T]) -> TaskBuilder[T]:
        self._on_failure = failure_handler
        return self

    def on_dead_execution(
        self, dead_execution_handler: DeadExecutionHandler[T]
    ) -> TaskBuilder[T]:
        self._on_dead_execution = dead_execution_handler
        return self

    def default_priority(self, priority: int) -> TaskBuilder[T]:
        self._default_priority = priority
        return self

    def schedule_on_startup(
        self,
        instance: str,
        initial_data: T | None = None,
        first_execution_time: Callable[[datetime], datetime] | Schedule | None = None,
    ) -> TaskBuilder[T]:
        """Schedule one execution at boot. Takes a function of now, or a schedule to ask."""
        when: Callable[[datetime], datetime] | None
        if isinstance(first_execution_time, Schedule):
            schedule = first_execution_time

            def when(now: datetime) -> datetime:
                return schedule.next_execution_time(ExecutionComplete.simulated_success(now))

        else:
            when = first_execution_time
        self._on_startup = ScheduleOnceOnStartup[T](instance, initial_data, when)
        return self

    def default_execution_time(
        self, default_execution_time: Callable[[datetime], datetime]
    ) -> TaskBuilder[T]:
        self._default_execution_time = default_execution_time
        return self

    def execute(self, execution_handler: ExecutionHandler[T]) -> CustomTask[T]:
        return CustomTask[T](
            self._name,
            self._data_class,
            self._on_startup,
            self._default_execution_time,
            self._on_failure,
            self._on_dead_execution,
            self._default_priority,
            execution_handler,
        )


class Tasks:
    """The factory, matching db-scheduler's `Tasks`."""

    DEFAULT_RETRY_INTERVAL = DEFAULT_RETRY_INTERVAL

    @staticmethod
    def recurring[T = Any](
        name: str | Any, schedule: Schedule, data_class: type[T] | None = None
    ) -> RecurringTaskBuilder[T]:
        task_name, resolved = _named(name, data_class)
        return RecurringTaskBuilder[T](task_name, schedule, resolved)

    @staticmethod
    def recurring_with_persistent_schedule[T = Any](
        name: str | Any, data_class: type[T] | None = None
    ) -> RecurringTaskWithPersistentScheduleBuilder[T]:
        task_name, resolved = _named(name, data_class)
        return RecurringTaskWithPersistentScheduleBuilder[T](task_name, resolved)

    @staticmethod
    def one_time[T = Any](
        name: str | Any, data_class: type[T] | None = None
    ) -> OneTimeTaskBuilder[T]:
        task_name, resolved = _named(name, data_class)
        return OneTimeTaskBuilder[T](task_name, resolved)

    @staticmethod
    def custom[T = Any](
        name: str | Any, data_class: type[T] | None = None
    ) -> TaskBuilder[T]:
        task_name, resolved = _named(name, data_class)
        return TaskBuilder[T](task_name, resolved)


# The builders are reachable as `Tasks.OneTimeTaskBuilder` too, as they are in Java. Bound
# after the class body rather than inside it, so that the return annotations in `Tasks` still
# name the module-level classes and not attributes of `Tasks` itself.
for _builder in (
    RecurringTaskBuilder,
    RecurringTaskWithPersistentScheduleBuilder,
    OneTimeTaskBuilder,
    TaskBuilder,
):
    setattr(Tasks, _builder.__name__, _builder)
del _builder


def _named(name: str | Any, data_class: type[Any] | None) -> tuple[str, type[Any] | None]:
    """Accept either a name or a `TaskDescriptor`, as every `Tasks` factory does in Java."""
    if isinstance(name, str):
        return name, data_class
    return name.task_name, data_class if data_class is not None else name.data_class
