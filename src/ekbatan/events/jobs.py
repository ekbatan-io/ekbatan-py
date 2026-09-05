"""The two event jobs, as jobs.

Fan-out turns committed events into per-handler notifications; handling delivers them. Both are
:class:`~ekbatan.jobs.DistributedJob`, so a cluster runs each of them once per slot rather than
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

from datetime import timedelta
from typing import Self

from sqlalchemy import Table

from ..clock import Clock, SystemClock
from ..errors import ValidationError
from ..jobs import DistributedJob, FixedDelay, JobContext, Schedule
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


class EventFanoutJob(DistributedJob):
    """Materialises notifications from the outbox, one round per slot.

    A round drains one batch from every shard and, if any of them had work, goes round again -
    so a burst is cleared without waiting a poll delay per batch. When every shard comes back
    empty the job returns and the schedule brings it back.
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

    @property
    def name(self) -> str:
        return self._name

    @property
    def schedule(self) -> Schedule:
        """A fixed delay after each round, so an idle cluster polls at a steady rate rather
        than drifting with how long the last round took."""
        return FixedDelay(self._poll_delay)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def fanout(self) -> EventFanout:
        """The drainer, for a caller that wants one round without the schedule around it."""
        return self._fanout

    def run(self, context: JobContext) -> None:
        self._fanout.drain_until_idle()


class EventHandlingJob(DistributedJob):
    """Delivers due notifications to their handlers, one round per slot.

    Off unless switched on. A deployment draining notifications through an external pipeline
    still registers its handlers - they are what decodes an event - and must not also run a
    consumer in process, so this is opt-in rather than something to remember to turn off.
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
        enabled: bool | None = None,
    ) -> None:
        tuning = config or LocalEventHandlerConfig()
        self._name = _valid_name(name, type(self).__name__)
        self._poll_delay = tuning.handling_poll_delay or DEFAULT_POLL_DELAY
        self._enabled = tuning.handling.enabled if enabled is None else enabled
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

    @property
    def name(self) -> str:
        return self._name

    @property
    def schedule(self) -> Schedule:
        return FixedDelay(self._poll_delay)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def dispatch(self) -> EventDispatch:
        """The drainer, for a caller that wants one round without the schedule around it."""
        return self._dispatch

    def run(self, context: JobContext) -> None:
        self._dispatch.drain_until_idle()


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
            enabled=self._enabled,
        )


def _valid_name(name: str, job: str) -> str:
    if not name or not name.strip():
        raise ValidationError(
            f"{job} has a blank name. The name is the primary key of this job's row and how "
            f"every instance recognises it as the same job."
        )
    return name
