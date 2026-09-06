"""What to do when a run finishes, fails, or is found dead.

A port of `CompletionHandler`, `FailureHandler` and `DeadExecutionHandler`. Each is a Protocol
rather than an interface, so an existing object can be a handler without inheriting anything -
and each ships the same named implementations db-scheduler does, because those names appear in
application code and should survive the crossing.

Every handler acts through `ExecutionOperations`. That seam is the reason a handler is testable
without a database, and the reason a handler cannot accidentally do something the scheduler has
not sanctioned: the four verbs are all it gets.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from . import ExecutionComplete, Result
from .schedule import Schedule

__all__ = [
    "CancelDeadExecution",
    "CompletionHandler",
    "DeadExecutionHandler",
    "ExecutionOperationsLike",
    "ExponentialBackoffFailureHandler",
    "FailureHandler",
    "MaxRetriesBuilder",
    "MaxRetriesExceededListener",
    "OnCompleteRemove",
    "OnCompleteReplace",
    "OnCompleteReschedule",
    "OnFailureReschedule",
    "OnFailureRetryLater",
    "ReviveDeadExecution",
    "max_retries",
]

LOG = logging.getLogger("db_scheduler.task")

#: db-scheduler's own default. Each retry waits half again as long as the last.
DEFAULT_MULTIPLIER = 1.5


@runtime_checkable
class ExecutionOperationsLike(Protocol):
    """The four things a handler may do to an execution."""

    def stop(self) -> None: ...
    def remove(self) -> None: ...
    def reschedule(
        self, completion: ExecutionComplete, next_time: datetime, new_data: Any = None
    ) -> None: ...
    def remove_and_schedule_new(self, schedulable: Any) -> None: ...


@runtime_checkable
class CompletionHandler[T](Protocol):
    """What to do when a run finished, however it finished."""

    def complete(
        self, completion: ExecutionComplete, operations: ExecutionOperationsLike
    ) -> None: ...


@runtime_checkable
class FailureHandler[T](Protocol):
    """What to do when a run raised."""

    def on_failure(
        self, completion: ExecutionComplete, operations: ExecutionOperationsLike
    ) -> None: ...


@runtime_checkable
class DeadExecutionHandler[T](Protocol):
    """What to do about a run whose instance stopped answering."""

    def dead_execution(
        self,
        completion: ExecutionComplete,
        operations: ExecutionOperationsLike,
        *,
        now: datetime,
    ) -> None: ...


#: Told when a task has failed more times than it is allowed to.
MaxRetriesExceededListener = Callable[[ExecutionComplete], None]


# ------------------------------------------------------------------ completion


class OnCompleteRemove[T]:
    """Delete the row. What a one-time task does when it succeeds."""

    __slots__ = ()

    def complete(self, completion: ExecutionComplete, operations: ExecutionOperationsLike) -> None:
        operations.stop()

    def __repr__(self) -> str:
        return "OnCompleteRemove()"


class OnCompleteReschedule[T]:
    """Move the row to its next time. What a recurring task does."""

    __slots__ = ("_new_data", "_set_new_data", "schedule")

    def __init__(self, schedule: Schedule, new_data: T | None = None, **kwargs: Any) -> None:
        self.schedule = schedule
        # Distinguishing "no new data" from "new data which happens to be None" - passing None
        # deliberately has to be able to clear the data.
        self._set_new_data = "new_data" in kwargs or new_data is not None
        self._new_data = new_data

    def complete(self, completion: ExecutionComplete, operations: ExecutionOperationsLike) -> None:
        next_time = self.schedule.next_execution_time(completion)
        LOG.debug(
            "Rescheduling task %s to %s", completion.execution.task_instance, next_time.isoformat()
        )
        if self._set_new_data:
            operations.reschedule(completion, next_time, self._new_data)
        else:
            operations.reschedule(completion, next_time)

    def __repr__(self) -> str:
        return f"OnCompleteReschedule(schedule={self.schedule!r})"


class OnCompleteReplace[T, N]:
    """Delete this row and schedule a different task in its place - a hand-off between steps."""

    __slots__ = ("_new_data", "new_task_name")

    def __init__(self, new_task_name: str, new_data: N | None = None) -> None:
        self.new_task_name = new_task_name
        self._new_data = new_data

    def complete(self, completion: ExecutionComplete, operations: ExecutionOperationsLike) -> None:
        from . import TaskInstance

        current = completion.execution.task_instance
        following = TaskInstance[Any](self.new_task_name, current.id, self._new_data)
        LOG.debug("Replacing %s with task %s", current, self.new_task_name)
        operations.remove_and_schedule_new(following)

    def __repr__(self) -> str:
        return f"OnCompleteReplace(new_task_name={self.new_task_name!r})"


# ------------------------------------------------------------------ failure


class OnFailureRetryLater[T]:
    """Try again after a fixed delay."""

    __slots__ = ("sleep_duration",)

    def __init__(self, sleep_duration: timedelta) -> None:
        self.sleep_duration = sleep_duration

    def on_failure(
        self, completion: ExecutionComplete, operations: ExecutionOperationsLike
    ) -> None:
        next_try = completion.time_done + self.sleep_duration
        LOG.debug("Execution failed. Retrying task %s at %s", completion.execution.task_instance, next_try)
        operations.reschedule(completion, next_try)

    def __repr__(self) -> str:
        return f"OnFailureRetryLater(sleep_duration={self.sleep_duration})"


class OnFailureReschedule[T]:
    """Leave the schedule alone: a failed recurring run waits for its next scheduled time."""

    __slots__ = ("schedule",)

    def __init__(self, schedule: Schedule) -> None:
        self.schedule = schedule

    def on_failure(
        self, completion: ExecutionComplete, operations: ExecutionOperationsLike
    ) -> None:
        next_time = self.schedule.next_execution_time(completion)
        LOG.debug("Execution failed. Rescheduling task %s to %s", completion.execution.task_instance, next_time)
        operations.reschedule(completion, next_time)

    def __repr__(self) -> str:
        return f"OnFailureReschedule(schedule={self.schedule!r})"


class ExponentialBackoffFailureHandler[T]:
    """Wait longer after each failure: ``sleep * rate ** consecutive_failures``.

    The count is what had already failed before this run, so the first retry waits exactly
    ``sleep`` - which is the formula db-scheduler uses, and the reason the two ports retry on
    the same wall-clock schedule.
    """

    __slots__ = ("rate", "sleep_duration")

    def __init__(self, sleep_duration: timedelta, rate: float = DEFAULT_MULTIPLIER) -> None:
        self.sleep_duration = sleep_duration
        self.rate = rate

    def on_failure(
        self, completion: ExecutionComplete, operations: ExecutionOperationsLike
    ) -> None:
        failures = completion.execution.consecutive_failures
        millis = round(self.sleep_duration / timedelta(milliseconds=1) * (self.rate**failures))
        next_try = completion.time_done + timedelta(milliseconds=millis)
        LOG.debug(
            "Execution failed %d time(s). Retrying task %s at %s",
            failures,
            completion.execution.task_instance,
            next_try,
        )
        operations.reschedule(completion, next_try)

    def __repr__(self) -> str:
        return (
            f"ExponentialBackoffFailureHandler(sleep_duration={self.sleep_duration}, "
            f"rate={self.rate})"
        )


class _MaxRetriesHandler[T]:
    """Retries until the cap, then hands over to a terminal handler."""

    __slots__ = ("_listener", "_retry", "_terminal", "max_retries")

    def __init__(
        self,
        max_retries: int,
        retry: FailureHandler[T],
        terminal: Callable[[ExecutionComplete, ExecutionOperationsLike], None],
        listener: MaxRetriesExceededListener,
    ) -> None:
        self.max_retries = max_retries
        self._retry = retry
        self._terminal = terminal
        self._listener = listener

    def on_failure(
        self, completion: ExecutionComplete, operations: ExecutionOperationsLike
    ) -> None:
        total_failures = completion.execution.consecutive_failures + 1
        if total_failures > self.max_retries:
            self._terminal(completion, operations)
            self._listener(completion)
        else:
            self._retry.on_failure(completion, operations)

    def __repr__(self) -> str:
        return f"MaxRetries({self.max_retries}, then {self._terminal!r})"


class MaxRetriesBuilder[T]:
    """Builds a capped retry policy. Obtain via :func:`max_retries`."""

    __slots__ = ("_max_retries", "_retry")

    def __init__(self, max_retries: int) -> None:
        self._max_retries = max_retries
        self._retry: FailureHandler[T] | None = None

    def with_backoff(
        self, initial_delay: timedelta, multiplier: float = DEFAULT_MULTIPLIER
    ) -> MaxRetriesBuilder[T]:
        self._retry = ExponentialBackoffFailureHandler[T](initial_delay, multiplier)
        return self

    def retry_every(self, delay: timedelta) -> MaxRetriesBuilder[T]:
        self._retry = OnFailureRetryLater[T](delay)
        return self

    def then_remove(
        self, listener: MaxRetriesExceededListener | None = None
    ) -> FailureHandler[T]:
        """After the cap, delete the row."""

        def remove(completion: ExecutionComplete, operations: ExecutionOperationsLike) -> None:
            operations.remove()

        return self.then(remove, listener)

    def then(
        self,
        handler: Callable[[ExecutionComplete, ExecutionOperationsLike], None] | FailureHandler[T],
        listener: MaxRetriesExceededListener | None = None,
    ) -> FailureHandler[T]:
        """After the cap, hand over to ``handler``."""
        terminal = handler.on_failure if hasattr(handler, "on_failure") else handler
        return _MaxRetriesHandler[T](
            self._max_retries, self._require_retry(), terminal, listener or _log_max_retries
        )

    def _require_retry(self) -> FailureHandler[T]:
        if self._retry is None:
            # Otherwise every failure before the cap would silently do nothing, and the task
            # would sit picked until something else noticed.
            raise ValueError(
                "specify the retry behaviour - with_backoff(...) or retry_every(...) - before "
                "the terminal operation"
            )
        return self._retry


def max_retries[T](count: int) -> MaxRetriesBuilder[T]:
    """Start building a capped retry policy: ``max_retries(3).retry_every(...).then_remove()``."""
    return MaxRetriesBuilder[T](count)


def _log_max_retries(completion: ExecutionComplete) -> None:
    LOG.warning(
        "Execution has failed %d times. Cancelling execution. %s",
        completion.execution.consecutive_failures + 1,
        completion.execution,
    )


# ------------------------------------------------------------------ dead executions


class ReviveDeadExecution[T]:
    """Reschedule to now, so somebody picks it up again.

    The default, and the right one for work that is safe to repeat - which is what a
    distributed job is required to be. An instance that died holding a row would otherwise
    leave it picked forever.
    """

    __slots__ = ()

    def dead_execution(
        self,
        completion: ExecutionComplete,
        operations: ExecutionOperationsLike,
        *,
        now: datetime,
    ) -> None:
        LOG.info("Reviving dead execution: %s to %s", completion.execution, now.isoformat())
        operations.reschedule(completion, now)

    def __repr__(self) -> str:
        return "ReviveDeadExecution()"


class CancelDeadExecution[T]:
    """Delete the row instead. For work whose repeat would be worse than its loss."""

    __slots__ = ()

    def dead_execution(
        self,
        completion: ExecutionComplete,
        operations: ExecutionOperationsLike,
        *,
        now: datetime,
    ) -> None:
        LOG.warning("Cancelling dead execution: %s", completion.execution)
        operations.stop()

    def __repr__(self) -> str:
        return "CancelDeadExecution()"


_ = Result  # re-exported through this module for handler authors
