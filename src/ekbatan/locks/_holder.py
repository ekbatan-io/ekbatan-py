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

from ._deadlines import Deadline, Deadlines
from .base import Lease

__all__ = ["KeyedReentrantHolder", "ReleaseReason"]

LOG = logging.getLogger("io.ekbatan.core.concurrent.KeyedReentrantHolder")


class ReleaseReason(Enum):
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class _Holding:
    """One thread's hold on one key, however many times it has re-entered."""

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

    def __init__(
        self,
        holder: KeyedReentrantHolder,
        key: str,
        payload: Any,
        release_cb: Callable[[Any, ReleaseReason], None],
    ) -> None:
        self._holder = holder
        self._gate = threading.Lock()
        self.key = key
        self.payload = payload
        self.release_cb = release_cb
        self.count = 1
        self.released = False
        self.deadline: Deadline | None = None

    @property
    def held(self) -> bool:
        return not self.released

    def enter(self) -> bool:
        with self._gate:
            if self.released:
                return False
            self.count += 1
            return True

    def release_one(self) -> None:
        with self._gate:
            if self.released:
                return
            self.count -= 1
            # Only the outermost close frees the key. A nested region that closed its own lease
            # has not left the outer one; releasing here would let another thread in while the
            # outer holder still believes it has exclusive access.
            if self.count > 0:
                return
            self.released = True
            deadline, payload, callback = self.deadline, self.payload, self.release_cb

        if deadline is not None:
            self._holder.deadlines.cancel(deadline)
        self._holder._forget(self)
        callback(payload, ReleaseReason.CLOSED)

    def expire(self) -> None:
        with self._gate:
            if self.released:
                return  # the holder closed first and owns the release
            self.released = True
            payload, callback = self.payload, self.release_cb

        self._holder._forget(self)
        try:
            callback(payload, ReleaseReason.EXPIRED)
            LOG.warning("released the lock on %r: its hold limit elapsed", self.key)
        except BaseException:
            # BaseException, as Java catches `RuntimeException | Error` here. This runs on the
            # watchdog thread, which every other hold depends on: anything that escapes would
            # reach stderr through the threading hook rather than the logger, and would take
            # the watchdog down with it.
            # Reported against the key that caused it. The backend lock may stay held until its
            # own expiry - but the hold is already marked released, and putting that back would
            # strand the entry instead.
            LOG.exception(
                "could not release %r after its hold limit elapsed; the backend lock may "
                "remain held until it expires on its own",
                self.key,
            )


class KeyedReentrantHolder:
    """Tracks who holds what, keyed by ``(thread, key)``.

    Per thread, not per call stack. Work handed to a pool from inside a held region is a
    different holder and waits like anyone else - anything else would let concurrency walk
    straight through the exclusion, silently, exactly where it was introduced.
    """

    __slots__ = ("_gate", "_held", "deadlines")

    def __init__(self) -> None:
        self._gate = threading.Lock()
        self._held: dict[tuple[int, str], _Holding] = {}
        #: One thread for every hold this holder is watching, rather than one each. Its
        #: thread is not started until the first hold is registered.
        self.deadlines = Deadlines()

    def reenter(self, key: str) -> Lease | None:
        with self._gate:
            holding = self._held.get((threading.get_ident(), key))
        if holding is None:
            return None
        if holding.enter():
            return Lease(holding)
        # A stale entry the watchdog has already fired on; clear it in passing.
        self._forget(holding)
        return None

    def register(
        self,
        key: str,
        payload: Any,
        max_hold: timedelta,
        release_cb: Callable[[Any, ReleaseReason], None],
    ) -> Lease:
        holding = _Holding(self, key, payload, release_cb)
        entry = (threading.get_ident(), key)
        try:
            with self._gate:
                self._held[entry] = holding
            holding.deadline = self.deadlines.after(max_hold.total_seconds(), holding.expire)
            return Lease(holding)
        except BaseException as failure:
            # Registration failed after the backend lock was taken. Give it back, or it is held
            # by nobody until it expires.
            with self._gate:
                self._held.pop(entry, None)
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

    def _forget(self, holding: _Holding) -> None:
        with self._gate:
            for entry, current in list(self._held.items()):
                if current is holding:
                    del self._held[entry]
                    return

    def release_all(self) -> None:
        with self._gate:
            holdings = list(self._held.values())
        for holding in holdings:
            holding.release_one()
