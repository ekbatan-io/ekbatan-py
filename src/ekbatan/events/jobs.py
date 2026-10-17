"""The two event jobs, as jobs.

Fan-out turns committed events into per-handler notifications; handling delivers them. Both are
:class:`~ekbatan.distributedjobs.DistributedJob`, so a cluster runs each of them once per slot
rather than
once per instance - registered in ``scheduled_tasks``, claimed atomically, heartbeated, and
reclaimed if the instance holding one stops answering.

That last part is why they are jobs at all. Ten instances each draining the same notifications
would deliver every event ten times, and nothing in the tables would say which of them was
supposed to.

Both are safe to run twice, which is the requirement a distributed job places on its work: the
notification insert is guarded by a unique constraint, and a delivery whose outcome was never
recorded is retried rather than lost.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta
from typing import Self

from db_scheduler.task import ExecutionContext
from db_scheduler.task.schedule import DisabledSchedule, FixedDelay, Schedule
from sqlalchemy import Table

from ..clock import Clock, SystemClock
from ..distributedjobs import DistributedJob
from ..errors import ValidationError
from ..persistence import DatabaseRegistry
from .config import LocalEventHandlerConfig
from .dispatch import DEFAULT_MAX_BACKOFF, DEFAULT_RETENTION, EventDispatch
from .fanout import EventFanout
from .handler import EventHandlerRegistry

__all__ = ["EventFanoutJob", "EventHandlingJob"]

#: The names these jobs are known by across the cluster. They are primary keys in
#: `scheduled_tasks`, and they are the Java implementation's, so a mixed deployment coordinates
#: on one row per job rather than two that never see each other.
FANOUT_JOB_NAME = "ekbatan-event-fanout"
HANDLING_JOB_NAME = "ekbatan-event-handling"

DEFAULT_POLL_DELAY = timedelta(seconds=1)

#: The batch sizes live on the drainers - EventFanout and EventDispatch - and are read from
#: their signatures here, so a job and a drainer used directly cannot disagree about them.
DEFAULT_FANOUT_BATCH_SIZE = 200
DEFAULT_HANDLING_BATCH_SIZE = 100

#: How finely the idle sleep is cut, so a shutdown is noticed promptly.
_SLEEP_SLICE = timedelta(milliseconds=100)


def _should_stop(ctx: ExecutionContext) -> bool:
    """Java's `shouldStop(ctx)`: the scheduler is winding down, or this thread was interrupted.

    Python has no `Thread.interrupt()`, so only the first half exists here - which is the half
    db-scheduler actually uses. `stop()` sets this flag before it waits for workers, so a round
    in progress sees it on its next check.
    """
    state = getattr(ctx, "scheduler_state", None)
    return bool(getattr(state, "is_shutting_down", False))


def _run_until_stopped(
    ctx: ExecutionContext, drain: Callable[[], int], poll_delay: timedelta
) -> None:
    """One execution for the life of the process, as Java's event jobs are.

    ``while (!shouldStop(ctx))`` around a round, sleeping ``poll_delay`` when a round found
    nothing. The alternative - draining and returning, letting the schedule call back - is what
    this port did until `EKB-0214`, and it is a different machine: the row is unpicked between
    rounds, so a crashed instance is taken over in one poll rather than after the
    dead-execution window. That was traded away deliberately here, to be the same machine Java
    is.

    The sleep is in slices so a shutdown is noticed within a slice rather than after a whole
    poll delay. Java gets that for free from `Thread.interrupt()`, which CPython has no
    counterpart to.
    """
    slice_seconds = min(_SLEEP_SLICE.total_seconds(), poll_delay.total_seconds())
    while not _should_stop(ctx):
        if drain() == 0 and not _should_stop(ctx):
            remaining = poll_delay.total_seconds()
            while remaining > 0 and not _should_stop(ctx):
                time.sleep(min(slice_seconds, remaining))
                remaining -= slice_seconds


class EventFanoutJob(DistributedJob):
    """Materialises notifications from the outbox, in one long-running execution.

    **One execution runs for the life of the process**, as Java's does: a round drains one
    batch from every shard, and the loop goes straight round again while there is work, so a
    burst is cleared without waiting a poll delay per batch. When every shard comes back empty
    it sleeps the poll delay and looks again.

    It leaves the loop when the scheduler starts shutting down, which is the only way out.
    """

    __slots__ = ("_enabled", "_fanout", "_name", "_poll_delay")

    def __init__(
        self,
        registry: DatabaseRegistry,
        handlers: EventHandlerRegistry,
        *,
        events: Table,
        notifications: Table,
        config: LocalEventHandlerConfig | None = None,
        clock: Clock | None = None,
        name: str = FANOUT_JOB_NAME,
        enabled: bool = True,
    ) -> None:
        tuning = config or LocalEventHandlerConfig()
        self._name = _valid_name(name, type(self).__name__)
        self._poll_delay = tuning.fanout_poll_delay or DEFAULT_POLL_DELAY
        self._enabled = enabled
        self._fanout = EventFanout(
            registry,
            handlers,
            events=events,
            notifications=notifications,
            clock=clock or SystemClock(),
            batch_size=tuning.fanout_batch_size or DEFAULT_FANOUT_BATCH_SIZE,
        )

    @classmethod
    def builder(cls) -> EventFanoutJobBuilder:
        return EventFanoutJobBuilder()

    def name(self) -> str:
        return self._name

    def schedule(self) -> Schedule:
        """A fixed delay after each round, so an idle cluster polls at a steady rate rather
        than drifting with how long the last round took.

        Switched off, it returns a `DisabledSchedule`: db-scheduler then writes no row at all,
        which is how a job is registered but never run. Java says the same thing the same way -
        there is no separate `enabled` flag, because the schedule already expresses it.
        """
        return FixedDelay(self._poll_delay) if self._enabled else DisabledSchedule()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def fanout(self) -> EventFanout:
        """The drainer, for a caller that wants one round without the schedule around it."""
        return self._fanout

    def execute(self, ctx: ExecutionContext) -> None:
        """Runs for the life of the process, as Java's `EventFanoutJob.execute` does."""
        _run_until_stopped(ctx, self._fanout.drain_once, self._poll_delay)


