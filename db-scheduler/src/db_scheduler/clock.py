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
    """The current time.

    Whatever this returns is what the scheduler stores and compares, as it is: nothing shifts it
    or gives it a zone. :class:`SystemClock` returns UTC with no zone attached.
    """

    def now(self) -> datetime: ...


class SystemClock:
    """The wall clock. What production uses: the current UTC time, no zone attached - the value
    a zone-less column holds, so what is written reads back equal to it."""

    __slots__ = ()

    def now(self) -> datetime:
        return _utc_now()

    def __repr__(self) -> str:
        return "SystemClock()"


class SettableClock:
    """A clock a test moves by hand.

    The counterpart to db-scheduler's `SettableClock` in its `testhelper` package. Time only
    changes when something asks it to, so a test that means "a day passes" says so rather than
    waiting a day - or, worse, sleeping just long enough to be flaky on a loaded machine.

    An instant handed to it is kept exactly as given; with none it starts at the current UTC
    time, no zone attached, as :class:`SystemClock` reads it.
    """

    __slots__ = ("_now",)

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now if now is not None else _utc_now()

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now

    def tick(self, amount: timedelta) -> datetime:
        """Move forward. Returns the new instant, so a test can assert against it directly."""
        self._now = self._now + amount
        return self._now

    def __repr__(self) -> str:
        return f"SettableClock({self._now.isoformat()})"


def _utc_now() -> datetime:
    """The current UTC time, no zone attached. The one place the scheduler reads the wall clock."""
    return datetime.now(UTC).replace(tzinfo=None)
