"""`ExecutionOperations` - the four things a completion or failure handler may do to a row.

Handlers do not touch the repository directly. They are handed this, and it is deliberately
narrow: remove, reschedule, or replace with something else. There is no "update the data and
leave the time alone", because a row that stays where it is is a row nobody will pick up again.

Every reschedule also *tells* the scheduler it happened. That hint is what lets a scheduler that
has just gone to sleep wake up for work it created itself, instead of waiting out the poll
interval.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..clock import Clock
from ..event import SchedulerListeners
from . import (
    Execution,
    ExecutionComplete,
    Result,
    SchedulableInstance,
    ScheduledTaskInstance,
    TaskInstanceId,
)

__all__ = ["ExecutionOperations"]


class _Unset:
    """Distinguishes "no new data" from "new data which happens to be None"."""

    __slots__ = ()


_UNSET = _Unset()


class ExecutionOperations[T = Any]:
    """What a handler may do to the execution it was called about."""

    __slots__ = ("_clock", "_execution", "_scheduler_listeners", "_task_repository")

    def __init__(
        self,
        task_repository: Any,
        scheduler_listeners: SchedulerListeners,
        execution: Execution,
        clock: Clock,
    ) -> None:
        self._task_repository = task_repository
        self._scheduler_listeners = scheduler_listeners
        self._execution = execution
        self._clock = clock

    def stop(self) -> None:
        """Done for good. The same thing as `remove` - the row is what would run it again."""
        self.remove()

    def remove(self) -> None:
        self._task_repository.remove(self._execution)

    def remove_and_schedule_new(self, schedulable_instance: SchedulableInstance[Any]) -> None:
        """Hand off to a different task in one statement.

        A replace rather than a delete and an insert, so a crash in between cannot lose the
        work: the row either names the old task or the new one, never neither.
        """
        execution_time = self._task_repository.replace(
            self._execution,
            ScheduledTaskInstance.fix_execution_time(schedulable_instance, self._clock),
        )
        self._hint_execution_scheduled(
            schedulable_instance.task_instance.to_id(), execution_time
        )

    def reschedule(
        self,
        completed: ExecutionComplete,
        next_execution_time: datetime,
        new_data: T | _Unset = _UNSET,
    ) -> None:
        """Move the row to its next time, carrying the outcome of this run into the columns.

        A success resets `consecutive_failures` to zero and leaves `last_failure` where it was;
        a failure increments the count and leaves `last_success` alone. Both are what the
        backoff handlers and the failing-task report read, so getting the pair the wrong way
        round would make a task that fails every other run look permanently healthy.
        """
        succeeded = completed.result is Result.OK
        last_success = completed.time_done if succeeded else self._execution.last_success
        last_failure = self._execution.last_failure if succeeded else completed.time_done
        consecutive_failures = 0 if succeeded else self._execution.consecutive_failures + 1

        if isinstance(new_data, _Unset):
            self._task_repository.reschedule(
                self._execution,
                next_execution_time,
                last_success=last_success,
                last_failure=last_failure,
                consecutive_failures=consecutive_failures,
            )
        else:
            self._task_repository.reschedule(
                self._execution,
                next_execution_time,
                last_success=last_success,
                last_failure=last_failure,
                consecutive_failures=consecutive_failures,
                new_data=new_data,
            )
        self._hint_execution_scheduled(
            completed.execution.task_instance.to_id(), next_execution_time
        )

    def _hint_execution_scheduled(
        self, task_instance_id: TaskInstanceId, next_execution_time: datetime
    ) -> None:
        # A hint that a new execution exists, so a scheduler that has just gone to sleep can
        # wake for work it created itself rather than waiting out the poll interval.
        self._scheduler_listeners.on_execution_scheduled(
            task_instance_id, next_execution_time
        )

    def __repr__(self) -> str:
        return f"ExecutionOperations({self._execution!r})"

