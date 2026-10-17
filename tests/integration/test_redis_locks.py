"""The Redis-backed keyed lock, against a real Redis.

Java's `RedisKeyedLockProviderIntegrationTest` and `RedisKeyedLockProviderLeaseTest`, case for
case, and the Redisson layout a Java service reads and writes. The three that are about
Redisson's `CompletionException` unwrapping have no counterpart - that is a detail of the client
Java uses, and `redis-py` raises what it raises.

Nothing here is mocked. A lock that works against a fake is a lock nobody has tested.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import timedelta
from pathlib import Path

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
            assert redis_client.exists(f"ekbatan-lock:{key(request)}")
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

    assert redis_client.exists(stored)
    time.sleep(0.6)
    assert not redis_client.exists(stored), "the lease expired without anyone unlocking"


def test_closing_a_lease_deletes_the_key(provider, redis_client, request):
    k = key(request)
    stored = f"{provider._prefix}{k}"

    lease = provider.acquire(k, SHORT)
    assert redis_client.exists(stored)
    lease.release()

    assert not redis_client.exists(stored)


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

    assert redis_client.exists(stored), "the second holder still has its key"
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

    assert not redis_client.exists(f"{provider._prefix}{k}"), "1ms, not forever"


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

    assert redis_client.exists(stored), "the outer holder still has it"
    outer.release()
    assert not redis_client.exists(stored)


def test_closing_the_outer_first_keeps_the_key_until_the_inner_closes(
    provider, redis_client, request
):
    """Order does not matter; the count does. Whichever closes last is the one that releases."""
    k = key(request)
    stored = f"{provider._prefix}{k}"

    outer = provider.acquire(k, SHORT)
    inner = provider.acquire(k, SHORT)
    outer.release()

    assert redis_client.exists(stored)
    inner.release()
    assert not redis_client.exists(stored)


def test_an_inner_max_hold_does_not_shorten_the_outer_lease(provider, redis_client, request):
    """The outermost `max_hold` governs, as Java's `KeyedReentrantHolder` enforces. An inner
    re-entry asking for less must not shorten the holder's lease under it."""
    k = key(request)
    stored = f"{provider._prefix}{k}"

    with provider.acquire(k, timedelta(seconds=10)):
        with provider.acquire(k, timedelta(milliseconds=100)):
            pass
        time.sleep(0.4)
        assert redis_client.exists(stored), "the inner hold did not shorten the TTL"
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

    assert not redis_client.exists(stored)


def test_closing_the_provider_leaves_open_leases_held(provider, redis_client, request):
    """Java's `close()` does nothing (Dracula 37); it used to delete every open lease's key."""
    k = key(request)
    lease = provider.acquire(k, SHORT)
    try:
        provider.close()
        assert lease.held
        assert redis_client.exists(f"{provider._prefix}{k}")
    finally:
        lease.release()


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
        provider, "_lock", lambda **_: (_ for _ in ()).throw(RuntimeError("no redis"))
    )

    with pytest.raises(LockAcquisitionError, match="the lock backend failed"):
        provider.acquire(key(request), SHORT)


# -- the layout a Java service shares -----------------------------------------------------------
#
# Java's provider is a Redisson RLock. A Python holder must look to Redisson exactly like a
# Redisson holder, or a Java service trying the same key fails with WRONGTYPE instead of waiting.


def test_the_lock_is_a_redisson_hash(provider, redis_client, request):
    """A hash, one field `<client id>:<thread id>` holding the count 1, TTL `max_hold`."""
    k = key(request)
    stored = f"{provider._prefix}{k}"

    with provider.acquire(k, timedelta(seconds=30)):
        assert redis_client.type(stored) in (b"hash", "hash")
        fields = redis_client.hgetall(stored)
        assert len(fields) == 1
        ((field, count),) = fields.items()
        field = field.decode() if isinstance(field, bytes) else field
        client_id, thread_id = field.rsplit(":", 1)
        assert client_id == provider._id
        assert thread_id == str(threading.get_ident())
        assert int(count) == 1
        assert 29_000 < redis_client.pttl(stored) <= 30_000


