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
import os
from abc import ABC, abstractmethod
from datetime import timedelta
from decimal import Decimal
from typing import Any, Self

from sqlalchemy import Connection, Engine

from .._guards import refuse_if_forked
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

SEGMENT = timedelta(seconds=5)


class SqlKeyedLockProvider(ABC):
    """Shared machinery: connection lifetime, segmented waiting, re-entry, the watchdog.

    A backend supplies two operations and nothing else.
    """

    #: Each backend logs under its Java class's name, as Java's do; this is only the default.
    _log = logging.getLogger("io.ekbatan.core.concurrent")

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._holder = KeyedReentrantHolder()
        #: The process that built it: its holds, its watchdog and its pool belong to that one.
        self._created_in = os.getpid()

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
    def _unlock(self, conn: Connection, key: int, user_key: str) -> None:
        """Give it back. ``user_key`` is only for the log lines, as Java's ``payload.userKey``."""

    #: Java's ERROR line when a release fails, per provider; ``%s`` is the caller's key.
    _release_failed = "Failed to release the lock for key %s; evicting connection"

    # ------------------------------------------------------------------ the API

    def acquire(self, key: str, max_hold: timedelta) -> Lease:
        refuse_if_forked(self._created_in, type(self).__name__)
        check(key, max_hold)
        reentered = self._holder.reenter(key)
        if reentered is not None:
            return reentered
        while True:
            lease = self._attempt(key, SEGMENT, max_hold)
            if lease is not None:
                return lease

    def try_acquire(self, key: str, max_wait: timedelta, max_hold: timedelta) -> Lease | None:
        refuse_if_forked(self._created_in, type(self).__name__)
        check(key, max_hold, max_wait)
        reentered = self._holder.reenter(key)
        if reentered is not None:
            return reentered

        for segment in segments(max_wait):
            lease = self._attempt(key, segment, max_hold)
            if lease is not None:
                return lease
        return None

    def close(self) -> None:  # noqa: B027 - deliberately empty, as Java's default
        """Does nothing, as Java's ``close()`` does nothing. Leases still open stay held until
        they are released or their ``max_hold`` passes; release them before disposing the engine."""

    # ---------------------------------------------------------------- internals

    def _attempt(self, key: str, wait: timedelta, max_hold: timedelta) -> Lease | None:
        hashed = lock_key_hash(key)
        # AUTOCOMMIT, as Java's pooled connection is - and not incidentally. Without it a
        # statement that fails leaves an open aborted transaction, and Postgres then refuses
        # every later statement on the connection until it is rolled back, which is how a
        # timed-out `try_acquire` came to report itself as a backend failure. An advisory lock
        # is session-scoped in both Postgres and MySQL, so there is no transaction to want.
        try:
            conn = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        except Exception as exc:
            # Drawing the connection is part of taking the lock, so an unreachable database is a
            # LockAcquisitionError like any other backend failure, as in Java. Nothing to give
            # back: the connection was never handed over.
            raise LockAcquisitionError(key, NO_CONNECTION) from exc
        try:
            acquired = self._try_lock(conn, hashed, wait)
        except LockAcquisitionError:
            _discard(conn, self._log)
            raise
        except Exception as exc:
            _discard(conn, self._log)
            raise LockAcquisitionError(key, backend_failed(exc)) from exc

        if not acquired:
            _discard(conn, self._log)
            return None
        return self._holder.register(key, (key, hashed, conn), max_hold, self._release)

    def _release(self, payload: Any, reason: ReleaseReason) -> None:
        user_key, hashed, conn = payload
        try:
            self._unlock(conn, hashed, user_key)
        except Exception:
            # Evicted rather than returned, as Java evicts here. The unlock did not happen, so
            # this session still holds the lock: handing the connection back to the pool would
            # hand the lock to whoever borrows it next, invisibly. Closing the physical
            # connection is what makes the server drop the session and its locks with it.
            self._log.exception(self._release_failed, user_key)
            conn.invalidate()
        else:
            if conn.info.get(_DIRTY):
                conn.invalidate()
        _discard(conn, self._log)


