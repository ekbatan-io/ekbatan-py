"""The Redis-backed keyed lock, against a real Redis.

Java's `RedisKeyedLockProviderIntegrationTest` and `RedisKeyedLockProviderLeaseTest`, case for
case. The three that are about Redisson's `CompletionException` unwrapping have no counterpart -
that is a detail of the client Java uses, and `redis-py` raises what it raises.

Nothing here is mocked. A lock that works against a fake is a lock nobody has tested.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest

from ekbatan.errors import LockAcquisitionError, ValidationError

pytest.importorskip("redis")

from ekbatan.locks.redis import (
    DEFAULT_NAMESPACE,
    RedisKeyedLockProvider,
    lease_millis,
)

SHORT = timedelta(seconds=5)
NONE = timedelta(0)


@pytest.fixture
def provider(redis_client, request):
    """A provider in a namespace of this test's own, so tests cannot collide."""
    made = RedisKeyedLockProvider(
        redis_client, namespace=f"t-{abs(hash(request.node.name)) % 100000}"
    )
    yield made
    made.close()


def key(request) -> str:
    return request.node.name.replace("[", "-").replace("]", "")


# -- what it refuses ----------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "   "])
def test_a_blank_key_is_refused(provider, bad):
    with pytest.raises(ValidationError):
        provider.acquire(bad, SHORT)


@pytest.mark.parametrize("bad", [timedelta(0), timedelta(seconds=-1)])
def test_a_max_hold_that_is_not_positive_is_refused(provider, bad):
    """A lock held for no time is not a lock. Java rejects it in both entry points."""
    with pytest.raises(ValidationError):
        provider.acquire("k", bad)
    with pytest.raises(ValidationError):
        provider.try_acquire("k", NONE, bad)


def test_a_negative_max_wait_is_refused(provider):
    with pytest.raises(ValidationError):
        provider.try_acquire("k", timedelta(seconds=-1), SHORT)


@pytest.mark.parametrize("bad", ["", "   "])
def test_a_blank_namespace_is_refused(redis_client, bad):
    with pytest.raises(LockAcquisitionError):
        RedisKeyedLockProvider(redis_client, namespace=bad)


def test_the_default_namespace_is_javas(redis_client, request):
    """The key prefix is shared state. A Java service and a Python service coordinating on
    `wallet:123` have to write the same Redis key or neither excludes the other."""
    assert DEFAULT_NAMESPACE == "ekbatan-lock"

    provider = RedisKeyedLockProvider(redis_client)
    try:
        with provider.acquire(key(request), SHORT):
            assert redis_client.get(f"ekbatan-lock:{key(request)}") is not None
    finally:
        provider.close()


# -- mutual exclusion ---------------------------------------------------------------------------


def test_one_key_excludes_another_thread(provider, request):
    k = key(request)
    blocked = threading.Event()

    with provider.acquire(k, SHORT):

        def other():
            if provider.try_acquire(k, NONE, SHORT) is None:
                blocked.set()

        thread = threading.Thread(target=other)
        thread.start()
        thread.join(5)

    assert blocked.is_set(), "a second thread must not get the same key"


def test_different_keys_do_not_block_each_other(provider, request):
    got = threading.Event()

    with provider.acquire(key(request) + "-a", SHORT):

        def other():
            lease = provider.try_acquire(key(request) + "-b", NONE, SHORT)
            if lease is not None:
                got.set()
                lease.release()

        thread = threading.Thread(target=other)
        thread.start()
        thread.join(5)

    assert got.is_set()


def test_try_acquire_with_no_wait_succeeds_when_the_key_is_free(provider, request):
    lease = provider.try_acquire(key(request), NONE, SHORT)

    assert lease is not None
    lease.release()


def test_try_acquire_waits_and_succeeds_when_the_holder_lets_go_in_time(provider, request):
    k = key(request)
    lease = provider.acquire(k, SHORT)
    threading.Timer(0.2, lease.release).start()

    def waiter():
        taken = provider.try_acquire(k, timedelta(seconds=3), SHORT)
        assert taken is not None, "the holder released well within the wait"
        taken.release()

    thread = threading.Thread(target=waiter)
    thread.start()
    thread.join(10)
    assert not thread.is_alive()


