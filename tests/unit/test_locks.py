"""Keyed locks, below the backends: the key hash, the argument check, and the holder.

What a lock promises - exclusion, re-entry, waiting, the hold limit - is the conformance suite's
job, in `tests/integration/test_locks.py`, against Postgres, MySQL and MariaDB, and against
Redis beside it. This file tests what those backends share and what is hard to make a real
server do: a registration that fails after the lock was taken, a watchdog whose release fails, a
close racing a watchdog. Java tests the same things the same way, on `KeyedReentrantHolder`
directly.
"""

from __future__ import annotations

import time
from datetime import timedelta

import pytest

from ekbatan.errors import ValidationError
from ekbatan.locks import lock_key_hash
from ekbatan.locks.base import check

MS = timedelta(milliseconds=1)
HOLD = timedelta(seconds=30)


def test_a_blank_key_and_a_non_positive_hold_are_refused():
    """Every backend calls this first, so it is tested once, here, rather than per backend."""
    with pytest.raises(ValidationError, match="blank"):
        check("", HOLD)
    with pytest.raises(ValidationError, match="blank"):
        check("   ", HOLD)
    with pytest.raises(ValidationError, match="max_hold"):
        check("wallet:w1", timedelta(0))
    with pytest.raises(ValidationError, match="max_wait"):
        check("wallet:w1", HOLD, -1 * MS)
    check("wallet:w1", HOLD, timedelta(0))  # a single attempt is allowed


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


#: The `GET_LOCK` name Java's `MySQLKeyedLockProvider` and `MariaDBKeyedLockProvider` give each
#: key - `Long.toHexString(LockKeyHash.hashUtf8(key))`, produced by running that Java code on this
#: machine. Bare hex, no prefix: Java is the reference, and a name that differs by one character
#: is a different lock, so a Python and a Java service would each hold "the same" lock at once.
JAVA_GET_LOCK_NAMES = {
    "wallet:abc": "813e4046e5b48a46",
    "order-42": "3bf8b157c4238eef",
    "": "e3b0c44298fc1c14",
    "\u043a\u043b\u044e\u0447": "1de36a32af798da0",
    "a": "ca978112ca1bbdca",
}


@pytest.mark.parametrize("provider", ["MySQLKeyedLockProvider", "MariaDBKeyedLockProvider"])
@pytest.mark.parametrize(("key", "expected"), sorted(JAVA_GET_LOCK_NAMES.items()))
def test_a_get_lock_name_is_the_one_java_gives_it(provider, key, expected):
    from ekbatan.locks import sql

    # The name is a pure function of the key; no engine is needed to compute it.
    cls = getattr(sql, provider)
    assert cls._name(cls.__new__(cls), lock_key_hash(key)) == expected


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


# -- what the holder promises when things go wrong ---------------------------------------------
#
# Java's `KeyedReentrantHolder` is 275 lines and most of it is this: who wins between a close and
# a watchdog, and what happens when the cleanup fails too.


def test_a_failed_registration_gives_the_lock_back(monkeypatch):
    """The backend lock is already taken by the time the holder registers it, so a failure here
    has to hand it back - or it is held by nobody until it expires."""
    from ekbatan.locks._holder import KeyedReentrantHolder

    holder = KeyedReentrantHolder()
    returned: list[tuple] = []

    from ekbatan.locks._deadlines import Deadlines

    def cannot_start(self, delay, fire):
        raise OSError("cannot start a thread")

    monkeypatch.setattr(Deadlines, "after", cannot_start)

    with pytest.raises(OSError):
        holder.register("k", "payload", HOLD, lambda p, r: returned.append((p, r)))

    assert [p for p, _ in returned] == ["payload"], "the lock was given back"
    assert holder.reenter("k") is None, (
        "and nothing was left behind for a re-entry to find - Java's "
        "`registration_failure_does_not_leave_stale_reentry`"
    )


def test_a_cleanup_that_also_fails_is_attached_rather_than_substituted(monkeypatch):
    """Java attaches it with `addSuppressed`, and the reason survives translation: raising the
    cleanup failure instead puts "returning the connection failed" in front of the caller and
    buries what actually went wrong."""
    from ekbatan.locks._holder import KeyedReentrantHolder

    holder = KeyedReentrantHolder()

    def cleanup_also_fails(payload, reason):
        raise RuntimeError("returning the connection failed too")

    from ekbatan.locks._deadlines import Deadlines

    def cannot_start(self, delay, fire):
        raise OSError("the registration failure")

    monkeypatch.setattr(Deadlines, "after", cannot_start)

    with pytest.raises(OSError, match="the registration failure") as raised:
        holder.register("k", "payload", HOLD, cleanup_also_fails)

    notes = getattr(raised.value, "__notes__", [])
    assert any("releasing the lock afterwards also failed" in n for n in notes)
    assert any("returning the connection failed too" in n for n in notes)


