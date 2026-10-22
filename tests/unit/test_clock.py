"""The clock a test can stop, move, or offset.

Java's `VirtualClockTest`, case for case. `getZone`/`withZone` have no counterpart - they are
what `java.time.Clock` requires of an implementation, and the protocol here asks only for
`instant()`, Java's `clock.instant()`.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from ekbatan.clock import SystemClock, VirtualClock
from ekbatan.errors import EkbatanError

TARGET = datetime(2025, 6, 15, 12, 0, tzinfo=UTC)
LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)


def test_a_clock_with_no_instant_reads_the_wall_clock():
    """Java's `default_clock_returns_real_time`. The default matters: an application handed one
    of these by accident still tells the time."""
    clock = VirtualClock()

    before = datetime.now(UTC).replace(tzinfo=None)
    reading = clock.instant()
    after = datetime.now(UTC).replace(tzinfo=None)

    assert before <= reading <= after


def test_a_clock_built_with_an_instant_starts_frozen_there():
    """Where this differs from Java, deliberately.

    Java's constructor takes a real clock and starts running; freezing is a separate call. Here
    the common case in a test is a fixed instant, so passing one freezes at it - and
    `VirtualClock()` still gives Java's default.
    """
    clock = VirtualClock(TARGET)

    assert clock.instant() == TARGET
    time.sleep(0.01)
    assert clock.instant() == TARGET


def test_pause_freezes_wherever_the_clock_is():
    clock = VirtualClock()
    clock.pause()

    first = clock.instant()
    time.sleep(0.01)

    assert clock.instant() == first


def test_pause_at_freezes_at_a_named_instant():
    clock = VirtualClock()

    clock.pause_at(TARGET)

    assert clock.instant() == TARGET
    assert clock.instant() == TARGET, "and stays there on a second reading"


def test_advance_moves_a_frozen_clock_forward():
    clock = VirtualClock()
    clock.pause_at(TARGET)

    clock.advance(timedelta(minutes=5))

    assert clock.instant() == datetime(2025, 6, 15, 12, 5, tzinfo=UTC)


def test_advance_is_refused_on_a_clock_that_is_not_paused():
    """Java's `advance_throws_when_not_paused`. Advancing something already running means
    nothing, and the reading a moment later would not show the move."""
    clock = VirtualClock()

    with pytest.raises(EkbatanError, match="not paused"):
        clock.advance(timedelta(minutes=5))


def test_resume_from_runs_at_wall_clock_rate_from_somewhere_else():
    """Java's `resume_from_ticks_forward_from_given_instant`. This is the mode for testing
    something that has to cope with a machine whose clock disagrees with everyone else's."""
    clock = VirtualClock()

    clock.resume_from(LONG_AGO)
    time.sleep(0.05)
    reading = clock.instant()

    assert reading > LONG_AGO
    assert (reading - LONG_AGO) >= timedelta(milliseconds=50)


def test_resume_returns_to_the_wall_clock():
    clock = VirtualClock(LONG_AGO)

    clock.resume()

    before = datetime.now(UTC).replace(tzinfo=None)
    reading = clock.instant()
    after = datetime.now(UTC).replace(tzinfo=None)
    assert before <= reading <= after


def test_pausing_an_offset_clock_freezes_it_at_the_offset():
    """Java's `pause_after_resume_from_freezes_at_offset_time`. `pause` freezes wherever the
    clock is, which for a skewed clock is not now and not the anchor either."""
    clock = VirtualClock()
    clock.resume_from(LONG_AGO)
    time.sleep(0.05)

    clock.pause()
    frozen = clock.instant()
    time.sleep(0.01)

    assert clock.instant() == frozen
    assert frozen > LONG_AGO


def test_concurrent_advances_do_not_lose_any():
    """Java's `concurrent_advances_do_not_lose_updates`.

    Honest about what this proves on this interpreter: **not much**. A bare
    `self._frozen = self._frozen + delta` is a read, an add and a write, and on a free-threaded
    build two of those interleaving drops one silently. On a GIL build the window is small
    enough that removing the lock still passes - measured at 0 lost updates in 20 runs of this
    exact shape - so this test does not, here, fail without the lock.

    It is kept because `requires-python` allows a free-threaded 3.13+, where it does fail, and
    because the guarantee is worth stating whether or not today's interpreter enforces it.
    Java's version of this test is genuine: its threads are real and its `AtomicReference` is
    load-bearing.
    """
    clock = VirtualClock(TARGET)
    workers, each = 8, 1000
    start = threading.Barrier(workers)

    def advance_many() -> None:
        start.wait()
        for _ in range(each):
            clock.advance(timedelta(milliseconds=1))

    threads = [threading.Thread(target=advance_many) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert clock.instant() == TARGET + timedelta(milliseconds=workers * each)


def test_the_system_clock_is_utc_with_no_zone_attached():
    """The default clock gives the current UTC time, naive - what a zone-less column holds."""
    reading = SystemClock().instant()
    utc_now = datetime.now(UTC).replace(tzinfo=None)

    assert reading.tzinfo is None
    assert abs(reading - utc_now) < timedelta(seconds=1)


def test_a_virtual_clock_keeps_the_instant_it_is_given_as_it_is():
    """No zone added, removed or moved: a naive instant stays naive, an aware one keeps its
    own offset."""
    from zoneinfo import ZoneInfo

    tehran = datetime(2026, 1, 1, 13, 30, tzinfo=ZoneInfo("Asia/Tehran"))
    naive = datetime(2026, 1, 1, 10, 0, 0, 123456)

    assert VirtualClock(tehran).instant() is tehran
    assert VirtualClock(naive).instant() is naive
    paused = VirtualClock()
    paused.pause_at(tehran)
    assert paused.instant() is tehran
    assert paused.instant().utcoffset() == timedelta(hours=3, minutes=30)


def test_a_virtual_clock_in_real_time_reads_as_the_system_clock_does():
    assert VirtualClock().instant().tzinfo is None


def test_now_is_gone_and_instant_is_the_one_reading():
    """Renamed outright, no alias: `Clock.now()` became `Clock.instant()`, as Java's
    `clock.instant()`, and an action's `self.now()` became `self.instant()`. A `now()` coming
    back would be a second name for one reading."""
    from ekbatan import Action, Clock
    from ekbatan.aio import AsyncAction

    for kind in (Clock, SystemClock, VirtualClock, Action, AsyncAction):
        assert not hasattr(kind, "now"), kind
        assert callable(getattr(kind, "instant", None)), kind
    assert not hasattr(SystemClock(), "now")
    assert isinstance(SystemClock(), Clock)


def test_the_system_clock_gives_an_instant_naive_and_utc():
    """`instant()` is naive and within a second of UTC now - the value a zone-less column
    holds and an `Instant` field writes with `Z`."""
    reading = SystemClock().instant()
    assert type(reading) is datetime, "a label, not a class: the value is a plain datetime"
    assert reading.tzinfo is None
    assert abs(reading - datetime.now(UTC).replace(tzinfo=None)) < timedelta(seconds=1)
