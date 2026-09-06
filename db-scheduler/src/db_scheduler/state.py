"""What the scheduler is doing, and what a running execution knows about itself.

Four small pieces that the executor and the client both read:

`SchedulerState` - started, paused, shutting down.
`HeartbeatConfig` - how often to say "still working", and how long silence means dead.
`HeartbeatState` - whether *this* execution has been saying it, and how close to dead it is.
`CurrentlyExecuting` - one running execution, with its heartbeat state and how long it has run.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from .clock import Clock
from .task import Execution, TaskInstance

__all__ = [
    "CurrentlyExecuting",
    "HeartbeatConfig",
    "HeartbeatState",
    "SchedulerState",
    "SettableSchedulerState",
]

LOG = logging.getLogger("db_scheduler.state")

#: A heartbeat is late once a quarter of its interval has passed on top of it - capped, because
#: on a ten-minute interval a quarter would be two and a half minutes of unnecessary patience.
STALE_GRACE_FRACTION = 0.25
STALE_GRACE_CAP = timedelta(seconds=10)


class SchedulerState:
    """Started, paused, shutting down. Read from several threads."""

    __slots__ = ()

    @property
    def is_shutting_down(self) -> bool:
        raise NotImplementedError

    @property
    def is_started(self) -> bool:
        raise NotImplementedError

    @property
    def is_paused(self) -> bool:
        raise NotImplementedError


class SettableSchedulerState(SchedulerState):
    """The implementation the scheduler owns.

    Pausing is guarded by a lock rather than a bare assignment so that the log line fires once:
    two threads pausing at the same moment should not both announce it.
    """

    __slots__ = ("_is_paused", "_is_shutting_down", "_is_started", "_lock")

    def __init__(self) -> None:
        self._is_shutting_down = False
        self._is_started = False
        self._is_paused = False
        self._lock = threading.Lock()

    @property
    def is_shutting_down(self) -> bool:
        return self._is_shutting_down

    @property
    def is_started(self) -> bool:
        return self._is_started

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    def set_is_shutting_down(self) -> None:
        self._is_shutting_down = True

    def set_started(self) -> None:
        self._is_started = True

    def set_paused(self, is_paused: bool) -> None:
        with self._lock:
            changed = self._is_paused != is_paused
            self._is_paused = is_paused
        if changed:
            LOG.info("Scheduler is paused." if is_paused else "Scheduler is resumed.")

    def __repr__(self) -> str:
        return (
            f"SettableSchedulerState(started={self._is_started}, paused={self._is_paused}, "
            f"shutting_down={self._is_shutting_down})"
        )


@dataclass(frozen=True, slots=True)
class HeartbeatConfig:
    """How often an execution says it is still working, and when silence means dead.

    The three are related: `max_age_before_considered_dead` should be several heartbeat
    intervals, or a single slow write declares a healthy execution dead and a second instance
    starts running it alongside the first.
    """

    heartbeat_interval: timedelta
    missed_heartbeats_limit: int
    max_age_before_considered_dead: timedelta


class HeartbeatState:
    """Whether this execution has been managing to say it is alive.

    `fraction_dead` is what a long-running handler can poll to decide whether to give up: at 1.0
    another instance is entitled to take the row over, and continuing past that means two
    instances running the same work.
    """

    __slots__ = (
        "_clock",
        "_config",
        "_failures_since_last_success",
        "_last_failure",
        "_last_success",
        "_start_time",
        "_successes_since_last_failure",
    )

    def __init__(self, clock: Clock, start_time: datetime, config: HeartbeatConfig) -> None:
        self._clock = clock
        self._start_time = start_time
        self._config = config
        self._successes_since_last_failure = 0
        self._failures_since_last_success = 0
        self._last_success: datetime = start_time
        self._last_failure: datetime | None = None

    @property
    def has_stale_heartbeat(self) -> bool:
        since_last_success = self._clock.now() - self._last_success
        interval = self._config.heartbeat_interval
        grace = min(STALE_GRACE_CAP, interval * STALE_GRACE_FRACTION)
        return self._failures_since_last_success > 0 or since_last_success > interval + grace

    @property
    def fraction_dead(self) -> float:
        """How far through the dead-execution window this one is. 1.0 means takeable."""
        since_last_success = self._clock.now() - self._last_success
        return (
            since_last_success.total_seconds()
            / self._config.max_age_before_considered_dead.total_seconds()
        )

    @property
    def failed_heartbeats(self) -> int:
        return self._failures_since_last_success

    def heartbeat(self, successful: bool, last_heartbeat_attempt: datetime) -> None:
        if successful:
            self._last_success = last_heartbeat_attempt
            self._successes_since_last_failure += 1
            self._failures_since_last_success = 0
        else:
            self._last_failure = last_heartbeat_attempt
            self._successes_since_last_failure = 0
            self._failures_since_last_success += 1

    def describe(self) -> str:
        return (
            "HeartbeatState{"
            f"successesSinceLastFailure={self._successes_since_last_failure}, "
            f"failuresSinceLastSuccess={self._failures_since_last_success}, "
            f"lastSuccess={self._last_success}, "
            f"lastFailure={self._last_failure}, "
            f"missedHeartbeatsLimit={self._config.missed_heartbeats_limit}"
            "}"
        )

    def __repr__(self) -> str:
        return self.describe()


class CurrentlyExecuting:
    """One execution, running right now on this instance."""

    __slots__ = ("_clock", "_execution", "_heartbeat_state", "_start_time")

    def __init__(
        self, execution: Execution, clock: Clock, heartbeat_config: HeartbeatConfig
    ) -> None:
        self._execution = execution
        self._clock = clock
        self._start_time = clock.now()
        self._heartbeat_state = HeartbeatState(clock, self._start_time, heartbeat_config)

    @property
    def duration(self) -> timedelta:
        return self._clock.now() - self._start_time

    @property
    def start_time(self) -> datetime:
        return self._start_time

    @property
    def task_instance(self) -> TaskInstance[object]:
        return self._execution.task_instance

    @property
    def execution(self) -> Execution:
        return self._execution

    @property
    def heartbeat_state(self) -> HeartbeatState:
        return self._heartbeat_state

    def heartbeat(self, successful: bool, now: datetime) -> None:
        self._heartbeat_state.heartbeat(successful, now)

    def __repr__(self) -> str:
        return f"CurrentlyExecuting({self._execution!r}, duration={self.duration})"