def test_a_release_publishes_redissons_unlock_message(provider, redis_client, request):
    """`0` on `redisson_lock__channel:{<key>}` is what wakes a Redisson waiter."""
    k = key(request)
    pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(f"redisson_lock__channel:{{{provider._prefix}{k}}}")
    try:
        provider.acquire(k, SHORT).release()
        message = None
        end = time.monotonic() + 2
        while message is None and time.monotonic() < end:
            message = pubsub.get_message(timeout=0.5)
        assert message is not None and message["data"] in (b"0", "0")
    finally:
        pubsub.close()


def test_a_waiter_is_woken_by_the_release_not_by_a_poll(provider, request):
    """Redisson waiters subscribe; so do these. Hand-off is immediate even with a long TTL."""
    k = key(request)
    lease = provider.acquire(k, timedelta(seconds=30))
    took: list[float] = []

    def waiter():
        got = provider.try_acquire(k, timedelta(seconds=10), SHORT)
        took.append(time.monotonic())
        assert got is not None
        got.release()

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.3)
    released_at = time.monotonic()
    lease.release()
    thread.join(10)

    assert took and took[0] - released_at < 0.2, "woken by the unlock message"


def test_a_bounded_wait_ends_on_time(provider, request):
    """Redisson's wait is against a deadline; it does not drift by the time each call takes."""
    k = key(request)
    with provider.acquire(k, timedelta(seconds=30)):
        outcome: list[object] = []
        started = time.monotonic()
        thread = threading.Thread(
            target=lambda: outcome.append(provider.try_acquire(k, timedelta(seconds=1), SHORT))
        )
        thread.start()
        thread.join(10)
        waited = time.monotonic() - started

    assert outcome == [None]
    assert 0.95 <= waited < 1.2, f"waited {waited:.2f}s"


def test_a_redisson_holder_keeps_a_python_waiter_out_until_it_unlocks(
    provider, redis_client, request
):
    """A Java holder, played with Redisson's own scripts and a foreign client id: Python waits
    (no WRONGTYPE), and Redisson's unlock - delete plus the `0` message - lets it in at once."""
    from ekbatan.locks.redis import _LOCK, _UNLOCK

    k = key(request)
    stored = f"{provider._prefix}{k}"
    channel = f"redisson_lock__channel:{{{stored}}}"
    java_owner = "00000000-0000-0000-0000-00000000java:1"
    assert redis_client.eval(_LOCK, 1, stored, 30_000, java_owner) is None

    assert provider.try_acquire(k, NONE, SHORT) is None
    took: list[object] = []
    thread = threading.Thread(
        target=lambda: took.append(provider.try_acquire(k, timedelta(seconds=10), SHORT))
    )
    thread.start()
    time.sleep(0.3)
    latch = f"redisson_unlock_latch:{{{stored}}}:x"
    assert (
        redis_client.eval(
            _UNLOCK, 3, stored, channel, latch, 0, 30_000, java_owner, "publish", 13_500
        )
        == 1
    )
    thread.join(5)

    assert took and took[0] is not None, "the unlock message woke the waiter"
    took[0].release()


# -- pinned to real Redisson -------------------------------------------------------------------
#
# `tests/fixtures/redisson/java-3.39.0.monitor.log` is what Redis's MONITOR printed while Ekbatan's
# Java `RedisKeyedLockProvider`, on Redisson 3.39.0, ran the scenarios in
# `RedissonRecordingTest.java` beside it. The tests above check this port against its own copy of
# Redisson's scripts; these two check that copy against the real thing, without needing Java.
# To re-record after a Redisson upgrade: run that test against a Redis under `redis-cli monitor`.

_RECORDING = Path(__file__).parents[1] / "fixtures" / "redisson" / "java-3.39.0.monitor.log"
_SKIPPED = {"ping", "client", "info", "config", "hello", "select", "command", "script"}
_OWNER = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:\d+$")
_LATCH = re.compile(r"^(redisson_unlock_latch:\{[^}]*\}):[0-9a-f]+$")


