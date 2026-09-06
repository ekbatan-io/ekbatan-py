"""Being told what the scheduler is doing.

A port of `com.github.kagkarlsson.scheduler.event`. Two things live here:

`SchedulerListener` is the observation point - scheduled, started, completed, dead, failed
heartbeat, plus two internal event streams that db-scheduler's own tests assert on. It runs *in
the executing thread*, which is why the docstring on every method says not to do I/O in one: a
listener that blocks blocks the execution it is reporting.

`ExecutionInterceptor` is the other kind - it wraps the execution rather than watching it, so it
can set up a context, time the body, or decide the completion handler.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, ClassVar, Protocol, runtime_checkable

from ..stats import CandidateStatsEvent, SchedulerStatsEvent
from ..task import Execution, ExecutionComplete, ExecutionContext, TaskInstance, TaskInstanceId
from ..task.handlers import CompletionHandler, ExecutionHandler

__all__ = [
    "NOOP",
    "AbstractSchedulerListener",
    "CandidateEventType",
    "ExecutionChain",
    "ExecutionInterceptor",
    "SchedulerEventType",
    "SchedulerListener",
    "SchedulerListeners",
]

LOG = logging.getLogger("db_scheduler.event")


class SchedulerEventType(enum.Enum):
    """An internal scheduler event. Primarily intended for testing.

    Each maps onto a `SchedulerStatsEvent`, which is how a `StatsRegistry` plugged in as a
    listener gets its counts.
    """

    UNEXPECTED_ERROR = SchedulerStatsEvent.UNEXPECTED_ERROR
    FAILED_HEARTBEAT = SchedulerStatsEvent.FAILED_HEARTBEAT
    COMPLETIONHANDLER_ERROR = SchedulerStatsEvent.COMPLETIONHANDLER_ERROR
    FAILUREHANDLER_ERROR = SchedulerStatsEvent.FAILUREHANDLER_ERROR
    DEAD_EXECUTION = SchedulerStatsEvent.DEAD_EXECUTION
    RAN_UPDATE_HEARTBEATS = SchedulerStatsEvent.RAN_UPDATE_HEARTBEATS
    RAN_DETECT_DEAD = SchedulerStatsEvent.RAN_DETECT_DEAD
    RAN_EXECUTE_DUE = SchedulerStatsEvent.RAN_EXECUTE_DUE
    FAILED_MULTIPLE_HEARTBEATS = SchedulerStatsEvent.FAILED_MULTIPLE_HEARTBEATS
    UNRESOLVED_TASK = SchedulerStatsEvent.UNRESOLVED_TASK

    def to_stats_registry_event(self) -> SchedulerStatsEvent:
        return self.value


class CandidateEventType(enum.Enum):
    """What became of a row a poll offered up."""

    STALE = CandidateStatsEvent.STALE
    ALREADY_PICKED = CandidateStatsEvent.ALREADY_PICKED
    EXECUTED = CandidateStatsEvent.EXECUTED

    def to_stats_registry_event(self) -> CandidateStatsEvent:
        return self.value


@runtime_checkable
class SchedulerListener(Protocol):
    """Told what the scheduler is doing, as it does it.

    Runs in the same thread as the execution, so a method here must not do I/O or anything else
    slow - it would hold up the very execution it is reporting on.
    """

    def on_execution_scheduled(
        self, task_instance_id: TaskInstanceId, execution_time: datetime
    ) -> None: ...

    def on_execution_start(self, currently_executing: Any) -> None: ...

    def on_execution_complete(self, execution_complete: ExecutionComplete) -> None: ...

    def on_execution_dead(self, execution: Execution) -> None: ...

    def on_execution_failed_heartbeat(self, currently_executing: Any) -> None: ...

    def on_scheduler_event(self, event_type: SchedulerEventType) -> None: ...

    def on_candidate_event(self, event_type: CandidateEventType) -> None: ...


class AbstractSchedulerListener:
    """Every method, doing nothing. Override the ones you care about."""

    __slots__ = ()

    def on_execution_scheduled(
        self, task_instance_id: TaskInstanceId, execution_time: datetime
    ) -> None:
        """Nothing."""

    def on_execution_start(self, currently_executing: Any) -> None:
        """Nothing."""

    def on_execution_complete(self, execution_complete: ExecutionComplete) -> None:
        """Nothing."""

    def on_execution_dead(self, execution: Execution) -> None:
        """Nothing."""

    def on_execution_failed_heartbeat(self, currently_executing: Any) -> None:
        """Nothing."""

    def on_scheduler_event(self, event_type: SchedulerEventType) -> None:
        """Nothing."""

    def on_candidate_event(self, event_type: CandidateEventType) -> None:
        """Nothing."""


#: What Java spells `SchedulerListener.NOOP`.
NOOP = AbstractSchedulerListener()


class SchedulerListeners(AbstractSchedulerListener):
    """Several listeners behaving as one.

    A listener that raises is logged and skipped, never propagated: third-party observation code
    must not be able to fail an execution, or take the scheduler down with it.
    """

    __slots__ = ("_listeners",)

    #: What Java spells `SchedulerListeners.NOOP`. Bound just after the class body.
    NOOP: ClassVar[SchedulerListeners]

    def __init__(self, listeners: Sequence[SchedulerListener] | None = None) -> None:
        self._listeners: list[SchedulerListener] = list(listeners or ())

    def add(self, listener: SchedulerListener) -> None:
        self._listeners.append(listener)

    def on_execution_scheduled(
        self, task_instance_id: TaskInstanceId, execution_time: datetime
    ) -> None:
        for listener in self._listeners:
            self.fire_and_log_errors(
                listener,
                "on_execution_scheduled",
                lambda listener=listener: listener.on_execution_scheduled(  # type: ignore[misc]
                    task_instance_id, execution_time
                ),
            )

    def on_execution_start(self, currently_executing: Any) -> None:
        for listener in self._listeners:
            self.fire_and_log_errors(
                listener,
                "on_execution_start",
                lambda listener=listener: listener.on_execution_start(currently_executing),  # type: ignore[misc]
            )

    def on_execution_complete(self, execution_complete: ExecutionComplete) -> None:
        for listener in self._listeners:
            self.fire_and_log_errors(
                listener,
                "on_execution_complete",
                lambda listener=listener: listener.on_execution_complete(execution_complete),  # type: ignore[misc]
            )

    def on_execution_dead(self, execution: Execution) -> None:
        for listener in self._listeners:
            self.fire_and_log_errors(
                listener,
                "on_execution_dead",
                lambda listener=listener: listener.on_execution_dead(execution),  # type: ignore[misc]
            )

    def on_execution_failed_heartbeat(self, currently_executing: Any) -> None:
        for listener in self._listeners:
            self.fire_and_log_errors(
                listener,
                "on_execution_failed_heartbeat",
                lambda listener=listener: listener.on_execution_failed_heartbeat(  # type: ignore[misc]
                    currently_executing
                ),
            )

    def on_scheduler_event(self, event_type: SchedulerEventType) -> None:
        for listener in self._listeners:
            self.fire_and_log_errors(
                listener,
                "on_scheduler_event",
                lambda listener=listener: listener.on_scheduler_event(event_type),  # type: ignore[misc]
            )

    def on_candidate_event(self, event_type: CandidateEventType) -> None:
        for listener in self._listeners:
            self.fire_and_log_errors(
                listener,
                "on_candidate_event",
                lambda listener=listener: listener.on_candidate_event(event_type),  # type: ignore[misc]
            )

    def fire_and_log_errors(
        self, listener: SchedulerListener, method: str, call: Callable[[], None]
    ) -> None:
        try:
            call()
        except BaseException:
            LOG.warning(
                "Listener '%s' method '%s' threw an unexpected Exception",
                type(listener).__name__,
                method,
                exc_info=True,
            )

    def __repr__(self) -> str:
        return f"SchedulerListeners({len(self._listeners)} listeners)"


SchedulerListeners.NOOP = SchedulerListeners()


@runtime_checkable
class ExecutionInterceptor(Protocol):
    """Wraps an execution rather than watching it.

    The difference from a listener: this one is *in* the call, so it can put something around the
    body - a context, a timer, a retry of its own - and it decides whether to proceed at all.
    """

    def execute(
        self,
        task_instance: TaskInstance[Any],
        execution_context: ExecutionContext,
        chain: ExecutionChain,
    ) -> CompletionHandler[Any]: ...


class ExecutionChain:
    """The interceptors left to run, and the body at the end of them."""

    __slots__ = ("_execution_handler", "_interceptors")

    def __init__(
        self,
        interceptors: Sequence[ExecutionInterceptor],
        execution_handler: ExecutionHandler[Any],
    ) -> None:
        self._interceptors = list(interceptors)
        self._execution_handler = execution_handler

    def proceed(
        self, task_instance: TaskInstance[Any], execution_context: ExecutionContext
    ) -> CompletionHandler[Any]:
        if not self._interceptors:
            return self._execution_handler(task_instance, execution_context)
        next_interceptor, *rest = self._interceptors
        return next_interceptor.execute(
            task_instance,
            execution_context,
            ExecutionChain(rest, self._execution_handler),
        )

    def __repr__(self) -> str:
        return f"ExecutionChain({len(self._interceptors)} interceptors)"
