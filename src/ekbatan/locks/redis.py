"""A keyed lock held in Redis, for coordinating across processes that share no database.

The SQL providers in :mod:`ekbatan.locks.sql` need a database connection held open for the
lifetime of the lease. This one needs a Redis key with a TTL, which is cheaper and is the right
answer when the thing being coordinated is not itself a database write.

    provider = RedisKeyedLockProvider(redis.Redis(host="localhost"))

    with provider.acquire("wallet:" + str(wallet_id), timedelta(seconds=30)) as lease:
        ...

**Single-master only.** Mutual exclusion here is `SET NX PX` against one Redis, exactly as the
Java implementation's Redisson-backed provider is. That is correct for a single master, including
Sentinel-managed primary-with-replicas where reads and writes go to the master. It is *not*
Redlock, so it is not safe under multi-master Redis, and a failover that loses the in-memory key
loses the lock with it. A deployment that needs more than this needs a Redlock provider, and
should not pretend this one is it.

The TTL is the release mechanism, not a hint. A holder that dies releases nothing; the key
expires and the next caller takes it. That is why ``max_hold`` has to be an honest upper bound on
the work, in both ports.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any, Self

from .._metrics import counter
from ..errors import LockAcquisitionError, ValidationError
from ._holder import KeyedReentrantHolder, ReleaseReason
from .base import Lease, check

__all__ = ["DEFAULT_NAMESPACE", "RedisKeyedLockProvider", "RedisKeyedLockProviderBuilder"]

LOG = logging.getLogger("ekbatan.locks")

#: What Java's builder defaults to. The colon is added by the provider.
DEFAULT_NAMESPACE = "ekbatan-lock"

#: How often a blocking acquire asks again. Redis has no blocking `SET NX`, so waiting is
#: polling; short enough to feel immediate, long enough not to be a busy loop.
POLL = timedelta(milliseconds=50)

#: Delete the key only if it still holds *our* token. A plain `DEL` releases whoever holds it
#: now, which after a TTL expiry is somebody else - and they would then be running unprotected
#: while believing they held the lock. Java relies on Redisson's owner check for the same reason.
_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

RELEASES_FAILED = counter(
    "ekbatan.locks.release_failures",
    "Redis lock releases that did not reach the server; the key stays held until its TTL",
    "{release}",
)


def lease_millis(max_hold: timedelta) -> int:
    """``max_hold`` as whole milliseconds, never zero.

    Redis rejects `PX 0` outright - verified against a real server - and a sub-millisecond
    ``max_hold`` truncates to exactly that. Java rounds up here for the same reason, where the
    consequence is worse: Redisson reads a non-positive lease as "no lease" and starts renewing
    the key for as long as the process lives, which is the opposite of what was asked for.
    """
    return max(1, int(max_hold.total_seconds() * 1000))


class RedisKeyedLockProvider:
    """Mutual exclusion across processes, keyed by a string.

    Re-entry is tracked in this process, as it is for the SQL providers: the first acquire talks
    to Redis, re-entries only bump a counter, and the outermost release is the one that deletes
    the key. That is what makes the *outermost* ``max_hold`` govern the TTL - an inner re-entry
    asking for a shorter one must not shorten the holder's lease.
    """

    __slots__ = ("_client", "_holder", "_prefix", "_release")

    def __init__(self, client: Any, *, namespace: str = DEFAULT_NAMESPACE) -> None:
        if not namespace or not namespace.strip():
            raise LockAcquisitionError(namespace, "namespace cannot be blank")
        self._client = client
        self._prefix = f"{namespace}:"
        self._holder = KeyedReentrantHolder()
        self._release = client.register_script(_RELEASE)

    @classmethod
    def builder(cls) -> RedisKeyedLockProviderBuilder:
        return RedisKeyedLockProviderBuilder()

    def acquire(self, key: str, max_hold: timedelta) -> Lease:
        """Take the lock, waiting as long as it takes."""
        check(key, max_hold)
        reentered = self._holder.reenter(key)
        if reentered is not None:
            return reentered
        while True:
            lease = self._attempt(key, max_hold)
            if lease is not None:
                return lease
            _sleep(POLL)

    def try_acquire(self, key: str, max_wait: timedelta, max_hold: timedelta) -> Lease | None:
        """Take the lock if it comes free within ``max_wait``, else ``None``.

        Contention is an empty result, never an exception: a caller cannot tell a busy lock from
        a broken one if both arrive the same way, and the two call for opposite responses.
        """
        check(key, max_hold, max_wait)
        reentered = self._holder.reenter(key)
        if reentered is not None:
            return reentered

        remaining = max_wait
        while True:
            lease = self._attempt(key, max_hold)
            if lease is not None:
                return lease
            if remaining <= timedelta(0):
                return None
            _sleep(min(remaining, POLL))
            remaining -= POLL

    def close(self) -> None:
        self._holder.release_all()

    # ---------------------------------------------------------------- internals

    def _attempt(self, key: str, max_hold: timedelta) -> Lease | None:
        token = str(uuid.uuid4())
        try:
            taken = self._client.set(self._prefix + key, token, nx=True, px=lease_millis(max_hold))
        except Exception as error:
            raise LockAcquisitionError(key, f"the lock backend failed: {error}") from error
        if not taken:
            return None
        return self._holder.register(key, (key, token), max_hold, self._give_back)

    def _give_back(self, payload: tuple[str, str], reason: ReleaseReason) -> None:
        key, token = payload
        if reason is ReleaseReason.EXPIRED:
            # The watchdog fired, which means `max_hold` has passed - and `max_hold` is exactly
            # the TTL Redis was given, so the key is already gone or about to be. Sending a
            # delete here would race a lease somebody else has just taken.
            return
        try:
            self._release(keys=[self._prefix + key], args=[token])
        except Exception as error:
            # Nothing can release a lock on a server it cannot reach, so the TTL is the only
            # remedy. What matters is that it is not silent: every other holder blocks for up to
            # `max_hold`, and at DEBUG - which is where the Java implementation had it until
            # EKB-0059 - a shard stalling leaves no trace at all.
            RELEASES_FAILED.add(1)
            LOG.error(
                "failed to release the Redis lock for %r; it stays held until its TTL expires",
                key,
                exc_info=error,
            )


def _sleep(delay: timedelta) -> None:
    import time

    time.sleep(max(0.0, delay.total_seconds()))


class RedisKeyedLockProviderBuilder:
    """Fluent builder for :class:`RedisKeyedLockProvider`. Obtain via
    :meth:`RedisKeyedLockProvider.builder`.

    ``client`` is Java's ``redissonClient``: a ``redis.Redis`` here.
    """

    __slots__ = ("_client", "_namespace")

    def __init__(self) -> None:
        self._client: Any = None
        self._namespace = DEFAULT_NAMESPACE

    def client(self, client: Any) -> Self:
        self._client = client
        return self

    def namespace(self, namespace: str) -> Self:
        """Refused when it is set, as Java refuses it, rather than at ``build()``."""
        if not namespace or not namespace.strip():
            raise ValidationError("namespace cannot be blank")
        self._namespace = namespace
        return self

    def build(self) -> RedisKeyedLockProvider:
        if self._client is None:
            raise ValidationError("client is required")
        return RedisKeyedLockProvider(self._client, namespace=self._namespace)
