"""Time, injected.

Every timestamp the framework writes comes from a clock it was handed, never from
``datetime.now()`` reached for at the point of use. Tests need to freeze it, the conformance
scenarios freeze it (``given.now``), and a framework that calls the wall clock from six places
cannot be frozen from one.

A clock has one method, :meth:`Clock.instant`, as Java's ``clock.instant()``: the current moment
as an :data:`~ekbatan.time.Instant` - UTC digits with no zone attached.

**What a clock gives is what is stored.** The framework never changes a time's zone or value on
its way to or from the database. :class:`SystemClock` gives the current UTC time with no zone
attached; a clock of your own may give anything, and that is what gets written and compared.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from .errors import EkbatanError
from .time import Instant

__all__ = ["Clock", "SystemClock", "VirtualClock"]


@runtime_checkable
class Clock(Protocol):
    def instant(self) -> Instant:
        """The current moment: UTC digits with no zone attached, as :class:`SystemClock` gives.

        Whatever this returns is what the framework stores and compares, as it is: nothing is
        shifted or given a zone.
        """
        ...


class SystemClock:
    """The wall clock. The default everywhere.

    The current UTC time with no zone attached - ``2026-10-19 08:30:00.123456``, not
    ``...+00:00``. That is the value a zone-less ``TIMESTAMP`` column holds, so what is written
    reads back equal and compares with the next ``instant()``.
    """

    __slots__ = ()

    def instant(self) -> Instant:
        return _utc_now()

    def __repr__(self) -> str:
        return "SystemClock()"


class VirtualClock:
    """A clock a test can stop, move, or offset.

    Three modes, as Java's has:

    - **real time**, which is what ``VirtualClock()`` starts in. Reads pass straight through to
      the wall clock.
    - **frozen**, after :meth:`pause` or :meth:`pause_at`. Every read returns the same instant
      until :meth:`advance` moves it. ``VirtualClock(instant)`` starts here, because a test
      asserting on an absolute timestamp against a running clock is a test that passes on the
      machine it was written on.
    - **skewed**, after :meth:`resume_from`. Time runs at wall-clock rate from an instant that
      is not now - which is how you test something that has to cope with a machine whose clock
      disagrees with everyone else's.

    Not only for tests. The conformance scenarios freeze it too, through ``given.now``.

    An instant handed to it is kept exactly as given - give it naive UTC, as the default clock
    reads. In real time it reads what :class:`SystemClock` reads: UTC, no zone attached.

    Every mode change and every read takes a lock. Java uses an ``AtomicReference`` and has a
    test that eight threads advancing a thousand times each lose nothing; a bare ``+=`` here
    would be a read, an add and a write, and two of those interleaving silently drops one.
    """

    __slots__ = ("_frozen", "_lock", "_real_anchor", "_virtual_anchor")

    def __init__(self, instant: datetime | None = None) -> None:
        self._lock = threading.Lock()
        self._frozen: datetime | None = instant
        self._virtual_anchor: datetime | None = None
        self._real_anchor: Instant | None = None

    def instant(self) -> Instant:
        with self._lock:
            return Instant(self._reading())

    def pause(self) -> None:
        """Freeze wherever the clock is now, whichever mode it was in."""
        with self._lock:
            self._frozen = self._reading()
            self._virtual_anchor = self._real_anchor = None

    def pause_at(self, instant: datetime) -> None:
        """Freeze at a particular instant."""
        with self._lock:
            self._frozen = instant
            self._virtual_anchor = self._real_anchor = None

    def advance(self, delta: timedelta) -> None:
        """Move a frozen clock forward.

        Refused when the clock is not frozen, as Java refuses it: advancing something that is
        already running means nothing, and the reading a moment later would not show the move.
        """
        with self._lock:
            if self._frozen is None:
                raise EkbatanError(
                    "cannot advance: the clock is not paused. Call pause() or pause_at(...) "
                    "first, or construct it with an instant."
                )
            self._frozen += delta

    def resume_from(self, instant: datetime) -> None:
        """Run at wall-clock rate, but anchored somewhere other than now."""
        with self._lock:
            self._virtual_anchor = instant
            self._real_anchor = _utc_now()
            self._frozen = None

    def resume(self) -> None:
        """Back to the wall clock, discarding any freeze or offset."""
        with self._lock:
            self._frozen = self._virtual_anchor = self._real_anchor = None

    def _reading(self) -> datetime:
        """The current instant. Called with the lock held."""
        if self._frozen is not None:
            return self._frozen
        if self._virtual_anchor is not None and self._real_anchor is not None:
            elapsed: timedelta = _utc_now() - self._real_anchor
            return self._virtual_anchor + elapsed
        return _utc_now()

    def __repr__(self) -> str:
        with self._lock:
            if self._frozen is not None:
                return f"VirtualClock(paused at {self._frozen.isoformat()})"
            if self._virtual_anchor is not None:
                return f"VirtualClock(running from {self._virtual_anchor.isoformat()})"
            return "VirtualClock(real time)"


def _utc_now() -> Instant:
    """The current UTC time, no zone attached. The one place the framework reads the wall clock."""
    return Instant(datetime.now(UTC).replace(tzinfo=None))
