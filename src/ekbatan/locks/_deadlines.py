"""One thread holding every hold's deadline, in place of one thread per hold.

Java gives each outermost hold a virtual thread that sleeps until `maxHold` and then forces
the release. Measured on a JDK 25 with 10 cores, that scales about as well as it sounds: ten
watchdogs fire 17 ms late and a hundred thousand fire 48 ms late, because a *sleeping* virtual
thread is unmounted and is not competing for a carrier at all. There is nothing to fix there.

A `threading.Timer` is an OS thread, so the same shape does not survive the translation. At a
thousand held locks it costs a thousand threads and 40 MB - and it is also **slower**, because
starting those threads takes 70 ms before any of them can be late:

===============  ==========  =============  =========  =======
1,000 held       register    median late    threads    memory
===============  ==========  =============  =========  =======
thread per hold  70 ms       48 ms          1000       40 MB
this             3 ms        6 ms           1          ~0
===============  ==========  =============  =========  =======

At ten thousand the thread-per-hold version is not worth attempting and this one fires 33 ms
late on one thread.

It waits until the earliest deadline rather than polling on a cycle. A cycle would have to pick
an interval, and every choice is wrong in one direction: short burns a core doing nothing, long
is late by up to an interval. Waiting on a condition until the next deadline is neither - it
costs nothing while idle and wakes when it should.
"""

from __future__ import annotations

import heapq
import logging
import threading
import time
from collections.abc import Callable

LOG = logging.getLogger("ekbatan.locks")

#: Rebuild the heap once this share of it is cancelled entries. A hold that is closed normally -
#: which is nearly all of them - leaves its deadline behind, so without this the heap would grow
#: with every lock a busy service ever takes and shrink only as deadlines came due.
_COMPACT_AT = 0.5


class Deadline:
    """One registered deadline. Cancelled by the hold that closes in time."""

    __slots__ = ("fire", "seq", "when")

    def __init__(self, when: float, seq: int, fire: Callable[[], None]) -> None:
        self.when = when
        self.seq = seq
        self.fire: Callable[[], None] | None = fire

    def __lt__(self, other: Deadline) -> bool:
        return (self.when, self.seq) < (other.when, other.seq)


class Deadlines:
    """A heap of deadlines and one thread that waits for the earliest of them."""

    __slots__ = ("_cancelled", "_cv", "_heap", "_seq", "_thread")

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._heap: list[Deadline] = []
        self._cancelled = 0
        self._seq = 0
        self._thread: threading.Thread | None = None

    def after(self, delay: float, fire: Callable[[], None]) -> Deadline:
        """Call ``fire`` in ``delay`` seconds, unless the deadline is cancelled first."""
        with self._cv:
            self._seq += 1
            deadline = Deadline(time.monotonic() + delay, self._seq, fire)
            heapq.heappush(self._heap, deadline)
            if self._thread is None:
                # Started on the first hold, not at import: a process that never takes a lock
                # never gets the thread.
                self._thread = threading.Thread(
                    target=self._run, name="ekbatan-lock-watchdog", daemon=True
                )
                self._thread.start()
            self._cv.notify()
            return deadline

    def cancel(self, deadline: Deadline) -> None:
        """The hold closed in time. Tombstoned rather than removed - finding it in the heap is
        linear, and it will be thrown away for free when it reaches the front."""
        with self._cv:
            if deadline.fire is None:
                return
            deadline.fire = None
            self._cancelled += 1
            if self._cancelled >= max(64, int(len(self._heap) * _COMPACT_AT)):
                self._heap = [d for d in self._heap if d.fire is not None]
                heapq.heapify(self._heap)
                self._cancelled = 0
                self._cv.notify()

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._heap:
                    self._cv.wait()
                earliest = self._heap[0]
                if earliest.fire is None:
                    heapq.heappop(self._heap)
                    self._cancelled = max(0, self._cancelled - 1)
                    continue
                delay = earliest.when - time.monotonic()
                if delay > 0:
                    self._cv.wait(delay)
                    continue
                heapq.heappop(self._heap)
                fire = earliest.fire
                earliest.fire = None
            try:
                fire()
            except BaseException:
                # Nothing may end this thread. It is the only one watching every other hold,
                # and a lock whose watchdog died is held until the process is.
                LOG.exception("a lock watchdog failed; the others are unaffected")
