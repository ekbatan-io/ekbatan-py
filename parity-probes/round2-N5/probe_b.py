"""Area N5 (b): a sharded flow end to end on PostgreSQL - an action on shard 1, its outbox row,
fan-out and handling on that shard, a retry, the default-shard fallback and the fail policy.

Twin of ParityN5ProbeTest#b_sharded. Databases: testdb = shard 0:0 (default), shard1 = shard 0:1.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    DEFAULT_SHARD,
    EVENTS,
    NOTIFS,
    CreateParams,
    DatabaseRegistry,
    ShardIdentifier,
    TransactionManager,
    WidgetCreateAction,
    WidgetCreatedEvent,
    WidgetRepository,
    cfg,
    DataSource,
    executor_for,
    p,
    quiet,
    raw_engine,
    scalar,
    truncate,
    yn,
)

from ekbatan.clock import VirtualClock  # noqa: E402
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob  # noqa: E402
from ekbatan.events import EventHandler  # noqa: E402
from ekbatan.events.config import LocalEventHandlerConfig  # noqa: E402
from ekbatan.sharding import read_shard  # noqa: E402

S1 = ShardIdentifier(0, 1)
S5 = ShardIdentifier(0, 5)
T0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
R0 = raw_engine("pg", "testdb")
R1 = raw_engine("pg", "shard1")


def sh(s) -> str:
    return f"{s.group}:{s.member}"


def counts(table: str) -> str:
    return f"{scalar(R0, f'select count(*) from {table}')}/{scalar(R1, f'select count(*) from {table}')}"


def sharded(policy, clock):
    tm0 = TransactionManager(DEFAULT_SHARD, DataSource.from_config(cfg("pg", "testdb", pool=3)))
    tm1 = TransactionManager(S1, DataSource.from_config(cfg("pg", "shard1", pool=3)))
    b = DatabaseRegistry.builder().with_database(tm0).with_database(tm1).with_default_database(tm0)
    if policy is not None:
        b = b.with_unknown_shard_policy(policy)
    reg = b.build()
    repo = WidgetRepository(reg)
    return reg, repo, executor_for(reg, repo, clock)


def main() -> None:
    quiet()
    truncate(R0, "pg")
    truncate(R1, "pg")
    clock = VirtualClock(T0)
    reg, repo, ex = sharded(None, clock)
    calls: list[str] = []

    class Handler(EventHandler[WidgetCreatedEvent]):
        def name(self) -> str:
            return "n5-shard-handler"

        def event_type(self) -> type[WidgetCreatedEvent]:
            return WidgetCreatedEvent

        def handle(self, env) -> None:
            calls.append(env.event.name)
            if env.event.name == "b1" and calls.count("b1") == 1:
                raise RuntimeError("first attempt on shard 1 fails")

    handlers = EventHandlerRegistry.builder().with_handler(Handler()).build()
    conf = LocalEventHandlerConfig.defaults()
    fan = EventFanoutJob.builder().registry(reg).handlers(handlers).events(EVENTS).notifications(NOTIFS).config(conf).clock(clock).build()
    hand = EventHandlingJob.builder().registry(reg).handlers(handlers).notifications(NOTIFS).config(conf).clock(clock).build()

    w = ex.execute(WidgetCreateAction, CreateParams(shard=S1, name="b1", color="red"))
    p("b1.id_shard", sh(read_shard(w.id.value)))
    p("b1.widgets_shard0/shard1", counts("widgets"))
    p("b1.events_shard0/shard1", counts("eventlog.events"))
    p("b1.found_by_id", yn(repo.find_by_id(w.id.value) is not None))
    fan.fanout.drain_once()
    p("b1.after_fanout.notifications_shard0/shard1", counts("eventlog.event_notifications"))
    p("b1.after_fanout.delivered_on_shard1", scalar(R1, "select count(*) from eventlog.events where delivered"))
    hand.dispatch.drain_once()
    q = "select concat(state, '/', attempts, '/', extract(epoch from next_retry_at - event_date)::int) from eventlog.event_notifications"
    p("b1.after_fail.notification_on_shard1", scalar(R1, q))
    clock.advance(timedelta(seconds=31))
    hand.dispatch.drain_once()
    p("b1.after_retry.notification_on_shard1", scalar(R1, "select concat(state, '/', attempts) from eventlog.event_notifications"))
    p("b1.handler_calls", ",".join(calls))

    w5 = ex.execute(WidgetCreateAction, CreateParams(shard=S5, name="b2", color="red"))
    i5 = str(w5.id.value)
    p("b2.id_shard", sh(read_shard(w5.id.value)))
    p("b2.widget_in_shard0/shard1", f"{scalar(R0, "select count(*) from widgets where name = 'b2'")}/{scalar(R1, "select count(*) from widgets where name = 'b2'")}")
    p("b2.event_in_shard0/shard1", f"{scalar(R0, f"select count(*) from eventlog.events where model_id = '{i5}'")}/{scalar(R1, f"select count(*) from eventlog.events where model_id = '{i5}'")}")
    p("b2.found_by_id", yn(repo.find_by_id(w5.id.value) is not None))
    fan.fanout.drain_once()
    hand.dispatch.drain_once()
    p("b2.handled_from_default_shard", scalar(R0, f"select concat(state, '/', attempts) from eventlog.event_notifications where model_id = '{i5}'"))
    p("b2.handler_calls", ",".join(calls))
    reg.close()

    freg, frepo, fex = sharded("fail", clock)
    try:
        fex.execute(WidgetCreateAction, CreateParams(shard=S5, name="b3", color="red"))
        p("b3.write_unknown_shard", "written")
    except Exception as e:  # noqa: BLE001
        p("b3.write_unknown_shard", "refused")
        p("b3.error_message", str(e))
    p("b3.widget_rows_anywhere", scalar(R0, "select count(*) from widgets where name = 'b3'") + scalar(R1, "select count(*) from widgets where name = 'b3'"))
    try:
        frepo.find_by_id(w5.id.value)
        p("b3.read_unknown_shard", "read")
    except Exception as e:  # noqa: BLE001
        p("b3.read_unknown_shard", "refused")
        p("b3.read_error_message", str(e))
    freg.close()


if __name__ == "__main__":
    main()
