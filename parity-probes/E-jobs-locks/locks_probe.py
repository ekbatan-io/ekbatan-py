"""Parity scan area E: keyed locks, Python side.

Mirror of ParityELocksProbeTest.java. Runs against containers started by hand on fixed ports:
PostgreSQL 56432, MySQL 56306, MariaDB 56307, Redis 56379 (user test, root for admin queries).
Prints `PROBE <key>: <value>` lines with the same keys as the Java probe.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import timedelta

import redis
from sqlalchemy import create_engine, text

from ekbatan.errors import LockAcquisitionError
from ekbatan.locks import (
    MariaDBKeyedLockProvider,
    MySQLKeyedLockProvider,
    PostgresKeyedLockProvider,
)
from ekbatan.locks.redis import RedisKeyedLockProvider

PW = os.environ.get("PARITY_PW", "test")
FIVE_MIN = timedelta(minutes=5)
URLS = {
    "pg": "postgresql+psycopg://test:{pw}@localhost:56432/testdb",
    "mysql": "mysql+pymysql://test:{pw}@localhost:56306/testdb",
    "mariadb": "mysql+pymysql://test:{pw}@localhost:56307/testdb",
}
ADMIN = {
    "pg": "postgresql+psycopg://test:{pw}@localhost:56432/testdb",
    "mysql": "mysql+pymysql://root:{pw}@localhost:56306/testdb",
    "mariadb": "mysql+pymysql://root:{pw}@localhost:56307/testdb",
}
CLIENT = redis.Redis(host="localhost", port=56379)


def p(k: str, v: object) -> None:
    if isinstance(v, bool):
        v = str(v).lower()
    if isinstance(v, list):
        v = "[" + ", ".join(v) + "]"
    print(f"PROBE {k}: {v}", flush=True)


def bucket(ms: float) -> int:
    return int(round(ms / 250.0)) * 250


def uk(b: str) -> str:
    return f"parity:{b}:{uuid.uuid4()}"


def engine(b: str, size: int):
    return create_engine(URLS[b].format(pw=PW), pool_size=size, max_overflow=0)


def provider(b: str, size: int):
    if b == "pg":
        return PostgresKeyedLockProvider(engine(b, size))
    if b == "mysql":
        return MySQLKeyedLockProvider(engine(b, size))
    if b == "mariadb":
        return MariaDBKeyedLockProvider(engine(b, size))
    return RedisKeyedLockProvider(CLIENT)


_admin_engines: dict[str, object] = {}


def admin(b: str):
    if b not in _admin_engines:
        _admin_engines[b] = create_engine(ADMIN[b].format(pw=PW), poolclass=None)
    return _admin_engines[b].connect()


def held_names(b: str) -> list[str]:
    if b == "redis":
        out = []
        for key in CLIENT.keys("ekbatan-lock:*"):
            t = CLIENT.type(key).decode()
            out.append(f"{key.decode()}|{t}")
        return sorted(out)
    sql = {
        "pg": "SELECT ((classid::bigint << 32) | objid::bigint)::text FROM pg_locks "
        "WHERE locktype = 'advisory' AND granted ORDER BY 1",
        "mysql": "SELECT OBJECT_NAME FROM performance_schema.metadata_locks "
        "WHERE OBJECT_TYPE = 'USER LEVEL LOCK' ORDER BY 1",
        "mariadb": "SELECT TABLE_SCHEMA FROM information_schema.METADATA_LOCK_INFO "
        "WHERE LOCK_TYPE = 'User lock' ORDER BY 1",
    }[b]
    with admin(b) as c:
        return [r[0] for r in c.execute(text(sql))]


def kill_holders(b: str) -> None:
    with admin(b) as c:
        if b == "pg":
            c.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype = 'advisory' "
                    "AND pid <> pg_backend_pid()"
                )
            )
            c.commit()
            return
        for name in held_names(b):
            cid = c.execute(text("SELECT IS_USED_LOCK(:n)"), {"n": name}).scalar()
            c.execute(text(f"KILL {int(cid)}"))


def in_thread(fn):
    box: dict[str, object] = {}

    def run():
        try:
            box["v"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=run)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]  # type: ignore[misc]
    return box.get("v")


def other_can_acquire(l, key: str) -> bool:
    def go():
        lease = l.try_acquire(key, timedelta(0), FIVE_MIN)
        if lease is not None:
            lease.release()
            return True
        return False

    try:
        return bool(in_thread(go))
    except Exception:
        return False


def other_try_timed(l, key: str, wait: timedelta) -> tuple[bool, float]:
    def go():
        t0 = time.monotonic()
        lease = l.try_acquire(key, wait, FIVE_MIN)
        ms = (time.monotonic() - t0) * 1000
        if lease is not None:
            lease.release()
            return True, ms
        return False, ms

    try:
        return in_thread(go)  # type: ignore[return-value]
    except Exception:
        return False, -1


def refused(fn) -> str:
    try:
        fn()
        return "accepted"
    except Exception:
        return "refused"


def type_of(fn) -> str:
    try:
        fn()
        return "none"
    except Exception as e:
        return type(e).__name__


def run_backend(b: str) -> None:
    l = provider(b, 16)

    # 1. lock key -> backend name/number
    with l.acquire("wallet:abc", timedelta(seconds=30)):
        p(f"{b}.name.wallet_abc", held_names(b))
        if b == "redis":
            p(f"{b}.ttl_s.wallet_abc", round(CLIENT.pttl("ekbatan-lock:wallet:abc") / 1000.0))
    with l.acquire("café:1", timedelta(seconds=30)):
        p(f"{b}.name.unicode_cafe", held_names(b))
    p(f"{b}.name.after_release", held_names(b))

    # 2. re-entry and inner close three times
    k = uk(b)
    outer = l.acquire(k, FIVE_MIN)
    t0 = time.monotonic()
    inner = l.acquire(k, FIVE_MIN)
    p(f"{b}.reenter.fast", (time.monotonic() - t0) * 1000 < 50)
    p(f"{b}.reenter.other_thread_can_acquire", other_can_acquire(l, k))
    inner.release()
    inner.release()
    inner.release()
    p(f"{b}.inner_close_x3.outer_held", outer.held)
    p(f"{b}.inner_close_x3.inner_held", inner.held)
    p(f"{b}.inner_close_x3.other_can_acquire", other_can_acquire(l, k))
    outer.release()
    p(f"{b}.inner_close_x3.after_outer_close.other_can_acquire", other_can_acquire(l, k))

    # 3. inner lease reports after its own close
    k = uk(b)
    outer = l.acquire(k, FIVE_MIN)
    inner = l.acquire(k, FIVE_MIN)
    inner.release()
    p(f"{b}.inner_close_once.inner_held", inner.held)
    p(f"{b}.inner_close_once.outer_held", outer.held)
    p(f"{b}.inner_close_once.other_can_acquire", other_can_acquire(l, k))
    outer.release()

    # 4. outer closed first
    k = uk(b)
    outer = l.acquire(k, FIVE_MIN)
    inner = l.acquire(k, FIVE_MIN)
    outer.release()
    p(f"{b}.outer_first.outer_held", outer.held)
    p(f"{b}.outer_first.inner_held", inner.held)
    p(f"{b}.outer_first.other_can_acquire", other_can_acquire(l, k))
    inner.release()
    p(f"{b}.outer_first.after_inner_close.other_can_acquire", other_can_acquire(l, k))

    # 5. try_acquire re-entry with zero wait
    k = uk(b)
    outer = l.acquire(k, FIVE_MIN)
    re = l.try_acquire(k, timedelta(0), FIVE_MIN)
    p(f"{b}.try_reenter_zero_wait.present", re is not None)
    if re is not None:
        re.release()
    outer.release()

    # 6. close twice
    k = uk(b)
    lease = l.acquire(k, FIVE_MIN)

    def twice():
        lease.release()
        lease.release()

    p(f"{b}.close_twice", "no-error" if refused(twice) == "accepted" else "error")
    p(f"{b}.close_twice.other_can_acquire", other_can_acquire(l, k))

    # 7. try_acquire timing against a held key
    k = uk(b)
    lease = l.acquire(k, FIVE_MIN)
    for w in (0, 500, 1000, 1500):
        got, ms = other_try_timed(l, k, timedelta(milliseconds=w))
        p(f"{b}.try_held.wait_{w}ms.got", got)
        p(f"{b}.try_held.wait_{w}ms.elapsed_bucket_ms", bucket(ms))
    lease.release()

    # 8. try_acquire succeeds when the holder releases in time
    k = uk(b)
    lease = l.acquire(k, FIVE_MIN)
    result: dict[str, object] = {}
    t = threading.Thread(
        target=lambda: result.update(r=other_try_timed(l, k, timedelta(seconds=5)))
    )
    t.start()
    time.sleep(0.3)
    lease.release()
    t.join()
    got, ms = result["r"]  # type: ignore[misc]
    p(f"{b}.try_released_in_time.got", got)
    p(f"{b}.try_released_in_time.under_1500ms", ms < 1500)

    # 9. blocking acquire waits, then gets it
    k = uk(b)
    lease = l.acquire(k, FIVE_MIN)
    flag = threading.Event()

    def blocker():
        with l.acquire(k, FIVE_MIN):
            flag.set()

    t = threading.Thread(target=blocker)
    t.start()
    time.sleep(0.5)
    p(f"{b}.blocking_acquire.got_while_held", flag.is_set())
    lease.release()
    t.join(3)
    p(f"{b}.blocking_acquire.got_after_release", flag.is_set())

    # 10. max hold expiry
    k = uk(b)
    lease = l.acquire(k, timedelta(milliseconds=500))
    time.sleep(1.5)
    p(f"{b}.expiry.held", lease.held)
    p(f"{b}.expiry.other_can_acquire", other_can_acquire(l, k))
    p(f"{b}.expiry.close_after", "no-error" if refused(lease.release) == "accepted" else "error")

    # 11. re-entry after expiry takes a fresh lock
    k = uk(b)
    lease = l.acquire(k, timedelta(milliseconds=300))
    time.sleep(0.9)
    again = l.acquire(k, FIVE_MIN)
    p(f"{b}.reenter_after_expiry.held", again.held)
    p(f"{b}.reenter_after_expiry.other_can_acquire", other_can_acquire(l, k))
    again.release()
    lease.release()

    # 12. inner max hold ignored
    k = uk(b)
    outer = l.acquire(k, FIVE_MIN)
    inner = l.acquire(k, timedelta(milliseconds=300))
    time.sleep(0.9)
    p(f"{b}.inner_max_hold_ignored.outer_held", outer.held)
    p(f"{b}.inner_max_hold_ignored.inner_held", inner.held)
    p(f"{b}.inner_max_hold_ignored.other_can_acquire", other_can_acquire(l, k))
    if b == "redis":
        p(f"{b}.inner_max_hold_ignored.ttl_over_60s", CLIENT.pttl("ekbatan-lock:" + k) > 60_000)
    inner.release()
    outer.release()

    # 13. outer max hold governs the inner lease
    k = uk(b)
    outer = l.acquire(k, timedelta(milliseconds=500))
    inner = l.acquire(k, FIVE_MIN)
    time.sleep(1.3)
    p(f"{b}.outer_max_hold_governs.inner_held", inner.held)
    p(f"{b}.outer_max_hold_governs.other_can_acquire", other_can_acquire(l, k))
    inner.release()
    outer.release()

    # 14. a lease closed from another thread
    k = uk(b)
    lease = in_thread(lambda: l.acquire(k, FIVE_MIN))
    r = refused(lease.release)
    p(f"{b}.close_from_other_thread", "no-error" if r == "accepted" else "error")
    p(f"{b}.close_from_other_thread.held", lease.held)
    p(f"{b}.close_from_other_thread.other_can_acquire", other_can_acquire(l, k))

    # 15. provider close with a lease open
    k = uk(b)
    p2 = provider(b, 4)
    lease = p2.acquire(k, FIVE_MIN)
    p2.close()
    p(f"{b}.provider_close.lease_held", lease.held)
    p(f"{b}.provider_close.other_can_acquire", other_can_acquire(l, k))
    lease.release()

    # 16. argument checks
    p(f"{b}.validate.blank_key", refused(lambda: l.acquire("  ", FIVE_MIN)))
    p(f"{b}.validate.empty_key", refused(lambda: l.acquire("", FIVE_MIN)))
    p(f"{b}.validate.zero_max_hold", refused(lambda: l.acquire("k", timedelta(0))))
    p(f"{b}.validate.negative_max_hold", refused(lambda: l.acquire("k", timedelta(milliseconds=-1))))
    p(
        f"{b}.validate.negative_max_wait",
        refused(lambda: l.try_acquire("k", timedelta(milliseconds=-1), FIVE_MIN)),
    )
    p(
        f"{b}.validate.try_zero_max_hold",
        refused(lambda: l.try_acquire("k", timedelta(0), timedelta(0))),
    )
    p(f"{b}.validate.type_blank_key", type_of(lambda: l.acquire("  ", FIVE_MIN)))

    # 17. lock held across a connection drop (SQL) / key deleted under the holder (Redis)
    if b != "redis":
        k = uk(b)
        lease = l.acquire(k, FIVE_MIN)
        kill_holders(b)
        time.sleep(0.5)
        p(f"{b}.conn_drop.other_can_acquire", other_can_acquire(l, k))
        p(f"{b}.conn_drop.lease_held", lease.held)
        p(f"{b}.conn_drop.close", "no-error" if refused(lease.release) == "accepted" else "error")
        k2 = uk(b)
        try:
            after = l.try_acquire(k2, timedelta(0), FIVE_MIN)
        except Exception:
            after = None
        p(f"{b}.conn_drop.provider_still_works", after is not None)
        if after is not None:
            after.release()
    else:
        k = uk(b)
        lease = l.acquire(k, FIVE_MIN)
        CLIENT.delete("ekbatan-lock:" + k)
        other = in_thread(lambda: l.try_acquire(k, timedelta(0), FIVE_MIN))
        p(f"{b}.conn_drop.other_can_acquire", other is not None)
        p(f"{b}.conn_drop.lease_held", lease.held)
        p(f"{b}.conn_drop.close", "no-error" if refused(lease.release) == "accepted" else "error")
        p(
            f"{b}.conn_drop.new_owner_key_survives_old_close",
            CLIENT.exists("ekbatan-lock:" + k) == 1,
        )
        if other is not None:
            in_thread(other.release)

    # 18. interrupting a waiting acquire: Python has no thread interrupt and no cancel API.
    p(f"{b}.interrupt_waiting_acquire", "no-api")

    print(f"PROBE-DONE {b}", flush=True)


def pg_only() -> None:
    holder_locks = provider("pg", 4)
    pool1 = engine("pg", 1)
    p1 = PostgresKeyedLockProvider(pool1)
    k = uk("pg")
    lease = holder_locks.acquire(k, FIVE_MIN)
    res = in_thread(lambda: p1.try_acquire(k, timedelta(milliseconds=200), FIVE_MIN))
    p("pg.lock_timeout_after_timed_out_try.got", res is not None)
    with pool1.connect() as c:
        p("pg.lock_timeout_after_timed_out_try.next_borrower_sees", c.exec_driver_sql("SHOW lock_timeout").scalar())
    lease.release()
    ok = p1.try_acquire(k, timedelta(milliseconds=200), FIVE_MIN)
    p("pg.lock_timeout_after_acquired_try.got", ok is not None)
    if ok is not None:
        ok.release()
    with pool1.connect() as c:
        p("pg.lock_timeout_after_acquired_try.next_borrower_sees", c.exec_driver_sql("SHOW lock_timeout").scalar())

    dead = PostgresKeyedLockProvider(
        create_engine("postgresql+psycopg://test:x@localhost:1/none", pool_size=1)
    )
    try:
        dead.try_acquire("k", timedelta(0), FIVE_MIN)
        p("pg.backend_down.raises", "nothing")
    except Exception as e:
        p("pg.backend_down.is_lock_acquisition_error", isinstance(e, LockAcquisitionError))
        p("pg.backend_down.type", type(e).__name__)
    print("PROBE-DONE pgOnly", flush=True)


def asyncio_probe() -> None:
    """Python only (EKB-0250): a lock taken on an event loop's thread is refused."""
    import asyncio

    l = provider("pg", 2)

    async def main():
        try:
            l.acquire(uk("pg"), FIVE_MIN).release()
            return "accepted"
        except Exception as e:
            return type(e).__name__

    p("pg.on_asyncio_loop", asyncio.run(main()))


if __name__ == "__main__":
    CLIENT.delete(*CLIENT.keys("ekbatan-lock:*") or ["none"])
    for b in ("pg", "mysql", "mariadb", "redis"):
        run_backend(b)
    pg_only()
    asyncio_probe()
