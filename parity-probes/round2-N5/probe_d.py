"""Area N5 (d): start and stop a whole app - registry, executor, keyed locks, job registry with
fan-out and handling - with the right password and with a wrong one. PostgreSQL.

Twin of ParityN5ProbeTest#d_lifecycle.
"""

from __future__ import annotations

import re
import sys
import threading
import time
import traceback
from collections import Counter
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    DEFAULT_SHARD,
    EVENTS,
    MARKER,
    NOTIFS,
    CreateParams,
    DatabaseRegistry,
    TransactionManager,
    WidgetCreateAction,
    WidgetCreatedEvent,
    WidgetRepository,
    executor_for,
    DataSource,
    p,
    quiet,
    raw_engine,
    scalar,
    secs,
    truncate,
    url,
    yn,
)

from ekbatan.clock import SystemClock  # noqa: E402
from ekbatan.config import DataSourceConfig  # noqa: E402
from ekbatan.distributedjobs import job_registry  # noqa: E402
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob  # noqa: E402
from ekbatan.events.config import LocalEventHandlerConfig  # noqa: E402
from ekbatan.locks.sql import PostgresKeyedLockProvider  # noqa: E402

RAW = raw_engine("pg")
WRONG = "N5-WRONG-marker"


def thread_names() -> Counter:
    return Counter(re.sub(r"[0-9]+", "#", t.name) for t in threading.enumerate() if t.is_alive())


def new_threads(before: Counter) -> dict:
    now = thread_names()
    return dict(sorted((k, v - before.get(k, 0)) for k, v in now.items() if v - before.get(k, 0) > 0))


def conns() -> int:
    return scalar(RAW, "select count(*) from pg_stat_activity where usename = 'test'") - 1


class Whole:
    def __init__(self, password: str, seen: list) -> None:
        def c(pool: int) -> DataSourceConfig:
            return DataSourceConfig(url=url("pg"), username="test", password=password, pool_size=pool)

        clock = SystemClock()
        self.tm = TransactionManager(DEFAULT_SHARD, DataSource.from_config(c(5)))
        self.reg = DatabaseRegistry({DEFAULT_SHARD: self.tm})
        repo = WidgetRepository(self.reg)
        self.ex = executor_for(self.reg, repo, clock)
        self.lock_data_source = DataSource.from_config(c(3))
        self.locks = PostgresKeyedLockProvider.builder().data_source(self.lock_data_source).build()
        self.jobs_data_source = DataSource.from_config(c(3))

        class Handler:
            name = "n5-life-handler"
            event_type = WidgetCreatedEvent

            def handle(self, env) -> None:
                seen.append(env.event.name)

        handlers = EventHandlerRegistry.builder().with_handler(Handler()).build()
        conf = (
            LocalEventHandlerConfig.builder()
            .fanout_poll_delay(timedelta(milliseconds=200))
            .handling_poll_delay(timedelta(milliseconds=200))
            .build()
        )
        self.jobs = (
            job_registry()
            .data_source(self.jobs_data_source)
            .poll_interval(timedelta(milliseconds=200))
            .stop_on_exit(False)
            .with_job(EventFanoutJob.builder().registry(self.reg).handlers(handlers).events(EVENTS).notifications(NOTIFS).config(conf).clock(clock).build())
            .with_job(EventHandlingJob.builder().registry(self.reg).handlers(handlers).notifications(NOTIFS).config(conf).clock(clock).build())
            .build()
        )

    def stop(self) -> None:
        self.jobs.stop()
        self.jobs_data_source.close()
        self.locks.close()
        self.lock_data_source.close()
        self.reg.close()


def chain(e: BaseException) -> str:
    return "".join(traceback.format_exception(e))


def main() -> None:
    quiet()
    truncate(RAW, "pg")
    before = thread_names()
    base = conns()
    seen: list[str] = []
    t0 = time.monotonic()
    app = Whole(MARKER, seen)
    built = time.monotonic() - t0
    app.jobs.start()
    started = time.monotonic() - t0
    p("d1.build_took", secs(built))
    p("d1.start_took", secs(started))
    time.sleep(2)
    p("d1.connections_open_2s_after_start", conns() - base)
    t1 = time.monotonic()
    app.ex.execute(WidgetCreateAction, CreateParams(shard=DEFAULT_SHARD, name="d1", color="red"), principal="tester")
    while "d1" not in seen and time.monotonic() - t1 < 20:
        time.sleep(0.02)
    p("d1.action_to_handler", ("within " + ("3s" if time.monotonic() - t1 < 3 else "20s")) if "d1" in seen else "never")
    p("d1.threads_while_running", new_threads(before))
    t2 = time.monotonic()
    app.stop()
    p("d1.stop_took", secs(time.monotonic() - t2))
    time.sleep(1.5)
    p("d1.connections_left_after_stop", conns() - base)
    p("d1.threads_left_after_stop", new_threads(before))

    before2 = thread_names()
    t3 = time.monotonic()
    bad = Whole(WRONG, seen)
    p("d2.build_took", secs(time.monotonic() - t3))
    t4 = time.monotonic()
    try:
        bad.jobs.start()
        outcome = "returned"
    except Exception as e:  # noqa: BLE001
        outcome = "threw " + type(e).__name__
        m = chain(e)
        assert WRONG not in m, "wrong password leaked"
        p("d2.start_error_names_auth", yn("password authentication failed" in m.lower()))
    p("d2.start", f"{outcome} after {secs(time.monotonic() - t4)}")
    t5 = time.monotonic()
    try:
        bad.ex.execute(WidgetCreateAction, CreateParams(shard=DEFAULT_SHARD, name="d2", color="red"), principal="tester")
        p("d2.action", "ran")
    except Exception as e:  # noqa: BLE001
        m = chain(e)
        assert WRONG not in m, "wrong password leaked"
        p("d2.action", f"failed after {secs(time.monotonic() - t5)}")
        p("d2.action_error_names_auth", yn("password authentication failed" in m.lower()))
    p("d2.threads_while_running", new_threads(before2))
    t6 = time.monotonic()
    bad.stop()
    p("d2.stop_took", secs(time.monotonic() - t6))
    time.sleep(1.5)
    p("d2.threads_left_after_stop", new_threads(before2))


if __name__ == "__main__":
    main()
