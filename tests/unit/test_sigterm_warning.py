"""The one warning `JobRegistry.start()` gives when nothing in the process handles SIGTERM.

SIGTERM is how a container is stopped. With its default handling CPython ends the process at once,
so a job part way through is cut off and runs again elsewhere after the dead-execution window.
The registry does not take the signal itself - that is `register_shutdown_hook()`, and it is
opt-in - but it says so, once, when nobody has it. Anybody else holding the signal (uvicorn,
gunicorn, asyncio, the application) means somebody owns shutdown, and it stays quiet.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import threading
from datetime import timedelta

import pytest

import ekbatan.distributedjobs as distributedjobs
from ekbatan.distributedjobs import JobRegistry, ManualJobRegistry

LOGGER = "io.ekbatan.distributedjobs.JobRegistry"


class FakeScheduler:
    """Enough of db-scheduler's `Scheduler` for `start()` and `stop()`; touches no database."""

    max_age_before_considered_dead = timedelta(seconds=60)

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def get_scheduled_executions_for_task(self, name: str, filter: object) -> list[object]:
        return []


def a_registry(*, hook: bool = False, cls: type[JobRegistry] = JobRegistry) -> JobRegistry:
    # No jobs to verify, and kept off `atexit`: neither is what is being tested.
    return cls(FakeScheduler(), [], [], shutdown_hook=hook, stop_on_exit=False)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _a_fresh_process(monkeypatch):
    """Each test starts as a process that has not warned yet, with SIGTERM's default handling,
    and leaves the real handlers as it found them."""
    before = {n: signal.getsignal(n) for n in (signal.SIGTERM, signal.SIGINT)}
    monkeypatch.setattr(distributedjobs, "_sigterm_warned", False)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    yield
    for number, handler in before.items():
        signal.signal(number, handler)


def warnings_logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == LOGGER and r.levelno == logging.WARNING and "SIGTERM" in r.getMessage()
    ]


def test_nobody_handles_sigterm_so_it_warns(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)

    a_registry().start()

    assert warnings_logged(caplog) == [
        "Nothing in this process handles SIGTERM. Jobs still running when the process gets "
        "SIGTERM (a container stop or a deploy) are cut off, and another instance runs them "
        "again after the dead-execution window (60 seconds). To let them finish first, call "
        "register_shutdown_hook() on the job registry builder, or call stop() on the "
        "JobRegistry from your own shutdown code, such as a FastAPI lifespan."
    ]


def test_it_warns_once_per_process_however_often_registries_start(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    first, second = a_registry(), a_registry()

    first.start()
    first.stop()
    first.start()
    second.start()

    assert len(warnings_logged(caplog)) == 1


def test_it_changes_no_handler():
    a_registry().start()

    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL


def test_the_window_named_is_the_schedulers_own(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    registry = a_registry()
    registry.scheduler.max_age_before_considered_dead = timedelta(minutes=5)  # type: ignore[misc]

    registry.start()

    assert "(300 seconds)" in warnings_logged(caplog)[0]


def test_with_the_hook_it_is_silent(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    registry = a_registry(hook=True)

    registry.start()
    try:
        assert warnings_logged(caplog) == []
    finally:
        registry.stop()


@pytest.mark.parametrize(
    "handler",
    [lambda *_: None, signal.SIG_IGN],
    ids=["the application's own handler", "SIGTERM ignored on purpose"],
)
def test_somebody_else_holding_sigterm_keeps_it_silent(caplog, handler):
    caplog.set_level(logging.INFO, logger=LOGGER)
    signal.signal(signal.SIGTERM, handler)

    a_registry().start()

    assert warnings_logged(caplog) == []


def test_an_asyncio_signal_handler_keeps_it_silent(caplog):
    """`loop.add_signal_handler` - what an asyncio server uses - counts as somebody owning it."""
    caplog.set_level(logging.INFO, logger=LOGGER)

    async def started_inside_the_loop() -> None:
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, lambda: None)
        a_registry().start()

    asyncio.run(started_inside_the_loop())

    assert warnings_logged(caplog) == []


@pytest.mark.parametrize("handled", [False, True], ids=["nobody handles it", "a handler is set"])
def test_started_off_the_main_thread_it_reads_the_same_process_wide_answer(caplog, handled):
    """A FastAPI lifespan can start the registry from a thread pool. The signal's handler is the
    process's, and `getsignal` reads it from any thread."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    if handled:
        signal.signal(signal.SIGTERM, lambda *_: None)
    failures: list[BaseException] = []

    def start() -> None:
        try:
            a_registry().start()
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=start)
    worker.start()
    worker.join()

    assert failures == []
    assert len(warnings_logged(caplog)) == (0 if handled else 1)


def test_a_manual_registry_is_silent(caplog):
    """It runs nothing by itself - only what a test asks for, on the test's own thread."""
    caplog.set_level(logging.INFO, logger=LOGGER)

    a_registry(cls=ManualJobRegistry).start()

    assert warnings_logged(caplog) == []
