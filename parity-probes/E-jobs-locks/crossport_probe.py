"""Parity scan area E: Java and Python contending on the same containers.

Run alongside ParityECrossPortTest.java, coordinating through marker files:

    python crossport_probe.py tries   # with the Java test `javaHolds`
    python crossport_probe.py holds   # with the Java test `javaTries`
    python crossport_probe.py jobs    # with the Java test `sharedJobTable`
"""

from __future__ import annotations

import os
import sys
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent))
from locks_probe import p, provider  # noqa: E402

DIR = Path(
    os.environ.get(
        "PARITY_DIR",
        "/private/tmp/claude-501/-Users-mike-workspace-ekbatan/73e3c410-5a5d-4b46-86a8-40a1e2b99616/scratchpad/xport",
    )
)
KEYS = ["xport:wallet:abc", "xport:café"]
WAKE = "xport:wake"
FIVE_MIN = timedelta(minutes=5)
BACKENDS = ("pg", "mysql", "mariadb", "redis")


def mark(name: str) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / name).write_text("x")


def wait_for(name: str) -> None:
    deadline = time.time() + 300
    while not (DIR / name).exists():
        if time.time() > deadline:
            raise SystemExit(f"timed out waiting for {name}")
        time.sleep(0.05)


def tries() -> None:
    ps = {b: provider(b, 4) for b in BACKENDS}
    wait_for("java-holding")
    for b, l in ps.items():
        for k in KEYS:
            tag = f"xport.java_holds.{b}.{'wallet' if 'wallet' in k else 'unicode'}"
            try:
                got = l.try_acquire(k, timedelta(0), FIVE_MIN)
                p(f"{tag}.python_try_zero_got", got is not None)
                if got is not None:
                    got.release()
            except Exception as e:
                p(f"{tag}.python_try_zero_got", f"raised {type(e).__name__}")
    mark("python-tried")
    for b, l in ps.items():
        tag = f"xport.java_holds.{b}.wallet"
        try:
            got = l.try_acquire(KEYS[0], timedelta(seconds=15), FIVE_MIN)
            p(f"{tag}.python_gets_it_after_java_releases", got is not None)
            if got is not None:
                got.release()
        except Exception as e:
            p(f"{tag}.python_gets_it_after_java_releases", f"raised {type(e).__name__}")
    # Redis only: Python is already waiting when Java releases, with a 5-minute TTL on the key,
    # so only Redisson's unlock message can let it in before the 15 s wait ends.
    wait_for("java-holding-wake")
    mark("python-waiting-wake")
    t0 = time.monotonic()
    got = ps["redis"].try_acquire(WAKE, timedelta(seconds=15), FIVE_MIN)
    p("xport.java_holds.redis.wake.python_got", got is not None)
    p("xport.java_holds.redis.wake.python_woken_under_3s", time.monotonic() - t0 < 3)
    if got is not None:
        got.release()


def holds() -> None:
    ps = {b: provider(b, 4) for b in BACKENDS}
    leases = [l.acquire(k, FIVE_MIN) for l in ps.values() for k in KEYS]
    mark("python-holding")
    wait_for("java-tried")
    time.sleep(1)
    for lease in leases:
        lease.release()
    p("xport.python_holds.released", True)
    # Redis only: Java waits on this key, and only our unlock message can wake it in time.
    wake = ps["redis"].acquire(WAKE, FIVE_MIN)
    mark("python-holding-wake")
    wait_for("java-waiting-wake")
    time.sleep(1)
    wake.release()


def jobs() -> None:
    from db_scheduler.task.schedule import FixedDelay

    from ekbatan.distributedjobs import DistributedJob, job_registry
    from jobs_probe import DDL, ENGINE

    with ENGINE.begin() as c:
        c.execute(text("drop table if exists scheduled_tasks"))
        c.execute(text(DDL))
        c.execute(text("drop table if exists job_runs"))
        c.execute(
            text(
                "create table job_runs (id bigserial primary key, port text not null,"
                " started_at timestamptz not null, ended_at timestamptz)"
            )
        )

    class Shared(DistributedJob):
        def name(self) -> str:
            return "shared-job"

        def schedule(self):
            return FixedDelay.of(timedelta(milliseconds=300))

        def execute(self, ctx) -> None:
            with ENGINE.begin() as c:
                c.execute(text("insert into job_runs (port, started_at) values ('python', clock_timestamp())"))
            time.sleep(0.2)
            with ENGINE.begin() as c:
                c.execute(
                    text(
                        "update job_runs set ended_at = clock_timestamp() where id = "
                        "(select max(id) from job_runs where port = 'python')"
                    )
                )

    reg = (
        job_registry()
        .engine(ENGINE)
        .stop_on_exit(False)
        .poll_interval(timedelta(milliseconds=100))
        .with_job(Shared())
        .build()
    )
    reg.start()
    mark("python-jobs-started")
    wait_for("java-jobs-started")
    time.sleep(10)
    reg.stop()
    wait_for("java-jobs-stopped")
    with ENGINE.connect() as c:
        counts = dict(c.execute(text("select port, count(*) from job_runs group by port")).all())
        overlaps = c.execute(
            text(
                "select count(*) from job_runs a join job_runs b on a.id < b.id"
                " and a.started_at < b.ended_at and b.started_at < a.ended_at"
            )
        ).scalar()
        rows = c.execute(text("select count(*) from scheduled_tasks where task_name = 'shared-job'")).scalar()
    p("xport.shared_job.java_ran", counts.get("java", 0) > 0)
    p("xport.shared_job.python_ran", counts.get("python", 0) > 0)
    p("xport.shared_job.overlapping_runs", overlaps)
    p("xport.shared_job.rows", rows)
    print(f"DETAIL counts={counts}", flush=True)


if __name__ == "__main__":
    {"tries": tries, "holds": holds, "jobs": jobs}[sys.argv[1]]()
