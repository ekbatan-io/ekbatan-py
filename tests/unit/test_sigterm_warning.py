"""The one warning `JobRegistry.start()` gives when nothing in the process handles SIGTERM.

SIGTERM is how a container is stopped. With its default handling CPython ends the process at once,
so a job part way through is cut off and runs again elsewhere after the dead-execution window.
`register_shutdown_hook` - on by default - takes the signal where nobody has it, so the warning is
left for the two cases where it cannot: the hook is off, or the registry was started off the main
thread, where CPython lets nobody set a signal handler. Anybody else holding the signal (uvicorn,
gunicorn, asyncio, the application) means somebody owns shutdown, and it stays quiet.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import threading
from collections.abc import Iterator
from datetime import timedelta

import pytest

import ekbatan.distributedjobs as distributedjobs
from ekbatan.distributedjobs import JobRegistry, ManualJobRegistry, _shutdown

LOGGER = "io.ekbatan.distributedjobs.JobRegistry"

WINDOW_AND_CUT_OFF = (
    "Jobs still running when the process gets SIGTERM (a container stop or a deploy) are cut "
    "off, and another instance runs them again after the dead-execution window (60 seconds). "
)


class FakeScheduler:
    """Enough of db-scheduler's `Scheduler` for `start()` and `stop()`; touches no database."""

    max_age_before_considered_dead = timedelta(seconds=60)

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def get_scheduled_executions_for_task(self, name: str, filter: object) -> list[object]:
        return []


#: The registries a test made, stopped after it: none is left on the exit list or holding SIGTERM.
MADE: list[JobRegistry] = []


@pytest.fixture(autouse=True)
def _a_fresh_process(monkeypatch) -> Iterator[None]:
    """Each test starts as a process that has not warned yet, with SIGTERM's default handling,
    and leaves the real handlers, the exit list and the hook's registries as it found them."""
    before = {n: signal.getsignal(n) for n in (signal.SIGTERM, signal.SIGINT)}
    monkeypatch.setattr(distributedjobs, "_sigterm_warned", False)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    yield
    while MADE:
        MADE.pop().stop()
    for number, handler in before.items():
        signal.signal(number, handler)


def a_registry(*, hook: bool, cls: type[JobRegistry] = JobRegistry) -> JobRegistry:
    # No jobs to verify: that is not what is being tested.
    registry = cls(FakeScheduler(), [], [], register_shutdown_hook=hook)  # type: ignore[arg-type]
    MADE.append(registry)
    return registry


def warnings_logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == LOGGER and r.levelno == logging.WARNING and "SIGTERM" in r.getMessage()
    ]


def start_off_the_main_thread(registry: JobRegistry) -> list[BaseException]:
    failures: list[BaseException] = []

    def start() -> None:
        try:
            registry.start()
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=start)
    worker.start()
    worker.join()
    return failures


def test_the_hook_is_off_and_nobody_handles_sigterm_so_it_warns(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)

    a_registry(hook=False).start()

    assert warnings_logged(caplog) == [
        "Nothing in this process handles SIGTERM, and register_shutdown_hook is off. "
        + WINDOW_AND_CUT_OFF
        + "To let them finish first, turn register_shutdown_hook back on, or call stop() on the "
        "JobRegistry from a SIGTERM handler of your own."
    ]


def test_started_off_the_main_thread_the_hook_cannot_take_sigterm_so_it_warns(caplog):
    """A FastAPI lifespan can start the registry from a thread pool, and Starlette's TestClient
    always does. CPython sets signal handlers from the main thread only."""
    caplog.set_level(logging.INFO, logger=LOGGER)

    failures = start_off_the_main_thread(a_registry(hook=True))

    assert failures == [], "it refused instead of warning"
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    assert warnings_logged(caplog) == [
        "Nothing in this process handles SIGTERM, and the JobRegistry cannot: it was started off "
        "the main thread, and only the main thread can set a signal handler. "
        + WINDOW_AND_CUT_OFF
        + "To let them finish first, start the JobRegistry on the main thread, or call its "
        "stop() from a SIGTERM handler of your own."
    ]


def test_on_the_main_thread_the_hook_takes_sigterm_so_it_is_silent(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)

    a_registry(hook=True).start()

    assert warnings_logged(caplog) == []
    assert signal.getsignal(signal.SIGTERM) is _shutdown._on_sigterm


def test_it_warns_once_per_process_however_often_registries_start(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    first, second = a_registry(hook=False), a_registry(hook=False)

    first.start()
    first.stop()
    first.start()
    second.start()

    assert len(warnings_logged(caplog)) == 1


def test_with_the_hook_off_it_changes_no_handler():
    a_registry(hook=False).start()

    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL


def test_the_window_named_is_the_schedulers_own(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    registry = a_registry(hook=False)
    registry.scheduler.max_age_before_considered_dead = timedelta(minutes=5)  # type: ignore[misc]

    registry.start()

    assert "(300 seconds)" in warnings_logged(caplog)[0]


@pytest.mark.parametrize("hook", [True, False], ids=["hook on", "hook off"])
@pytest.mark.parametrize(
    "handler",
    [lambda *_: None, signal.SIG_IGN],
    ids=["the application's own handler", "SIGTERM ignored on purpose"],
)
def test_somebody_else_holding_sigterm_keeps_it_silent_and_keeps_it(caplog, handler, hook):
    caplog.set_level(logging.INFO, logger=LOGGER)
    signal.signal(signal.SIGTERM, handler)

    a_registry(hook=hook).start()

    assert warnings_logged(caplog) == []
    assert signal.getsignal(signal.SIGTERM) is handler, "the registry took SIGTERM from its owner"


def test_an_asyncio_signal_handler_keeps_it_silent_and_keeps_it(caplog):
    """`loop.add_signal_handler` - what an asyncio server uses - counts as somebody owning it."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    taken: list[object] = []

    async def started_inside_the_loop() -> None:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, lambda: None)
        try:
            a_registry(hook=True).start()
            taken.append(signal.getsignal(signal.SIGTERM))
        finally:
            loop.remove_signal_handler(signal.SIGTERM)

    asyncio.run(started_inside_the_loop())

    assert warnings_logged(caplog) == []
    assert taken and taken[0] is not _shutdown._on_sigterm


@pytest.mark.parametrize("handled", [False, True], ids=["nobody handles it", "a handler is set"])
def test_started_off_the_main_thread_it_reads_the_same_process_wide_answer(caplog, handled):
    """The signal's handler is the process's, and `getsignal` reads it from any thread."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    if handled:
        signal.signal(signal.SIGTERM, lambda *_: None)

    failures = start_off_the_main_thread(a_registry(hook=False))

    assert failures == []
    assert len(warnings_logged(caplog)) == (0 if handled else 1)


def test_a_manual_registry_is_silent_and_takes_nothing(caplog):
    """It runs nothing by itself - only what a test asks for, on the test's own thread."""
    caplog.set_level(logging.INFO, logger=LOGGER)

    a_registry(hook=False, cls=ManualJobRegistry).start()

    assert warnings_logged(caplog) == []
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
