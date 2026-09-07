"""The SQL keyed locks, against the databases that actually implement them.

Java runs these three times - `PostgresKeyedLockProviderIntegrationTest`,
`MySQLKeyedLockProviderIntegrationTest`, `MariaDBKeyedLockProviderIntegrationTest` - and the
files differ by one case. Here it is one suite over `--dialects`, with that one case marked.

SQLite has no advisory lock, so there is nothing to test on it. That is the whole point of
running the suite against the real three before anything is believed.

A lease holds a connection open for its lifetime, which is the failure mode worth testing: a
lock that leaks one takes the pool down long before it takes correctness down.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest

from ekbatan.locks.sql import (
    MariaDBKeyedLockProvider,
    MySQLKeyedLockProvider,
    PostgresKeyedLockProvider,
)

HOLD = timedelta(seconds=30)
NOW = timedelta(0)

PROVIDERS = {
    "postgres": PostgresKeyedLockProvider,
    "mysql": MySQLKeyedLockProvider,
    "mariadb": MariaDBKeyedLockProvider,
}


@pytest.fixture
def locks(registry, dialect):
    """The provider this dialect implements, over the shard's own engine."""
    if dialect not in PROVIDERS:
        pytest.skip(f"{dialect} has no advisory lock; the SQL providers are for the real three")
    made = PROVIDERS[dialect](registry.default.primary)
    yield made
    made.close()


def key(request) -> str:
    return request.node.name.replace("[", "-").replace("]", "")


def pool_in_use(registry) -> int:
    return registry.default.primary.pool.checkedout()


# -- mutual exclusion ---------------------------------------------------------------------------


def test_one_key_excludes_another_thread(locks, request):
    k = key(request)
    blocked = threading.Event()

    with locks.acquire(k, HOLD):

        def other():
            if locks.try_acquire(k, NOW, HOLD) is None:
                blocked.set()

        thread = threading.Thread(target=other)
        thread.start()
        thread.join(20)

    assert blocked.is_set()


def test_different_keys_do_not_block_each_other(locks, request):
    got = threading.Event()

    with locks.acquire(key(request) + "-a", HOLD):

        def other():
            lease = locks.try_acquire(key(request) + "-b", NOW, HOLD)
            if lease is not None:
                got.set()
                lease.release()

        thread = threading.Thread(target=other)
        thread.start()
        thread.join(20)

    assert got.is_set()


def test_try_acquire_with_no_wait_succeeds_when_free(locks, request):
    lease = locks.try_acquire(key(request), NOW, HOLD)

    assert lease is not None
    lease.release()


def test_try_acquire_waits_and_succeeds_when_the_holder_lets_go_in_time(locks, request):
    k = key(request)
    lease = locks.acquire(k, HOLD)
    threading.Timer(0.3, lease.release).start()
    taken: list[object] = []

    def waiter():
        taken.append(locks.try_acquire(k, timedelta(seconds=10), HOLD))

    thread = threading.Thread(target=waiter)
    thread.start()
    thread.join(30)

    assert taken and taken[0] is not None
    taken[0].release()


def test_try_acquire_gives_up_when_the_holder_does_not_release(locks, request):
    k = key(request)
    outcome: list[object] = []

    with locks.acquire(k, HOLD):

        def waiter():
            outcome.append(locks.try_acquire(k, timedelta(seconds=1), HOLD))

        thread = threading.Thread(target=waiter)
        thread.start()
        thread.join(30)

    assert outcome == [None], "contention is an empty result, never an exception"


def test_a_blocked_acquire_goes_through_once_the_holder_releases(locks, request):
    """Java's `blocked_acquire_should_still_acquire_when_holder_releases`. `acquire` waits as
    long as it takes, in segments, so a release part way through is picked up."""
    k = key(request)
    lease = locks.acquire(k, HOLD)
    taken: list[object] = []

    def waiter():
        taken.append(locks.acquire(k, HOLD))

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.3)
    lease.release()
    thread.join(30)

    assert not thread.is_alive(), "the waiter was still blocked after the holder let go"
    assert taken and taken[0] is not None
    taken[0].release()


def test_threads_contending_for_one_key_serialise(locks, request):
    """Twelve threads, one key, one counter. A lock that does not exclude shows up as a count
    that is not twelve."""
    k = key(request)
    counter = {"n": 0}
    start = threading.Barrier(12)

    def increment():
        start.wait()
        with locks.acquire(k, HOLD):
            seen = counter["n"]
            time.sleep(0.005)
            counter["n"] = seen + 1

    threads = [threading.Thread(target=increment) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)

    assert counter["n"] == 12


# -- the lease is bounded, and so is the pool ---------------------------------------------------


def test_max_hold_releases_the_lock_on_its_own(locks, request):
    """A holder that never closes must not hold the key forever."""
    k = key(request)
    locks.acquire(k, timedelta(milliseconds=400))
    time.sleep(1.2)

    lease = locks.try_acquire(k, timedelta(seconds=2), HOLD)
    assert lease is not None, "the watchdog gave it back"
    lease.release()


