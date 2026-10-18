"""A keyed lock held in Redis, for code on an event loop - on redis-py's own ``redis.asyncio``.

The sync provider's lock (``ekbatan.locks.redis``), byte for byte: Redisson's hash, its two
scripts, its unlock channel and latch, ``PEXPIRE`` set to ``max_hold``. So an async service, a
sync one and a Java one contend for the same lock, and a release on any of them wakes a waiter
on the others.

    provider = AsyncRedisKeyedLockProvider(redis.asyncio.Redis(host="localhost"))

    async with provider.acquire("wallet:" + str(wallet_id), timedelta(seconds=30)):
        await executor.execute(DepositMoneyAction, params)

**The owner field.** Redisson's is ``<client id>:<thread id>``. Here the second half is a number
drawn once per outermost acquisition rather than a thread id: the holder is a task, re-entry is
counted in this process as on the sync side, and a number never reused cannot be mistaken for a
task that came later. The hash and the scripts do not care what the number is.

**Cancelled while taking it.** A task cancelled while the lock script is on its way may have
taken the lock in Redis. The release script is sent for that owner before the cancellation goes
on - it deletes nothing that owner does not hold - so the key is not left held until its TTL.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
import uuid
from datetime import timedelta
from typing import Any, Self

from ...errors import LockAcquisitionError, ValidationError
from ...locks._holder import ReleaseReason
from ...locks.base import check_arguments
from ...locks.redis import (
    _IDLE_WAIT,
    _LATCH_MILLIS,
    _LOCK,
    _UNLOCK,
    DEFAULT_NAMESPACE,
    RELEASE_FAILED_LINE,
    RELEASES_FAILED,
    UNLOCK_MESSAGE,
    lease_millis,
    lock_channel,
    refuse_blank_namespace,
    refuse_blank_namespace_setting,
    unlock_latch,
)
from ...locks.sql import backend_failed
from .base import Acquiring, AsyncKeyedReentrantHolder, AsyncLease

__all__ = ["AsyncRedisKeyedLockProvider", "AsyncRedisKeyedLockProviderBuilder"]

LOG = logging.getLogger("io.ekbatan.keyedlock.redis.RedisKeyedLockProvider")


class AsyncRedisKeyedLockProvider:
    """Mutual exclusion across processes, keyed by a string, over a ``redis.asyncio.Redis``.

    Re-entry is tracked in this process, per task: the first acquire talks to Redis, re-entries
    only count up, and the outermost release is the one that unlocks - so the outermost
    ``max_hold`` governs the TTL, as on the sync side.
    """

    __slots__ = ("_client", "_holder", "_id", "_lock", "_owners", "_prefix", "_release")

    def __init__(self, client: Any, *, namespace: str = DEFAULT_NAMESPACE) -> None:
        refuse_blank_namespace(namespace)
        self._client = client
        self._prefix = f"{namespace}:"
        self._holder = AsyncKeyedReentrantHolder()
        #: Redisson's client id: one UUID per client, the first half of every owner field.
        self._id = str(uuid.uuid4())
        self._owners = itertools.count(1)
        self._lock = client.register_script(_LOCK)
        self._release = client.register_script(_UNLOCK)

    @classmethod
    def builder(cls) -> AsyncRedisKeyedLockProviderBuilder:
        return AsyncRedisKeyedLockProviderBuilder()

    def acquire(self, key: str, max_hold: timedelta) -> Acquiring:
        """Take the lock, waiting as long as it takes. Awaited, or entered with ``async with``."""
        return Acquiring(lambda: self._acquire(key, max_hold))

    async def _acquire(self, key: str, max_hold: timedelta) -> AsyncLease:
        check_arguments(key, max_hold)
        reentered = self._holder.reenter(key)
        if reentered is not None:
            return reentered
        lease = await self._wait(key, None, max_hold)
        assert lease is not None
        return lease

    async def try_acquire(
        self, key: str, max_wait: timedelta, max_hold: timedelta
    ) -> AsyncLease | None:
        """Take the lock if it comes free within ``max_wait``, else ``None`` - contention is an
        empty result, never an exception."""
        check_arguments(key, max_hold, max_wait)
        reentered = self._holder.reenter(key)
        if reentered is not None:
            return reentered
        return await self._wait(key, time.monotonic() + max_wait.total_seconds(), max_hold)

    def close(self) -> None:
        """Does nothing, as the sync ``close()``. The client is yours to close."""

    # ---------------------------------------------------------------- internals

    async def _wait(
        self, key: str, deadline: float | None, max_hold: timedelta
    ) -> AsyncLease | None:
        """Try, subscribe, then try again each time an unlock message arrives or the holder's
        TTL runs out, until the deadline - as the sync provider, without a thread to block."""
        owner = f"{self._id}:{next(self._owners)}"
        ttl = await self._try_lock(key, owner, max_hold)
        if ttl is None:
            return await self._register(key, owner, max_hold)
        if deadline is not None and time.monotonic() >= deadline:
            return None

        try:
            pubsub = self._client.pubsub(ignore_subscribe_messages=True)
            await pubsub.subscribe(lock_channel(self._prefix + key))
        except Exception as error:
            raise LockAcquisitionError(key, backend_failed(error)) from error
        try:
            while True:
                ttl = await self._try_lock(key, owner, max_hold)
                if ttl is None:
                    return await self._register(key, owner, max_hold)
                wait = ttl / 1000 if ttl >= 0 else _IDLE_WAIT.total_seconds()
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    wait = min(wait, remaining)
                await _await_unlock(pubsub, key, wait)
        finally:
            try:
                await asyncio.shield(pubsub.aclose())
            except Exception:  # pragma: no cover - the connection is already gone
                LOG.debug("failed to close the unlock subscription", exc_info=True)

    async def _try_lock(self, key: str, owner: str, max_hold: timedelta) -> int | None:
        millis = lease_millis(max_hold)
        try:
            ttl = await self._lock(keys=[self._prefix + key], args=[millis, owner])
        except Exception as error:
            raise LockAcquisitionError(key, backend_failed(error)) from error
        except BaseException:
            # Cancelled with the script on its way: it may have run. Give back what this owner
            # may hold, then let the cancellation go on.
            await asyncio.shield(self._give_back((key, owner, millis), ReleaseReason.CLOSED))
            raise
        return None if ttl is None else int(ttl)

    async def _register(self, key: str, owner: str, max_hold: timedelta) -> AsyncLease:
        return await self._holder.register(
            key, (key, owner, lease_millis(max_hold)), max_hold, self._give_back
        )

    async def _give_back(self, payload: tuple[str, str, int], reason: ReleaseReason) -> None:
        key, owner, millis = payload
        if reason is ReleaseReason.EXPIRED:
            # `max_hold` is the TTL Redis was given: the key is gone or about to be, and an
            # unlock now would race a lease somebody else has just taken. As the sync provider.
            return
        stored = self._prefix + key
        latch = unlock_latch(stored)
        try:
            result = await self._release(
                keys=[stored, lock_channel(stored), latch],
                args=[UNLOCK_MESSAGE, millis, owner, "publish", _LATCH_MILLIS],
            )
        except Exception as error:
            RELEASES_FAILED.add(1)
            LOG.error(RELEASE_FAILED_LINE, key, exc_info=error)
            return
        try:
            await self._client.delete(latch)
        except Exception:
            LOG.debug("failed to delete the unlock latch for %r; it expires on its own", key)
        if result is None:
            LOG.debug("Redis lock for %s was no longer held by this owner", key)


async def _await_unlock(pubsub: Any, key: str, seconds: float) -> None:
    """Wait until an unlock message arrives or ``seconds`` pass."""
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        try:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=remaining)
        except Exception as error:
            raise LockAcquisitionError(key, backend_failed(error)) from error
        if message is not None and message.get("type") == "message":
            return


class AsyncRedisKeyedLockProviderBuilder:
    """Fluent builder for :class:`AsyncRedisKeyedLockProvider`. Obtain via
    :meth:`AsyncRedisKeyedLockProvider.builder`. ``client`` is a ``redis.asyncio.Redis``."""

    __slots__ = ("_client", "_namespace")

    def __init__(self) -> None:
        self._client: Any = None
        self._namespace = DEFAULT_NAMESPACE

    def client(self, client: Any) -> Self:
        self._client = client
        return self

    def namespace(self, namespace: str) -> Self:
        """Refused when it is set, as the sync builder refuses it."""
        refuse_blank_namespace_setting(namespace)
        self._namespace = namespace
        return self

    def build(self) -> AsyncRedisKeyedLockProvider:
        if self._client is None:
            raise ValidationError("client is required")
        return AsyncRedisKeyedLockProvider(self._client, namespace=self._namespace)
