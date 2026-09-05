"""Tuning for the two event jobs.

Every knob is optional and falls through to the framework default when unset, so a deployment
sets the subset it cares about. The same shape as ``ekbatan.local-event-handler.*`` in the Java
implementation, key for key, so one service's configuration reads the same as the other's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from ..errors import ValidationError

__all__ = ["HandlingConfig", "LocalEventHandlerConfig"]


@dataclass(frozen=True, kw_only=True, slots=True)
class HandlingConfig:
    """Whether the in-process handling job runs at all.

    Off by default, and deliberately: a deployment that drains notifications through an
    external pipeline still registers its handlers - they are what decodes an event - but must
    not also run a consumer in process. Turning it on is a decision somebody makes, not one
    they inherit.
    """

    enabled: bool = False

    @classmethod
    def defaults(cls) -> HandlingConfig:
        return cls()


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
    #: The handling job's own toggle.
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