class EventHandlingJob(DistributedJob):
    """Delivers due notifications to their handlers, in one long-running execution.

    **A job you build runs**, as Java's does: its schedule is a fixed delay of the handling poll
    delay, 1 second by default. It does not read ``handling.enabled`` from the config. In Java
    that switch lives in the Spring, Quarkus and Micronaut wiring, which simply does not create
    the job when it is off; here the wiring is your ``build()``, so read the switch there - pass
    ``enabled=config.handling.enabled``, or leave the job out - as the examples do.
    ``enabled=False`` registers the job with a `DisabledSchedule`, so it never runs.
    """

    __slots__ = ("_dispatch", "_enabled", "_name", "_poll_delay")

    def __init__(
        self,
        registry: DatabaseRegistry,
        handlers: EventHandlerRegistry,
        *,
        notifications: Table,
        config: LocalEventHandlerConfig | None = None,
        clock: Clock | None = None,
        name: str = HANDLING_JOB_NAME,
        enabled: bool = True,
    ) -> None:
        tuning = config or LocalEventHandlerConfig()
        self._name = _valid_name(name, type(self).__name__)
        self._poll_delay = tuning.handling_poll_delay or DEFAULT_POLL_DELAY
        self._enabled = enabled
        self._dispatch = EventDispatch(
            registry,
            handlers,
            notifications=notifications,
            clock=clock or SystemClock(),
            batch_size=tuning.handling_batch_size or DEFAULT_HANDLING_BATCH_SIZE,
            retention=tuning.handling_retention_window or DEFAULT_RETENTION,
            max_backoff=tuning.handling_max_backoff_cap or DEFAULT_MAX_BACKOFF,
        )

    @classmethod
    def builder(cls) -> EventHandlingJobBuilder:
        return EventHandlingJobBuilder()

    def name(self) -> str:
        return self._name

    def schedule(self) -> Schedule:
        """As :meth:`EventFanoutJob.schedule`: a fixed delay, or disabled when switched off."""
        return FixedDelay(self._poll_delay) if self._enabled else DisabledSchedule()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def dispatch(self) -> EventDispatch:
        """The drainer, for a caller that wants one round without the schedule around it."""
        return self._dispatch

    def execute(self, ctx: ExecutionContext) -> None:
        """Runs for the life of the process, as Java's `EventHandlingJob.execute` does."""
        _run_until_stopped(ctx, self._dispatch.drain_once, self._poll_delay)


