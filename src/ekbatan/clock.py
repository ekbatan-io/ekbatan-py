"""Time, injected.

Every timestamp the framework writes comes from a clock it was handed, never from
``datetime.now()`` reached for at the point of use. Tests need to freeze it, the conformance
scenarios freeze it (``given.now``), and a framework that calls the wall clock from six places
cannot be frozen from one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

__all__ = ["Clock", "SystemClock", "VirtualClock", "to_storage"]


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """The current instant, timezone-aware and in UTC."""
        ...


class SystemClock:
    """The wall clock. The default everywhere."""

    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(UTC)

    def __repr__(self) -> str:
        return "SystemClock()"


class VirtualClock:
    """A clock that does not move until told to.

    Not only for tests: a scenario asserting on an absolute timestamp against a running clock
    is a scenario that passes on the machine it was written on.
    """

    __slots__ = ("_now",)

    def __init__(self, now: datetime) -> None:
        self._now = _as_utc(now)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, now: datetime) -> None:
        self._now = _as_utc(now)

    def __repr__(self) -> str:
        return f"VirtualClock({self._now.isoformat()})"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def to_storage(value: datetime) -> datetime:
    """Normalise an instant to what the timestamp columns hold: UTC, no zone, microseconds.

    The columns are deliberately zone-less. A ``TIMESTAMP WITH TIME ZONE`` shifts values by the
    session's offset, and two readers of one row then disagree about when the thing happened.
    So the offset is applied here, once, and discarded - the stored value is UTC by
    construction rather than by everyone remembering.

    Microseconds because that is what Postgres and MariaDB store. A value that does not survive
    its own round trip is a bug waiting for a test to find it, usually months later and in an
    equality check that has nothing to do with time.
    """
    return _as_utc(value).replace(tzinfo=None)
