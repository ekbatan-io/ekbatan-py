"""PARITY-PROBE F3: the lock while migrating, two migrators at once, a lock held by someone else.
Twin of ParityFLockProbeTest.java."""

import threading
import time

from common import SQL, cfg, connect, fresh, history, msg, probe, query, tables

from ekbatan.migrations import YoyoMigrator

HOLD_SECONDS = 70


def sample_locks(dialect: str, db: str) -> list[str]:
    if dialect == "pg":
        return query(
            dialect,
            "postgres",
            "SELECT locktype || '|' || classid || '|' || objid || '|' || objsubid || '|' || mode "
            "|| '|' || granted FROM pg_locks WHERE locktype = 'advisory' AND database = "
            f"(SELECT oid FROM pg_database WHERE datname = '{db}')",
        )
    return query(
        dialect,
        "",
        "SELECT CONCAT(OBJECT_NAME, '|', LOCK_TYPE, '|', LOCK_STATUS) FROM "
        "performance_schema.metadata_locks WHERE OBJECT_TYPE = 'USER LEVEL LOCK'",
    )


def concurrent(dialect: str) -> str | None:
    db = "f3_lock"
    k = f"{dialect}.concurrent"
    fresh(dialect, db)
    outcomes: list[str] = []
    start = threading.Event()

    def one() -> None:
        start.wait()
        try:
            r = YoyoMigrator.migrate_data_source(cfg(dialect, db), str(SQL / f"lock_{dialect}"))
            outcomes.append(f"OK applied={r.migrations_executed}")
        except Exception as e:
            outcomes.append("FAIL " + msg(e))

    threads = [threading.Thread(target=one) for _ in range(2)]
    for t in threads:
        t.start()
    t0 = time.monotonic()
    start.set()
    seen: set[str] = set()
    for _ in range(12):
        time.sleep(0.5)
        seen.update(sample_locks(dialect, db))
    for t in threads:
        t.join()
    secs = round(time.monotonic() - t0)
    probe(k + ".outcomes", sorted(outcomes))
    probe(k + ".tables", tables(dialect, db))
    probe(k + ".history", history(dialect, db))
    probe(k + ".locks_seen", sorted(seen))
    probe(k + ".locks_left_after", sample_locks(dialect, db))
    return sorted(seen)[0] if seen else None


def held(dialect: str, lock: str | None) -> None:
    db = "f3_lock"
    k = f"{dialect}.held"
    fresh(dialect, db)
    if lock is None:
        probe(k + ".outcome", "no lock was seen to hold")
        return
    ready = threading.Event()

    def hold() -> None:
        c = connect(dialect, db)
        try:
            cur = c.cursor()
            if dialect == "pg":
                p = lock.split("|")
                key = (int(p[1]) << 32) | (int(p[2]) & 0xFFFFFFFF)
                if key >= 1 << 63:
                    key -= 1 << 64
                cur.execute(f"SELECT pg_advisory_lock({key})")
            else:
                cur.execute(f"SELECT GET_LOCK('{lock.split('|')[0]}', 0)")
            cur.fetchall()
            ready.set()
            time.sleep(HOLD_SECONDS)
        except Exception as e:
            probe(k + ".holder_error", msg(e))
            ready.set()
        finally:
            c.close()

    holder = threading.Thread(target=hold)
    holder.start()
    ready.wait()
    t0 = time.monotonic()
    try:
        r = YoyoMigrator.migrate_data_source(cfg(dialect, db), str(SQL / "quick"))
        probe(k + ".outcome", f"OK applied={r.migrations_executed}")
    except Exception as e:
        probe(k + ".outcome", "FAIL " + msg(e))
    secs = int(time.monotonic() - t0)
    probe(k + ".seconds_rounded_to_10", round(secs / 10.0) * 10)
    holder.join()
    probe(k + ".history", history(dialect, db))


for dialect in ("pg", "mysql"):
    held(dialect, concurrent(dialect))
