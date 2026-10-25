"""The SIGTERM half of `register_shutdown_hook`: one handler for the whole process.

Java's JVM shutdown hook runs on SIGTERM as on any other exit. CPython has one handler per
signal, so the registry takes SIGTERM only where nothing else handles it, and only from the main
thread, where CPython allows it. On SIGTERM it stops every registry that asked for the hook - so
their running jobs finish - and then the process dies of the signal, as it would have.

The process-level behaviour - the job really finishing, the process really dying - is measured
in real processes in `tests/integration/test_jobs_at_exit.py`. These pin the rules, in this
process, with the signal itself replaced where it would end the test run.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from collections.abc import Iterator

import pytest

from ekbatan.distributedjobs import _shutdown

LOGGER = "io.ekbatan.distributedjobs.JobRegistry"


class FakeRegistry:
    """What the handler needs of a `JobRegistry`: `stop()`, which takes it off, as the real one."""

    def __init__(self, name: str, log: list[str], fails: bool = False) -> None:
        self.name, self.log, self.fails = name, log, fails

    def stop(self) -> None:
        _shutdown.remove(self)  # type: ignore[arg-type]
        self.log.append(f"{self.name} stopped, SIGTERM {signal.getsignal(signal.SIGTERM)!r}")
        if self.fails:
            raise RuntimeError(f"{self.name} failed to stop")

    def __repr__(self) -> str:
        return self.name


@pytest.fixture(autouse=True)
def _leave_the_process_as_we_found_it() -> Iterator[None]:
    """Every test here sets real handlers. Start from SIGTERM's default, with no registries, and
    put the originals back either way."""
    before = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    _shutdown._registries.clear()
    yield
    _shutdown._registries.clear()
    signal.signal(signal.SIGTERM, before)


@pytest.fixture
def ending(monkeypatch) -> list[str]:
    """Stands in for the two calls that would end the test run: re-raising SIGTERM, and the exit
    that follows if the process is still alive after it."""
    ended: list[str] = []
    monkeypatch.setattr(signal, "raise_signal", lambda number: ended.append(f"raise {number!r}"))
    monkeypatch.setattr(os, "_exit", lambda code: ended.append(f"_exit {code}"))
    return ended


def test_it_takes_a_sigterm_nobody_handles_and_says_so(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)

    _shutdown.add(FakeRegistry("jobs", []))  # type: ignore[arg-type]

    assert signal.getsignal(signal.SIGTERM) is _shutdown._on_sigterm
    assert [r.getMessage() for r in caplog.records if r.name == LOGGER] == [
        "Nothing else in this process handles SIGTERM, so the JobRegistry does: on SIGTERM, "
        "running jobs finish, then the process stops. A SIGTERM handler set after this "
        "replaces it; register_shutdown_hook(False) turns it off."
    ]


@pytest.mark.parametrize(
    "handler",
    [lambda *_: None, signal.SIG_IGN],
    ids=["the application's own handler", "SIGTERM ignored on purpose"],
)
def test_a_sigterm_somebody_handles_is_left_to_them(handler, caplog):
    """uvicorn and gunicorn set theirs before the application starts; then they own shutdown,
    and the application's shutdown code calls stop()."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    signal.signal(signal.SIGTERM, handler)

    _shutdown.add(FakeRegistry("jobs", []))  # type: ignore[arg-type]

    assert signal.getsignal(signal.SIGTERM) is handler
    assert not caplog.records


def test_off_the_main_thread_it_takes_nothing_and_raises_nothing():
    """CPython sets signal handlers from the main thread only. `start()` warns instead."""
    failures: list[BaseException] = []

    def add() -> None:
        try:
            _shutdown.add(FakeRegistry("jobs", []))  # type: ignore[arg-type]
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=add)
    worker.start()
    worker.join()

    assert failures == []
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL


def test_one_handler_serves_every_registry_and_the_last_to_stop_gives_sigterm_back():
    first, second = FakeRegistry("first", []), FakeRegistry("second", [])
    _shutdown.add(first)  # type: ignore[arg-type]
    _shutdown.add(second)  # type: ignore[arg-type]

    _shutdown.remove(first)  # type: ignore[arg-type]
    assert signal.getsignal(signal.SIGTERM) is _shutdown._on_sigterm, "given back too early"

    _shutdown.remove(second)  # type: ignore[arg-type]
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    _shutdown.remove(second)  # type: ignore[arg-type]  # and twice is not an error


def test_a_handler_set_after_it_is_left_alone():
    """It replaced the registry's - CPython has one per signal - and it is somebody else's now."""
    registry = FakeRegistry("jobs", [])
    _shutdown.add(registry)  # type: ignore[arg-type]
    theirs = lambda *_: None  # noqa: E731
    signal.signal(signal.SIGTERM, theirs)

    _shutdown.remove(registry)  # type: ignore[arg-type]

    assert signal.getsignal(signal.SIGTERM) is theirs


def test_on_sigterm_every_registry_stops_then_the_process_dies_of_it(ending, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    log: list[str] = []
    _shutdown.add(FakeRegistry("first", log))  # type: ignore[arg-type]
    _shutdown.add(FakeRegistry("second", log))  # type: ignore[arg-type]

    _shutdown._on_sigterm(signal.SIGTERM, None)

    default = repr(signal.SIG_DFL)
    assert log == [f"first stopped, SIGTERM {default}", f"second stopped, SIGTERM {default}"], (
        "the default was not put back before the jobs were waited for"
    )
    assert ending == [f"raise {signal.SIGTERM!r}", "_exit 143"]
    assert "Received SIGTERM: letting running jobs finish before the process stops" in [
        r.getMessage() for r in caplog.records
    ]


def test_a_registry_that_fails_to_stop_keeps_neither_the_others_nor_the_process(ending, caplog):
    """The point of the hook is that the process ends the way it was asked to."""
    log: list[str] = []
    _shutdown.add(FakeRegistry("first", log, fails=True))  # type: ignore[arg-type]
    _shutdown.add(FakeRegistry("second", log))  # type: ignore[arg-type]

    _shutdown._on_sigterm(signal.SIGTERM, None)

    assert [line.split(",")[0] for line in log] == ["first stopped", "second stopped"]
    assert ending == [f"raise {signal.SIGTERM!r}", "_exit 143"]
    assert any(
        r.levelno == logging.ERROR and "first" in r.getMessage() and r.exc_info
        for r in caplog.records
    ), "the failure was not logged"


def test_with_no_registry_left_sigterm_is_what_it_was(ending):
    """Left installed when the last registry stopped off the main thread, where it could not be
    given back. With nothing to stop it is the default again: the process dies of the signal."""
    registry = FakeRegistry("jobs", [])
    _shutdown.add(registry)  # type: ignore[arg-type]
    stopper = threading.Thread(target=_shutdown.remove, args=(registry,))
    stopper.start()
    stopper.join()
    assert signal.getsignal(signal.SIGTERM) is _shutdown._on_sigterm

    _shutdown._on_sigterm(signal.SIGTERM, None)

    assert ending == [f"raise {signal.SIGTERM!r}", "_exit 143"]


def test_a_copy_made_by_fork_forgets_the_parents_registries(ending):
    """The child has the parent's registries but none of their threads - they are the parent's
    to stop. Measured with a real fork in `test_jobs_at_exit.py`."""
    log: list[str] = []
    _shutdown.add(FakeRegistry("parent's", log))  # type: ignore[arg-type]

    _shutdown._forget_after_fork()
    _shutdown._on_sigterm(signal.SIGTERM, None)

    assert log == []
    assert ending == [f"raise {signal.SIGTERM!r}", "_exit 143"]
