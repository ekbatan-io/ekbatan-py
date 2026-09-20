"""Draining on SIGTERM without taking the signal away from the application.

Java turns its equivalent on by default and nobody minds: a JVM shutdown hook is additive, any
number can be registered, and none displaces another. CPython has one handler slot per signal
and `signal.signal()` replaces what is there - so this is opt-in, and it puts back what it
found.
"""

from __future__ import annotations

import os
import signal
import threading

import pytest

from ekbatan.distributedjobs._shutdown import ShutdownHook
from ekbatan.errors import ValidationError


@pytest.fixture(autouse=True)
def _leave_the_process_as_we_found_it():
    """Every test here installs real handlers. Put the originals back either way."""
    before = {n: signal.getsignal(n) for n in (signal.SIGTERM, signal.SIGINT)}
    yield
    for number, handler in before.items():
        signal.signal(number, handler)


def test_it_drains_when_the_signal_arrives():
    drained = threading.Event()
    hook = ShutdownHook(drained.set)
    hook.install()
    # Nothing was there before us, so the hook would restore SIG_DFL and re-raise - which would
    # kill the test runner. Give it something to hand back to instead.
    hook._previous[signal.SIGTERM] = lambda *_: None

    os.kill(os.getpid(), signal.SIGTERM)

    assert drained.wait(2), "the hook did not drain"


def test_the_application_keeps_its_own_handler():
    """The thing that makes this safe to offer at all."""
    ran: list[str] = []
    signal.signal(signal.SIGTERM, lambda *_: ran.append("the app's handler"))

    hook = ShutdownHook(lambda: ran.append("jobs.stop()"))
    hook.install()
    os.kill(os.getpid(), signal.SIGTERM)

    assert ran == ["jobs.stop()", "the app's handler"], "the app's handler was displaced"


def test_removing_it_puts_back_exactly_what_was_there():
    original = lambda *_: None  # noqa: E731
    signal.signal(signal.SIGTERM, original)
    hook = ShutdownHook(lambda: None)

    hook.install()
    assert signal.getsignal(signal.SIGTERM) is not original

    hook.remove()
    assert signal.getsignal(signal.SIGTERM) is original
    hook.remove()  # and twice is not an error


def test_a_signal_that_was_being_ignored_stays_ignored():
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    drained = threading.Event()
    hook = ShutdownHook(drained.set)
    hook.install()

    os.kill(os.getpid(), signal.SIGTERM)

    assert drained.wait(2), "it should still drain"
    # and the process is still here, which is the assertion: SIG_IGN was honoured rather than
    # turned into an exit.


def test_a_failure_while_draining_does_not_strand_the_process():
    """The hook exists so the process ends the way the operator asked. A drain that throws
    must not turn a shutdown into a hang."""
    ran: list[str] = []
    signal.signal(signal.SIGTERM, lambda *_: ran.append("the app's handler"))

    def boom() -> None:
        raise RuntimeError("stop() failed")

    hook = ShutdownHook(boom)
    hook.install()
    os.kill(os.getpid(), signal.SIGTERM)

    assert ran == ["the app's handler"], "the shutdown did not continue past the failure"


def test_it_refuses_off_the_main_thread_rather_than_doing_nothing():
    """`signal.signal` raises there. A hook that quietly failed to install would be discovered
    during an outage, by its absence."""
    failure: list[BaseException] = []

    def off_main() -> None:
        try:
            ShutdownHook(lambda: None).install()
        except BaseException as error:
            failure.append(error)

    worker = threading.Thread(target=off_main)
    worker.start()
    worker.join()

    assert failure and isinstance(failure[0], ValidationError)
    assert "main thread" in str(failure[0])


def test_installing_twice_is_a_no_op_and_keeps_the_original():
    original = lambda *_: None  # noqa: E731
    signal.signal(signal.SIGTERM, original)
    hook = ShutdownHook(lambda: None)

    hook.install()
    hook.install()
    hook.remove()

    assert signal.getsignal(signal.SIGTERM) is original, "the second install lost the original"
