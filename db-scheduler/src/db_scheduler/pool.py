"""The worker pool executions actually run on.

`concurrent.futures.ThreadPoolExecutor` cannot be used for this. Its workers are non-daemon and
the interpreter joins every non-daemon thread at exit, so one task that never returns keeps the
*process* alive for ever - and Python has no counterpart to Java's `Thread.interrupt()` to shake
it loose. Measured on this codebase: a hanging execution blocked exit indefinitely with the
standard pool, and not at all with this one.

Daemon threads are safe here in a way they would not be in general. Abandoning a running
execution at process death is the exact failure this library is built to survive: the row stays
picked, its heartbeat stops, and another instance - or this one after a restart - finds it dead
and revives it. A handler still running at shutdown is indistinguishable from the machine losing
power, which is already handled and already tested. Hanging the process instead would turn a
recoverable situation into an unrecoverable one.

`stop()` still waits: the pool is asked to finish gracefully, given `shutdown_max_wait`, and
only then left behind - with a warning naming what did not finish.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Protocol, runtime_checkable

__all__ = ["DaemonThreadPool", "WorkerPool"]

LOG = logging.getLogger("db_scheduler.pool")


@runtime_checkable
class WorkerPool(Protocol):
    """What the scheduler needs of a pool.

    Two methods, matching `ThreadPoolExecutor`'s - so one can still be passed in by a caller who
    wants the standard pool's semantics and accepts what they cost at exit.
    """

    def submit(self, fn: Callable[[], None]) -> object: ...

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None: ...


class _Stop:
    """Tells one worker to finish. One is queued per thread at shutdown."""

    __slots__ = ()


_STOP = _Stop()


class DaemonThreadPool:
    """A fixed pool of daemon workers pulling from one queue."""

    __slots__ = ("_is_shutdown", "_lock", "_max_workers", "_queue", "_threads")

    def __init__(self, max_workers: int, thread_name_prefix: str = "db-scheduler-") -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be greater than 0")
        self._max_workers = max_workers
        self._queue: queue.Queue[Callable[[], None] | _Stop] = queue.Queue()
        self._lock = threading.Lock()
        self._is_shutdown = False
        self._threads = [
            threading.Thread(
                target=self._work, name=f"{thread_name_prefix}{n}", daemon=True
            )
            for n in range(max_workers)
        ]
        for thread in self._threads:
            thread.start()

    def _work(self) -> None:
        while True:
            job = self._queue.get()
            if isinstance(job, _Stop):
                return
            try:
                job()
            except BaseException:
                # A worker that dies takes a slot with it, and the pool silently shrinks until
                # nothing runs. `ThreadPoolExecutor` survives this by putting the exception on
                # the future; there are no futures here, so it is logged and the worker lives.
                LOG.error("Unhandled exception in worker thread. Continuing.", exc_info=True)

    def submit(self, fn: Callable[[], None]) -> None:
        with self._lock:
            if self._is_shutdown:
                raise RuntimeError("cannot schedule new futures after shutdown")
            self._queue.put(fn)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._lock:
            already = self._is_shutdown
            self._is_shutdown = True
        if cancel_futures:
            # Drop what is queued but not started. The closest Python has to Java's
            # `shutdownNow()`, which also interrupts the running ones.
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
        if not already:
            for _ in self._threads:
                self._queue.put(_STOP)
        if wait:
            for thread in self._threads:
                thread.join()

    @property
    def is_shutdown(self) -> bool:
        return self._is_shutdown

    @property
    def alive_workers(self) -> int:
        return sum(1 for thread in self._threads if thread.is_alive())

    def __repr__(self) -> str:
        return (
            f"DaemonThreadPool(workers={self._max_workers}, alive={self.alive_workers}, "
            f"shutdown={self._is_shutdown})"
        )
