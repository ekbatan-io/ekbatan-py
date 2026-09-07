"""Keyed locks, against the in-process backend.

The SQL backends share every line of this behaviour - re-entry, the watchdog, segmented
waiting - through SqlKeyedLockProvider; what they add is two statements each, which the conformance
suite exercises against real databases.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest

from ekbatan.errors import ValidationError
from ekbatan.locks import InProcessKeyedLockProvider, lock_key_hash

MS = timedelta(milliseconds=1)
HOLD = timedelta(seconds=30)
NOW = timedelta(0)


@pytest.fixture
def locks():
    lock = InProcessKeyedLockProvider()
    yield lock
    lock.close()


def in_thread(fn):
    """Run something on another thread and hand back whatever it produced."""
    box: list = []
    t = threading.Thread(target=lambda: box.append(_capture(fn)))
    t.start()
    t.join(5)
    assert not t.is_alive(), "the thread never finished"
    outcome, value = box[0]
    if outcome == "raised":
        raise value
    return value


def _capture(fn):
    try:
        return ("returned", fn())
    except BaseException as exc:
        return ("raised", exc)


def test_the_same_key_serialises_and_a_different_key_does_not(locks):
    with locks.acquire("wallet:w1", HOLD) as held:
        assert held.held
        assert in_thread(lambda: locks.try_acquire("wallet:w1", NOW, HOLD)) is None
        other = in_thread(lambda: locks.try_acquire("wallet:w2", NOW, HOLD))
        assert other is not None, "different keys must not block each other"
        other.release()


def test_a_waiter_gets_the_lock_as_soon_as_the_holder_releases(locks):
    held = locks.acquire("wallet:w1", HOLD)
    threading.Timer(0.05, held.release).start()

    started = time.monotonic()
    waiter = in_thread(lambda: locks.try_acquire("wallet:w1", timedelta(seconds=5), HOLD))
    assert waiter is not None
    assert time.monotonic() - started < 2, "hand-off is immediate, not on the next poll"
    waiter.release()


def test_a_waiter_gives_up_when_the_holder_does_not_release(locks):
    with locks.acquire("wallet:w1", HOLD):
        started = time.monotonic()
        assert in_thread(lambda: locks.try_acquire("wallet:w1", 200 * MS, HOLD)) is None
        assert time.monotonic() - started >= 0.15, "it actually waited"


def test_a_thread_may_reenter_a_key_it_holds(locks):
    with locks.acquire("wallet:w1", HOLD) as outer, locks.acquire("wallet:w1", HOLD) as inner:
        probe = locks.try_acquire("wallet:w1", NOW, HOLD)
        assert probe is not None, "re-entry never blocks and never touches the backend"
        probe.release()
        assert inner.held and outer.held


def test_only_the_outermost_release_frees_the_key(locks):
    outer = locks.acquire("wallet:w1", HOLD)
    inner = locks.acquire("wallet:w1", HOLD)
    inner.release()

    assert outer.held
    assert in_thread(lambda: locks.try_acquire("wallet:w1", NOW, HOLD)) is None

    outer.release()
    taken = in_thread(lambda: locks.try_acquire("wallet:w1", NOW, HOLD))
    assert taken is not None
    taken.release()


def test_a_reentry_cannot_shorten_the_outer_hold(locks):
    outer = locks.acquire("wallet:w1", HOLD)
    locks.acquire("wallet:w1", 50 * MS).release()  # an inner hold limit, deliberately tiny
    time.sleep(0.3)

    assert outer.held, "the outermost holder's commitment cannot be shortened from inside"
    assert in_thread(lambda: locks.try_acquire("wallet:w1", NOW, HOLD)) is None
    outer.release()


def test_a_child_thread_is_a_different_holder(locks):
    with locks.acquire("wallet:w1", HOLD):
        assert in_thread(lambda: locks.try_acquire("wallet:w1", NOW, HOLD)) is None, (
            "reentrancy is per thread, not per call stack"
        )


def test_a_hold_that_outlives_its_limit_is_released(locks):
    abandoned = locks.acquire("wallet:w1", 100 * MS)  # and never released
    time.sleep(0.4)

    assert not abandoned.held, "the lease reports honestly once the limit has fired"
    successor = in_thread(lambda: locks.try_acquire("wallet:w1", timedelta(seconds=2), HOLD))
    assert successor is not None, "one stuck holder must not stop the key forever"
    successor.release()
    abandoned.release()  # the forgetful holder returning: harmless


def test_releasing_twice_is_harmless(locks):
    lease = locks.acquire("wallet:w1", HOLD)
    lease.release()
    lease.release()
    assert not lease.held

    after = locks.try_acquire("wallet:w1", timedelta(seconds=1), HOLD)
    assert after is not None
    after.release()


def test_a_blank_key_and_a_non_positive_hold_are_refused(locks):
    with pytest.raises(ValidationError, match="blank"):
        locks.acquire("", HOLD)
    with pytest.raises(ValidationError, match="max_hold"):
        locks.acquire("wallet:w1", timedelta(0))
    with pytest.raises(ValidationError, match="max_wait"):
        locks.try_acquire("wallet:w1", -1 * MS, HOLD)


def test_concurrent_increments_under_one_key_add_up(locks):
    balance = {"value": 0}

    def deposit() -> None:
        with locks.acquire("wallet:w1", HOLD):
            current = balance["value"]
            time.sleep(0.001)  # widen the window a lost update would slip through
            balance["value"] = current + 1

    threads = [threading.Thread(target=deposit) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)

    assert balance["value"] == 20


def test_keys_do_not_accumulate(locks):
    for i in range(200):
        locks.acquire(f"wallet:{i}", HOLD).release()
    assert locks._locks == {}, "a lock object per key ever touched is a leak"


def test_the_key_hash_matches_the_other_ports():
    # First eight bytes of SHA-256, big-endian signed - the derivation every port uses, so
    # services in two languages contending for one key actually contend.
    assert lock_key_hash("wallet:w1") == 0x3633E13B37734F8D
    assert lock_key_hash("a") != lock_key_hash("b")


# -- the lock id is a cross-language contract --------------------------------------------------


#: Produced by running Java's `io.ekbatan.core.internal.LockKeyHash.hashUtf8` on this machine,
#: not by reasoning about it. A Java service and a Python service contending for `wallet:abc`
#: have to arrive at the same `pg_advisory_lock` argument or each quietly holds its own idea of
#: the lock and neither excludes the other - a failure that looks like nothing at all until two
#: writers run at once.
JAVA_LOCK_IDS = {
    "wallet:abc": -9133792321015084474,
    "": -2039914840885289964,
    "a": -3848465438864589366,
    "wallet:00000000-0000-0000-0000-000000000001": -2991874751685414135,
    "tenant/42:invoice": 6736202581112334014,
    "éà中文": 1179023550167011916,
    "x" * 500: -4356058416807128726,
}


@pytest.mark.parametrize(("key", "expected"), sorted(JAVA_LOCK_IDS.items()))
def test_a_lock_key_hashes_to_the_same_id_java_gives_it(key, expected):
    from ekbatan.locks.base import lock_key_hash

    assert lock_key_hash(key) == expected


def test_the_lock_id_is_signed_and_fits_a_bigint():
    """SHA-256's first eight bytes read as a *signed* big-endian long, which is what
    `ByteBuffer.getLong()` returns. Read unsigned, half of all keys would land outside the
    range Postgres's `pg_advisory_lock(bigint)` accepts, and none of them would match Java."""
    from ekbatan.locks.base import lock_key_hash

    ids = [lock_key_hash(f"key-{n}") for n in range(2000)]

    assert all(-(2**63) <= i < 2**63 for i in ids)
    assert any(i < 0 for i in ids), "unsigned would never produce one"


def test_the_hash_is_sha256_truncated_as_the_nist_vectors_say():
    """Java's `LockKeyHashTest` pins the same two, which is what makes this a shared algorithm
    rather than two implementations that happen to agree on the keys someone tried."""
    from ekbatan.locks.base import lock_key_hash

    def as_signed(value: int) -> int:
        return value - 2**64 if value >= 2**63 else value

    # SHA-256("")    = e3b0c44298fc1c14 9afbf4c8996fb924 ...
    # SHA-256("abc") = ba7816bf8f01cfea 414140de5dae2223 ...
    assert lock_key_hash("") == as_signed(0xE3B0C44298FC1C14)
    assert lock_key_hash("abc") == as_signed(0xBA7816BF8F01CFEA)
