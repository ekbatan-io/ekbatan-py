"""The event loop async event handlers run on.

Dispatch runs handlers on threads, a batch at a time. A handler whose ``handle`` is
``async def`` is run on this loop instead: the delivery thread hands the coroutine over and waits
for it, so everything dispatch decides afterwards - success, retry, backoff, expiry - is decided
exactly as for a sync handler, from what the handler returned or raised.

**One loop, for as long as it is open.** It is started on its own thread the first time an async
handler runs, and every async handler after that runs on it - a batch of them at the same time.
Not ``asyncio.run`` per event: the pools an async handler awaits are bound to the loop they were
opened on, and a loop per event would open them again for every event.

**What a handler awaits belongs to this loop.** An async registry, executor or Redis client used
by async handlers is used only here - build them for the handlers, as the sync side builds a pool
for them - and close them here too, before the loop: ``loop.run(registry.aclose())``. Closing one
from another loop leaves its connections behind.

A dispatch makes one for itself unless it is handed one; hand it one to close what the handlers
use on it at shutdown.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from collections.abc import Awaitable
from typing import Any

__all__ = ["HandlerLoop"]

LOG = logging.getLogger("io.ekbatan.events.localeventhandler.job.EventHandlingJob")


class HandlerLoop:
    """An asyncio event loop on a thread of its own, and a blocking way to run work on it."""

    __slots__ = ("_closed", "_gate", "_loop", "_name", "_thread")

    def __init__(self, *, name: str = "ekbatan-handler-loop") -> None:
        self._name = name
        self._gate = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._closed = False

    def run[T](self, work: Awaitable[T]) -> T:
        """Run ``work`` on the loop and wait for its result, from any thread but the loop's own.

        Whatever ``work`` raises is raised here, as it was raised: a cancellation as
        ``asyncio.CancelledError``, and a ``KeyboardInterrupt`` or ``SystemExit`` too - carried
        across rather than let loose on the loop, where asyncio would stop the loop with it.
        """
        loop = self._started()
        if threading.current_thread() is self._thread:
            _close(work)
            raise RuntimeError(
                "HandlerLoop.run was called from the handler loop's own thread: it would wait for "
                "itself. Await the work instead."
            )
        done: concurrent.futures.Future[Any] = concurrent.futures.Future()

        async def guarded() -> None:
            try:
                result = await work
            except asyncio.CancelledError:
                done.set_exception(asyncio.CancelledError())
                raise
            except BaseException as error:  # carried to the caller, never loose on the loop
                done.set_exception(error)
            else:
                done.set_result(result)

        def start() -> None:
            loop.create_task(guarded())

        loop.call_soon_threadsafe(start)
        return done.result()  # type: ignore[no-any-return]

    @property
    def running(self) -> bool:
        """Whether its thread has been started and not yet closed."""
        return self._thread is not None and not self._closed

    def close(self, timeout: float = 10.0) -> None:
        """Cancel what is still running on it, stop it and let its thread go. Safe to call twice,
        and safe to call on one never started."""
        with self._gate:
            if self._closed:
                return
            self._closed = True
            loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return

        async def wind_down() -> None:
            current = asyncio.current_task()
            pending = [t for t in asyncio.all_tasks() if t is not current]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await loop.shutdown_asyncgens()

        try:
            asyncio.run_coroutine_threadsafe(wind_down(), loop).result(timeout)
        except Exception:
            LOG.warning("the handler loop did not wind down in %ss", timeout, exc_info=True)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout)
        if not thread.is_alive():
            loop.close()

    def __repr__(self) -> str:
        state = "closed" if self._closed else "running" if self._thread else "not started"
        return f"HandlerLoop({self._name!r}, {state})"

    def _started(self) -> asyncio.AbstractEventLoop:
        with self._gate:
            if self._closed:
                raise RuntimeError(f"{self!r} is closed")
            if self._loop is None:
                loop = asyncio.new_event_loop()
                ready = threading.Event()

                def serve() -> None:
                    asyncio.set_event_loop(loop)
                    loop.call_soon(ready.set)
                    loop.run_forever()

                thread = threading.Thread(target=serve, name=self._name, daemon=True)
                thread.start()
                ready.wait()
                self._loop, self._thread = loop, thread
            return self._loop


def _close(work: Awaitable[Any]) -> None:
    close = getattr(work, "close", None)
    if callable(close):
        close()