def test_try_acquire_gives_up_when_the_holder_does_not_release(provider, request):
    k = key(request)
    outcome: list[object] = []

    with provider.acquire(k, SHORT):

        def waiter():
            outcome.append(provider.try_acquire(k, timedelta(milliseconds=300), SHORT))

        thread = threading.Thread(target=waiter)
        thread.start()
        thread.join(10)

    assert outcome == [None], "contention is an empty result, never an exception"


def test_threads_contending_for_one_key_serialise(provider, request):
    """Sixteen threads, one key, one counter. A lock that does not exclude shows up here as a
    count that is not sixteen."""
    k = key(request)
    counter = {"n": 0}
    start = threading.Barrier(16)

    def increment():
        start.wait()
        with provider.acquire(k, timedelta(seconds=30)):
            seen = counter["n"]
            time.sleep(0.001)
            counter["n"] = seen + 1

    threads = [threading.Thread(target=increment) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)

    assert counter["n"] == 16


# -- the lease is bounded -----------------------------------------------------------------------


def test_max_hold_releases_the_key_on_its_own(provider, redis_client, request):
    """The TTL is the release mechanism, not a hint. A holder that dies releases nothing."""
    k = key(request)
    provider.acquire(k, timedelta(milliseconds=300))
    stored = f"{provider._prefix}{k}"

    assert redis_client.get(stored) is not None
    time.sleep(0.6)
    assert redis_client.get(stored) is None, "the lease expired without anyone unlocking"


def test_closing_a_lease_deletes_the_key(provider, redis_client, request):
    k = key(request)
    stored = f"{provider._prefix}{k}"

    lease = provider.acquire(k, SHORT)
    assert redis_client.get(stored) is not None
    lease.release()

    assert redis_client.get(stored) is None


def test_an_expired_lease_does_not_delete_the_next_holders_key(provider, redis_client, request):
    """Java's `max_hold_watchdog_should_not_send_stale_redis_unlock`.

    The watchdog fires at `max_hold`, which is exactly the TTL Redis was given - so the key is
    already gone. Sending a delete then would race whoever has just taken it.
    """
    k = key(request)
    stored = f"{provider._prefix}{k}"

    provider.acquire(k, timedelta(milliseconds=200))
    time.sleep(0.5)

    second = provider.acquire(k, SHORT)
    time.sleep(0.3)

    assert redis_client.get(stored) is not None, "the second holder still has its key"
    second.release()


def test_a_lease_that_expired_reports_itself_as_no_longer_held(provider, request):
    lease = provider.acquire(key(request), timedelta(milliseconds=200))

    assert lease.held
    time.sleep(0.5)
    assert not lease.held


# -- the lease rounding -------------------------------------------------------------------------
#
# Java's RedisKeyedLockProviderLeaseTest.


def test_a_sub_millisecond_hold_does_not_truncate_to_zero():
    """Redis rejects `PX 0` outright - measured against a real server. Java rounds up here for
    a worse reason: Redisson reads a non-positive lease as "no lease" and renews the key for as
    long as the process lives."""
    assert lease_millis(timedelta(microseconds=1)) == 1
    assert lease_millis(timedelta(microseconds=999)) == 1


def test_whole_millisecond_holds_pass_through_unchanged():
    assert lease_millis(timedelta(milliseconds=250)) == 250
    assert lease_millis(timedelta(seconds=5)) == 5_000


def test_a_very_long_hold_is_not_clamped():
    assert lease_millis(timedelta(days=1)) == 86_400_000


def test_a_sub_millisecond_hold_still_produces_an_expiring_lease(provider, redis_client, request):
    k = key(request)
    provider.acquire(k, timedelta(microseconds=500))
    time.sleep(0.05)

    assert redis_client.get(f"{provider._prefix}{k}") is None, "1ms, not forever"


def test_a_sub_millisecond_hold_does_not_block_a_later_acquirer(provider, request):
    k = key(request)
    provider.acquire(k, timedelta(microseconds=500))
    time.sleep(0.05)

    lease = provider.try_acquire(k, NONE, SHORT)
    assert lease is not None
    lease.release()


# -- re-entry -----------------------------------------------------------------------------------


def test_the_same_thread_re_entering_does_not_block(provider, request):
    k = key(request)

    with provider.acquire(k, SHORT), provider.acquire(k, SHORT):
        assert True, "re-entry returns immediately rather than deadlocking on itself"


