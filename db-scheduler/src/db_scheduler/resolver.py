"""`TaskResolver` and `Resolvable` - turning a task *name* in a row back into a task.

The table stores a name, not a class. Every poll therefore has to look the name up, and the
interesting case is when it is not found: an execution for a task this instance does not know
about. That happens for good reasons - a rolling deploy where half the cluster has the new task,
or a service that owns a task another service schedules - so it must not be an error.

What db-scheduler does instead is remember the name, so the poll can *exclude* it from the next
query rather than fetching the same unrunnable rows for ever. That is what `unresolved` is for,
and why the exclusion filter is the point of this class rather than a side note.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .clock import Clock
from .event import SchedulerEventType, SchedulerListeners
from .task import Execution, HasTaskName
from .task.helper import Task

__all__ = ["Resolvable", "SimpleResolvable", "TaskResolver", "UnresolvedTask"]

LOG = logging.getLogger("db_scheduler.resolver")


class Resolvable(HasTaskName, ABC):
    """Something that names a task and says when it was meant to run.

    The execution time is not decoration: it is what an unresolved name is remembered *from*, so
    that "how long has this been unresolvable" can be answered.
    """

    __slots__ = ()

    @property
    @abstractmethod
    def execution_time(self) -> datetime: ...

    # Java interfaces do not inherit each other's static methods, so `Resolvable.of` and
    # `HasTaskName.of` coexist there. Python does inherit them, so this one shadows the base's -
    # which is what the Java code means, just said less politely.
    @staticmethod
    def of(task_name: str, execution_time: datetime) -> SimpleResolvable:  # type: ignore[override]
        return SimpleResolvable(task_name, execution_time)


class SimpleResolvable(Resolvable):
    """A name and a time, and nothing else. What `Resolvable.of` hands back."""

    __slots__ = ("_execution_time", "_task_name")

    def __init__(self, task_name: str, execution_time: datetime) -> None:
        self._task_name = task_name
        self._execution_time = execution_time

    @property
    def task_name(self) -> str:
        return self._task_name

    @property
    def execution_time(self) -> datetime:
        return self._execution_time

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SimpleResolvable):
            return NotImplemented
        return (
            self._task_name == other._task_name
            and self._execution_time == other._execution_time
        )

    def __hash__(self) -> int:
        return hash((self._task_name, self._execution_time))

    def __repr__(self) -> str:
        return f"SimpleResolvable({self._task_name!r}, {self._execution_time.isoformat()})"


# `Execution` is a `Resolvable` in Java - it is the thing the poll actually resolves. It is
# declared so from here rather than from the model, which must not import the resolver.
Resolvable.register(Execution)


@dataclass(frozen=True, slots=True)
class UnresolvedTask:
    """A task name seen in the table that this instance has no task for."""

    task_name: str
    first_unresolved: datetime


class TaskResolver:
    """The known tasks, by name, plus the names that turned out not to be known."""

    __slots__ = ("_clock", "_scheduler_listeners", "_task_map", "_unresolved_tasks")

    def __init__(
        self,
        scheduler_listeners: SchedulerListeners,
        clock: Clock,
        known_tasks: Iterable[Task[Any]] = (),
    ) -> None:
        self._scheduler_listeners = scheduler_listeners
        self._clock = clock
        self._task_map: dict[str, Task[Any]] = {task.name: task for task in known_tasks}
        self._unresolved_tasks: dict[str, UnresolvedTask] = {}

    def resolve(
        self, resolvable: Resolvable, add_unresolved_to_exclusion_filter: bool = True
    ) -> Task[Any] | None:
        task_name = resolvable.task_name
        task = self._task_map.get(task_name)
        if task is None and add_unresolved_to_exclusion_filter:
            self._add_unresolved(task_name, resolvable.execution_time)
            self._scheduler_listeners.on_scheduler_event(SchedulerEventType.UNRESOLVED_TASK)
            LOG.info(
                "Found execution with unknown task-name '%s'. Adding it to the list of known "
                "unresolved task-names.",
                task_name,
            )
        return task

    def _add_unresolved(self, task_name: str, execution_time: datetime) -> None:
        # First one wins: `first_unresolved` is what `unresolved_task_names` measures against,
        # so a later sighting must not reset the clock on a name that has been broken for hours.
        self._unresolved_tasks.setdefault(
            task_name, UnresolvedTask(task_name, execution_time)
        )

    def add_task(self, task: Task[Any]) -> None:
        self._task_map[task.name] = task

    @property
    def unresolved(self) -> list[UnresolvedTask]:
        return list(self._unresolved_tasks.values())

    def unresolved_task_names(self, unresolved_for: timedelta) -> list[str]:
        """The names unresolvable for longer than this - what the poll excludes.

        A grace period, not an immediate exclusion: during a rolling deploy a name is briefly
        unknown to half the cluster and then becomes known, and excluding it at once would keep
        the new task's first executions waiting.
        """
        now = self._clock.now()
        return [
            unresolved.task_name
            for unresolved in self._unresolved_tasks.values()
            if now - unresolved.first_unresolved > unresolved_for
        ]

    def is_unresolved(self, task_name: str) -> bool:
        return task_name in self._unresolved_tasks

    def clear_unresolved(self, task_name: str) -> None:
        self._unresolved_tasks.pop(task_name, None)

    @property
    def known_task_names(self) -> Sequence[str]:
        return list(self._task_map)

    def __repr__(self) -> str:
        return (
            f"TaskResolver(known={len(self._task_map)}, "
            f"unresolved={len(self._unresolved_tasks)})"
        )
