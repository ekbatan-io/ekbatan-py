"""One thread watching every hold, in place of one thread per hold.

Java gives each hold a virtual thread that sleeps until `maxHold`. Measured on JDK 25 with ten
cores, ten of those fire 17 ms late and a hundred thousand fire 48 ms late - a sleeping virtual
thread is unmounted and competes for nothing, so there is nothing to fix on that side.

A `threading.Timer` is an OS thread and does not survive the translation. Measured here, a
thousand held locks: thread-per-hold takes 70 ms to register, fires 48 ms late and costs a
thousand threads and 40 MB; this takes 3 ms, fires 6 ms late and costs one thread.
"""

from __future__ import annotations

import threading
import time

from ekbatan.locks._deadlines import Deadlines


def test_a_deadline_fires_after_its_delay():
    fired = threading.Event()
    watcher = Deadlines()

    watcher.after(0.02, fired.set)

    assert fired.wait(2), "the deadline never fired"


def test_a_cancelled_deadline_does_not_fire():
    fired = threading.Event()
    watcher = Deadlines()

    watcher.cancel(watcher.after(0.02, fired.set))

    assert not fired.wait(0.15), "a hold that closed in time was expired anyway"


def test_the_earliest_deadline_is_the_one_waited_for():
    """Registered out of order, fired in order - which is the whole reason for a heap."""
    order: list[int] = []
    done = threading.Event()
    watcher = Deadlines()

    watcher.after(0.09, lambda: (order.append(3), done.set()))
    watcher.after(0.01, lambda: order.append(1))
    watcher.after(0.05, lambda: order.append(2))

    assert done.wait(2)
    assert order == [1, 2, 3]


def test_no_thread_is_started_until_something_is_actually_watched():
    """A process that never takes a lock never pays for the watchdog."""
    before = threading.active_count()
    watcher = Deadlines()

    assert threading.active_count() == before

    watcher.after(0.01, lambda: None)
    assert threading.active_count() == before + 1


def test_one_thread_watches_them_all():
    before = threading.active_count()
    watcher = Deadlines()
    done = threading.Semaphore(0)

    for _ in range(200):
        watcher.after(0.01, done.release)
    for _ in range(200):
        assert done.acquire(timeout=2)

    assert threading.active_count() == before + 1, "200 deadlines, one thread"


def test_cancelled_deadlines_do_not_pile_up_forever():
    """The case that decides whether this is usable: nearly every hold closes in time, so nearly
    every deadline is cancelled rather than fired. Tombstoning without compaction would grow the
    heap with every lock the process ever takes."""
    watcher = Deadlines()

    for _ in range(5_000):
        watcher.cancel(watcher.after(3600, lambda: None))

    assert len(watcher._heap) < 200, f"heap kept {len(watcher._heap)} dead entries"


def test_a_deadline_that_raises_does_not_take_the_watchdog_with_it(caplog):
    """It is the only thread watching every other hold. A lock whose watchdog died is held
    until the process is."""
    survived = threading.Event()
    watcher = Deadlines()

    def boom() -> None:
        raise RuntimeError("release failed")

    with caplog.at_level("ERROR", logger="ekbatan.locks"):
        watcher.after(0.01, boom)
        time.sleep(0.05)
        watcher.after(0.01, survived.set)

        assert survived.wait(2), "the watchdog died with the failing deadline"
    assert "a lock watchdog failed" in caplog.text
