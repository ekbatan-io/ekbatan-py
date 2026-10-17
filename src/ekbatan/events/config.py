"""Tuning for the two event jobs.

Every knob is optional and falls through to the framework default when unset, so a deployment
sets the subset it cares about. The same shape as ``ekbatan.local-event-handler.*`` in the Java
implementation, key for key, so one service's configuration reads the same as the other's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Self

from .._builder import FieldBuilder
from ..errors import ValidationError

__all__ = [
    "HandlingConfig",
    "HandlingConfigBuilder",
    "LocalEventHandlerConfig",
    "LocalEventHandlerConfigBuilder",
]


@dataclass(frozen=True, kw_only=True, slots=True)
class HandlingConfig:
    """Whether the application runs the in-process handling job: Java's
    ``ekbatan.local-event-handler.handling.enabled``.

    Off by default, as in Java: a deployment that drains notifications through an external
    pipeline still registers its handlers - they are what decodes an event - but must not also
    run a consumer in process.

    The job itself does not read it, as Java's does not. Java's DI wiring reads it and does not
    create the job when it is off; here your ``build()`` is the wiring, so it reads it -
    ``EventHandlingJob.builder()...enabled(config.handling.enabled)``.
    """

    enabled: bool = False

    @classmethod
    def defaults(cls) -> HandlingConfig:
        return cls()

    @classmethod
    def builder(cls) -> HandlingConfigBuilder:
        return HandlingConfigBuilder()


@dataclass(frozen=True, kw_only=True, slots=True)
class LocalEventHandlerConfig:
    """What the fan-out and handling jobs are tuned with. ``None`` means the framework default."""

    #: Delay between fan-out polls.
    fanout_poll_delay: timedelta | None = None
    #: How many events one fan-out round reads per shard.
    fanout_batch_size: int | None = None
    #: Delay between handling polls.
    handling_poll_delay: timedelta | None = None
    #: How many notifications one handling round reads per shard.
    handling_batch_size: int | None = None
    #: Ceiling on the backoff between handler retries.
    handling_max_backoff_cap: timedelta | None = None
    #: How long a notification may keep being retried before it expires.
    handling_retention_window: timedelta | None = None
    #: Whether the application runs the handling job; read by the wiring, not by the job.
    handling: HandlingConfig = field(default_factory=HandlingConfig)

    def __post_init__(self) -> None:
        for name in ("fanout_poll_delay", "handling_poll_delay"):
            value = getattr(self, name)
            if value is not None and value <= timedelta(0):
                raise ValidationError(f"{name} must be positive, got {value}")
        for name in ("fanout_batch_size", "handling_batch_size"):
            value = getattr(self, name)
            if value is not None and value < 1:
                raise ValidationError(f"{name} must be at least 1, got {value}")
        for name in ("handling_max_backoff_cap", "handling_retention_window"):
            value = getattr(self, name)
            if value is not None and value <= timedelta(0):
                raise ValidationError(f"{name} must be positive, got {value}")

    @classmethod
    def defaults(cls) -> LocalEventHandlerConfig:
        """Every knob at the framework default."""
        return cls()

    @classmethod
    def builder(cls) -> LocalEventHandlerConfigBuilder:
        return LocalEventHandlerConfigBuilder()


class HandlingConfigBuilder(FieldBuilder[HandlingConfig]):
    """Fluent builder for :class:`HandlingConfig`. Obtain via :meth:`HandlingConfig.builder`."""

    __slots__ = ()
    _target = HandlingConfig

    def enabled(self, enabled: bool) -> Self:
        return self._set("enabled", enabled)


class LocalEventHandlerConfigBuilder(FieldBuilder[LocalEventHandlerConfig]):
    """Fluent builder for :class:`LocalEventHandlerConfig`. Obtain via
    :meth:`LocalEventHandlerConfig.builder`.

    ``None`` is accepted by every setter and means the framework default, as Java's
    ``Optional.ofNullable`` does; ``handling(None)`` means :meth:`HandlingConfig.defaults`.
    """

    __slots__ = ()
    _target = LocalEventHandlerConfig

    def fanout_poll_delay(self, fanout_poll_delay: timedelta | None) -> Self:
        return self._set("fanout_poll_delay", fanout_poll_delay)

    def fanout_batch_size(self, fanout_batch_size: int | None) -> Self:
        return self._set("fanout_batch_size", fanout_batch_size)

    def handling_poll_delay(self, handling_poll_delay: timedelta | None) -> Self:
        return self._set("handling_poll_delay", handling_poll_delay)

    def handling_batch_size(self, handling_batch_size: int | None) -> Self:
        return self._set("handling_batch_size", handling_batch_size)

    def handling_max_backoff_cap(self, handling_max_backoff_cap: timedelta | None) -> Self:
        return self._set("handling_max_backoff_cap", handling_max_backoff_cap)

    def handling_retention_window(self, handling_retention_window: timedelta | None) -> Self:
        return self._set("handling_retention_window", handling_retention_window)

    def handling(self, handling: HandlingConfig | None) -> Self:
        return self._set("handling", handling if handling is not None else HandlingConfig())
