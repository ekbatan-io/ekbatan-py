"""Parity scan area E: distributed jobs, Python side.

Mirror of ParityEJobsProbeTest.java, against PostgreSQL on localhost:56432 (container started by
hand). The scheduled_tasks DDL is Java's verbatim. Prints `PROBE <key>: <value>` lines.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import timedelta

from db_scheduler.task.schedule import DisabledSchedule, FixedDelay
from sqlalchemy import create_engine, text

from ekbatan import DataSource
from ekbatan.config import DataSourceConfig
from ekbatan.distributedjobs import DistributedJob, JobsConfig, job_registry

PW = os.environ.get("PARITY_PW", "test")
ENGINE = create_engine(f"postgresql+psycopg://test:{PW}@localhost:56432/testdb", pool_size=10)
SOURCE = DataSource.from_config(
    DataSourceConfig(url="postgresql+psycopg://localhost:56432/testdb", username="test", password=PW, pool_size=10)
)
DDL = (
    "create table scheduled_tasks (task_name text not null, task_instance text not null,"
    " task_data bytea, execution_time timestamp with time zone not null, picked BOOLEAN not null,"
    " picked_by text, last_success timestamp with time zone, last_failure timestamp with time zone,"
    " consecutive_failures INT, last_heartbeat timestamp with time zone, version BIGINT not null,"
    " priority SMALLINT, PRIMARY KEY (task_name, task_instance))"
)


def p(k: str, v: object) -> None:
    if isinstance(v, bool):
        v = str(v).lower()
    print(f"PROBE {k}: {v}", flush=True)


def bucket(ms: float, size: int) -> int:
    return int(round(ms / size)) * size


def refused(fn) -> str:
    try:
        fn()
        return "accepted"
    except Exception:
        return "refused"


def sql(s: str) -> None:
    with ENGINE.begin() as c:
        c.execute(text(s))


def _fmt(v: object) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "t" if v else "f"
    return str(v)


def query1(s: str) -> str:
    with ENGINE.connect() as c:
        row = c.execute(text(s)).first()
    if row is None:
        return "no-row"
    return "|".join(_fmt(v) for v in row)


class Job(DistributedJob):
    def __init__(self, name, schedule, body) -> None:
        self._name, self._schedule, self._body = name, schedule, body

    def name(self) -> str:
        return self._name

    def schedule(self):
        return self._schedule

    def execute(self, ctx) -> None:
        self._body(ctx)


def ms(n: int) -> timedelta:
    return timedelta(milliseconds=n)


def base():
    return job_registry().data_source(SOURCE).stop_on_exit(False).poll_interval(ms(100))


def main() -> None:
    sql("drop table if exists scheduled_tasks")
    sql("drop table if exists scheduled_tasks_hidden")
    sql(DDL)

    noop = Job("v", FixedDelay.of(ms(200)), lambda ctx: None)

    # J1 validation
    p("validate.no_jobs", refused(lambda: job_registry().data_source(SOURCE).build()))
    p("validate.no_connection", refused(lambda: job_registry().with_job(noop).build()))
    p("validate.null_job", refused(lambda: job_registry().with_job(None)))
    p("validate.null_in_jobs", refused(lambda: job_registry().with_jobs([noop, None])))
    p("validate.blank_name", refused(lambda: base().with_job(Job(" ", FixedDelay.of(ms(200)), lambda c: None)).build()))
    p(
        "validate.duplicate_names",
        refused(
            lambda: base()
            .with_job(Job("d", FixedDelay.of(ms(200)), lambda c: None))
            .with_job(Job("d", FixedDelay.of(ms(300)), lambda c: None))
            .build()
        ),
    )
    p("validate.null_schedule", refused(lambda: base().with_job(Job("n", None, lambda c: None)).build()))
    p("validate.zero_poll", refused(lambda: base().with_job(noop).poll_interval(timedelta(0)).build()))
    p("validate.negative_poll", refused(lambda: base().with_job(noop).poll_interval(ms(-1)).build()))
    p("validate.zero_heartbeat", refused(lambda: base().with_job(noop).heartbeat_interval(timedelta(0)).build()))
    p("validate.negative_shutdown_wait", refused(lambda: base().with_job(noop).shutdown_max_wait(ms(-1)).build()))
    p("validate.zero_shutdown_wait", refused(lambda: base().with_job(noop).shutdown_max_wait(timedelta(0)).build()))
    p("config.zero_polling", refused(lambda: JobsConfig.builder().polling_interval(timedelta(0)).build()))
    p("config.zero_heartbeat", refused(lambda: JobsConfig.builder().heartbeat_interval(timedelta(0)).build()))
    p("config.negative_shutdown", refused(lambda: JobsConfig.builder().shutdown_max_wait(timedelta(seconds=-1)).build()))
    p("config.zero_shutdown", refused(lambda: JobsConfig.builder().shutdown_max_wait(timedelta(0)).build()))
    p("config.one_microsecond_polling", refused(lambda: JobsConfig.builder().polling_interval(timedelta(microseconds=1)).build()))
    d = JobsConfig.defaults()
    p("config.defaults_all_unset", d.polling_interval is None and d.heartbeat_interval is None and d.shutdown_max_wait is None)

    # J2
    runs = [0]

    def count(ctx):
        runs[0] += 1

    reg = base().with_job(Job("j2-fixed", FixedDelay.of(timedelta(hours=1)), count)).build()
    reg.start()
    time.sleep(1.5)
    p("j2.ran_at_start", runs[0])
    p(
        "j2.row",
        query1(
            "select task_name, task_instance, task_data is null, picked, picked_by is null,"
            " consecutive_failures, last_success is not null, last_failure is null, version, priority"
            " from scheduled_tasks where task_name = 'j2-fixed'"
        ),
    )
    p(
        "j2.next_run_in_minutes",
        query1(
            "select round(extract(epoch from execution_time - now()) / 60)"
            " from scheduled_tasks where task_name = 'j2-fixed'"
        ),
    )
    reg.stop()
    reg2 = base().with_job(Job("j2-fixed", FixedDelay.of(timedelta(hours=1)), count)).build()
    reg2.start()
    time.sleep(0.5)
    reg2.stop()
    p("j2.restart.rows", query1("select count(*) from scheduled_tasks where task_name = 'j2-fixed'"))
    p("j2.restart.ran_again", runs[0] > 1)

    # J3
    reg = base().with_job(Job("j3-disabled", DisabledSchedule(), lambda c: None)).build()
    p("j3.start", refused(reg.start))
    p("j3.rows", query1("select count(*) from scheduled_tasks where task_name = 'j3-disabled'"))
    reg.stop()

    # J4
    runs4 = [0]

    def c4(ctx):
        runs4[0] += 1

    reg = base().with_job(Job("j4-repeat", FixedDelay.of(ms(200)), c4)).build()
    reg.start()
    time.sleep(2)
    reg.stop()
    p("j4.runs_at_least_4", runs4[0] >= 4)

    # J5
    runs5 = [0]

    def boom(ctx):
        runs5[0] += 1
        raise RuntimeError("boom")

    reg = base().with_job(Job("j5-throws", FixedDelay.of(ms(200)), boom)).build()
    reg.start()
    time.sleep(1.5)
    reg.stop()
    p("j5.runs_at_least_2", runs5[0] >= 2)
    p(
        "j5.row",
        query1(
            "select consecutive_failures >= 2, last_failure is not null, last_success is null,"
            " picked from scheduled_tasks where task_name = 'j5-throws'"
        ),
    )

    # J6
    reg = base().with_job(Job("j6-missing", FixedDelay.of(ms(200)), lambda c: None)).build()
    sql("alter table scheduled_tasks rename to scheduled_tasks_hidden")
    try:
        reg.start()
        p("j6.start", "accepted")
    except Exception as e:
        p("j6.start", "refused")
        p("j6.type", type(e).__name__)
        p("j6.message_names_job", "j6-missing" in str(e))
    finally:
        sql("alter table scheduled_tasks_hidden rename to scheduled_tasks")
        reg.stop()

    # J7
    sql(
        "insert into scheduled_tasks (task_name, task_instance, execution_time, picked, version)"
        " values ('ghost', 'recurring', now() - interval '1 minute', false, 1)"
    )
    reg = base().with_job(Job("j7-real", FixedDelay.of(ms(200)), lambda c: None)).build()
    p("j7.start", refused(reg.start))
    time.sleep(1.5)
    reg.stop()
    p("j7.ghost_row", query1("select picked, consecutive_failures, version from scheduled_tasks where task_name = 'ghost'"))

    # J8a
    started = threading.Event()
    done = threading.Event()

    def graceful(ctx):
        started.set()
        time.sleep(1.5)
        done.set()

    reg = base().shutdown_max_wait(timedelta(seconds=5)).with_job(Job("j8a-graceful", FixedDelay.of(timedelta(hours=1)), graceful)).build()
    reg.start()
    started.wait()
    t0 = time.monotonic()
    reg.stop()
    p("j8a.stop_elapsed_s", bucket((time.monotonic() - t0) * 1000, 500) / 1000.0)
    p("j8a.job_done_when_stop_returned", done.is_set())
    p("j8a.row", query1("select picked, last_success is not null from scheduled_tasks where task_name = 'j8a-graceful'"))

    # J8b
    started = threading.Event()
    done = threading.Event()

    def slow(ctx):
        started.set()
        time.sleep(4)  # nothing in Python can interrupt this
        done.set()

    reg = base().shutdown_max_wait(timedelta(seconds=1)).with_job(Job("j8b-slow", FixedDelay.of(timedelta(hours=1)), slow)).build()
    reg.start()
    started.wait()
    t0 = time.monotonic()
    reg.stop()
    p("j8b.stop_elapsed_s", bucket((time.monotonic() - t0) * 1000, 500) / 1000.0)
    p("j8b.job_interrupted", False)
    p("j8b.job_done_when_stop_returned", done.is_set())
    time.sleep(4.5)
    p("j8b.job_done_later", done.is_set())
    p("j8b.row_after", query1("select picked, last_success is not null from scheduled_tasks where task_name = 'j8b-slow'"))

    # J9
    lock = threading.Lock()
    state = {"running": 0, "max": 0}

    def par(ctx):
        with lock:
            state["running"] += 1
            state["max"] = max(state["max"], state["running"])
        time.sleep(1.5)
        with lock:
            state["running"] -= 1

    reg = base().with_jobs([Job(f"j9-par-{i}", FixedDelay.of(timedelta(hours=1)), par) for i in range(3)]).build()
    reg.start()
    time.sleep(2.5)
    reg.stop()
    p("j9.max_concurrent_of_3", state["max"])

    # J10
    started = threading.Event()

    def hb(ctx):
        started.set()
        time.sleep(3.5)

    reg = base().heartbeat_interval(timedelta(seconds=1)).with_job(Job("j10-hb", FixedDelay.of(timedelta(hours=1)), hb)).build()
    reg.start()
    started.wait()
    time.sleep(0.3)
    h1 = query1("select last_heartbeat from scheduled_tasks where task_name = 'j10-hb'")
    time.sleep(2.2)
    h2 = query1("select last_heartbeat from scheduled_tasks where task_name = 'j10-hb'")
    p("j10.picked_row_has_heartbeat", h1 != "null")
    p("j10.heartbeat_moved_with_1s_interval", h1 != h2)
    reg.stop()

    # J11 defaults
    starts: list[float] = []
    t0 = time.monotonic()

    def d11(ctx):
        starts.append((time.monotonic() - t0) * 1000)
        if len(starts) == 1:
            time.sleep(12.5)

    reg = job_registry().data_source(SOURCE).stop_on_exit(False).with_job(Job("j11-defaults", FixedDelay.of(timedelta(seconds=1)), d11)).build()
    reg.start()
    time.sleep(1.5)
    h1 = query1("select last_heartbeat from scheduled_tasks where task_name = 'j11-defaults'")
    time.sleep(6)
    h2 = query1("select last_heartbeat from scheduled_tasks where task_name = 'j11-defaults'")
    time.sleep(5.5)
    h3 = query1("select last_heartbeat from scheduled_tasks where task_name = 'j11-defaults'")
    p("j11.heartbeat_unchanged_at_7s", h1 == h2)
    p("j11.heartbeat_changed_by_13s", h1 != h3)
    time.sleep(12)
    reg.stop()
    p("j11.run_starts_s", "[" + ", ".join(str(bucket(s, 1000) // 1000) for s in starts) + "]")

    print("PROBE-DONE jobs", flush=True)


if __name__ == "__main__":
    main()
