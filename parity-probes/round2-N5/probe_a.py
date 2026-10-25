"""Area N5 (a): nested transactions meeting actions, repositories, handlers, jobs and keyed locks.

Twin of ParityN5ProbeTest#a_nested. Usage: probe_a.py <pg|mysql|mariadb>
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    DEFAULT_SHARD,
    CreateParams,
    DatabaseRegistry,
    RenameParams,
    ThrowAction,
    TransactionManager,
    WidgetCreateAction,
    WidgetCreatedEvent,
    WidgetRenameAction,
    WidgetRepository,
    cfg,
    DataSource,
    executor_for,
    p,
    quiet,
    raw_engine,
    scalar,
    secs,
    short,
    timed,
    truncate,
    yn,
    NOTIFS,
)

from db_scheduler.task.schedule import FixedDelay  # noqa: E402

from ekbatan.clock import SystemClock, VirtualClock  # noqa: E402
from ekbatan.distributedjobs import DistributedJob, job_registry  # noqa: E402
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob  # noqa: E402
from ekbatan.events import EventHandler  # noqa: E402
from ekbatan.events.config import LocalEventHandlerConfig  # noqa: E402
from ekbatan.ids import ShardedId  # noqa: E402
from ekbatan.locks.sql import MariaDBKeyedLockProvider, MySQLKeyedLockProvider, PostgresKeyedLockProvider  # noqa: E402

D = sys.argv[1]
RAW = raw_engine(D)
T0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


class Boom(Exception):
    pass


def side(i: int) -> int:
    return scalar(RAW, f"select count(*) from side_rows where id = {i}")


def widget_count(where: str = "1=1") -> int:
    return scalar(RAW, f"select count(*) from widgets where {where}")


def wid(w) -> str:
    return str(w.id.value)


def build(pool: int = 5, clock=None):
    clock = clock or SystemClock()
    tm = TransactionManager(DEFAULT_SHARD, DataSource.from_config(cfg(D, pool=pool)))
    reg = DatabaseRegistry({DEFAULT_SHARD: tm})
    widgets = WidgetRepository(reg)
    return tm, reg, widgets, executor_for(reg, widgets, clock), clock


def a1_a2(tm, widgets, ex) -> None:
    created = {}
    try:
        with tm.transaction() as t:
            t.execute(text("insert into side_rows values (1, 'outer')"))
            created["w"] = ex.execute(WidgetCreateAction, CreateParams(shard=DEFAULT_SHARD, name="a1", color="red"))
            p("a1.action_row_visible_outside_while_outer_open", yn(widget_count(f"id = '{wid(created['w'])}'") == 1))
            raise Boom("outer fails")
    except Boom:
        pass
    p("a1.outer_row_after_outer_rollback", side(1))
    p("a1.action_widget_after_outer_rollback", widget_count(f"id = '{wid(created['w'])}'"))
    p("a1.action_outbox_rows_after_outer_rollback", scalar(RAW, "select count(*) from eventlog.events where action_name = 'WidgetCreateAction'"))

    with tm.transaction() as t:
        t.execute(text("insert into side_rows values (2, 'outer')"))
        try:
            ex.execute(ThrowAction, CreateParams(shard=DEFAULT_SHARD, name="a2", color="red"))
            p("a2.action_error", "none")
        except Exception as e:  # noqa: BLE001
            p("a2.action_error_message", str(e))
    p("a2.outer_row_after_commit", side(2))
    p("a2.thrown_action_widget", widget_count("name = 'a2'"))
    p("a2.thrown_action_outbox_rows", scalar(RAW, "select count(*) from eventlog.events where action_name = 'ThrowAction'"))


def a3(tm, widgets, ex, clock) -> None:
    from common import Widget

    w = Widget.create(DEFAULT_SHARD, "a3", "red", clock.now())
    with tm.transaction():
        widgets.add(w)
        p("a3.repo_add_in_outer_visible_outside", yn(widget_count(f"id = '{wid(w)}'") == 1))
        p("a3.repo_find_in_outer_sees_outer_row", yn(widgets.find_by_id(w.id.value) is not None))
        p("a3.repo_tx_db_else_db_sees_outer_row", yn(_tx_else_count(widgets, w) == 1))
        try:
            ex.execute(WidgetRenameAction, RenameParams(id=w.id.value, name="renamed"))
            p("a3.action_sees_outer_row", "yes")
        except Exception as e:  # noqa: BLE001
            p("a3.action_sees_outer_row", "no: " + type(e).__name__)
        with tm.transaction() as inner:
            p("a3.inner_tx_sees_outer_row", yn(inner.execute(text(f"select count(*) from widgets where id = '{wid(w)}'")).scalar() == 1))
        p("a3.after_inner_current_is_outer", yn(tm.current() is not None))
        raise_after = True
    p("a3.repo_add_committed_with_outer", yn(widget_count(f"id = '{wid(w)}'") == 1))
    _ = raise_after


def _tx_else_count(widgets, w) -> int:
    with widgets.tx_db_else_db() as db:
        return db.execute(text(f"select count(*) from widgets where id = '{wid(w)}'")).scalar()


def a4(tm, widgets, ex) -> None:
    w = ex.execute(WidgetCreateAction, CreateParams(shard=DEFAULT_SHARD, name="a4", color="red"))
    err = {}
    t0 = time.monotonic()
    with tm.transaction() as t:
        t.execute(text(f"update widgets set color = 'locked' where id = '{wid(w)}'"))
        try:
            ex.execute(WidgetRenameAction, RenameParams(id=w.id.value, name="a4-renamed"))
            err["e"] = None
        except Exception as e:  # noqa: BLE001
            err["e"] = e
        p("a4.blocked_for", secs(time.monotonic() - t0))
        e = err["e"]
        p("a4.action_failed", yn(e is not None))
        if e is not None:
            m = str(e).lower()
            p("a4.error_is_lock_timeout", yn("lock" in m and ("timeout" in m or "wait" in m)))
        p("a4.outer_still_usable", t.execute(text("select 1")).scalar())
    p("a4.final_color", scalar(RAW, f"select color from widgets where id = '{wid(w)}'"))
    p("a4.final_name", scalar(RAW, f"select name from widgets where id = '{wid(w)}'"))
    p("a4.rename_outbox_rows", scalar(RAW, "select count(*) from eventlog.events where action_name = 'WidgetRenameAction'"))


def a5() -> None:
    tm, reg, widgets, ex, _ = build(pool=1)
    time.sleep(1)
    t0 = time.monotonic()
    with tm.transaction():
        try:
            ex.execute(WidgetCreateAction, CreateParams(shard=DEFAULT_SHARD, name="a5", color="red"))
            p("a5.action_inside_outer_on_pool_of_one", "ran")
        except Exception as e:  # noqa: BLE001
            p("a5.action_inside_outer_on_pool_of_one", "failed")
            p("a5.error", type(e).__name__)
        p("a5.waited", secs(time.monotonic() - t0))
    p("a5.widget_rows", widget_count("name = 'a5'"))
    reg.close()


def a6(D_: str) -> None:
    clock = VirtualClock(T0)
    tm, reg, widgets, ex, _ = build(clock=clock)
    with RAW.connect() as c:
        c.execute(text("delete from eventlog.event_notifications"))
        c.execute(text("delete from eventlog.events"))
    calls = []

    class TxHandler(EventHandler[WidgetCreatedEvent]):
        def name(self) -> str:
            return "n5-tx-handler"

        def event_type(self) -> type[WidgetCreatedEvent]:
            return WidgetCreatedEvent

        def handle(self, envelope) -> None:
            if envelope.event.name != "a6":
                return
            calls.append(tm.current())
            n = len(calls)
            with tm.transaction() as t:
                t.execute(text(f"insert into side_rows values ({600 + n}, 'handler')"))
                ex.execute(WidgetCreateAction, CreateParams(shard=DEFAULT_SHARD, name=f"a6-from-handler-{n}", color="red"))
                if n == 1:
                    raise Boom("first attempt fails after its own work")

    handlers = EventHandlerRegistry.builder().with_handler(TxHandler()).build()
    conf = LocalEventHandlerConfig.defaults()
    fan = EventFanoutJob.builder().registry(reg).handlers(handlers).events(__import__("common").EVENTS).notifications(NOTIFS).config(conf).clock(clock).build()
    hand = EventHandlingJob.builder().registry(reg).handlers(handlers).notifications(NOTIFS).config(conf).clock(clock).build()
    ex.execute(WidgetCreateAction, CreateParams(shard=DEFAULT_SHARD, name="a6", color="red"))
    fan.fanout.drain_once()
    hand.dispatch.drain_once()
    p("a6.handler_saw_ambient_tx", yn(calls[0] is not None))
    p("a6.after_fail.handler_side_rows", scalar(RAW, "select count(*) from side_rows where id > 600"))
    p("a6.after_fail.handler_widgets", widget_count("name like 'a6-from-handler-%'"))
    p("a6.after_fail.notification", scalar(RAW, "select concat(state, '/', attempts) from eventlog.event_notifications where handler_name = 'n5-tx-handler' order by created_date limit 1"))
    clock.advance(timedelta(seconds=31))
    hand.dispatch.drain_once()
    p("a6.after_retry.calls", len(calls))
    p("a6.after_retry.handler_side_rows", scalar(RAW, "select count(*) from side_rows where id > 600"))
    p("a6.after_retry.handler_widgets", widget_count("name like 'a6-from-handler-%'"))
    p("a6.after_retry.notification", scalar(RAW, "select concat(state, '/', attempts) from eventlog.event_notifications where handler_name = 'n5-tx-handler' order by created_date limit 1"))
    reg.close()


def a7(tm, ex) -> None:
    seen = {}
    done = threading.Event()

    class TxJob(DistributedJob):
        def name(self) -> str:
            return "n5-tx-job"

        def schedule(self):
            return FixedDelay.of(timedelta(hours=1))

        def execute(self, ctx) -> None:
            try:
                seen["ambient"] = tm.current()
                with tm.transaction() as t:
                    t.execute(text("insert into side_rows values (700, 'job')"))
                    ex.execute(WidgetCreateAction, CreateParams(shard=DEFAULT_SHARD, name="a7-from-job", color="red"))
                    raise Boom("job fails after the action")
            finally:
                done.set()

    jobs_data_source = DataSource.from_config(cfg(D, pool=3))
    jobs = job_registry().data_source(jobs_data_source).stop_on_exit(False).poll_interval(timedelta(milliseconds=200)).with_job(TxJob()).build()
    jobs.start()
    p("a7.job_ran", yn(done.wait(20)))
    time.sleep(0.5)
    jobs.stop()
    jobs_data_source.close()
    p("a7.job_saw_ambient_tx", yn(seen.get("ambient") is not None))
    p("a7.job_side_row", side(700))
    p("a7.job_action_widget", widget_count("name = 'a7-from-job'"))
    p("a7.job_failures_recorded", scalar(RAW, "select consecutive_failures from scheduled_tasks where task_name = 'n5-tx-job'"))


def a8(tm, ex) -> None:
    lock_data_source = DataSource.from_config(cfg(D, pool=3))
    cls = {"pg": PostgresKeyedLockProvider, "mysql": MySQLKeyedLockProvider, "mariadb": MariaDBKeyedLockProvider}[D]
    locks = cls.builder().data_source(lock_data_source).build()
    other = {}

    def try_other(tag: str) -> None:
        def run() -> None:
            lease = locks.try_acquire("n5-key", timedelta(milliseconds=500), timedelta(seconds=30))
            other[tag] = lease is not None
            if lease is not None:
                lease.release()

        th = threading.Thread(target=run)
        th.start()
        th.join(10)

    lease = locks.acquire("n5-key", timedelta(seconds=30))
    with tm.transaction() as t:
        t.execute(text("insert into side_rows values (800, 'locked')"))
        w = ex.execute(WidgetCreateAction, CreateParams(shard=DEFAULT_SHARD, name="a8", color="red"))
        again = locks.try_acquire("n5-key", timedelta(0), timedelta(seconds=30))
        p("a8.reentrant_in_nested_scope", yn(again is not None))
        if again is not None:
            again.release()
        try_other("while_held")
    p("a8.other_thread_while_held", yn(other["while_held"]))
    p("a8.work_committed_under_lock", f"{side(800)}/{widget_count(f'id = {chr(39)}{wid(w)}{chr(39)}')}")
    lease.release()
    try_other("after_release")
    p("a8.other_thread_after_release", yn(other["after_release"]))
    lock_data_source.close()


def main() -> None:
    quiet()
    truncate(RAW, D)
    tm, reg, widgets, ex, clock = build()
    p("dialect", D)
    a1_a2(tm, widgets, ex)
    a3(tm, widgets, ex, clock)
    a4(tm, widgets, ex)
    if D == "pg":
        a5()
    a6(D)
    a7(tm, ex)
    a8(tm, ex)
    reg.close()


if __name__ == "__main__":
    main()
_ = (short, timed)
