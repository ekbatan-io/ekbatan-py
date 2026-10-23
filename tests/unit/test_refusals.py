"""The two refusals' helpers, and the one object that owns a thread but no connection.

On real databases - every door, both modes, the four databases, real forks - they are proven in
`tests/integration/test_refusals.py`.
"""

from __future__ import annotations

import asyncio
import os
import select
import signal
import time
import warnings
from collections.abc import Callable

import pytest

import ekbatan
from ekbatan import BlockingCallError, EkbatanError, ForkedProcessError
from ekbatan._guards import refuse_if_forked, refuse_on_an_event_loop
from ekbatan.events import HandlerLoop


def test_both_errors_are_exported_and_are_ekbatan_errors():
    assert ekbatan.ForkedProcessError is ForkedProcessError
    assert ekbatan.BlockingCallError is BlockingCallError
    assert "ForkedProcessError" in ekbatan.__all__ and "BlockingCallError" in ekbatan.__all__
    for kind in (ForkedProcessError, BlockingCallError):
        assert issubclass(kind, EkbatanError) and issubclass(kind, RuntimeError)


def test_the_fork_refusal_says_what_happened_and_what_to_do():
    refuse_if_forked(os.getpid(), "Thing(1)")  # the process that made it: nothing to say
    with pytest.raises(ForkedProcessError) as error:
        refuse_if_forked(os.getpid() + 1, "Thing(1)")
    assert str(error.value) == (
        "Thing(1) was created in another process and copied into this one by fork() - "
        "gunicorn --preload, uwsgi, Celery prefork, RQ and multiprocessing all fork. Its "
        "connections and threads belong to the process that created it. Create ekbatan when "
        "your app starts - in the FastAPI lifespan, or at the top of the worker's main() - not "
        "at import time."
    )


def test_the_loop_refusal_fires_on_a_loop_s_thread_only():
    refuse_on_an_event_loop("the sync API (X)")  # no loop at all

    async def on_the_loop() -> None:
        refuse_on_an_event_loop("the sync API (X)")

    with pytest.raises(BlockingCallError) as error:
        asyncio.run(on_the_loop())
    assert str(error.value) == (
        "the sync API (X) was called on an asyncio event loop's thread (inside `async def`). The "
        "sync API waits on the database, and here it would stop the event loop and every request "
        "on it. In `async def` code use ekbatan.aio; or make the endpoint a plain `def`; or call "
        "it with `await asyncio.to_thread(...)`."
    )

    async def off_the_loop() -> None:
        await asyncio.to_thread(refuse_on_an_event_loop, "the sync API (X)")
        await asyncio.get_running_loop().run_in_executor(
            None, refuse_on_an_event_loop, "the sync API (X)"
        )

    asyncio.run(off_the_loop())  # a worker thread has no running loop


def _in_a_child(work: Callable[[], object], timeout: float = 20.0) -> str:
    """``work`` in a child made by ``os.fork()``: what it returned, or ``"<type>: <message>"``;
    ``"the child hung"`` if it did not finish in time."""
    read, write = os.pipe()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)  # forking with threads, on purpose
        pid = os.fork()
    if pid == 0:
        try:
            os.close(read)
            try:
                said = str(work())
            except BaseException as error:
                said = f"{type(error).__name__}: {error}"
            os.write(write, said.encode())
        finally:
            os._exit(0)
    os.close(write)
    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    try:
        while True:
            ready, _, _ = select.select([read], [], [], max(deadline - time.monotonic(), 0))
            if not ready:
                os.kill(pid, signal.SIGKILL)
                return "the child hung"
            chunk = os.read(read, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(read)
        os.waitpid(pid, 0)
    return b"".join(chunks).decode()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="no fork() on this platform")
def test_a_handler_loop_started_before_fork_is_refused_in_the_child_rather_than_hanging():
    """Its thread does not survive fork(): `run` in the copy would wait for ever."""
    loop = HandlerLoop()
    try:
        assert loop.run(asyncio.sleep(0, "parent")) == "parent"

        said = _in_a_child(lambda: loop.run(asyncio.sleep(0, "child")))
        assert said.startswith("ForkedProcessError: HandlerLoop('ekbatan-handler-loop', running)")
        assert said.endswith("not at import time.")

        def own() -> object:
            mine = HandlerLoop()
            try:
                return mine.run(asyncio.sleep(0, "its own"))
            finally:
                mine.close()

        assert _in_a_child(own) == "its own"
        assert loop.run(asyncio.sleep(0, "still")) == "still", "the parent's loop is untouched"
    finally:
        loop.close()
