"""What an async keyed lock is: the sync one's rules, with the task as the holder.

Everything that decides something is the sync code's, in ``ekbatan.locks``: the argument checks,
the counting of nested holds, the arbitration between a close and the hold limit firing, the
lease that may be closed twice. What differs here is who the holder is and what waits:

- **The holder is the task**, as in the sync API it is the thread. A task that takes a key it
  already holds re-enters; another task - one it started inside the held region included -
  waits like anyone else. ``asyncio.gather`` inside a held region does not share the lock.
- **Releasing awaits.** The backend is given the lock back over the network, so
  ``release()`` is a coroutine, and ``async with`` is how a lease is closed.
- **The hold limit runs on the event loop** - ``loop.call_later`` - rather than on a watchdog
  thread: giving back a lock held on an async connection is itself awaited, on the loop the
  connection belongs to.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Generator
from datetime import timedelta
from types import TracebackType
from typing import Any, Protocol, runtime_checkable

from ...errors import ValidationError
from ...locks._holder import (
    EXPIRED_LINE,
    EXPIRY_FAILED_LINE,
    LOG,
    ReleaseReason,
    _HolderCore,
    _HoldingCore,
)
from ...locks.base import _LeaseCore

__all__ = ["AsyncKeyedLockProvider", "AsyncLease"]

#: What a backend's release looks like here: awaited, with the payload and the reason.
AsyncRelease = Callable[[Any, ReleaseReason], Awaitable[None]]


class AsyncLease(_LeaseCore):
    """A held lock. Closing releases it; closing twice is harmless.

    Every acquisition returns its own lease, re-entries included, and each must be closed once -
    ``async with`` does that.
    """

    __slots__ = ()

    async def release(self) -> None:
        if self._close_once():
            await self._holding.release_one()

    async def __aenter__(self) -> AsyncLease:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.release()


class Acquiring:
    """What ``acquire`` hands back: awaited, it is the lease; entered with ``async with``, it
    takes the lease and releases it on the way out.

        async with locks.acquire(key, max_hold):        # the usual way
            ...
        lease = await locks.acquire(key, max_hold)      # or by hand
    """

    __slots__ = ("_lease", "_take")

    def __init__(self, take: Callable[[], Awaitable[AsyncLease]]) -> None:
        self._take = take
        self._lease: AsyncLease | None = None

    def __await__(self) -> Generator[Any, None, AsyncLease]:
        return self._take().__await__()

    async def __aenter__(self) -> AsyncLease:
        self._lease = await self._take()
        return self._lease

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._lease is not None:
            await self._lease.release()


@runtime_checkable
class AsyncKeyedLockProvider(Protocol):
    """Mutual exclusion on a string key, for code on an event loop."""

    def acquire(self, key: str, max_hold: timedelta) -> Acquiring:
        """Wait for the key, however long it takes, then hold it for at most ``max_hold``.
        Awaited, or entered with ``async with``."""
        ...

    async def try_acquire(
        self, key: str, max_wait: timedelta, max_hold: timedelta
    ) -> AsyncLease | None:
        """Wait up to ``max_wait``. ``None`` means someone else has it, not that anything
        went wrong. Pass zero for a single attempt."""
        ...

    def close(self) -> None: ...


class _AsyncHolding(_HoldingCore):
    """One task's hold on one key."""

    __slots__ = ()

    async def release_one(self) -> None:
        step = self._count_down()
        if step is None:
            return
        deadline, payload, callback = step
        if deadline is not None:
            deadline.cancel()
        self._holder._forget(self)
        await callback(payload, ReleaseReason.CLOSED)

    def expire(self) -> None:
        """Called on the loop when the hold limit passes."""
        step = self._mark_expired()
        if step is None:
            return
        payload, callback = step
        self._holder._forget(self)
        self._holder._background(self._give_back_expired(payload, callback))

    async def _give_back_expired(self, payload: Any, callback: AsyncRelease) -> None:
        try:
            await callback(payload, ReleaseReason.EXPIRED)
            LOG.warning(EXPIRED_LINE, self.key)
        except Exception:
            # Logged against the key, as the sync watchdog does; nothing else is waiting on
            # this task to hear about it.
            LOG.exception(EXPIRY_FAILED_LINE, self.key)


class AsyncKeyedReentrantHolder(_HolderCore):
    """Tracks who holds what, keyed by ``(task, key)``."""

    __slots__ = ("_tasks",)

    def __init__(self) -> None:
        super().__init__()
        #: Releases started by an expired hold, kept until they finish - a task nobody holds
        #: may be collected before it runs.
        self._tasks: set[asyncio.Task[None]] = set()

    def reenter(self, key: str) -> AsyncLease | None:
        holding = self._reentered(_task(), key)
        return None if holding is None else AsyncLease(holding)

    async def register(
        self, key: str, payload: Any, max_hold: timedelta, release_cb: AsyncRelease
    ) -> AsyncLease:
        holding = _AsyncHolding(self, key, payload, release_cb)
        task = _task()
        try:
            self._remember(task, key, holding)
            loop = asyncio.get_running_loop()
            holding.deadline = loop.call_later(max_hold.total_seconds(), holding.expire)
            return AsyncLease(holding)
        except BaseException as failure:
            # As the sync holder: the backend lock was taken, so give it back.
            self._drop(task, key)
            if holding.deadline is not None:
                holding.deadline.cancel()
            try:
                await release_cb(payload, ReleaseReason.CLOSED)
            except BaseException as cleanup:
                failure.add_note(f"releasing the lock afterwards also failed: {cleanup!r}")
            raise

    def _background(self, work: Awaitable[None]) -> None:
        task: asyncio.Task[None] = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _task() -> asyncio.Task[Any]:
    task = asyncio.current_task()
    if task is None:
        raise ValidationError("an async keyed lock is taken from inside a task")
    return task
