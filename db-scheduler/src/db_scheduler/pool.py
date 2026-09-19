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
import os
import queue
import threading
import time
import weakref
from collections.abc import Callable
from typing import Protocol, runtime_checkable

__all__ = ["DaemonThreadPool", "WorkerPool"]

LOG = logging.getLogger("db_scheduler.pool")

#: Every live pool, so a `fork()` can mark them all. Weak, so it never keeps one alive.
_LIVE_POOLS: weakref.WeakSet[DaemonThreadPool] = weakref.WeakSet()


def _mark_pools_forked() -> None:
    """Threads do not survive `fork()`: the child gets the queue and none of the workers.

    Left alone, a pool in a forked child accepts work and silently never runs it - the worst
    failure available. It is marked broken instead, so submitting raises and says what to do.

    Recreating the threads in the child would be worse, not better: the child would also have
    inherited the parent's database connections, which cannot be shared across a fork, and the
    scheduler would then poll on a corrupted connection. A `Scheduler` must be built *after* the
    fork - which is the same rule SQLAlchemy has for engines, for the same reason.
    """
    for pool in _LIVE_POOLS:
        pool.mark_forked()


if hasattr(os, "register_at_fork"):  # not on Windows, which has no fork
    os.register_at_fork(after_in_child=_mark_pools_forked)


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

    # `__weakref__` so a pool can go in `_LIVE_POOLS`; a slotted class is not weak-referenceable
    # without it.
    __slots__ = (
        "__weakref__",
        "_forked",
        "_idle",
        "_is_shutdown",
        "_lock",
        "_max_workers",
        "_name",
        "_queue",
        "_threads",
    )

    def __init__(self, max_workers: int, thread_name_prefix: str = "db-scheduler-") -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be greater than 0")
        self._max_workers = max_workers
        self._name = thread_name_prefix
        self._queue: queue.Queue[Callable[[], None] | _Stop] = queue.Queue()
        self._lock = threading.Lock()
        self._is_shutdown = False
        self._forked = False
        #: Workers blocked on the queue, waiting for something to do. A new one is started only
        #: when there are none of these and the ceiling has not been reached.
        self._idle = 0
        #: Started on demand, not here. `max_workers` is a ceiling rather than a reservation:
        #: a registry of twenty jobs that are rarely due held twenty idle OS threads for the
        #: life of the process, and now holds as many as it has actually needed at once.
        #:
        #: Java's `newCachedThreadPool()` also *retires* a thread that has been idle for a
        #: minute. Nothing in CPython does - a `ThreadPoolExecutor` grows and never shrinks -
        #: and neither does this: what it costs to build is a race at the seam between the
        #: last worker exiting and new work arriving, and what it buys is only a service that
        #: bursts and then goes quiet for a long time.
        self._threads: list[threading.Thread] = []
        _LIVE_POOLS.add(self)

    def _work(self) -> None:
        while True:
            with self._lock:
                self._idle += 1
            job = self._queue.get()
            with self._lock:
                self._idle -= 1
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
        if self._forked:
            raise RuntimeError(
                "this worker pool was created before a fork() and has no worker threads in "
                "this process - anything submitted here would never run. Build the Scheduler "
                "after forking (the same rule SQLAlchemy has for engines, and for the same "
                "reason: the database connections did not survive either)."
            )
        with self._lock:
            if self._is_shutdown:
                raise RuntimeError("cannot schedule new futures after shutdown")
            # `_idle` can be a moment stale - a worker that has taken a job but not yet
            # decremented it reads as idle - so this occasionally starts one thread more than
            # was strictly needed. Bounded by `max_workers`, and the other way round would be
            # the expensive mistake: a job queued with nobody to run it waits for the next
            # submission.
            if self._idle == 0 and len(self._threads) < self._max_workers:
                worker = threading.Thread(
                    target=self._work,
                    name=f"{self._name}{len(self._threads)}",
                    daemon=True,
                )
                self._threads.append(worker)
                worker.start()
            self._queue.put(fn)

    def shutdown(
        self,
        wait: bool = True,
        *,
        cancel_futures: bool = False,
        timeout: float | None = None,
    ) -> None:
        """Stop accepting work and tell the workers to finish.

        The draining and the stop-signals happen together, under the lock, and the signals are
        queued *every* time. Both matter: `cancel_futures` empties the queue, so a second
        shutdown that drained first would throw away the signals the first one queued and strand
        every worker for ever - and a `wait=True` caller would then block for ever waiting for
        threads that had been told nothing. Queueing extra signals is harmless; losing them is
        not.

        `timeout` bounds the join. `ThreadPoolExecutor` has no such argument and blocks for as
        long as it takes, which for a class whose entire purpose is that shutdown terminates is
        the wrong default to inherit blindly.
        """
        with self._lock:
            self._is_shutdown = True
            if cancel_futures:
                # Drop what is queued but not started. The closest Python has to Java's
                # `shutdownNow()`, which also interrupts the running ones. A job already handed
                # to a worker still runs - `ThreadPoolExecutor` cannot cancel those either.
                while True:
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        break
            for _ in self._threads:
                self._queue.put(_STOP)

        # Nothing to join in a forked child: the thread objects were copied but the threads
        # themselves were not, and joining one would block for ever on a lock nobody holds.
        if wait and not self._forked:
            deadline = None if timeout is None else time.monotonic() + timeout
            for thread in self._threads:
                thread.join(
                    None if deadline is None else max(deadline - time.monotonic(), 0.0)
                )

    @property
    def is_shutdown(self) -> bool:
        return self._is_shutdown

    def mark_forked(self) -> None:
        """Called in a child process after `fork()`. Not for general use."""
        self._forked = True

    @property
    def is_forked(self) -> bool:
        """True in a process that inherited this pool through `fork()`."""
        return self._forked

    @property
    def alive_workers(self) -> int:
        return sum(1 for thread in self._threads if thread.is_alive())

    def __repr__(self) -> str:
        return (
            f"DaemonThreadPool(workers={self._max_workers}, alive={self.alive_workers}, "
            f"shutdown={self._is_shutdown})"
        )