def test_releasing_a_lease_gives_its_connection_back(locks, registry, request):
    """The failure mode that matters. A lease holds a connection for its lifetime, so a leak
    here empties the pool long before it breaks correctness."""
    before = pool_in_use(registry)

    lease = locks.acquire(key(request), HOLD)
    assert pool_in_use(registry) == before + 1, "the lease borrowed one"
    lease.release()

    assert pool_in_use(registry) == before, "and gave it back"


def test_an_expired_lease_gives_its_connection_back_too(locks, registry, request):
    before = pool_in_use(registry)

    locks.acquire(key(request), timedelta(milliseconds=300))
    time.sleep(1.0)

    assert pool_in_use(registry) == before, "the watchdog returned it, not just the lock"


def test_a_refused_try_acquire_borrows_nothing(locks, registry, request):
    k = key(request)
    with locks.acquire(k, HOLD):
        during = pool_in_use(registry)

        def other():
            assert locks.try_acquire(k, NOW, HOLD) is None

        thread = threading.Thread(target=other)
        thread.start()
        thread.join(20)

        assert pool_in_use(registry) == during, "a failed attempt must not strand a connection"


# -- re-entry -----------------------------------------------------------------------------------


def test_re_entering_does_not_block_or_borrow_another_connection(locks, registry, request):
    """Java's case name says both halves. The second is the one with teeth: a re-entry that
    borrowed its own connection would exhaust the pool at depth, not at breadth."""
    k = key(request)

    with locks.acquire(k, HOLD):
        borrowed = pool_in_use(registry)
        with locks.acquire(k, HOLD):
            assert pool_in_use(registry) == borrowed, "re-entry shares the outer's connection"


def test_an_inner_release_does_not_give_up_the_lock(locks, request):
    k = key(request)
    outer = locks.acquire(k, HOLD)
    inner = locks.acquire(k, HOLD)
    inner.release()

    blocked = threading.Event()

    def other():
        if locks.try_acquire(k, NOW, HOLD) is None:
            blocked.set()

    thread = threading.Thread(target=other)
    thread.start()
    thread.join(20)
    outer.release()

    assert blocked.is_set(), "the outer holder still had it"


def test_releasing_the_outer_first_keeps_it_until_the_inner_does(locks, registry, request):
    k = key(request)
    before = pool_in_use(registry)

    outer = locks.acquire(k, HOLD)
    inner = locks.acquire(k, HOLD)
    outer.release()
    assert pool_in_use(registry) == before + 1, "still held, so still borrowing"

    inner.release()
    assert pool_in_use(registry) == before


def test_an_inner_max_hold_does_not_shorten_the_outer_one(locks, request):
    """The outermost `max_hold` governs. An inner re-entry asking for less must not pull the
    lease out from under the holder."""
    k = key(request)

    with locks.acquire(k, timedelta(seconds=20)) as outer:
        with locks.acquire(k, timedelta(milliseconds=200)):
            pass
        time.sleep(0.8)
        assert outer.held, "the inner hold did not expire the outer lease"


def test_a_child_thread_is_a_different_holder_and_is_excluded(locks, request):
    k = key(request)
    blocked = threading.Event()

    with locks.acquire(k, HOLD):

        def child():
            if locks.try_acquire(k, NOW, HOLD) is None:
                blocked.set()

        thread = threading.Thread(target=child)
        thread.start()
        thread.join(20)

    assert blocked.is_set()


def test_re_entering_through_try_acquire_succeeds_immediately(locks, request):
    k = key(request)

    with locks.acquire(k, HOLD):
        reentered = locks.try_acquire(k, NOW, HOLD)
        assert reentered is not None
        reentered.release()


def test_releasing_twice_is_harmless(locks, registry, request):
    before = pool_in_use(registry)

    lease = locks.acquire(key(request), HOLD)
    lease.release()
    lease.release()

    assert pool_in_use(registry) == before


# -- the one case that is not shared ------------------------------------------------------------


def test_what_a_sub_second_wait_actually_does_on_each_server(locks, dialect, request):
    """Java has a case here for MySQL and MariaDB and none for Postgres, because `GET_LOCK`
    takes its timeout as a `DOUBLE` and the two servers disagree about the fraction.

    Measured, not assumed - holding the lock and timing a second session against the containers
    this suite runs:

        timeout   MySQL 8.4.11   MariaDB 11.4
        0.25s     0.00s          0.26s
        0.5s      0.00s          0.51s
        1.5s      2.01s          1.51s

    MySQL truncates a sub-second timeout to zero, so a 500ms wait is a poll. MariaDB honours it.
    Postgres waits with its own `lock_timeout`, in whatever resolution it was given.
    """
    if dialect not in ("mysql", "mariadb"):
        pytest.skip("Postgres waits with its own timeout, in any resolution")

    k = key(request)
    with locks.acquire(k, HOLD):
        started = time.monotonic()

        def waiter():
            assert locks.try_acquire(k, timedelta(milliseconds=500), HOLD) is None, (
                "the lock is held, so the wait must end empty either way"
            )

        thread = threading.Thread(target=waiter)
        thread.start()
        thread.join(30)
        waited = time.monotonic() - started

    if dialect == "mariadb":
        assert waited >= 0.4, f"MariaDB honours the fraction; waited {waited:.2f}s"
    else:
        assert waited < 0.4, (
            f"MySQL truncates a sub-second timeout to zero, so this returns at once; "
            f"waited {waited:.2f}s"
        )
