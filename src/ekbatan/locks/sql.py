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
from typing import Any, Self

from sqlalchemy import Connection, Engine

from ..errors import LockAcquisitionError, ValidationError
from ._holder import KeyedReentrantHolder, ReleaseReason
from .base import Lease, check, lock_key_hash

__all__ = [
    "MariaDBKeyedLockProvider",
    "MySQLKeyedLockProvider",
    "PostgresKeyedLockProvider",
    "SqlKeyedLockProvider",
    "SqlKeyedLockProviderBuilder",
]

LOG = logging.getLogger("ekbatan.locks")

SEGMENT = timedelta(seconds=5)


class SqlKeyedLockProvider(ABC):
    """Shared machinery: connection lifetime, segmented waiting, re-entry, the watchdog.

    A backend supplies two operations and nothing else.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._holder = KeyedReentrantHolder()

    @classmethod
    def builder(cls) -> SqlKeyedLockProviderBuilder[Self]:
        """Java's ``postgresKeyedLockProvider()`` / ``mySQLKeyedLockProvider()`` /
        ``mariaDBKeyedLockProvider()``: one builder, building whichever class it was asked on."""
        return SqlKeyedLockProviderBuilder(cls)

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
        # AUTOCOMMIT, as Java's pooled connection is - and not incidentally. Without it a
        # statement that fails leaves an open aborted transaction, and Postgres then refuses
        # every later statement on the connection until it is rolled back, which is how a
        # timed-out `try_acquire` came to report itself as a backend failure. An advisory lock
        # is session-scoped in both Postgres and MySQL, so there is no transaction to want.
        conn = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            acquired = self._try_lock(conn, hashed, wait)
        except LockAcquisitionError:
            _discard(conn)
            raise
        except Exception as exc:
            _discard(conn)
            raise LockAcquisitionError(key, f"the lock backend failed: {exc}") from exc

        if not acquired:
            _discard(conn)
            return None
        return self._holder.register(key, (hashed, conn), max_hold, self._release)

    def _release(self, payload: Any, reason: ReleaseReason) -> None:
        hashed, conn = payload
        try:
            self._unlock(conn, hashed)
        except Exception:
            # Evicted rather than returned, as Java evicts here. The unlock did not happen, so
            # this session still holds the lock: handing the connection back to the pool would
            # hand the lock to whoever borrows it next, invisibly. Closing the physical
            # connection is what makes the server drop the session and its locks with it.
            LOG.exception("failed to release a database lock; discarding its session")
            conn.invalidate()
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


def _is_lock_timeout(error: Exception) -> bool:
    """Postgres's 55P03, however the driver chose to report it."""
    original = getattr(error, "orig", None)
    return getattr(original, "sqlstate", None) == "55P03" or "lock timeout" in str(error).lower()


class PostgresKeyedLockProvider(SqlKeyedLockProvider):
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
        except Exception as exc:
            if not _is_lock_timeout(exc):
                raise
            return False
        try:
            conn.exec_driver_sql("SET lock_timeout = 0")
        except Exception:
            # Java marks the connection dirty here and evicts it rather than releasing it. The
            # next borrower would otherwise inherit this lock_timeout and wait on some
            # unrelated statement for a duration nobody set.
            LOG.warning("could not reset lock_timeout; the connection will be discarded")
            conn.invalidate()
            raise
        return True

    def _unlock(self, conn: Connection, key: int) -> None:
        from sqlalchemy import text

        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})


class MySQLKeyedLockProvider(SqlKeyedLockProvider):
    """MySQL user-level locks via ``GET_LOCK``.

    **A sub-second wait is not a wait on MySQL.** ``GET_LOCK`` takes a ``DOUBLE``, and the two
    servers have diverged on what they do with the fraction. Measured against the containers
    this suite runs, holding the lock and timing a second session:

    ==============  ==================  ==============
    timeout asked   MySQL 8.4.11        MariaDB 11.4
    ==============  ==================  ==============
    0.25s           0.00s               0.26s
    0.5s            0.00s               0.51s
    1s              1.01s               1.01s
    1.5s            2.01s               1.51s
    ==============  ==================  ==============

    So MySQL truncates a sub-second timeout to zero and rounds a fractional one above a second
    up; MariaDB honours the fraction. A ``try_acquire`` with a 500ms wait therefore returns
    almost immediately on MySQL - it is a poll, not a wait.

    The value is passed through as-is anyway, as the Java implementation passes it. Rounding it
    up here would make a 50ms wait take a second, which is a surprise in the other direction,
    and polling in a loop would trade a bounded imprecision for load on the database. What is
    worth having is that this is written down and tested rather than discovered.

    (Java's own comment says MySQL "rounds up to the next whole second" for these. That holds
    above one second and not below it - a 500ms wait does not become 1s, it becomes none.)
    """

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
            raise LockAcquisitionError(
                self._name(key), "GET_LOCK returned NULL (server-side failure)"
            )
        return bool(int(result))

    def _unlock(self, conn: Connection, key: int) -> None:
        from sqlalchemy import text

        conn.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": self._name(key)})

    def _name(self, key: int) -> str:
        # Bare unsigned hex - Java's `Long.toHexString(LockKeyHash.hashUtf8(key))`, character for
        # character, so a Python service and a Java service contending for one key actually
        # contend. Pinned against Java's output in `test_locks.py`.
        return f"{key & 0xFFFFFFFFFFFFFFFF:x}"


class MariaDBKeyedLockProvider(MySQLKeyedLockProvider):
    """MariaDB user-level locks.

    Same statements as MySQL, and separate because the two have diverged on the timeout
    argument: MariaDB honours millisecond precision where MySQL rounds up to a second. Kept as
    its own class rather than a flag, because a flag is something to set wrongly.
    """


class SqlKeyedLockProviderBuilder[P: SqlKeyedLockProvider]:
    """Fluent builder for the SQL lock providers. Obtain via ``PostgresKeyedLockProvider.builder()``
    or the MySQL / MariaDB one.

    ``engine`` is Java's ``connectionProvider``: the pool the locks' sessions are borrowed from.
    """

    __slots__ = ("_engine", "_provider")

    def __init__(self, provider: type[P]) -> None:
        self._provider = provider
        self._engine: Engine | None = None

    def engine(self, engine: Engine) -> Self:
        self._engine = engine
        return self

    def build(self) -> P:
        if self._engine is None:
            raise ValidationError("engine is required")
        return self._provider(self._engine)
