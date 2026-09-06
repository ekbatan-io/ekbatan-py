"""Counters, for whoever is watching.

A port of `com.github.kagkarlsson.scheduler.stats`. The registry is the older of the two
observation points - `SchedulerListener` is the richer one - and `StatsRegistryAdapter` is the
bridge that lets a registry be plugged in as a listener, exactly as it does in Java.

`MicrometerStatsRegistry` is not ported: Micrometer is a Java metrics library, and the Python
equivalent would be somebody's choice of prometheus_client or OpenTelemetry, not ours.
"""

from __future__ import annotations

import enum
from typing import Protocol, runtime_checkable

from ..task import ExecutionComplete

__all__ = [
    "CandidateStatsEvent",
    "DefaultStatsRegistry",
    "ExecutionStatsEvent",
    "SchedulerStatsEvent",
    "StatsRegistry",
]


class SchedulerStatsEvent(enum.Enum):
    """Something the scheduler itself did, or failed to do."""

    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"
    FAILED_HEARTBEAT = "FAILED_HEARTBEAT"
    COMPLETIONHANDLER_ERROR = "COMPLETIONHANDLER_ERROR"
    FAILUREHANDLER_ERROR = "FAILUREHANDLER_ERROR"
    DEAD_EXECUTION = "DEAD_EXECUTION"
    RAN_UPDATE_HEARTBEATS = "RAN_UPDATE_HEARTBEATS"
    RAN_DETECT_DEAD = "RAN_DETECT_DEAD"
    RAN_EXECUTE_DUE = "RAN_EXECUTE_DUE"
    FAILED_MULTIPLE_HEARTBEATS = "FAILED_MULTIPLE_HEARTBEATS"
    UNRESOLVED_TASK = "UNRESOLVED_TASK"


class CandidateStatsEvent(enum.Enum):
    """What became of a row the poll picked up.

    `ALREADY_PICKED` climbing means instances are racing for the same rows - the sign to look at
    the polling strategy before adding more of them.
    """

    STALE = "STALE"
    ALREADY_PICKED = "ALREADY_PICKED"
    EXECUTED = "EXECUTED"


class ExecutionStatsEvent(enum.Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@runtime_checkable
class StatsRegistry(Protocol):
    """Where counts go. The default counts nothing."""

    def register(
        self, event: SchedulerStatsEvent | CandidateStatsEvent | ExecutionStatsEvent
    ) -> None: ...

    def register_single_completed_execution(self, completion: ExecutionComplete) -> None: ...


class DefaultStatsRegistry:
    """Counts nothing. `StatsRegistry.NOOP` in Java."""

    __slots__ = ()

    def register(
        self, event: SchedulerStatsEvent | CandidateStatsEvent | ExecutionStatsEvent
    ) -> None:
        """Nothing."""

    def register_single_completed_execution(self, completion: ExecutionComplete) -> None:
        """Nothing."""

    def __repr__(self) -> str:
        return "DefaultStatsRegistry()"


#: What Java spells `StatsRegistry.NOOP`.
NOOP = DefaultStatsRegistry()