def test_an_inner_close_does_not_release_the_key(provider, redis_client, request):
    k = key(request)
    stored = f"{provider._prefix}{k}"

    outer = provider.acquire(k, SHORT)
    inner = provider.acquire(k, SHORT)
    inner.release()

    assert redis_client.get(stored) is not None, "the outer holder still has it"
    outer.release()
    assert redis_client.get(stored) is None


def test_closing_the_outer_first_keeps_the_key_until_the_inner_closes(
    provider, redis_client, request
):
    """Order does not matter; the count does. Whichever closes last is the one that releases."""
    k = key(request)
    stored = f"{provider._prefix}{k}"

    outer = provider.acquire(k, SHORT)
    inner = provider.acquire(k, SHORT)
    outer.release()

    assert redis_client.get(stored) is not None
    inner.release()
    assert redis_client.get(stored) is None


def test_an_inner_max_hold_does_not_shorten_the_outer_lease(provider, redis_client, request):
    """The outermost `max_hold` governs, as Java's `KeyedReentrantHolder` enforces. An inner
    re-entry asking for less must not shorten the holder's lease under it."""
    k = key(request)
    stored = f"{provider._prefix}{k}"

    with provider.acquire(k, timedelta(seconds=10)):
        with provider.acquire(k, timedelta(milliseconds=100)):
            pass
        time.sleep(0.4)
        assert redis_client.get(stored) is not None, "the inner hold did not shorten the TTL"
        assert redis_client.pttl(stored) > 1000


def test_a_child_thread_is_a_different_holder_and_is_excluded(provider, request):
    """Re-entry is per thread. A thread the holder started is somebody else."""
    k = key(request)
    blocked = threading.Event()

    with provider.acquire(k, SHORT):

        def child():
            if provider.try_acquire(k, NONE, SHORT) is None:
                blocked.set()

        thread = threading.Thread(target=child)
        thread.start()
        thread.join(5)

    assert blocked.is_set()


def test_re_entering_through_try_acquire_succeeds_immediately(provider, request):
    k = key(request)

    with provider.acquire(k, SHORT):
        reentered = provider.try_acquire(k, NONE, SHORT)
        assert reentered is not None, "the holder is not made to wait for itself"
        reentered.release()


def test_closing_a_lease_twice_is_harmless(provider, redis_client, request):
    k = key(request)
    stored = f"{provider._prefix}{k}"

    lease = provider.acquire(k, SHORT)
    lease.release()
    lease.release()

    assert redis_client.get(stored) is None


# -- when the backend is unhappy ----------------------------------------------------------------


def test_a_failing_release_does_not_escape_the_with_block(provider, request, caplog, monkeypatch):
    """Nothing can release a lock on a server it cannot reach, so the TTL is the only remedy -
    but it must not be silent, and it must not turn the caller's `with` block into a failure."""
    import logging

    k = key(request)
    lease = provider.acquire(k, SHORT)
    monkeypatch.setattr(
        provider, "_release", lambda **_: (_ for _ in ()).throw(RuntimeError("redis is gone"))
    )

    with caplog.at_level(logging.ERROR):
        lease.release()

    assert any("stays held until its TTL" in r.message for r in caplog.records)


def test_a_failed_release_does_not_wedge_the_provider_for_that_key(provider, request, monkeypatch):
    """The lease is gone from this process either way, so the next acquire must be allowed to
    try - it will simply wait for the TTL."""
    k = key(request)
    lease = provider.acquire(k, timedelta(milliseconds=300))
    monkeypatch.setattr(
        provider, "_release", lambda **_: (_ for _ in ()).throw(RuntimeError("redis is gone"))
    )
    lease.release()

    monkeypatch.undo()
    time.sleep(0.5)
    again = provider.try_acquire(k, NONE, SHORT)

    assert again is not None, "once the TTL expired, the key is takeable again"
    again.release()


def test_a_backend_failure_on_acquire_is_reported_as_one(provider, request, monkeypatch):
    """A broken backend and a busy one call for opposite responses, so they must not arrive the
    same way. Contention is `None`; a failure is an exception."""
    monkeypatch.setattr(
        provider._client, "set", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no redis"))
    )

    with pytest.raises(LockAcquisitionError, match="the lock backend failed"):
        provider.acquire(key(request), SHORT)