class _JobBuilder:
    """What both job builders share: the collaborators, and the check that they were given."""

    __slots__ = (
        "_clock",
        "_config",
        "_enabled",
        "_handlers",
        "_name",
        "_notifications",
        "_registry",
    )

    def __init__(self) -> None:
        self._registry: DatabaseRegistry | None = None
        self._handlers: EventHandlerRegistry | None = None
        self._notifications: Table | None = None
        self._config: LocalEventHandlerConfig | None = None
        self._clock: Clock | None = None
        self._name: str | None = None
        self._enabled: bool | None = None

    def registry(self, registry: DatabaseRegistry) -> Self:
        self._registry = registry
        return self

    def handlers(self, handlers: EventHandlerRegistry) -> Self:
        self._handlers = handlers
        return self

    def notifications(self, table: Table) -> Self:
        self._notifications = table
        return self

    def config(self, config: LocalEventHandlerConfig) -> Self:
        self._config = config
        return self

    def clock(self, clock: Clock) -> Self:
        self._clock = clock
        return self

    def name(self, name: str) -> Self:
        """Override the cluster-wide name. Only for running two of these against different
        tables in one cluster; two jobs sharing a name would each steal the other's slots."""
        self._name = name
        return self

    def enabled(self, enabled: bool) -> Self:
        self._enabled = enabled
        return self

    def _required(self, **given: object) -> None:
        missing = sorted(name for name, value in given.items() if value is None)
        if missing:
            raise ValidationError(
                f"{type(self).__name__.removesuffix('Builder')} needs {missing} - "
                f"call the matching builder method before build()."
            )


class EventFanoutJobBuilder(_JobBuilder):
    """Obtain via :meth:`EventFanoutJob.builder`."""

    __slots__ = ("_events",)

    def __init__(self) -> None:
        super().__init__()
        self._events: Table | None = None

    def events(self, table: Table) -> Self:
        self._events = table
        return self

    def build(self) -> EventFanoutJob:
        self._required(
            registry=self._registry,
            handlers=self._handlers,
            events=self._events,
            notifications=self._notifications,
        )
        assert self._registry is not None
        assert self._handlers is not None
        assert self._events is not None
        assert self._notifications is not None
        return EventFanoutJob(
            self._registry,
            self._handlers,
            events=self._events,
            notifications=self._notifications,
            config=self._config,
            clock=self._clock,
            name=self._name if self._name is not None else FANOUT_JOB_NAME,
            enabled=self._enabled if self._enabled is not None else True,
        )


class EventHandlingJobBuilder(_JobBuilder):
    """Obtain via :meth:`EventHandlingJob.builder`."""

    __slots__ = ()

    def build(self) -> EventHandlingJob:
        self._required(
            registry=self._registry,
            handlers=self._handlers,
            notifications=self._notifications,
        )
        assert self._registry is not None
        assert self._handlers is not None
        assert self._notifications is not None
        return EventHandlingJob(
            self._registry,
            self._handlers,
            notifications=self._notifications,
            config=self._config,
            clock=self._clock,
            name=self._name if self._name is not None else HANDLING_JOB_NAME,
            enabled=self._enabled if self._enabled is not None else True,
        )


def _valid_name(name: str, job: str) -> str:
    if not name or not name.strip():
        raise ValidationError(
            f"{job} has a blank name. The name is the primary key of this job's row and how "
            f"every instance recognises it as the same job."
        )
    return name
