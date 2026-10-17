"""Time, injected.

Every timestamp the framework writes comes from a clock it was handed, never from
``datetime.now()`` reached for at the point of use. Tests need to freeze it, the conformance
scenarios freeze it (``given.now``), and a framework that calls the wall clock from six places
cannot be frozen from one.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from .errors import EkbatanError

__all__ = ["Clock", "SystemClock", "VirtualClock", "to_instant", "to_storage"]


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

    Every mode change and every read takes a lock. Java uses an ``AtomicReference`` and has a
    test that eight threads advancing a thousand times each lose nothing; a bare ``+=`` here
    would be a read, an add and a write, and two of those interleaving silently drops one.
    """

    __slots__ = ("_frozen", "_lock", "_real_anchor", "_virtual_anchor")

    def __init__(self, now: datetime | None = None) -> None:
        self._lock = threading.Lock()
        self._frozen: datetime | None = None if now is None else _as_utc(now)
        self._virtual_anchor: datetime | None = None
        self._real_anchor: datetime | None = None

    def now(self) -> datetime:
        with self._lock:
            return self._reading()

    def pause(self) -> None:
        """Freeze wherever the clock is now, whichever mode it was in."""
        with self._lock:
            self._frozen = self._reading()
            self._virtual_anchor = self._real_anchor = None

    def pause_at(self, instant: datetime) -> None:
        """Freeze at a particular instant."""
        with self._lock:
            self._frozen = _as_utc(instant)
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
            self._virtual_anchor = _as_utc(instant)
            self._real_anchor = datetime.now(UTC)
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
            return self._virtual_anchor + (datetime.now(UTC) - self._real_anchor)
        return datetime.now(UTC)

    def __repr__(self) -> str:
        with self._lock:
            if self._frozen is not None:
                return f"VirtualClock(paused at {self._frozen.isoformat()})"
            if self._virtual_anchor is not None:
                return f"VirtualClock(running from {self._virtual_anchor.isoformat()})"
            return "VirtualClock(real time)"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def to_instant(value: datetime) -> datetime:
    """An instant as the framework hands every timestamp back: aware, in UTC - Java's ``Instant``.

    A value with no zone is taken as UTC, which is what the timestamp columns hold. So a
    timestamp read back equals the one written, and compares with ``clock.now()``.
    """
    return _as_utc(value)


def to_storage(value: datetime) -> datetime:
    """Normalise an instant to what the timestamp columns hold: UTC, no zone, microseconds.

    The columns are zone-less, as Java's are, and Java writes an ``Instant`` into them as its
    UTC wall-clock time; this writes the same value. Only the stored form drops the zone: every
    timestamp the framework hands back is aware (:func:`to_instant`).
    """
    return _as_utc(value).replace(tzinfo=None)
