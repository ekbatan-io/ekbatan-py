"""`RunUntilShutdown` - the loop behind the poll, the heartbeat and dead-execution detection.

Two things it must never do: end because the body threw, and keep sleeping after being told to
shut down. The second is subtle, because how a shutdown reaches a sleeping thread differs
between the two languages:

Java calls `Thread.interrupt()`, which sets a flag **on the thread**. It sticks - a shutdown
signalled while the body was running is still there when the next wait begins, so the signal
cannot be missed.

Python's `Condition.notify()` has no memory. It reaches whoever is waiting at that instant and
is otherwise gone. So the loop is made not to depend on it: `Waiter` keeps its own note for the
sleeping case, and the loop re-reads the shutdown flag - which is itself sticky - before
sleeping at all. `TestShutdownIsNotSignalDependent` is the proof.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

from db_scheduler.event import AbstractSchedulerListener, SchedulerEventType, SchedulerListeners
from db_scheduler.executor import Waiter
from db_scheduler.scheduler import RunAndLogErrors, RunUntilShutdown
from db_scheduler.state import SettableSchedulerState

#: Real threads need a real bound, generous enough not to flake on a loaded machine.
PATIENCE = 15.0

#: Far longer than any test should take, so a loop that sleeps it out is unmistakably stuck.
A_LONG_NAP = timedelta(seconds=20)


class Recording(AbstractSchedulerListener):
    def __init__(self) -> None:
        self.events: list[SchedulerEventType] = []

    def on_scheduler_event(self, event_type: SchedulerEventType) -> None:
        self.events.append(event_type)


class DeafWaiter(Waiter):
    """A waiter whose wake-ups all go missing. Stands in for a lost signal."""

    __slots__ = ()

    def wake(self) -> bool:
        return False

    def wake_or_skip_next_wait(self) -> None:
        """Nothing. Deliberately."""


class Loop:
    """A `RunUntilShutdown` on its own thread, with the knobs a test needs."""

    def __init__(
        self,
        *,
        body_takes: float = 0.0,
        waiter: Waiter | None = None,
        listener: Recording | None = None,
    ) -> None:
        self.state = SettableSchedulerState()
        self.waiter = waiter or Waiter(A_LONG_NAP)
        self.listener = listener or Recording()
        self.runs = 0
        self.body_running = threading.Event()
        self._body_takes = body_takes
        self._raises: BaseException | None = None
        self._loop = RunUntilShutdown(
            self._body, self.waiter, self.state, SchedulerListeners([self.listener])
        )
        self._thread = threading.Thread(target=self._loop.run, daemon=True)

    def _body(self) -> None:
        self.runs += 1
        self.body_running.set()
        if self._body_takes:
            time.sleep(self._body_takes)
        if self._raises is not None:
            raise self._raises

    def raising(self, error: BaseException) -> Loop:
        self._raises = error
        return self

    def start(self) -> Loop:
        self._thread.start()
        return self

    def stop_and_time(self) -> float:
        began = time.monotonic()
        self.state.set_is_shutting_down()
        self.waiter.wake_or_skip_next_wait()
        self._thread.join(timeout=PATIENCE + 10)
        assert not self._thread.is_alive(), "the loop never exited"
        return time.monotonic() - began

    @property
    def is_running(self) -> bool:
        return self._thread.is_alive()


def until(predicate: object, what: str) -> None:
    deadline = time.monotonic() + PATIENCE
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return
        time.sleep(0.01)
    raise AssertionError(f"{what} never happened")


class TestStoppingPromptly:
    def test_a_shutdown_while_it_is_napping_ends_it_at_once(self) -> None:
        loop = Loop().start()
        until(lambda: loop.waiter.is_waiting, "the loop settling into its nap")

        assert loop.stop_and_time() < 2.0

    def test_a_shutdown_mid_body_ends_it_at_once(self) -> None:
        """The bug this file exists for. The wake-up arrives while the loop is *working*, so
        there is nobody asleep to receive it - and it used to sleep the whole interval before
        noticing."""
        loop = Loop(body_takes=0.4).start()
        assert loop.body_running.wait(PATIENCE)

        assert loop.stop_and_time() < 2.0, "it waited out the poll interval"

    def test_it_does_not_run_the_body_again_after_being_stopped(self) -> None:
        loop = Loop(body_takes=0.3).start()
        assert loop.body_running.wait(PATIENCE)

        loop.stop_and_time()

        assert loop.runs == 1, "it started another round after being told to stop"


class TestShutdownIsNotSignalDependent:
    """With every wake-up thrown away, the loop must still stop the moment it can.

    This is what replaces Java's sticky interrupt flag. Java cannot miss a shutdown signal;
    Python's `notify` can be lost entirely, so the loop is built not to need it.
    """

    def test_a_shutdown_mid_body_needs_no_signal_at_all(self) -> None:
        loop = Loop(body_takes=0.4, waiter=DeafWaiter(A_LONG_NAP)).start()
        assert loop.body_running.wait(PATIENCE)

        assert loop.stop_and_time() < 2.0

    def test_only_a_thread_already_asleep_depends_on_the_signal(self) -> None:
        """Stated rather than hidden: nothing can wake a sleeping thread except a signal, so
        this is the one case where losing it costs the remainder of the nap. `Waiter` covers it
        by leaving a note, which is exactly the case the note is for."""
        loop = Loop(waiter=DeafWaiter(timedelta(seconds=1))).start()
        until(lambda: loop.waiter.is_waiting, "the loop settling into its nap")

        assert loop.stop_and_time() < 3.0, "it should still end when the nap runs out"


class TestSurvivingFailure:
    def test_a_body_that_raises_does_not_end_the_loop(self) -> None:
        """Otherwise a single bad poll - a database blip, a listener throwing - would stop the
        scheduler scheduling while the process stayed up looking healthy."""
        loop = Loop(waiter=Waiter(timedelta(milliseconds=10))).raising(RuntimeError("boom"))
        loop.start()

        until(lambda: loop.runs >= 3, "the loop continuing after failures")

        assert loop.is_running
        loop.stop_and_time()

    def test_and_it_is_reported(self) -> None:
        loop = Loop(waiter=Waiter(timedelta(milliseconds=10))).raising(RuntimeError("boom"))
        loop.start()

        until(lambda: loop.listener.events, "the failure being reported")

        assert SchedulerEventType.UNEXPECTED_ERROR in loop.listener.events
        loop.stop_and_time()

    def test_even_a_base_exception_does_not_end_it(self) -> None:
        loop = Loop(waiter=Waiter(timedelta(milliseconds=10)))
        loop.raising(KeyboardInterrupt()).start()

        until(lambda: loop.runs >= 3, "the loop continuing")

        assert loop.is_running
        loop.stop_and_time()


class TestPausing:
    def test_a_paused_loop_does_not_run_the_body(self) -> None:
        loop = Loop(waiter=Waiter(timedelta(milliseconds=10)))
        loop.state.set_paused(True)
        loop.start()

        time.sleep(0.3)

        assert loop.runs == 0
        loop.stop_and_time()

    def test_and_resumes(self) -> None:
        loop = Loop(waiter=Waiter(timedelta(milliseconds=10)))
        loop.state.set_paused(True)
        loop.start()
        time.sleep(0.2)

        loop.state.set_paused(False)

        until(lambda: loop.runs >= 1, "the loop resuming")
        loop.stop_and_time()

    def test_a_paused_loop_still_stops(self) -> None:
        """Pausing must not make a scheduler un-stoppable."""
        loop = Loop()
        loop.state.set_paused(True)
        loop.start()
        until(lambda: loop.waiter.is_waiting, "the loop settling into its nap")

        assert loop.stop_and_time() < 2.0


class TestRunAndLogErrors:
    """The housekeeping loops' equivalent guarantee: one bad run must not stop the rest."""

    def test_it_swallows_and_reports(self) -> None:
        listener = Recording()

        def explode() -> None:
            raise RuntimeError("boom")

        RunAndLogErrors(explode, SchedulerListeners([listener])).run()

        assert listener.events == [SchedulerEventType.UNEXPECTED_ERROR]

    def test_a_clean_run_reports_nothing(self) -> None:
        listener = Recording()
        ran = []

        RunAndLogErrors(lambda: ran.append(1), SchedulerListeners([listener])).run()

        assert ran == [1]
        assert listener.events == []
