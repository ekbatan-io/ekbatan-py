"""`SchedulerClient` - scheduling work, without running any of it.

The front door. An application server that only ever *creates* work uses this and never starts a
`Scheduler`; the worker instances run the scheduler and pick the rows up. That split is the
point of the class: the client writes rows, and needs a database and nothing else.

Everything here refuses to touch a row that is currently executing. Rescheduling a picked row
would move an execution out from under the instance running it, which then completes and writes
its own outcome over the change - so it raises instead.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Self

from .clock import Clock, SystemClock
from .event import SchedulerListeners
from .exceptions import (
    TaskInstanceCurrentlyExecutingException,
    TaskInstanceNotFoundException,
)
from .scheduled_execution import (
    ScheduledExecution,
    ScheduledExecutionsFilter,
    TaskSummary,
)
from .scheduler_name import SchedulerName
from .task import (
    Execution,
    RescheduleUpdate,
    SchedulableInstance,
    ScheduledTaskInstance,
    TaskInstance,
    TaskInstanceId,
)

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from .jdbc import JdbcCustomization
    from .serializer import Serializer

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "ClientEvent",
    "ClientEventContext",
    "ScheduleOptions",
    "SchedulerClient",
    "SchedulerClientName",
    "StandardSchedulerClient",
    "WhenExists",
]

LOG = logging.getLogger("db_scheduler.client")

#: How many rows go in one INSERT when a caller hands over an iterable rather than a list.
DEFAULT_BATCH_SIZE = 100


class WhenExists(enum.Enum):
    """What `schedule` does when the instance is already in the table."""

    RESCHEDULE = "RESCHEDULE"
    DO_NOTHING = "DO_NOTHING"


class ScheduleOptions:
    """The policy for an instance that already exists."""

    __slots__ = ("_when_exists",)

    #: The two anybody actually uses, as constants - Java exposes them the same way.
    WHEN_EXISTS_DO_NOTHING: ScheduleOptions
    WHEN_EXISTS_RESCHEDULE: ScheduleOptions

    def __init__(self) -> None:
        self._when_exists: WhenExists | None = None

    @staticmethod
    def default_options() -> ScheduleOptions:
        return ScheduleOptions()

    def when_exists_reschedule(self) -> Self:
        self._when_exists = WhenExists.RESCHEDULE
        return self

    def when_exists_do_nothing(self) -> Self:
        self._when_exists = WhenExists.DO_NOTHING
        return self

    @property
    def when_exists(self) -> WhenExists | None:
        return self._when_exists

    def __repr__(self) -> str:
        return f"ScheduleOptions(when_exists={self._when_exists})"


ScheduleOptions.WHEN_EXISTS_DO_NOTHING = ScheduleOptions().when_exists_do_nothing()
ScheduleOptions.WHEN_EXISTS_RESCHEDULE = ScheduleOptions().when_exists_reschedule()


class SchedulerClientName(SchedulerName):
    """What a client writes into `picked_by` - which it never does, since it picks nothing.

    It exists so the repository has a name to hold, and so a row that somehow *was* picked by a
    client is immediately identifiable as a bug.
    """

    __slots__ = ()

    @property
    def name(self) -> str:
        return "SchedulerClient"


class SchedulerClient:
    """Scheduling, rescheduling, cancelling and listing. No execution."""

    __slots__ = ()

    @staticmethod
    def builder(engine: Engine, *known_tasks: Any) -> SchedulerClientBuilder:
        """Java's ``SchedulerClient.Builder.create(dataSource, knownTasks...)``: a client for a
        process that schedules work and runs none - a web server handing one-time tasks to the
        instances that run a scheduler, over the same table.

        ``known_tasks`` are the tasks whose rows the client reads back: a row no known task
        claims is dropped from what it lists. One list or tuple is taken as the tasks, as Java's
        ``create(dataSource, List)`` takes one.
        """
        tasks: Sequence[Any]
        if len(known_tasks) == 1 and isinstance(known_tasks[0], list | tuple):
            tasks = known_tasks[0]
        else:
            tasks = known_tasks
        return SchedulerClientBuilder(engine, tasks)

    def schedule(
        self,
        task_instance: TaskInstance[Any] | SchedulableInstance[Any],
        execution_time: datetime | None = None,
        schedule_options: ScheduleOptions | None = None,
    ) -> bool:
        raise NotImplementedError

    def schedule_if_not_exists(
        self,
        task_instance: TaskInstance[Any] | SchedulableInstance[Any],
        execution_time: datetime | None = None,
    ) -> bool:
        raise NotImplementedError

    def schedule_batch(
        self,
        task_instances: Iterable[TaskInstance[Any] | SchedulableInstance[Any]],
        execution_time: datetime | None = None,
    ) -> None:
        raise NotImplementedError

    def reschedule(
        self,
        task_instance_id: TaskInstanceId | SchedulableInstance[Any],
        new_execution_time: datetime | None = None,
        new_data: Any = None,
    ) -> bool:
        raise NotImplementedError

    def cancel(self, task_instance_id: TaskInstanceId) -> None:
        raise NotImplementedError

    def get_scheduled_execution(
        self, task_instance_id: TaskInstanceId
    ) -> ScheduledExecution[Any] | None:
        raise NotImplementedError

    def get_scheduled_executions(
        self, filter: ScheduledExecutionsFilter | None = None
    ) -> list[ScheduledExecution[Any]]:
        raise NotImplementedError

    def get_scheduled_executions_for_task(
        self,
        task_name: str,
        data_class: type[Any] | None = None,
        filter: ScheduledExecutionsFilter | None = None,
    ) -> list[ScheduledExecution[Any]]:
        raise NotImplementedError

    def get_scheduled_executions_summary_by_task(self) -> list[TaskSummary]:
        raise NotImplementedError


class StandardSchedulerClient(SchedulerClient):
    """The one implementation: a repository, a clock, and whoever is listening."""

    __slots__ = ("_clock", "_scheduler_listeners", "task_repository")

    def __init__(
        self,
        task_repository: Any,
        scheduler_listeners: SchedulerListeners | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.task_repository = task_repository
        self._scheduler_listeners = scheduler_listeners or SchedulerListeners()
        self._clock = clock or SystemClock()

    # ------------------------------------------------------------------ scheduling

    def _fix(
        self,
        instance: TaskInstance[Any] | SchedulableInstance[Any],
        execution_time: datetime | None,
    ) -> ScheduledTaskInstance:
        """Accept either shape: an instance plus a time, or one that carries its own."""
        if isinstance(instance, SchedulableInstance):
            return ScheduledTaskInstance.fix_execution_time(instance, self._clock)
        if execution_time is None:
            raise TypeError("an execution time is required for a plain TaskInstance")
        return ScheduledTaskInstance(instance, execution_time)

    def schedule_if_not_exists(
        self,
        task_instance: TaskInstance[Any] | SchedulableInstance[Any],
        execution_time: datetime | None = None,
    ) -> bool:
        scheduled = self._fix(task_instance, execution_time)
        success: bool = self.task_repository.create_if_not_exists(scheduled)
        if success:
            self._scheduler_listeners.on_execution_scheduled(
                scheduled.to_id(), scheduled.execution_time
            )
        return success

    def schedule(
        self,
        task_instance: TaskInstance[Any] | SchedulableInstance[Any],
        execution_time: datetime | None = None,
        schedule_options: ScheduleOptions | None = None,
    ) -> bool:
        """Schedule it, and say what to do if it is already there.

        With no options this is `schedule_if_not_exists` and returns whether it wrote a row -
        which is the shape Java's deprecated `schedule` has, minus the discarded return value.
        """
        scheduled = self._fix(task_instance, execution_time)
        if self.schedule_if_not_exists(scheduled.task_instance, scheduled.execution_time):
            return True
        if schedule_options is None:
            return False

        when_exists = schedule_options.when_exists
        if when_exists is WhenExists.DO_NOTHING:
            LOG.debug(
                "Task instance already exists. Keeping existing. task-instance=%s",
                scheduled.task_instance,
            )
            return False
        if when_exists is WhenExists.RESCHEDULE:
            if self.get_scheduled_execution(scheduled.to_id()) is None:
                # Between the failed insert and this lookup, somebody ran it and deleted the
                # row. Rescheduling now would resurrect work that has already been done.
                LOG.warning(
                    "Task-instance should already exist, but failed to find it. It must have "
                    "been processed and deleted. task-instance=%s",
                    scheduled.task_instance,
                )
                return False
            LOG.debug(
                "Task instance already exists. Rescheduling. task-instance=%s",
                scheduled.task_instance,
            )
            return self.reschedule(
                scheduled.to_id(),
                scheduled.execution_time,
                scheduled.task_instance.data,
            )
        raise ValueError(f"Unknown WhenExists value: {when_exists}")

    def schedule_batch(
        self,
        task_instances: Iterable[TaskInstance[Any] | SchedulableInstance[Any]],
        execution_time: datetime | None = None,
    ) -> None:
        """Schedule several. All or nothing, and chunked if there are many.

        A list goes in one statement; anything lazier is chunked at `DEFAULT_BATCH_SIZE`, which
        is what Java's `Stream` overloads do - a million-row insert is not one statement anybody
        wants to hold a lock for.
        """
        if isinstance(task_instances, Sequence):
            self._schedule_one_batch(list(task_instances), execution_time)
            return
        chunk: list[Any] = []
        for instance in task_instances:
            chunk.append(instance)
            if len(chunk) == DEFAULT_BATCH_SIZE:
                self._schedule_one_batch(chunk, execution_time)
                chunk = []
        if chunk:
            self._schedule_one_batch(chunk, execution_time)

    def _schedule_one_batch(
        self,
        task_instances: Sequence[TaskInstance[Any] | SchedulableInstance[Any]],
        execution_time: datetime | None,
    ) -> None:
        batch = [self._fix(one, execution_time) for one in task_instances]
        self.task_repository.create_batch(batch)
        for scheduled in batch:
            self._scheduler_listeners.on_execution_scheduled(
                scheduled.to_id(), scheduled.execution_time
            )

    # ------------------------------------------------------------------ changing

    def reschedule(
        self,
        task_instance_id: TaskInstanceId | SchedulableInstance[Any],
        new_execution_time: datetime | None = None,
        new_data: Any = None,
    ) -> bool:
        """Move an existing execution, and reset its failure history.

        The reset is deliberate: a caller rescheduling by hand is saying "try this again as if
        new", and leaving five failures behind would have the backoff handler wait an hour
        before the run they just asked for.
        """
        if isinstance(task_instance_id, SchedulableInstance):
            schedulable = task_instance_id
            task_instance_id = schedulable.task_instance.to_id()
            new_execution_time = schedulable.next_execution_time(self._clock.now())
            new_data = schedulable.task_instance.data
        if new_execution_time is None:
            raise TypeError("a new execution time is required")

        execution = self._require_not_executing(task_instance_id)

        reschedule_update = (
            RescheduleUpdate.to_execution_time(new_execution_time)
            .last_success(None)
            .last_failure(None)
            .consecutive_failures(0)
        )
        if new_data is not None:
            reschedule_update = reschedule_update.data(new_data)

        success: bool = self.task_repository.reschedule(execution, reschedule_update.build())
        if success:
            self._scheduler_listeners.on_execution_scheduled(
                task_instance_id, new_execution_time
            )
        else:
            LOG.warning("Failed to reschedule task instance: %s", task_instance_id)
        return success

    def cancel(self, task_instance_id: TaskInstanceId) -> None:
        self.task_repository.remove(self._require_not_executing(task_instance_id))

    def _require_not_executing(self, task_instance_id: TaskInstanceId) -> Execution:
        """The row, if it exists and nobody is running it.

        Both refusals matter. Changing a row that does not exist would silently do nothing;
        changing one that is running would be overwritten moments later by the instance running
        it, which finishes and writes its own outcome.
        """
        task_name, instance_id = task_instance_id.task_name, task_instance_id.id
        execution = self.task_repository.get_execution(task_name, instance_id)
        if execution is None:
            raise TaskInstanceNotFoundException(task_name, instance_id)
        if execution.picked:
            raise TaskInstanceCurrentlyExecutingException(task_name, instance_id)
        return execution  # type: ignore[no-any-return]

    # ------------------------------------------------------------------ reading

    def get_scheduled_execution(
        self, task_instance_id: TaskInstanceId
    ) -> ScheduledExecution[Any] | None:
        execution = self.task_repository.get_execution(
            task_instance_id.task_name, task_instance_id.id
        )
        return None if execution is None else ScheduledExecution[Any](None, execution)

    def fetch_scheduled_executions(
        self,
        consumer: Callable[[ScheduledExecution[Any]], None],
        filter: ScheduledExecutionsFilter | None = None,
    ) -> None:
        """Hand each one to `consumer` rather than building a list.

        For a table with a million scheduled rows, which is what an export or a migration
        looks at.
        """
        for execution in self.task_repository.get_scheduled_executions(
            filter or ScheduledExecutionsFilter.all().with_picked(False)
        ):
            consumer(ScheduledExecution[Any](None, execution))

    def get_scheduled_executions(
        self, filter: ScheduledExecutionsFilter | None = None
    ) -> list[ScheduledExecution[Any]]:
        found: list[ScheduledExecution[Any]] = []
        self.fetch_scheduled_executions(found.append, filter)
        return found

    def fetch_scheduled_executions_for_task(
        self,
        task_name: str,
        consumer: Callable[[ScheduledExecution[Any]], None],
        data_class: type[Any] | None = None,
        filter: ScheduledExecutionsFilter | None = None,
    ) -> None:
        for execution in self.task_repository.get_scheduled_executions(
            filter or ScheduledExecutionsFilter.all().with_picked(False), task_name
        ):
            consumer(ScheduledExecution[Any](data_class, execution))

    def get_scheduled_executions_for_task(
        self,
        task_name: str,
        data_class: type[Any] | None = None,
        filter: ScheduledExecutionsFilter | None = None,
    ) -> list[ScheduledExecution[Any]]:
        found: list[ScheduledExecution[Any]] = []
        self.fetch_scheduled_executions_for_task(task_name, found.append, data_class, filter)
        return found

    def get_scheduled_executions_summary_by_task(self) -> list[TaskSummary]:
        summaries: list[TaskSummary] = (
            self.task_repository.get_scheduled_executions_summary_by_task()
        )
        return summaries

    def __repr__(self) -> str:
        return f"StandardSchedulerClient({self.task_repository!r})"


@dataclass(frozen=True, slots=True)
class ClientEventContext:
    """What a client did, to which instance, and for when."""

    event_type: ClientEvent.EventType
    task_instance_id: TaskInstanceId
    execution_time: datetime


class ClientEvent:
    """A scheduling event, as its own value.

    Nothing in db-scheduler publishes one - `SchedulerListener.onExecutionScheduled` is what
    actually fires - and it is ported for the same reason it still exists there: it is public
    API, so somebody's code may name it.
    """

    __slots__ = ("_context",)

    class EventType(enum.Enum):
        SCHEDULE = "SCHEDULE"
        RESCHEDULE = "RESCHEDULE"
        CANCEL = "CANCEL"

    def __init__(self, context: ClientEventContext) -> None:
        self._context = context

    @property
    def context(self) -> ClientEventContext:
        return self._context

    def __repr__(self) -> str:
        return f"ClientEvent({self._context})"


class SchedulerClientBuilder:
    """Java's ``SchedulerClient.Builder``, setter for setter. Obtain via
    :meth:`SchedulerClient.builder`; every setter returns the builder, as Java's do."""

    __slots__ = (
        "_engine",
        "_jdbc_customization",
        "_known_tasks",
        "_priority",
        "_serializer",
        "_table_name",
    )

    def __init__(self, engine: Engine, known_tasks: Iterable[Any] = ()) -> None:
        self._engine = engine
        self._known_tasks: list[Any] = list(known_tasks)
        self._serializer: Serializer | None = None
        self._table_name: str | None = None
        self._jdbc_customization: JdbcCustomization | None = None
        self._priority = False

    def serializer(self, serializer: Serializer) -> Self:
        self._serializer = serializer
        return self

    def table_name(self, table_name: str) -> Self:
        self._table_name = table_name
        return self

    def enable_priority(self) -> Self:
        """Makes ``get_scheduled_executions`` return executions in priority order, as Java's."""
        self._priority = True
        return self

    def jdbc_customization(self, jdbc_customization: JdbcCustomization) -> Self:
        self._jdbc_customization = jdbc_customization
        return self

    def build(self) -> StandardSchedulerClient:
        """Java's ``build()``: a system clock, a resolver over the known tasks, the dialect's
        customization unless one was given, and a repository named ``SchedulerClient`` - a name
        a client never writes into ``picked_by``, since it picks nothing."""
        from .jdbc import JdbcTaskRepository, autodetect
        from .resolver import TaskResolver

        clock = SystemClock()
        customization = self._jdbc_customization or autodetect(
            self._engine.dialect.name,
            getattr(self._engine.dialect, "server_version_info", None),
            False,
        )
        repository = JdbcTaskRepository(
            self._engine,
            scheduler_name=SchedulerClientName().name,
            serializer=self._serializer,
            table_name=self._table_name,
            clock=clock,
            customization=customization,
            order_by_priority=self._priority,
            persist_timestamp_in_utc=customization.persist_timestamp_in_utc,
            task_resolver=TaskResolver(SchedulerListeners(), clock, self._known_tasks),
        )
        return StandardSchedulerClient(repository, clock=clock)