#: Marks a connection whose session settings could not be put back; it is discarded on release.
_DIRTY = "ekbatan.lock.dirty"


def _discard(conn: Connection, log: logging.Logger) -> None:
    """Close and return the connection, whatever state it is in.

    A connection that raised part-way through a lock statement may be carrying settings the
    next borrower would inherit. Returning it to the pool is how one caller's timeout becomes
    another's mystery.
    """
    try:
        conn.close()
    except Exception:  # pragma: no cover - the pool is already unhappy
        log.debug("failed to close a lock connection", exc_info=True)


#: Shared with ``ekbatan.aio.locks``: what each backend sends, and how its answers are read.
#: The single try. The sync provider sends it in psycopg's own spelling; the same statement.
PG_TRY_LOCK = "SELECT pg_try_advisory_lock(:k)"
PG_LOCK = "SELECT pg_advisory_lock(:k)"
PG_UNLOCK = "SELECT pg_advisory_unlock(:k)"
PG_RESET_LOCK_TIMEOUT = "SET lock_timeout = 0"
PG_RELEASE_FAILED = "Failed to release advisory lock for key %s; evicting connection"
PG_RESET_FAILED = "Failed to reset lock_timeout on connection; marking dirty"
MYSQL_GET_LOCK = "SELECT GET_LOCK(:name, :timeout)"
MYSQL_RELEASE_LOCK = "SELECT RELEASE_LOCK(:name)"
MYSQL_RELEASE_FAILED = "Failed to RELEASE_LOCK for key %s; evicting connection"
NO_CONNECTION = "could not obtain a connection"


def pg_lock_timeout(wait: timedelta) -> str:
    """The bounded wait, as Java sets it: whole milliseconds, at least one."""
    millis = max(1, int(wait.total_seconds() * 1000))
    return f"SET lock_timeout = {millis}"


def backend_failed(error: Exception) -> str:
    return f"the lock backend failed: {error}"


def mysql_got_lock(result: Any, name: str) -> bool:
    """``GET_LOCK``'s answer: 1 taken, 0 timed out, NULL a server-side failure.

    NULL is a server-side failure - out of memory, a killed session. It has to stay
    distinguishable from a timeout, because a blocking acquire retries a timeout forever and
    would spin on this instead of failing.
    """
    if result is None:
        raise LockAcquisitionError(name, "GET_LOCK returned NULL (server-side failure)")
    return bool(int(result))


def mysql_released(log: logging.Logger, result: Any, user_key: str) -> None:
    """``RELEASE_LOCK``'s answer. Java's two warnings: NULL means no such lock existed, 0 that
    this session did not hold it - either way something else released or never took what we
    think we held."""
    if result is None:
        log.warning("RELEASE_LOCK for key %s returned NULL (no such lock)", user_key)
    elif int(result) != 1:
        log.warning(
            "RELEASE_LOCK for key %s returned %s (lock not held by this session)",
            user_key,
            result,
        )


def mysql_lock_name(key: int) -> str:
    """Bare unsigned hex - Java's `Long.toHexString(LockKeyHash.hashUtf8(key))`, character for
    character, so a Python service and a Java service contending for one key actually contend.
    Pinned against Java's output in `test_locks.py`."""
    return f"{key & 0xFFFFFFFFFFFFFFFF:x}"


