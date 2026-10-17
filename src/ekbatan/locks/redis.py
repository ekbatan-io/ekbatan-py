"""A keyed lock held in Redis, for coordinating across processes that share no database.

The SQL providers in :mod:`ekbatan.locks.sql` need a database connection held open for the
lifetime of the lease. This one needs a Redis key with a TTL, which is cheaper and is the right
answer when the thing being coordinated is not itself a database write.

    provider = RedisKeyedLockProvider(redis.Redis(host="localhost"))

    with provider.acquire("wallet:" + str(wallet_id), timedelta(seconds=30)) as lease:
        ...

**The lock is Redisson's, byte for byte.** Java's provider is a Redisson ``RLock``, so this one
writes what Redisson writes and runs the scripts Redisson runs (Redisson 3.39.0, the version
Java's build pins): a hash at the key, one field ``<client id>:<thread id>`` holding the hold
count, ``PEXPIRE`` set to ``max_hold``, and on release a ``0`` published on
``redisson_lock__channel:{<key>}``. So a Java service and a Python service can share one lock:
either can hold it while the other tries or waits, and a release on one side wakes a waiter on
the other. A waiter here subscribes to that channel, as Redisson does, instead of polling.

**Single-master only.** That is correct for a single master, including Sentinel-managed
primary-with-replicas where reads and writes go to the master. It is *not* Redlock, so it is not
safe under multi-master Redis, and a failover that loses the in-memory key loses the lock with
it - the same as Java.

The TTL is the release mechanism, not a hint. A holder that dies releases nothing; the key
expires and the next caller takes it. That is why ``max_hold`` has to be an honest upper bound on
the work, in both ports.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import timedelta
from typing import Any, Self

from .._metrics import counter
from ..errors import LockAcquisitionError, ValidationError
from ._holder import KeyedReentrantHolder, ReleaseReason
from .base import Lease, check

__all__ = ["DEFAULT_NAMESPACE", "RedisKeyedLockProvider", "RedisKeyedLockProviderBuilder"]

LOG = logging.getLogger("io.ekbatan.keyedlock.redis.RedisKeyedLockProvider")

#: What Java's builder defaults to. The colon is added by the provider.
DEFAULT_NAMESPACE = "ekbatan-lock"

#: Redisson's `RedissonLock.tryLockInnerAsync`, verbatim. Takes the lock (or re-enters it for the
#: same owner field) and returns nil, or returns the key's remaining TTL in milliseconds.
_LOCK = (
    "if ((redis.call('exists', KEYS[1]) == 0) "
    "or (redis.call('hexists', KEYS[1], ARGV[2]) == 1)) then "
    "redis.call('hincrby', KEYS[1], ARGV[2], 1); "
    "redis.call('pexpire', KEYS[1], ARGV[1]); "
    "return nil; "
    "end; "
    "return redis.call('pttl', KEYS[1]);"
)

#: Redisson's `RedissonLock.unlockInnerAsync`, verbatim. Counts the owner's hold down; at zero it
#: deletes the key and publishes the unlock message that wakes waiters, Java's and ours. Returns
#: nil when this owner no longer holds the key - after a TTL expiry, somebody else may - so it
#: never deletes another owner's lock. KEYS[3] is a latch that makes a retried unlock idempotent.
_UNLOCK = (
    "local val = redis.call('get', KEYS[3]); "
    "if val ~= false then "
    "return tonumber(val);"
    "end; "
    "if (redis.call('hexists', KEYS[1], ARGV[3]) == 0) then "
    "return nil;"
    "end; "
    "local counter = redis.call('hincrby', KEYS[1], ARGV[3], -1); "
    "if (counter > 0) then "
    "redis.call('pexpire', KEYS[1], ARGV[2]); "
    "redis.call('set', KEYS[3], 0, 'px', ARGV[5]); "
    "return 0; "
    "else "
    "redis.call('del', KEYS[1]); "
    "redis.call(ARGV[4], KEYS[2], ARGV[1]); "
    "redis.call('set', KEYS[3], 1, 'px', ARGV[5]); "
    "return 1; "
    "end; "
)

#: `LockPubSub.UNLOCK_MESSAGE`.
UNLOCK_MESSAGE = 0

#: How long the unlock latch lives: Redisson's `(timeout + retryInterval) * retryAttempts` at its
#: defaults, `(3000 + 1500) * 3` ms.
_LATCH_MILLIS = 13_500

#: The longest a waiter sleeps before asking again when the key has no TTL to wait out - which a
#: lock taken by either port never lacks, since both always set one.
_IDLE_WAIT = timedelta(seconds=5)

RELEASES_FAILED = counter(
    "ekbatan.locks.release_failures",
    "Redis lock releases that did not reach the server; the key stays held until its TTL",
    "{release}",
)


def lease_millis(max_hold: timedelta) -> int:
    """``max_hold`` as whole milliseconds, never zero.

    A sub-millisecond ``max_hold`` truncates to 0, and ``PEXPIRE 0`` deletes the key at once - a
    lock that is gone the moment it is taken. Java rounds up here for a worse reason: Redisson
    reads a non-positive lease as "no lease" and renews the key for as long as the process lives.
    """
    return max(1, int(max_hold.total_seconds() * 1000))


def _prefix_name(prefix: str, name: str) -> str:
    """Redisson's `RedissonObject.prefixName`: the hash tag keeps a channel on the key's slot."""
    if "{" in name:
        return f"{prefix}:{name}"
    return f"{prefix}:{{{name}}}"


class RedisKeyedLockProvider:
    """Mutual exclusion across processes, keyed by a string.

    Re-entry is tracked in this process, as it is for the SQL providers and as Java tracks it
    over Redisson: the first acquire talks to Redis, re-entries only bump a counter, and the
    outermost release is the one that unlocks. That is what makes the *outermost* ``max_hold``
    govern the TTL - an inner re-entry asking for a shorter one must not shorten the holder's
    lease.
    """

    __slots__ = ("_client", "_holder", "_id", "_lock", "_prefix", "_release")

    def __init__(self, client: Any, *, namespace: str = DEFAULT_NAMESPACE) -> None:
        if not namespace or not namespace.strip():
            raise LockAcquisitionError(namespace, "namespace cannot be blank")
        self._client = client
        self._prefix = f"{namespace}:"
        self._holder = KeyedReentrantHolder()
        #: Redisson's client id: one UUID per client, the first half of every owner field.
        self._id = str(uuid.uuid4())
        self._lock = client.register_script(_LOCK)
        self._release = client.register_script(_UNLOCK)

    @classmethod
    def builder(cls) -> RedisKeyedLockProviderBuilder:
        return RedisKeyedLockProviderBuilder()

    def acquire(self, key: str, max_hold: timedelta) -> Lease:
        """Take the lock, waiting as long as it takes."""
        check(key, max_hold)
        reentered = self._holder.reenter(key)
        if reentered is not None:
            return reentered
        lease = self._wait(key, None, max_hold)
        assert lease is not None
        return lease

    def try_acquire(self, key: str, max_wait: timedelta, max_hold: timedelta) -> Lease | None:
        """Take the lock if it comes free within ``max_wait``, else ``None``.

        Contention is an empty result, never an exception: a caller cannot tell a busy lock from
        a broken one if both arrive the same way, and the two call for opposite responses.
        """
        check(key, max_hold, max_wait)
        reentered = self._holder.reenter(key)
        if reentered is not None:
            return reentered
        return self._wait(key, time.monotonic() + max_wait.total_seconds(), max_hold)

    def close(self) -> None:
        """Does nothing, as Java's ``close()`` does nothing. Leases still open stay held until
        they are released or their ``max_hold`` passes. The client is yours to close."""

    # ---------------------------------------------------------------- internals

    def _wait(self, key: str, deadline: float | None, max_hold: timedelta) -> Lease | None:
        """Redisson's `tryLock(wait, lease)` / `lock(lease)`: try, subscribe, then try again each
        time an unlock message arrives or the holder's TTL runs out, until the deadline."""
        owner = f"{self._id}:{threading.get_ident()}"
        ttl = self._try_lock(key, owner, max_hold)
        if ttl is None:
            return self._register(key, owner, max_hold)
        if deadline is not None and time.monotonic() >= deadline:
            return None

        try:
            pubsub = self._client.pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(_prefix_name("redisson_lock__channel", self._prefix + key))
        except Exception as error:
            raise LockAcquisitionError(key, f"the lock backend failed: {error}") from error
        try:
            while True:
                ttl = self._try_lock(key, owner, max_hold)
                if ttl is None:
                    return self._register(key, owner, max_hold)
                wait = ttl / 1000 if ttl >= 0 else _IDLE_WAIT.total_seconds()
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    wait = min(wait, remaining)
                _await_unlock(pubsub, key, wait)
        finally:
            try:
                pubsub.close()
            except Exception:  # pragma: no cover - the connection is already gone
                LOG.debug("failed to close the unlock subscription", exc_info=True)

    def _try_lock(self, key: str, owner: str, max_hold: timedelta) -> int | None:
        try:
            ttl = self._lock(keys=[self._prefix + key], args=[lease_millis(max_hold), owner])
        except Exception as error:
            raise LockAcquisitionError(key, f"the lock backend failed: {error}") from error
        return None if ttl is None else int(ttl)

    def _register(self, key: str, owner: str, max_hold: timedelta) -> Lease:
        return self._holder.register(
            key, (key, owner, lease_millis(max_hold)), max_hold, self._give_back
        )

    def _give_back(self, payload: tuple[str, str, int], reason: ReleaseReason) -> None:
        key, owner, millis = payload
        if reason is ReleaseReason.EXPIRED:
            # The watchdog fired, which means `max_hold` has passed - and `max_hold` is exactly
            # the TTL Redis was given, so the key is already gone or about to be. Sending an
            # unlock here would race a lease somebody else has just taken. Java does the same.
            return
        stored = self._prefix + key
        latch = f"{_prefix_name('redisson_unlock_latch', stored)}:{uuid.uuid4().hex}"
        try:
            # Unlocked as the owner that took it, so a lease closed from another thread still
            # releases, as Java's `unlockAsync(acquirerThreadId)` does.
            result = self._release(
                keys=[stored, _prefix_name("redisson_lock__channel", stored), latch],
                args=[UNLOCK_MESSAGE, millis, owner, "publish", _LATCH_MILLIS],
            )
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
            return
        try:
            # Redisson deletes the latch afterwards too, without checking; it expires anyway.
            self._client.delete(latch)
        except Exception:
            LOG.debug("failed to delete the unlock latch for %r; it expires on its own", key)
        if result is None:
            # Java's IllegalMonitorStateException: the TTL ran out first. Nothing is held.
            LOG.debug("Redis lock for %r was no longer held by this owner", key)


def _await_unlock(pubsub: Any, key: str, seconds: float) -> None:
    """Block until an unlock message arrives or ``seconds`` pass. Any message on the channel is
    a reason to try again; a stray one costs a single round trip."""
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        try:
            message = pubsub.get_message(timeout=remaining)
        except Exception as error:
            raise LockAcquisitionError(key, f"the lock backend failed: {error}") from error
        if message is not None and message.get("type") == "message":
            return


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
