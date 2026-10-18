"""Re-entry counting and the hold watchdog, shared by every backend.

Each backend only has to say how to take the underlying lock and how to give it back. Counting
nested holds, arbitrating between a close and an expiring watchdog, and making the lease report
honestly all happen here, once.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import timedelta
from enum import Enum
from typing import Any

from ._deadlines import Deadlines
from .base import Lease

__all__ = ["KeyedReentrantHolder", "ReleaseReason"]

LOG = logging.getLogger("io.ekbatan.core.concurrent.KeyedReentrantHolder")


class ReleaseReason(Enum):
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class _HoldingCore:
    """One holder's hold on one key, however many times it has re-entered - the counting and
    the close-or-expire arbitration, shared by the sync hold and ``ekbatan.aio.locks``' one.

    The two steps that decide something, `_count_down` and `_mark_expired`, run under the gate
    and say what to release; the subclass releases it, calling or awaiting the callback.
    """

    __slots__ = (
        "_gate",
        "_holder",
        "count",
        "deadline",
        "key",
        "payload",
        "release_cb",
        "released",
    )

    def __init__(self, holder: Any, key: str, payload: Any, release_cb: Callable[..., Any]) -> None:
        self._holder = holder
        self._gate = threading.Lock()
        self.key = key
        self.payload = payload
        self.release_cb = release_cb
        self.count = 1
        self.released = False
        self.deadline: Any = None

    @property
    def held(self) -> bool:
        return not self.released

    def enter(self) -> bool:
        with self._gate:
            if self.released:
                return False
            self.count += 1
            return True

    def _count_down(self) -> tuple[Any, Any, Callable[..., Any]] | None:
        """One close. The deadline, payload and callback to release with when this was the
        outermost one, else None."""
        with self._gate:
            if self.released:
                return None
            self.count -= 1
            # Only the outermost close frees the key. A nested region that closed its own lease
            # has not left the outer one; releasing here would let another holder in while the
            # outer holder still believes it has exclusive access.
            if self.count > 0:
                return None
            self.released = True
            return self.deadline, self.payload, self.release_cb

    def _mark_expired(self) -> tuple[Any, Callable[..., Any]] | None:
        """The hold limit fired. The payload and callback to release with, or None when the
        holder closed first and owns the release."""
        with self._gate:
            if self.released:
                return None
            self.released = True
            return self.payload, self.release_cb


class _Holding(_HoldingCore):
    """One thread's hold on one key."""

    __slots__ = ()

    def release_one(self) -> None:
        step = self._count_down()
        if step is None:
            return
        deadline, payload, callback = step
        if deadline is not None:
            self._holder.deadlines.cancel(deadline)
        self._holder._forget(self)
        callback(payload, ReleaseReason.CLOSED)

    def expire(self) -> None:
        step = self._mark_expired()
        if step is None:
            return
        payload, callback = step
        self._holder._forget(self)
        try:
            callback(payload, ReleaseReason.EXPIRED)
            LOG.warning(EXPIRED_LINE, self.key)
        except BaseException:
            # BaseException, as Java catches `RuntimeException | Error` here. This runs on the
            # watchdog thread, which every other hold depends on: anything that escapes would
            # reach stderr through the threading hook rather than the logger, and would take
            # the watchdog down with it.
            # Reported against the key that caused it. The backend lock may stay held until its
            # own expiry - but the hold is already marked released, and putting that back would
            # strand the entry instead.
            LOG.exception(EXPIRY_FAILED_LINE, self.key)


#: Java's two lines when the hold limit fires: released, or releasing failed.
EXPIRED_LINE = "Auto-released held lock for key %s (hold limit exceeded)"
EXPIRY_FAILED_LINE = (
    "Failed to auto-release held lock for key %s after the hold limit was exceeded; "
    "the backend lock may remain held until its own expiry"
)


class _HolderCore:
    """Who holds what, keyed by ``(holder, key)`` - a thread here, a task in ``ekbatan.aio``.

    Work handed elsewhere from inside a held region - a pool's thread, another task - is a
    different holder and waits like anyone else. Anything else would let concurrency walk
    straight through the exclusion, silently, exactly where it was introduced.
    """

    __slots__ = ("_gate", "_held")

    def __init__(self) -> None:
        self._gate = threading.Lock()
        self._held: dict[tuple[Any, str], Any] = {}

    def _reentered(self, holder: Any, key: str) -> Any:
        """The hold ``holder`` already has on ``key``, counted up once more; or None."""
        with self._gate:
            holding = self._held.get((holder, key))
        if holding is None:
            return None
        if holding.enter():
            return holding
        # A stale entry the watchdog has already fired on; clear it in passing.
        self._forget(holding)
        return None

    def _remember(self, holder: Any, key: str, holding: Any) -> None:
        with self._gate:
            self._held[(holder, key)] = holding

    def _drop(self, holder: Any, key: str) -> None:
        with self._gate:
            self._held.pop((holder, key), None)

    def _forget(self, holding: Any) -> None:
        with self._gate:
            for entry, current in list(self._held.items()):
                if current is holding:
                    del self._held[entry]
                    return


class KeyedReentrantHolder(_HolderCore):
    """Tracks who holds what, keyed by ``(thread, key)``.

    Per thread, not per call stack.
    """

    __slots__ = ("deadlines",)

    def __init__(self) -> None:
        super().__init__()
        #: One thread for every hold this holder is watching, rather than one each. Its
        #: thread is not started until the first hold is registered.
        self.deadlines = Deadlines()

    def reenter(self, key: str) -> Lease | None:
        holding = self._reentered(threading.get_ident(), key)
        return None if holding is None else Lease(holding)

    def register(
        self,
        key: str,
        payload: Any,
        max_hold: timedelta,
        release_cb: Callable[[Any, ReleaseReason], None],
    ) -> Lease:
        holding = _Holding(self, key, payload, release_cb)
        thread = threading.get_ident()
        try:
            self._remember(thread, key, holding)
            holding.deadline = self.deadlines.after(max_hold.total_seconds(), holding.expire)
            return Lease(holding)
        except BaseException as failure:
            # Registration failed after the backend lock was taken. Give it back, or it is held
            # by nobody until it expires.
            self._drop(thread, key)
            if holding.deadline is not None:
                self.deadlines.cancel(holding.deadline)
            try:
                release_cb(payload, ReleaseReason.CLOSED)
            except BaseException as cleanup:
                # Attached, never substituted. Java says the same thing with `addSuppressed`,
                # and the reason is worth keeping: raising the cleanup failure instead would
                # put "returning the connection failed" in front of the caller and bury the
                # thing that actually went wrong one link down the context chain.
                failure.add_note(f"releasing the lock afterwards also failed: {cleanup!r}")
            raise
