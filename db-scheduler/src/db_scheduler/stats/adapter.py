"""`StatsRegistryAdapter` - a `StatsRegistry` plugged in where a listener is expected."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..event import AbstractSchedulerListener, CandidateEventType, SchedulerEventType
from ..task import Execution, ExecutionComplete, Result, TaskInstanceId
from . import ExecutionStatsEvent, StatsRegistry

__all__ = ["StatsRegistryAdapter"]


class StatsRegistryAdapter(AbstractSchedulerListener):
    """Turns the events a listener sees into the counts a registry keeps.

    `None` is accepted and means "count nothing", because Java's constructor accepts a null
    registry and every method guards against it.
    """

    __slots__ = ("_stats_registry",)

    def __init__(self, stats_registry: StatsRegistry | None) -> None:
        self._stats_registry = stats_registry

    def on_execution_scheduled(
        self, task_instance_id: TaskInstanceId, execution_time: datetime
    ) -> None:
        """Not counted, as in Java."""

    def on_execution_start(self, currently_executing: Any) -> None:
        """Not counted, as in Java."""

    def on_execution_complete(self, execution_complete: ExecutionComplete) -> None:
        if self._stats_registry is None:
            return
        if execution_complete.result is Result.OK:
            self._stats_registry.register(ExecutionStatsEvent.COMPLETED)
        else:
            self._stats_registry.register(ExecutionStatsEvent.FAILED)
        self._stats_registry.register_single_completed_execution(execution_complete)

    def on_execution_dead(self, execution: Execution) -> None:
        """Not counted, as in Java."""

    def on_execution_failed_heartbeat(self, currently_executing: Any) -> None:
        """Not counted, as in Java."""

    def on_scheduler_event(self, event_type: SchedulerEventType) -> None:
        if self._stats_registry is None:
            return
        self._stats_registry.register(event_type.to_stats_registry_event())

    def on_candidate_event(self, event_type: CandidateEventType) -> None:
        if self._stats_registry is None:
            return
        self._stats_registry.register(event_type.to_stats_registry_event())

    def __repr__(self) -> str:
        return f"StatsRegistryAdapter({self._stats_registry!r})"
