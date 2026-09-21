"""One execution for the life of the process, as Java's event jobs are.

Java's `EventFanoutJob.execute` is `while (!shouldStop(ctx))` around a round, sleeping the poll
delay when a round found nothing. This port drained once and returned, letting the schedule call
it back - and that was never a decision: `git log -S` finds no commit that ever contained the
loop. It was not ported. `EKB-0214` ports it.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

from ekbatan.events.jobs import _run_until_stopped, _should_stop


class _State:
    """Stands in for db-scheduler's scheduler state, which is all `shouldStop` reads."""

    def __init__(self, shutting_down: bool = False) -> None:
        self.is_shutting_down = shutting_down


class _Ctx:
    def __init__(self, state: object) -> None:
        self.scheduler_state = state


A_SECOND = timedelta(seconds=1)


def test_it_keeps_going_round_until_it_is_told_to_stop():
    state = _State()
    rounds = 0

    def counting() -> int:
        nonlocal rounds
        rounds += 1
        if rounds == 5:
            state.is_shutting_down = True  # what JobRegistry.stop() sets
        return 1  # there was work, so no sleep between rounds

    _run_until_stopped(_Ctx(state), counting, A_SECOND)

    assert rounds == 5, "the loop did not keep going, or did not stop when told"


def test_it_does_not_start_a_round_when_it_is_already_stopping():
    ran = []
    _run_until_stopped(_Ctx(_State(shutting_down=True)), lambda: ran.append(1) or 0, A_SECOND)

    assert ran == [], "it began a round after shutdown had started"


def test_an_idle_round_waits_the_poll_delay_rather_than_spinning():
    state = _State()
    rounds = 0

    def empty() -> int:
        nonlocal rounds
        rounds += 1
        if rounds == 2:
            state.is_shutting_down = True
        return 0  # nothing to do, so it sleeps between rounds

    started = time.monotonic()
    _run_until_stopped(_Ctx(state), empty, timedelta(milliseconds=300))
    took = time.monotonic() - started

    assert rounds == 2
    assert took >= 0.25, f"it spun instead of waiting the poll delay ({took:.2f}s)"


def test_a_shutdown_is_noticed_without_waiting_out_the_whole_poll_delay():
    """The sleep is cut into slices. Java's is cut short by `Thread.interrupt()`, which CPython
    has no counterpart to - so a 30-second poll delay would otherwise be 30 seconds of a
    container waiting to stop."""
    state = _State()
    stopper = threading.Timer(0.2, lambda: setattr(state, "is_shutting_down", True))
    stopper.start()

    started = time.monotonic()
    _run_until_stopped(_Ctx(state), lambda: 0, timedelta(seconds=30))
    took = time.monotonic() - started
    stopper.cancel()

    assert took < 5, f"it slept out the whole 30s poll delay instead of noticing ({took:.2f}s)"


def test_a_context_without_a_scheduler_state_is_not_a_crash():
    """`ExecutionContext.scheduler_state` is typed `Any` and defaults to `None`. A job driven
    from a test harness can arrive with nothing there, and that must read as "carry on" rather
    than raise - a fan-out that died on shutdown detection would be worse than one that never
    checked."""

    class _Bare:
        scheduler_state = None

    assert _should_stop(_Bare()) is False