def _monitor_line(line: str) -> tuple[str, list[str]] | None:
    """`<time> [<db> <origin>] "CMD" "arg" ...` -> (origin, [cmd, args...]), MONITOR's escapes
    undone. Origin is `lua` for a command a script ran, else `client`."""
    head = re.match(r"^\d+\.\d+ \[\d+ ([^\]]+)\] ", line)
    if head is None:
        return None
    origin = "lua" if head.group(1) == "lua" else "client"
    words = [
        bytes(w, "latin-1").decode("unicode_escape")
        for w in re.findall(r'"((?:[^"\\]|\\.)*)"', line[head.end() :])
    ]
    return origin, words


def _normalised(lines: list[str], scripts: dict[str, str]) -> list[tuple[str, ...]]:
    """The commands from the first scenario marker on, with what differs per run - the owner's
    client id and thread, the latch's random suffix - replaced, and a script call named by the
    script it ran rather than by its text or SHA."""
    out: list[tuple[str, ...]] = []
    started = False
    for line in lines:
        parsed = _monitor_line(line)
        if parsed is None:
            continue
        origin, (cmd, *args) = parsed
        cmd = cmd.lower()
        if cmd == "exists" and args and args[0].startswith("rec:marker:"):
            started = True
        if not started or cmd in _SKIPPED:
            continue
        if origin == "client" and cmd in ("eval", "evalsha"):
            out.append((origin, "script", scripts[args[0]]))
            continue
        args = ["<owner>" if _OWNER.match(a) else _LATCH.sub(r"\1:<latch>", a) for a in args]
        out.append((origin, cmd, *args))
    # A failed EVALSHA (NOSCRIPT) is followed by the same call again; keep one.
    return [c for i, c in enumerate(out) if not (c[1] == "script" and out[i - 1 : i] == [c])]


def test_the_scripts_are_redissons_own():
    """The Lua this port sends is the Lua real Redisson sends, character for character."""
    from ekbatan.locks.redis import _LOCK, _UNLOCK

    sent = set()
    for line in _RECORDING.read_text().splitlines():
        parsed = _monitor_line(line)
        if parsed and parsed[0] == "client" and parsed[1][0].upper() == "EVAL":
            sent.add(parsed[1][1])
    assert sent == {_LOCK, _UNLOCK}, "Redisson 3.39.0 sends other scripts than this port copies"


def test_redis_sees_from_this_port_exactly_what_it_saw_from_java(redis_client):
    """The same three scenarios as `RedissonRecordingTest.java`, under MONITOR: every command
    Redis runs - the scripts' own `hincrby`, `pexpire`, `del`, `PUBLISH`, the latch - matches the
    Java recording, values included."""
    import hashlib

    from ekbatan.locks.redis import _LOCK, _UNLOCK

    scripts = {_LOCK: "lock", _UNLOCK: "unlock"}
    scripts |= {hashlib.sha1(s.encode()).hexdigest(): n for s, n in list(scripts.items())}
    provider = RedisKeyedLockProvider(redis_client)  # the default namespace, as Java's
    hold = timedelta(seconds=30)

    def mark(name: str) -> None:
        redis_client.exists(f"rec:marker:{name}")

    with redis_client.monitor() as monitor:
        mark("acquire-release")
        with provider.acquire("rec:one", hold):
            mark("while-held")
        mark("re-entry")
        with provider.acquire("rec:two", hold):
            with provider.acquire("rec:two", hold):
                mark("inner-held")
            mark("inner-closed")
        mark("try-acquire")
        lease = provider.try_acquire("rec:three", NONE, hold)
        assert lease is not None
        with lease:
            mark("try-held")
        mark("end")

        seen: list[str] = []
        while not seen or '"rec:marker:end"' not in seen[-1]:
            raw = monitor.connection.read_response()
            seen.append(raw.decode() if isinstance(raw, bytes) else raw)
    provider.close()

    java = _normalised(_RECORDING.read_text().splitlines(), scripts)
    assert len(java) > 30, "the recording lost its scenarios"
    assert _normalised(seen, scripts) == java