def segments(max_wait: timedelta) -> list[timedelta]:
    """A bounded wait cut into ``SEGMENT``-long waits; a zero wait is one zero-length try."""
    remaining, cut = max_wait, []
    while True:
        segment = min(remaining, SEGMENT) if remaining > SEGMENT else remaining
        cut.append(segment)
        remaining -= segment
        if remaining <= timedelta(0):
            return cut


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

    _log = logging.getLogger("io.ekbatan.core.concurrent.PostgresKeyedLockProvider")

    def _try_lock(self, conn: Connection, key: int, wait: timedelta) -> bool:
        from sqlalchemy import text

        if wait <= timedelta(0):
            return bool(
                conn.exec_driver_sql("SELECT pg_try_advisory_lock(%(k)s)", {"k": key}).scalar()
            )

        conn.exec_driver_sql(pg_lock_timeout(wait))
        acquired = False
        failure: Exception | None = None
        try:
            conn.execute(text(PG_LOCK), {"k": key})
            acquired = True
        except Exception as exc:
            if not _is_lock_timeout(exc):
                failure = exc
        # Back to 0 whether the lock came or not, as Java does. Skipping it after a timed-out
        # wait left the lock_timeout on the pooled connection, and the next borrower's ordinary
        # statements could then fail with lock timeouts nobody asked for.
        try:
            conn.exec_driver_sql(PG_RESET_LOCK_TIMEOUT)
        except Exception as reset:
            # Java marks the connection dirty here and evicts it instead of returning it.
            self._log.warning(PG_RESET_FAILED, exc_info=reset)
            if failure is not None:
                failure.add_note(f"resetting lock_timeout also failed: {reset!r}")
            if acquired:
                # The lock is kept, as Java keeps it; its connection is discarded on release.
                conn.info[_DIRTY] = True
            else:
                conn.invalidate()
        if failure is not None:
            raise failure
        return acquired

    _release_failed = PG_RELEASE_FAILED

    def _unlock(self, conn: Connection, key: int, user_key: str) -> None:
        from sqlalchemy import text

        conn.execute(text(PG_UNLOCK), {"k": key})


class MySQLKeyedLockProvider(SqlKeyedLockProvider):
    """MySQL user-level locks via ``GET_LOCK``.

    **MySQL rounds the wait to whole seconds; MariaDB keeps the fraction.** The seconds are sent
    as a ``Decimal``, because that is what Java's Connector/J puts on the wire (``0.5``), and
    MySQL rounds a decimal half up. PyMySQL would send a float as ``0.5e0``, which MySQL rounds
    half to even - so a 500ms wait became no wait at all in Python and one second in Java.
    Measured on MySQL 26.7 and MariaDB 12.2, holding the lock and timing a second session:

    ==============  ===============  ===============  ==============
    timeout asked   MySQL, decimal   MySQL, float     MariaDB, both
    ==============  ===============  ===============  ==============
    0.25s           0.00s            0.00s            0.25s
    0.5s            1.00s            0.00s            0.50s
    1s              1.01s            1.01s            1.00s
    1.5s            2.01s            2.00s            1.50s
    ==============  ===============  ===============  ==============

    The value is Java's too: ``0`` for no wait, otherwise the milliseconds (at least 1) over
    1000. A 250ms ``try_acquire`` on MySQL is therefore a single try, in both ports.
    """

    _log = logging.getLogger("io.ekbatan.core.concurrent.MySQLKeyedLockProvider")

    def _try_lock(self, conn: Connection, key: int, wait: timedelta) -> bool:
        from sqlalchemy import text

        name = mysql_lock_name(key)
        result = conn.execute(
            text(MYSQL_GET_LOCK), {"name": name, "timeout": _get_lock_timeout(wait)}
        ).scalar()
        return mysql_got_lock(result, name)

    _release_failed = MYSQL_RELEASE_FAILED

    def _unlock(self, conn: Connection, key: int, user_key: str) -> None:
        from sqlalchemy import text

        result = conn.execute(text(MYSQL_RELEASE_LOCK), {"name": mysql_lock_name(key)}).scalar()
        mysql_released(self._log, result, user_key)

    def _name(self, key: int) -> str:
        return mysql_lock_name(key)


def _get_lock_timeout(wait: timedelta) -> Decimal:
    """Java's ``toGetLockTimeout``: 0 for no wait, else whole milliseconds (at least 1) / 1000."""
    if wait <= timedelta(0):
        return Decimal(0)
    return Decimal(max(1, wait // timedelta(milliseconds=1))) / 1000


class MariaDBKeyedLockProvider(MySQLKeyedLockProvider):
    """MariaDB user-level locks.

    Same statements as MySQL, and separate because the two have diverged on the timeout
    argument: MariaDB honours millisecond precision where MySQL rounds to whole seconds. Kept as
    its own class rather than a flag, because a flag is something to set wrongly.
    """

    _log = logging.getLogger("io.ekbatan.core.concurrent.MariaDBKeyedLockProvider")


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
