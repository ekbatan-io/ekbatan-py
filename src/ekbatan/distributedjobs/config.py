"""Tuning for the distributed job scheduler.

A port of `io.ekbatan.distributedjobs.config.JobsConfig`. Every knob is optional - an absent
value is simply not applied, so a caller sets only the subset they care about. What an absent
value then resolves to is :class:`JobRegistryBuilder`'s default, which is *usually* but not
always db-scheduler's:

    polling_interval    db-scheduler's DEFAULT_POLLING_INTERVAL (10s)
    heartbeat_interval  **the framework's own 10s**, not db-scheduler's 5 minutes. Since
                        db-scheduler declares an execution dead after
                        ``heartbeat_interval * missed_heartbeats_limit`` and that limit
                        defaults to 6, the effective dead-execution window is 60 seconds
                        rather than 30 minutes. See ``JobRegistryBuilder.heartbeat_interval``
                        for why.
    shutdown_max_wait   db-scheduler's 30 minutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Self

from ..errors import ValidationError

__all__ = ["JobsConfig", "JobsConfigBuilder"]


def _require_positive(value: timedelta | None, name: str) -> timedelta | None:
    """Reject zero and negative intervals.

    db-scheduler waits between iterations with a `Waiter` that skips the wait entirely when the
    duration is not positive - so zero does not mean "wait briefly", it means *do not wait*, and
    the poll loop spins against the database as fast as the CPU allows. Measured on the port:
    around ten thousand polls a second, and nine hundred thousand heartbeat rounds.
    """
    if value is not None and value.total_seconds() <= 0:
        raise ValidationError(
            f"{name} must be positive, but was {value}. A zero or negative interval makes "
            f"db-scheduler skip its wait entirely and poll in a tight loop."
        )
    return value


def _require_non_negative(value: timedelta | None, name: str) -> timedelta | None:
    """Reject negative waits. Zero is allowed and means "do not wait for in-flight jobs at
    shutdown", a legitimate choice for a container about to be killed anyway."""
    if value is not None and value.total_seconds() < 0:
        raise ValidationError(f"{name} cannot be negative, but was {value}.")
    return value


@dataclass(frozen=True, slots=True)
class JobsConfig:
    """Every knob optional; an unset one falls through to the builder's default."""

    polling_interval: timedelta | None = None
    heartbeat_interval: timedelta | None = None
    shutdown_max_wait: timedelta | None = None

    def __post_init__(self) -> None:
        _require_positive(self.polling_interval, "ekbatan.jobs.polling-interval")
        _require_positive(self.heartbeat_interval, "ekbatan.jobs.heartbeat-interval")
        _require_non_negative(self.shutdown_max_wait, "ekbatan.jobs.shutdown-max-wait")

    @classmethod
    def defaults(cls) -> JobsConfig:
        """No overrides - every knob falls through to framework defaults."""
        return cls()

    @classmethod
    def builder(cls) -> JobsConfigBuilder:
        return JobsConfigBuilder()


class JobsConfigBuilder:
    """Fluent builder for :class:`JobsConfig`."""

    __slots__ = ("_heartbeat_interval", "_polling_interval", "_shutdown_max_wait")

    def __init__(self) -> None:
        self._polling_interval: timedelta | None = None
        self._heartbeat_interval: timedelta | None = None
        self._shutdown_max_wait: timedelta | None = None

    def polling_interval(self, polling_interval: timedelta | None) -> Self:
        self._polling_interval = polling_interval
        return self

    def heartbeat_interval(self, heartbeat_interval: timedelta | None) -> Self:
        self._heartbeat_interval = heartbeat_interval
        return self

    def shutdown_max_wait(self, shutdown_max_wait: timedelta | None) -> Self:
        self._shutdown_max_wait = shutdown_max_wait
        return self

    def build(self) -> JobsConfig:
        return JobsConfig(
            polling_interval=self._polling_interval,
            heartbeat_interval=self._heartbeat_interval,
            shutdown_max_wait=self._shutdown_max_wait,
        )
