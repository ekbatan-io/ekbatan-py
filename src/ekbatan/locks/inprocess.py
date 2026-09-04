"""A lock for one process.

Right for a single-instance service, and right in tests. Wrong the moment a second instance
starts, which is what the SQL and Redis backends are for - and the reason this one says so in
its name rather than being the unqualified default.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any

from ._holder import KeyedReentrantHolder, ReleaseReason
from .base import Lease, check

__all__ = ["InProcessKeyedLockProvider"]


class InProcessKeyedLockProvider:
    """Mutual exclusion within one process, with the same contract as the distributed ones."""

    __slots__ = ("_gate", "_holder", "_locks", "_waiters")

    def __init__(self) -> None:
        self._gate = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._waiters: dict[str, int] = {}
        self._holder = KeyedReentrantHolder()

    def acquire(self, key: str, max_hold: timedelta) -> Lease:
        check(key, max_hold)
        reentered = self._holder.reenter(key)
        if reentered is not None:
            return reentered
        lock = self._lock_for(key)
        lock.acquire()
        return self._register(key, lock, max_hold)

    def try_acquire(self, key: str, max_wait: timedelta, max_hold: timedelta) -> Lease | None:
        check(key, max_hold, max_wait)
        reentered = self._holder.reenter(key)
        if reentered is not None:
            return reentered

        lock = self._lock_for(key)
        seconds = max_wait.total_seconds()
        got = lock.acquire(blocking=False) if seconds <= 0 else lock.acquire(timeout=seconds)
        if not got:
            self._release_waiter(key)
            return None
        return self._register(key, lock, max_hold)

    def close(self) -> None:
        self._holder.release_all()

    # ---------------------------------------------------------------- internals

    def _register(self, key: str, lock: threading.Lock, max_hold: timedelta) -> Lease:
        return self._holder.register(key, (key, lock), max_hold, self._release)

    def _release(self, payload: Any, reason: ReleaseReason) -> None:
        key, lock = payload
        if lock.locked():
            lock.release()
        self._release_waiter(key)

    def _lock_for(self, key: str) -> threading.Lock:
        """One lock object per key, kept only while somebody wants it.

        Counting interest rather than keeping every key forever: a service locking on
        ``wallet:{id}`` would otherwise accumulate one object per wallet it has ever touched.
        """
        with self._gate:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.Lock()
            self._waiters[key] = self._waiters.get(key, 0) + 1
            return lock

    def _release_waiter(self, key: str) -> None:
        with self._gate:
            remaining = self._waiters.get(key, 1) - 1
            if remaining <= 0:
                self._waiters.pop(key, None)
                held = self._locks.get(key)
                if held is not None and not held.locked():
                    del self._locks[key]
            else:
                self._waiters[key] = remaining