def test_a_watchdog_whose_release_fails_says_so_and_does_not_die_quietly(caplog):
    """This runs on the watchdog thread. Anything that escapes reaches stderr through the threading
    hook rather than the logger, and Java's comment records what that cost: a log line asserting
    the release had succeeded, and the real failure nowhere near it."""
    import logging

    from ekbatan.locks._holder import KeyedReentrantHolder

    holder = KeyedReentrantHolder()

    def release_fails(payload, reason):
        raise BaseException("the backend refused")

    with caplog.at_level(logging.ERROR):
        holder.register("k", "payload", timedelta(milliseconds=50), release_fails)
        time.sleep(0.4)

    assert any("may remain held" in r.message for r in caplog.records), (
        "the failure is reported against the key that caused it"
    )
    assert not any("Auto-released held lock" in r.message for r in caplog.records), (
        "and the success line is not written, because the release did not succeed"
    )


def test_the_success_line_is_written_only_after_the_release_worked(caplog):
    import logging

    from ekbatan.locks._holder import KeyedReentrantHolder

    holder = KeyedReentrantHolder()
    released: list[str] = []

    with caplog.at_level(logging.WARNING):
        holder.register("k", "payload", timedelta(milliseconds=50), lambda p, r: released.append(p))
        time.sleep(0.4)

    assert released == ["payload"]
    assert any(
        r.message == "Auto-released held lock for key k (hold limit exceeded)"
        for r in caplog.records
    ), "Java's line, word for word"


def test_a_close_and_a_watchdog_cannot_both_release(caplog):
    """Java arbitrates this with a CAS on the counter; here it is a lock. Either way exactly one
    of them owns the release, and releasing twice would hand the key to a second waiter while
    the first still believes it holds it."""
    from ekbatan.locks._holder import KeyedReentrantHolder

    holder = KeyedReentrantHolder()
    releases: list[str] = []

    lease = holder.register(
        "k", "payload", timedelta(milliseconds=80), lambda p, r: releases.append(r.value)
    )
    time.sleep(0.3)
    lease.release()

    assert len(releases) == 1, f"released {len(releases)} times: {releases}"
    assert releases == ["EXPIRED"], "the watchdog got there first, so the close is a no-op"


def test_closing_an_inner_lease_twice_does_not_free_the_outer_hold():
    """As Java's `HeldLease`: each lease remembers it was closed. Counting the hold down on
    every close let a second close of the inner lease free the key under the outer region."""
    import threading

    from ekbatan.locks._holder import KeyedReentrantHolder

    holder = KeyedReentrantHolder()
    released: list[str] = []
    outer = holder.register("k", "payload", HOLD, lambda p, r: released.append(r.value))
    inner = holder.reenter("k")
    assert inner is not None

    inner.release()
    inner.release()

    assert released == [], "the outer region still holds the key"
    assert outer.held
    assert not inner.held, "a closed lease reports itself, not the whole hold"
    seen_from_another_thread: list[object] = []
    worker = threading.Thread(target=lambda: seen_from_another_thread.append(holder.reenter("k")))
    worker.start()
    worker.join()
    assert seen_from_another_thread == [None], "another thread is a different holder"

    outer.release()
    outer.release()
    assert released == ["CLOSED"], "the outermost close frees it, once"


# -- an unreachable database ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        ("PostgresKeyedLockProvider", "postgresql+psycopg://u:{pw}@127.0.0.1:1/x"),
        ("MySQLKeyedLockProvider", "mysql+pymysql://u:{pw}@127.0.0.1:1/x"),
        ("MariaDBKeyedLockProvider", "mysql+pymysql://u:{pw}@127.0.0.1:1/x"),
    ],
)
def test_an_unreachable_database_is_a_lock_acquisition_error(provider, url):
    """Java wraps a failed connection as `LockAcquisitionException` ("could not obtain a
    connection"), so a caller's `except` for the documented type catches the most likely
    failure of all. The password is in no message along the way."""
    from ekbatan import DataSource
    from ekbatan.errors import LockAcquisitionError
    from ekbatan.locks import sql
    from fixtures.logins import data_source

    marker = "Marker-Pw-5f3a9"
    source = DataSource.from_config(data_source(url.format(pw=marker), pool_size=1, max_overflow=0))
    locks = getattr(sql, provider)(source)
    try:
        for attempt in (
            lambda: locks.try_acquire("k", timedelta(0), HOLD),
            lambda: locks.acquire("k", HOLD),
        ):
            with pytest.raises(LockAcquisitionError, match="could not obtain a connection") as e:
                attempt()
            assert e.value.key == "k"
            chain = [e.value, e.value.__cause__]
            assert all(marker not in str(x) and marker not in repr(x) for x in chain)
    finally:
        source.close()
