"""Locks held by the database, for code on an event loop.

The sync providers' rules, statements and log lines (``ekbatan.locks.sql``), on an
``AsyncEngine``: a session-scoped lock, so each hold keeps a connection of its own for as long
as it lasts; waiting in bounded segments; AUTOCOMMIT; a connection whose release failed, or whose
settings could not be put back, closed rather than returned. Only the awaits are new - and one
rule the sync side has no use for: **a wait that is cancelled closes its connection**, because
the server may have granted the lock in the instant the task was cancelled, and a connection
returned to the pool holding a lock hands it to whoever borrows the connection next.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Any, Self

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ...errors import LockAcquisitionError, ValidationError
from ...locks._holder import ReleaseReason
from ...locks.base import check_arguments, lock_key_hash
from ...locks.sql import (
    _DIRTY,
    MYSQL_GET_LOCK,
    MYSQL_RELEASE_FAILED,
    MYSQL_RELEASE_LOCK,
    NO_CONNECTION,
    PG_LOCK,
    PG_RELEASE_FAILED,
    PG_RESET_FAILED,
    PG_RESET_LOCK_TIMEOUT,
    PG_TRY_LOCK,
    PG_UNLOCK,
    SEGMENT,
    _get_lock_timeout,
    _is_lock_timeout,
    backend_failed,
    mysql_got_lock,
    mysql_lock_name,
    mysql_released,
    pg_lock_timeout,
    segments,
)
from .base import Acquiring, AsyncKeyedReentrantHolder, AsyncLease

__all__ = [
    "AsyncMariaDBKeyedLockProvider",
    "AsyncMySQLKeyedLockProvider",
    "AsyncPostgresKeyedLockProvider",
    "AsyncSqlKeyedLockProvider",
    "AsyncSqlKeyedLockProviderBuilder",
]


class AsyncSqlKeyedLockProvider(ABC):
    """Shared machinery: connection lifetime, segmented waiting, re-entry, the hold limit.

    A backend supplies two operations and nothing else.
    """

    #: Each backend logs under its Java class's name, as the sync ones do.
    _log = logging.getLogger("io.ekbatan.core.concurrent")
    _release_failed = "Failed to release the lock for key %s; evicting connection"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._holder = AsyncKeyedReentrantHolder()

    @classmethod
    def builder(cls) -> AsyncSqlKeyedLockProviderBuilder[Self]:
        return AsyncSqlKeyedLockProviderBuilder(cls)

    # ------------------------------------------------------------ backend hooks

    @abstractmethod
    async def _try_lock(self, conn: AsyncConnection, key: int, wait: timedelta) -> bool:
        """Take the lock, waiting at most ``wait``. False means someone else has it."""

    @abstractmethod
    async def _unlock(self, conn: AsyncConnection, key: int, user_key: str) -> None:
        """Give it back. ``user_key`` is only for the log lines."""

    # ------------------------------------------------------------------ the API

    def acquire(self, key: str, max_hold: timedelta) -> Acquiring:
        """Wait for the key, however long it takes. Awaited, or entered with ``async with``."""
        return Acquiring(lambda: self._acquire(key, max_hold))

    async def _acquire(self, key: str, max_hold: timedelta) -> AsyncLease:
        check_arguments(key, max_hold)
        reentered = self._holder.reenter(key)
        if reentered is not None:
            return reentered
        while True:
            lease = await self._attempt(key, SEGMENT, max_hold)
            if lease is not None:
                return lease

    async def try_acquire(
        self, key: str, max_wait: timedelta, max_hold: timedelta
    ) -> AsyncLease | None:
        check_arguments(key, max_hold, max_wait)
        reentered = self._holder.reenter(key)
        if reentered is not None:
            return reentered
        for segment in segments(max_wait):
            lease = await self._attempt(key, segment, max_hold)
            if lease is not None:
                return lease
        return None

    def close(self) -> None:  # noqa: B027 - deliberately empty, as the sync one
        """Does nothing, as the sync ``close()``. Leases still open stay held until they are
        released or their ``max_hold`` passes; release them before disposing the engine."""

    # ---------------------------------------------------------------- internals

    async def _attempt(self, key: str, wait: timedelta, max_hold: timedelta) -> AsyncLease | None:
        hashed = lock_key_hash(key)
        try:
            conn = await self._engine.connect()
            await conn.execution_options(isolation_level="AUTOCOMMIT")
        except Exception as exc:
            raise LockAcquisitionError(key, NO_CONNECTION) from exc
        try:
            acquired = await self._try_lock(conn, hashed, wait)
        except LockAcquisitionError:
            await _discard(conn, self._log)
            raise
        except Exception as exc:
            await _discard(conn, self._log)
            raise LockAcquisitionError(key, backend_failed(exc)) from exc
        except BaseException:
            # Cancelled, or a timeout around it, part way through: the server may have granted
            # the lock already. Closing the connection is what makes it drop the session and
            # any lock with it; returning it would hand the lock to the next borrower.
            await asyncio.shield(_close_for_good(conn, self._log))
            raise

        if not acquired:
            await _discard(conn, self._log)
            return None
        return await self._holder.register(key, (key, hashed, conn), max_hold, self._release)

    async def _release(self, payload: Any, reason: ReleaseReason) -> None:
        user_key, hashed, conn = payload
        try:
            await self._unlock(conn, hashed, user_key)
        except Exception:
            # Evicted rather than returned, as the sync provider does: the session may still
            # hold the lock.
            self._log.exception(self._release_failed, user_key)
            await conn.invalidate()
        except BaseException:
            await asyncio.shield(_close_for_good(conn, self._log))
            raise
        else:
            if conn.info.get(_DIRTY):
                await conn.invalidate()
        await _discard(conn, self._log)


async def _discard(conn: AsyncConnection, log: logging.Logger) -> None:
    """Close and return the connection, whatever state it is in."""
    try:
        await conn.close()
    except Exception:  # pragma: no cover - the pool is already unhappy
        log.debug("failed to close a lock connection", exc_info=True)


async def _close_for_good(conn: AsyncConnection, log: logging.Logger) -> None:
    """Close the physical connection, so the server ends the session and its locks."""
    try:
        await conn.invalidate()
    except Exception:  # pragma: no cover - the connection is already gone
        log.debug("failed to invalidate a lock connection", exc_info=True)
    await _discard(conn, log)


class AsyncPostgresKeyedLockProvider(AsyncSqlKeyedLockProvider):
    """PostgreSQL advisory locks: ``pg_try_advisory_lock`` for a single attempt, and
    ``pg_advisory_lock`` under a ``lock_timeout`` for a bounded wait - as the sync provider."""

    _log = logging.getLogger("io.ekbatan.core.concurrent.PostgresKeyedLockProvider")
    _release_failed = PG_RELEASE_FAILED

    async def _try_lock(self, conn: AsyncConnection, key: int, wait: timedelta) -> bool:
        if wait <= timedelta(0):
            return bool((await conn.execute(text(PG_TRY_LOCK), {"k": key})).scalar())

        await conn.exec_driver_sql(pg_lock_timeout(wait))
        acquired = False
        failure: Exception | None = None
        try:
            await conn.execute(text(PG_LOCK), {"k": key})
            acquired = True
        except Exception as exc:
            if not _is_lock_timeout(exc):
                failure = exc
        # Back to 0 whether the lock came or not, as the sync provider does.
        try:
            await conn.exec_driver_sql(PG_RESET_LOCK_TIMEOUT)
        except Exception as reset:
            self._log.warning(PG_RESET_FAILED, exc_info=reset)
            if failure is not None:
                failure.add_note(f"resetting lock_timeout also failed: {reset!r}")
            if acquired:
                conn.info[_DIRTY] = True
            else:
                await conn.invalidate()
        if failure is not None:
            raise failure
        return acquired

    async def _unlock(self, conn: AsyncConnection, key: int, user_key: str) -> None:
        await conn.execute(text(PG_UNLOCK), {"k": key})


class AsyncMySQLKeyedLockProvider(AsyncSqlKeyedLockProvider):
    """MySQL user-level locks via ``GET_LOCK``. The wait is sent as the sync provider sends it -
    a ``Decimal``, which MySQL rounds to whole seconds and MariaDB does not."""

    _log = logging.getLogger("io.ekbatan.core.concurrent.MySQLKeyedLockProvider")
    _release_failed = MYSQL_RELEASE_FAILED

    async def _try_lock(self, conn: AsyncConnection, key: int, wait: timedelta) -> bool:
        name = mysql_lock_name(key)
        result = (
            await conn.execute(
                text(MYSQL_GET_LOCK), {"name": name, "timeout": _get_lock_timeout(wait)}
            )
        ).scalar()
        return mysql_got_lock(result, name)

    async def _unlock(self, conn: AsyncConnection, key: int, user_key: str) -> None:
        name = mysql_lock_name(key)
        result = (await conn.execute(text(MYSQL_RELEASE_LOCK), {"name": name})).scalar()
        mysql_released(self._log, result, user_key)


class AsyncMariaDBKeyedLockProvider(AsyncMySQLKeyedLockProvider):
    """MariaDB user-level locks: the MySQL statements; MariaDB keeps the wait's fraction."""

    _log = logging.getLogger("io.ekbatan.core.concurrent.MariaDBKeyedLockProvider")


class AsyncSqlKeyedLockProviderBuilder[P: AsyncSqlKeyedLockProvider]:
    """Fluent builder for the async SQL lock providers. Obtain via
    ``AsyncPostgresKeyedLockProvider.builder()`` or the MySQL / MariaDB one.

    ``engine`` is the async pool the locks' sessions are borrowed from.
    """

    __slots__ = ("_engine", "_provider")

    def __init__(self, provider: type[P]) -> None:
        self._provider = provider
        self._engine: AsyncEngine | None = None

    def engine(self, engine: AsyncEngine) -> Self:
        self._engine = engine
        return self

    def build(self) -> P:
        if self._engine is None:
            raise ValidationError("engine is required")
        return self._provider(self._engine)
