"""What the scheduler believes the time is.

Everything that needs "now" takes a Clock rather than calling the system clock, so a test can
drive time forward deliberately instead of sleeping. That is the same reason db-scheduler has
one, and it is why its own test suite can exercise a week of scheduling in a millisecond.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

__all__ = ["Clock", "SettableClock", "SystemClock"]


@runtime_checkable
class Clock(Protocol):
    """The current instant, always timezone-aware and in UTC."""

    def now(self) -> datetime: ...


class SystemClock:
    """The wall clock. What production uses."""

    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(UTC)

    def __repr__(self) -> str:
        return "SystemClock()"


class SettableClock:
    """A clock a test moves by hand.

    The counterpart to db-scheduler's `SettableClock` in its `testhelper` package. Time only
    changes when something asks it to, so a test that means "a day passes" says so rather than
    waiting a day - or, worse, sleeping just long enough to be flaky on a loaded machine.
    """

    __slots__ = ("_now",)

    def __init__(self, now: datetime | None = None) -> None:
        self._now = _aware(now if now is not None else datetime.now(UTC))

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = _aware(now)

    def tick(self, amount: timedelta) -> datetime:
        """Move forward. Returns the new instant, so a test can assert against it directly."""
        self._now = self._now + amount
        return self._now

    def __repr__(self) -> str:
        return f"SettableClock({self._now.isoformat()})"


def _aware(moment: datetime) -> datetime:
    """Refuse a naive datetime rather than guess which zone it meant.

    A scheduler compares instants written by other machines, in other zones. A naive value here
    is not a small imprecision - it is an execution time that is wrong by the offset, and only
    outside UTC, which is exactly the bug nobody reproduces.
    """
    if moment.tzinfo is None:
        raise ValueError(
            f"{moment!r} has no timezone. A scheduler compares instants across machines, so a "
            f"naive datetime is ambiguous - pass one with tzinfo, e.g. datetime.now(UTC)."
        )
    return moment.astimezone(UTC)
