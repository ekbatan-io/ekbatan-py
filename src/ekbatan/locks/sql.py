"""Locks held by the database.

Both backends here hold a *session*-scoped lock, so each hold keeps a connection of its own for
as long as it lasts. That is the cost of coordinating across processes without a second piece
of infrastructure, and it is why ``max_hold`` is not optional: a connection held forever is a
pool exhausted forever.

Waiting happens in bounded segments rather than one unbounded call. An unbounded wait parks
inside a socket read that nothing can interrupt, so a shutdown would hang on it; segmenting also
keeps the connection sending traffic, which stops a proxy from reaping it as idle. The cost is
that a long waiter re-enters the queue at the back each segment, so fairness under heavy
contention is best-effort. If that matters, you want optimistic locking, not a bigger lock.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Any

from sqlalchemy import Connection, Engine

from ..errors import LockError
from ._holder import ReentrantHolder, ReleaseReason
from .base import Lease, check, lock_key_hash

__all__ = ["MariaDBLock", "MySQLLock", "PostgresLock", "SqlKeyedLock"]

LOG = logging.getLogger("ekbatan.locks")

SEGMENT = timedelta(seconds=5)


class SqlKeyedLock(ABC):
    """Shared machinery: connection lifetime, segmented waiting, re-entry, the watchdog.

    A backend supplies two operations and nothing else.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._holder = ReentrantHolder()

    # ------------------------------------------------------------ backend hooks

    @abstractmethod
    def _try_lock(self, conn: Connection, key: int, wait: timedelta) -> bool:
        """Take the lock, waiting at most ``wait``. False means someone else has it."""

    @abstractmethod
    def _unlock(self, conn: Connection, key: int) -> None:
        """Give it back."""

    # ------------------------------------------------------------------ the API

    def acquire(self, key: str, max_hold: timedelta) -> Lease:
        check(key, max_hold)
        reentered = self._holder.reenter(key)
        if reentered is not None:
            return reentered
        while True:
            lease = self._attempt(key, SEGMENT, max_hold)
            if lease is not None:
                return lease

    def try_acquire(self, key: str, max_wait: timedelta, max_hold: timedelta) -> Lease | None:
        check(key, max_hold, max_wait)
        reentered = self._holder.reenter(key)
        if reentered is not None:
            return reentered

        remaining = max_wait
        while True:
            segment = min(remaining, SEGMENT) if remaining > SEGMENT else remaining
            lease = self._attempt(key, segment, max_hold)
            if lease is not None:
                return lease
            remaining -= segment
            if remaining <= timedelta(0):
                return None

    def close(self) -> None:
        self._holder.release_all()

    # ---------------------------------------------------------------- internals

    def _attempt(self, key: str, wait: timedelta, max_hold: timedelta) -> Lease | None:
        hashed = lock_key_hash(key)
        conn = self._engine.connect()
        try:
            acquired = self._try_lock(conn, hashed, wait)
        except LockError:
            _discard(conn)
            raise
        except Exception as exc:
            _discard(conn)
            raise LockError(key, f"the lock backend failed: {exc}") from exc

        if not acquired:
            _discard(conn)
            return None
        return self._holder.register(key, (hashed, conn), max_hold, self._release)

    def _release(self, payload: Any, reason: ReleaseReason) -> None:
        hashed, conn = payload
        try:
            self._unlock(conn, hashed)
        except Exception:
            LOG.exception("failed to release a database lock; its session will be closed anyway")
        finally:
            _discard(conn)


def _discard(conn: Connection) -> None:
    """Close and return the connection, whatever state it is in.

    A connection that raised part-way through a lock statement may be carrying settings the
    next borrower would inherit. Returning it to the pool is how one caller's timeout becomes
    another's mystery.
    """
    try:
        conn.close()
    except Exception:  # pragma: no cover - the pool is already unhappy
        LOG.debug("failed to close a lock connection", exc_info=True)


class PostgresLock(SqlKeyedLock):
    """Postgres advisory locks.

    ``pg_try_advisory_lock`` for a single attempt, and ``pg_advisory_lock`` under a
    ``lock_timeout`` for a bounded wait - so the waiter is genuinely queued in the database and
    a releasing holder hands over immediately, rather than the waiter polling.
    """

    def _try_lock(self, conn: Connection, key: int, wait: timedelta) -> bool:
        from sqlalchemy import text

        if wait <= timedelta(0):
            return bool(
                conn.exec_driver_sql("SELECT pg_try_advisory_lock(%(k)s)", {"k": key}).scalar()
            )

        millis = max(1, int(wait.total_seconds() * 1000))
        conn.exec_driver_sql(f"SET lock_timeout = {millis}")
        try:
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
            return True
        except Exception as exc:
            if "lock timeout" in str(exc).lower() or (
                getattr(exc, "orig", None) is not None
                and (getattr(getattr(exc, "orig", None), "sqlstate", None) == "55P03")
            ):
                return False
            raise
        finally:
            conn.exec_driver_sql("SET lock_timeout = 0")

    def _unlock(self, conn: Connection, key: int) -> None:
        from sqlalchemy import text

        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})


class MySQLLock(SqlKeyedLock):
    """MySQL user-level locks via ``GET_LOCK``.

    **A sub-second wait rounds up.** MySQL accepts a fractional timeout and then rounds it to
    the next whole second internally, so a 50ms wait takes about a second. The wait is still
    bounded and the call still returns - a port must not paper over this by polling in a loop,
    which trades a documented imprecision for load on the database.
    """

    _name_prefix = "ekbatan:"

    def _try_lock(self, conn: Connection, key: int, wait: timedelta) -> bool:
        from sqlalchemy import text

        result = conn.execute(
            text("SELECT GET_LOCK(:name, :timeout)"),
            {"name": self._name(key), "timeout": max(0.0, wait.total_seconds())},
        ).scalar()
        if result is None:
            # NULL is a server-side failure - out of memory, a killed session. It has to stay
            # distinguishable from a timeout, because a blocking acquire retries a timeout
            # forever and would spin on this instead of failing.
            raise LockError(self._name(key), "GET_LOCK returned NULL (server-side failure)")
        return bool(int(result))

    def _unlock(self, conn: Connection, key: int) -> None:
        from sqlalchemy import text

        conn.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": self._name(key)})

    def _name(self, key: int) -> str:
        # Unsigned hex, matching the Java implementation exactly, so a Python service and a
        # Java service contending for one key actually contend.
        return f"{self._name_prefix}{key & 0xFFFFFFFFFFFFFFFF:x}"


class MariaDBLock(MySQLLock):
    """MariaDB user-level locks.

    Same statements as MySQL, and separate because the two have diverged on the timeout
    argument: MariaDB honours millisecond precision where MySQL rounds up to a second. Kept as
    its own class rather than a flag, because a flag is something to set wrongly.
    """
