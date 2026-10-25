"""Parity scan area D (events): the Python twin of ParityDProbeTest.java.

Prints `PROBE <key>: <value>` lines with the same keys as the Java probe. Run with:

    uv run --no-sync python parity-probes/D-events/probe_events.py

Starts one PostgreSQL container (three databases: shard_0, shard_1, replica_0) and stops it.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

import tempfile  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

from backends import start  # noqa: E402
from ekbatan.config import (  # noqa: E402
    ShardGroupConfig,
    ShardingConfig,
    ShardMemberConfig,
)
from ekbatan.domain import ModelEvent  # noqa: E402
from ekbatan.events import (  # noqa: E402
    EventDispatch,
    EventFanout,
    EventFanoutJob,
    EventHandlerRegistry,
    EventHandlingJob,
    LocalEventHandlerConfig,
)
from ekbatan.events import EventHandler  # noqa: E402
from ekbatan.events.repository import (  # noqa: E402
    EventEntityRepository,
    EventNotificationRepository,
)
from ekbatan.persistence import DatabaseRegistry  # noqa: E402
from ekbatan.sharding import ShardIdentifier  # noqa: E402
from fixtures.logins import data_source  # noqa: E402
from fixtures.schema import EVENT_NOTIFICATIONS, EVENTS, metadata  # noqa: E402

S0 = ShardIdentifier(0, 0)
S1 = ShardIdentifier(1, 0)
EV = "events"
NT = "event_notifications"
T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
FMT = "%Y-%m-%d %H:%M:%S"


def p(key: str, value: Any) -> None:
    print(f"PROBE {key}: {value}", flush=True)


def jbool(v: bool) -> str:
    return "true" if v else "false"


def jlist(items: list[Any]) -> str:
    return "[" + ", ".join("null" if i is None else str(i) for i in items) + "]"


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now_value = now

    def now(self) -> datetime:
        return self.now_value


@dataclass(frozen=True, kw_only=True)
class WidgetCreatedEvent(ModelEvent["Widget"]):
    name: str
    color: str


@dataclass(frozen=True, kw_only=True)
class NoteCreatedEvent(ModelEvent["Note"]):
    # Java's field names, so the very same payload decodes on both sides.
    widgetId: str  # noqa: N815
    text: str


class Other:
    @dataclass(frozen=True, kw_only=True)
    class WidgetCreatedEvent(ModelEvent["Widget"]):
        pass


class Fatal(BaseException):
    """The nearest thing to a Java `Error`: not an `Exception`."""


CURRENT = 0
MAX = 0
LOCK = threading.Lock()


class Rec(EventHandler[Any]):
    def __init__(self, name: str, event_type: type, mode: str = "ok", aliases: tuple = ()):
        self._name = name
        self._event_type = event_type
        self.mode = mode
        self._aliases = frozenset(aliases)
        self.received: list[Any] = []
        self.order: list[str] = []

    def name(self) -> str:
        return self._name

    def aliases(self) -> frozenset[str]:
        return self._aliases

    def event_type(self) -> type:
        return self._event_type

    def handle(self, envelope: Any) -> None:
        global CURRENT, MAX
        self.received.append(envelope)
        self.order.append(envelope.model_id)
        if self.mode == "fail":
            raise RuntimeError("boom")
        if self.mode == "error":
            raise Fatal("error-boom")
        if self.mode == "slow":
            with LOCK:
                CURRENT += 1
                MAX = max(MAX, CURRENT)
            time.sleep(0.4)
            with LOCK:
                CURRENT -= 1


def uid(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012d}"


def lbl(u: Any) -> str:
    return "e" + str(int(str(u)[24:]))


def ts(d: datetime) -> str:
    return "'" + d.strftime(FMT) + "'"


def widget_payload(n: int, extra: str = "") -> str:
    return (
        '{"modelId":"m-%d","modelName":"Widget","name":"name-%d","color":"red"%s}' % (n, n, extra)
    )


def note_payload(n: int) -> str:
    return '{"modelId":"m-%d","modelName":"Note","widgetId":"w-%d","text":"t-%d"}' % (n, n, n)


def event_sql(n: int, typ: str | None, date: datetime, payload: str | None) -> str:
    return (
        f"INSERT INTO {EV} (id, namespace, action_id, action_name, action_params, started_date,"
        f" completion_date, model_id, model_type, event_type, payload, event_date, delivered) VALUES"
        f" ('{uid(n)}', 'ns', '{uid(1000 + n)}', 'SomeAction', '{{\"k\":\"v\"}}', {ts(date)},"
        f" {ts(date)}, {'NULL' if typ is None else repr('m-' + str(n))},"
        f" {'NULL' if typ is None else repr('Widget')}, {'NULL' if typ is None else repr(typ)},"
        f" {'NULL' if payload is None else repr(payload)}, {ts(date)}, FALSE)"
    )


def notif_sql(n: int, event_n: int, handler: str, next_retry: datetime, event_date: datetime) -> str:
    return (
        f"INSERT INTO {NT} (id, event_id, handler_name, namespace, action_id, action_name,"
        f" action_params, started_date, completion_date, model_id, model_type, event_type, payload,"
        f" event_date, state, attempts, next_retry_at, created_date, updated_date) VALUES"
        f" ('{uid(500 + n)}', '{uid(event_n)}', '{handler}', 'ns', '{uid(1000 + event_n)}',"
        f" 'SomeAction', '{{}}', {ts(event_date)}, {ts(event_date)}, 'm-{event_n}', 'Widget',"
        f" 'WidgetCreatedEvent', '{widget_payload(event_n)}', {ts(event_date)}, 'PENDING', 0,"
        f" {ts(next_retry)}, {ts(event_date)}, {ts(event_date)})"
    )


NCOLS = (
    "event_id::text, handler_name, state, attempts,"
    " to_char(next_retry_at,'YYYY-MM-DD HH24:MI:SS'), to_char(created_date,'YYYY-MM-DD HH24:MI:SS'),"
    " to_char(updated_date,'YYYY-MM-DD HH24:MI:SS')"
)


class World:
    def __init__(self) -> None:
        self.backend = start("postgres", Path(tempfile.mkdtemp()))
        self.urls = {}
        for name in ("shard_0", "shard_1", "replica_0"):
            self.urls[name] = self.backend.url(name)
            self.backend.prepare(name, metadata)
        self.raw = {
            S0: create_engine(self.urls["shard_0"], isolation_level="AUTOCOMMIT"),
            S1: create_engine(self.urls["shard_1"], isolation_level="AUTOCOMMIT"),
            "replica": create_engine(self.urls["replica_0"], isolation_level="AUTOCOMMIT"),
        }
        self.reg = self._registry(replica=False)
        self.replica_reg = self._registry(replica=True)

    def _registry(self, *, replica: bool) -> DatabaseRegistry:
        def member(m: int, name: str, with_replica: bool) -> ShardMemberConfig:
            b = (
                ShardMemberConfig.builder()
                .member(m)
                .name(name)
                .primary_config(data_source(self.urls[name], pool_size=8))
            )
            if with_replica:
                b = b.secondary_config(data_source(self.urls["replica_0"], pool_size=8))
            return b.build()

        groups = [
            ShardGroupConfig.builder()
            .group(0)
            .name("group-0")
            .with_member(member(0, "shard_0", replica))
            .build()
        ]
        if not replica:
            groups.append(
                ShardGroupConfig.builder()
                .group(1)
                .name("group-1")
                .with_member(member(0, "shard_1", False))
                .build()
            )
        config = ShardingConfig.builder().default_shard(S0).groups(groups).build()
        return DatabaseRegistry.from_config(config)

    def clean(self) -> None:
        for e in self.raw.values():
            with e.connect() as c:
                c.execute(text(f"TRUNCATE TABLE {NT}, {EV}"))

    def exec(self, shard: Any, sql: str) -> None:
        with self.raw[shard].connect() as c:
            c.exec_driver_sql(sql)

    def rows(self, shard: Any, sql: str) -> list[str]:
        with self.raw[shard].connect() as c:
            return [
                " | ".join("null" if v is None else str(v) for v in r) for r in c.exec_driver_sql(sql)
            ]

    def one(self, shard: Any, sql: str) -> str:
        v = self.rows(shard, sql)[0]
        return {"True": "t", "False": "f"}.get(v, v)

    def event(self, shard: Any, n: int, typ: str | None, date: datetime, payload: str | None):
        self.exec(shard, event_sql(n, typ, date, payload))

    def dump(self, prefix: str, shard: Any) -> None:
        rows = self.rows(shard, f"SELECT {NCOLS} FROM {NT} ORDER BY event_id, handler_name")
        p(f"{prefix}.count", len(rows))
        for r in rows:
            parts = r.split(" | ")
            p(f"{prefix}.{lbl(parts[0])}.{parts[1]}", " | ".join(parts[2:]))

    def state(self, shard: Any, event_n: int, handler: str) -> str:
        r = self.rows(
            shard,
            "SELECT state, attempts, to_char(next_retry_at,'YYYY-MM-DD HH24:MI:SS'),"
            f" to_char(updated_date,'YYYY-MM-DD HH24:MI:SS') FROM {NT} WHERE event_id = '{uid(event_n)}'"
            f" AND handler_name = '{handler}'",
        )
        return r[0] if r else "none"

    def fanout(self, handlers, clock, batch=200, reg=None) -> EventFanout:
        return EventFanout(
            reg or self.reg,
            handlers,
            events=EVENTS,
            notifications=EVENT_NOTIFICATIONS,
            clock=clock,
            batch_size=batch,
        )

    def handling(
        self,
        handlers,
        clock,
        batch=100,
        retention=timedelta(days=7),
        cap=timedelta(minutes=5),
        reg=None,
    ) -> EventDispatch:
        return EventDispatch(
            reg or self.reg,
            handlers,
            notifications=EVENT_NOTIFICATIONS,
            clock=clock,
            batch_size=batch,
            retention=retention,
            max_backoff=cap,
        )

    def stop(self) -> None:
        for e in self.raw.values():
            e.dispose()
        self.reg.close()
        self.replica_reg.close()
        self.backend.stop()


def any_(n: int) -> str:
    return jbool(n > 0)


def err(fn) -> str:
    try:
        fn()
        return "ok"
    except Exception as e:  # noqa: BLE001
        return f"refused: {e}"


def parse(s: str) -> datetime:
    return datetime.strptime(s, FMT).replace(tzinfo=UTC)


# ----------------------------------------------------------------------------------- probes


def f_fanout_and_happy_path(w: World) -> None:
    clock = MutableClock(T0)
    email = Rec("email", WidgetCreatedEvent)
    indexer = Rec("indexer", WidgetCreatedEvent)
    reg = EventHandlerRegistry.builder().with_handler(email).with_handler(indexer).build()

    w.event(S0, 1, "WidgetCreatedEvent", T0 - timedelta(seconds=300), widget_payload(1))
    w.event(S0, 2, "WidgetCreatedEvent", T0 - timedelta(seconds=600), widget_payload(2))
    w.event(S0, 3, "NoteCreatedEvent", T0 - timedelta(seconds=420), note_payload(3))
    w.event(S0, 4, None, T0 - timedelta(seconds=60), None)
    w.event(S1, 5, "WidgetCreatedEvent", T0 - timedelta(seconds=180), widget_payload(5))
    w.event(S0, 6, "WidgetCreatedEvent", T0 - timedelta(seconds=200), widget_payload(6, ',"extra":1'))

    fj = w.fanout(reg, clock)
    p("f.round1.any", any_(fj.drain_once()))
    for s in (S0, S1):
        for r in w.rows(s, f"SELECT id::text, delivered FROM {EV} ORDER BY id"):
            i, d = r.split(" | ")
            p(f"f.delivered.{lbl(i)}", "t" if d == "True" else "f")
    w.dump("f.s0", S0)
    w.dump("f.s1", S1)
    p(
        "f.copied_mismatch",
        w.one(
            S0,
            f"SELECT count(*) FROM {NT} n JOIN {EV} e ON e.id = n.event_id WHERE"
            " (n.namespace, n.action_id, n.action_name, n.action_params, n.started_date,"
            " n.completion_date, n.model_id, n.model_type, n.event_type, n.payload, n.event_date)"
            " IS DISTINCT FROM (e.namespace, e.action_id, e.action_name, e.action_params,"
            " e.started_date, e.completion_date, e.model_id, e.model_type, e.event_type,"
            " e.payload, e.event_date)",
        ),
    )
    p("f.round2.any", any_(fj.drain_once()))
    w.exec(S0, f"UPDATE {EV} SET delivered = FALSE WHERE id = '{uid(1)}'")
    p("f.refanout.any", any_(fj.drain_once()))
    p("f.refanout.count", w.one(S0, f"SELECT count(*) FROM {NT}"))
    p("f.refanout.e1_delivered", w.one(S0, f"SELECT delivered FROM {EV} WHERE id = '{uid(1)}'"))

    clock.now_value = T0
    hj = w.handling(reg, clock)
    p("f.handle1.any", any_(hj.drain_once()))
    w.dump("f.after.s0", S0)
    w.dump("f.after.s1", S1)
    for h in (email, indexer):
        lines = []
        for e in h.received:
            lines.append(
                "/".join(
                    [
                        lbl(e.event_id),
                        str(e.model_id),
                        str(e.model_type),
                        e.event.name,
                        e.event.color,
                        e.event.model_id,
                        e.event.model_name,
                        e.namespace,
                        str(e.action_id)[24:],
                        e.action_name,
                        json.dumps(e.action_params, separators=(",", ":")),
                        e.event_date.strftime(FMT),
                        e.started_date.strftime(FMT),
                        e.completion_date.strftime(FMT),
                    ]
                )
            )
        p(f"f.received.{h.name()}", jlist(sorted(lines)))
    p("f.handle2.any", any_(hj.drain_once()))

    audit = Rec("note-audit", NoteCreatedEvent)
    reg2 = (
        EventHandlerRegistry.builder()
        .with_handler(email)
        .with_handler(indexer)
        .with_handler(audit)
        .build()
    )
    p("f.late.any", any_(w.fanout(reg2, clock).drain_once()))
    p("f.late.e3_delivered", w.one(S0, f"SELECT delivered FROM {EV} WHERE id = '{uid(3)}'"))
    p("f.late.e3_row", w.state(S0, 3, "note-audit"))
    p(
        "f.late.e4_sentinel_delivered",
        w.one(S0, f"SELECT delivered FROM {EV} WHERE id = '{uid(4)}'"),
    )
    w.handling(reg2, clock).drain_once()
    p("f.late.audit_received", jlist([f"{e.event.widgetId}/{e.event.text}" for e in audit.received]))


def b_backoff_schedule(w: World) -> None:
    clock = MutableClock(T0)
    failing = Rec("failing", WidgetCreatedEvent, "fail")
    reg = EventHandlerRegistry.builder().with_handler(failing).build()
    w.event(S0, 1, "WidgetCreatedEvent", T0, widget_payload(1))
    w.fanout(reg, clock).drain_once()
    p("b.initial", w.state(S0, 1, "failing"))
    hj = w.handling(reg, clock)
    for step in range(1, 9):
        next_retry = max(parse(w.state(S0, 1, "failing").split(" | ")[2]), T0)
        clock.now_value = next_retry - timedelta(seconds=1)
        p(f"b.step{step}.early_any", any_(hj.drain_once()))
        clock.now_value = next_retry
        p(f"b.step{step}.any", any_(hj.drain_once()))
        st = w.state(S0, 1, "failing").split(" | ")
        delay = int((parse(st[2]) - clock.now_value).total_seconds())
        p(f"b.step{step}", f"{st[0]} attempts={st[1]} delay={delay}s updated={st[3]}")
    p("b.calls", len(failing.received))
    p("b.attempt_seen_by_handler", [e.attempt for e in failing.received])


def x_expiry(w: World) -> None:
    retention = timedelta(minutes=10)
    cap = timedelta(minutes=5)
    clock = MutableClock(T0)
    failing = Rec("failing", WidgetCreatedEvent, "fail")
    reg = EventHandlerRegistry.builder().with_handler(failing).build()
    w.event(S0, 1, "WidgetCreatedEvent", T0, widget_payload(1))
    w.fanout(reg, clock).drain_once()
    hj = w.handling(reg, clock, retention=retention, cap=cap)
    for step in range(1, 9):
        st0 = w.state(S0, 1, "failing").split(" | ")
        if st0[0] == "EXPIRED":
            break
        clock.now_value = max(parse(st0[2]), T0)
        hj.drain_once()
        st = w.state(S0, 1, "failing").split(" | ")
        p(
            f"x.step{step}",
            f"at=+{int((clock.now_value - T0).total_seconds())}s {st[0]} attempts={st[1]}"
            f" next=+{int((parse(st[2]) - T0).total_seconds())}s calls={len(failing.received)}",
        )

    w.clean()
    ok = Rec("ok", WidgetCreatedEvent)
    reg2 = EventHandlerRegistry.builder().with_handler(ok).build()
    w.event(S0, 11, "WidgetCreatedEvent", T0 - retention, widget_payload(11))
    w.event(S0, 12, "WidgetCreatedEvent", T0 - retention - timedelta(seconds=1), widget_payload(12))
    w.event(S0, 13, "WidgetCreatedEvent", T0 - retention + timedelta(seconds=1), widget_payload(13))
    clock.now_value = T0
    w.fanout(reg2, clock).drain_once()
    clock.now_value = T0
    w.handling(reg2, clock, retention=retention, cap=cap).drain_once()
    p("x.boundary.exact", w.state(S0, 11, "ok"))
    p("x.boundary.one_second_older", w.state(S0, 12, "ok"))
    p("x.boundary.one_second_younger", w.state(S0, 13, "ok"))
    p("x.boundary.calls", jlist(sorted(e.model_id for e in ok.received)))

    w.clean()
    f2 = Rec("failing", WidgetCreatedEvent, "fail")
    reg3 = EventHandlerRegistry.builder().with_handler(f2).build()
    w.event(S0, 21, "WidgetCreatedEvent", T0 - timedelta(seconds=590), widget_payload(21))
    clock.now_value = T0
    w.fanout(reg3, clock).drain_once()
    clock.now_value = T0
    w.handling(reg3, clock, retention=retention, cap=cap).drain_once()
    p("x.near_deadline.first_failure", w.state(S0, 21, "failing"))


def e_errors_missing_alias(w: World) -> None:
    clock = MutableClock(T0)
    ok = Rec("ok", WidgetCreatedEvent)
    error = Rec("error", WidgetCreatedEvent, "error")
    fail = Rec("fail", WidgetCreatedEvent, "fail")
    ghost = Rec("ghost", WidgetCreatedEvent)
    reg = EventHandlerRegistry([ok, error, fail, ghost])
    w.event(S0, 1, "WidgetCreatedEvent", T0, widget_payload(1))
    w.fanout(reg, clock).drain_once()
    without = EventHandlerRegistry([ok, error, fail])
    hj = w.handling(without, clock)
    p("e.round1.any", any_(hj.drain_once()))
    for h in ("ok", "error", "fail", "ghost"):
        p(f"e.round1.{h}", w.state(S0, 1, h))
    clock.now_value = T0 + timedelta(seconds=30)
    hj.drain_once()
    for h in ("ok", "error", "fail", "ghost"):
        p(f"e.round2.{h}", w.state(S0, 1, h))
    clock.now_value = T0 + timedelta(seconds=90)
    hj.drain_once()
    p("e.round3.ghost", w.state(S0, 1, "ghost"))
    p("e.calls", f"{len(ok.received)}/{len(error.received)}/{len(fail.received)}")

    w.clean()
    clock.now_value = T0
    old = Rec("old-name", NoteCreatedEvent)
    w.event(S0, 2, "NoteCreatedEvent", T0, note_payload(2))
    w.fanout(EventHandlerRegistry([old]), clock).drain_once()
    renamed = Rec("new-name", NoteCreatedEvent, aliases=("old-name",))
    reg2 = EventHandlerRegistry([renamed])
    w.handling(reg2, clock).drain_once()
    p("e.alias.old_row", w.state(S0, 2, "old-name"))
    p("e.alias.calls", len(renamed.received))
    w.event(S0, 3, "NoteCreatedEvent", T0, note_payload(3))
    w.fanout(reg2, clock).drain_once()
    p(
        "e.alias.new_rows",
        jlist(w.rows(S0, f"SELECT handler_name FROM {NT} WHERE event_id = '{uid(3)}'")),
    )


class Rollback(Exception):
    pass


def t_transactions(w: World) -> None:
    mgr = w.reg.manager(S0)
    events = EventEntityRepository(w.reg, EVENTS)
    notifs = EventNotificationRepository(w.reg, EVENT_NOTIFICATIONS)
    seen: dict[str, Any] = {}

    try:
        with mgr.transaction():
            with mgr.connection() as conn:
                conn.execute(text(event_sql(1, "WidgetCreatedEvent", T0, widget_payload(1))))
            seen["a"] = len(events.find_undelivered(S0, ["WidgetCreatedEvent"], 10))
            raise Rollback
    except Rollback:
        pass
    p("t.find_undelivered_sees_own_uncommitted", seen["a"])
    p("t.after_rollback_events", w.one(S0, f"SELECT count(*) FROM {EV}"))

    try:
        with mgr.transaction():
            with mgr.connection() as conn:
                conn.execute(text(notif_sql(1, 1, "h", T0, T0)))
            seen["b"] = len(notifs.find_due(S0, 10, T0.replace(tzinfo=None)))
            raise Rollback
    except Rollback:
        pass
    p("t.find_due_sees_own_uncommitted", seen["b"])

    w.event(S0, 2, "WidgetCreatedEvent", T0, widget_payload(2))
    try:
        with mgr.transaction():
            events.mark_delivered([__import__("uuid").UUID(uid(2))], S0)
            seen["c"] = len(events.find_undelivered(S0, ["WidgetCreatedEvent"], 10))
            raise Rollback
    except Rollback:
        pass
    p("t.undelivered_after_mark_in_same_tx", seen["c"])
    p(
        "t.mark_delivered_rolled_back",
        w.one(S0, f"SELECT delivered FROM {EV} WHERE id = '{uid(2)}'"),
    )

    w.exec(S0, f"TRUNCATE {NT}, {EV}")
    w.exec(
        S0,
        "CREATE OR REPLACE FUNCTION refuse_failed() RETURNS trigger AS $$ BEGIN IF NEW.state ="
        " 'FAILED' THEN RAISE EXCEPTION 'no FAILED allowed'; END IF; RETURN NEW; END $$ LANGUAGE"
        " plpgsql",
    )
    w.exec(
        S0,
        f"CREATE TRIGGER refuse_failed BEFORE UPDATE ON {NT} FOR EACH ROW EXECUTE FUNCTION"
        " refuse_failed()",
    )
    clock = MutableClock(T0)
    ok = Rec("ok", WidgetCreatedEvent)
    fail = Rec("fail", WidgetCreatedEvent, "fail")
    reg = EventHandlerRegistry([ok, fail])
    w.event(S0, 3, "WidgetCreatedEvent", T0, widget_payload(3))
    w.fanout(reg, clock).drain_once()
    threw = "false"
    try:
        w.handling(reg, clock).drain_once()
    except Exception:  # noqa: BLE001
        threw = "true"
    p("t.outcome_write_failed.threw", threw)
    p("t.outcome_write_failed.ok_row", w.state(S0, 3, "ok").split(" | ")[0])
    p("t.outcome_write_failed.fail_row", w.state(S0, 3, "fail").split(" | ")[0])
    try:
        w.handling(reg, clock).drain_once()
    except Exception:  # noqa: BLE001
        pass
    p("t.outcome_write_failed.ok_calls_after_two_rounds", len(ok.received))
    w.exec(S0, f"DROP TRIGGER refuse_failed ON {NT}")


def r_replica(w: World) -> None:
    clock = MutableClock(T0)
    ok = Rec("ok", WidgetCreatedEvent)
    reg = EventHandlerRegistry([ok])
    w.event(S0, 1, "WidgetCreatedEvent", T0, widget_payload(1))
    p(
        "r.find_undelivered",
        len(EventEntityRepository(w.replica_reg, EVENTS).find_undelivered(S0, ["WidgetCreatedEvent"], 10)),
    )
    p("r.fanout.any", any_(w.fanout(reg, clock, reg=w.replica_reg).drain_once()))
    p("r.fanout.primary_notifications", w.one(S0, f"SELECT count(*) FROM {NT}"))
    w.exec(S0, f"TRUNCATE {NT}")
    w.exec(S0, notif_sql(1, 1, "ok", T0, T0))
    p(
        "r.find_due",
        len(
            EventNotificationRepository(w.replica_reg, EVENT_NOTIFICATIONS).find_due(
                S0, 10, T0.replace(tzinfo=None)
            )
        ),
    )
    p("r.handling.any", any_(w.handling(reg, clock, reg=w.replica_reg).drain_once()))
    p("r.handling.calls", len(ok.received))


def c_batches_concurrency_order(w: World) -> None:
    global CURRENT, MAX
    clock = MutableClock(T0)
    ok = Rec("ok", WidgetCreatedEvent)
    reg = EventHandlerRegistry([ok])
    for i in range(1, 251):
        w.event(S0, i, "WidgetCreatedEvent", T0 - timedelta(seconds=1000 - i), widget_payload(i))
    fj = EventFanoutJob(w.reg, reg, events=EVENTS, notifications=EVENT_NOTIFICATIONS, clock=clock)
    fj.fanout.drain_once()
    p("c.default_fanout_batch", w.one(S0, f"SELECT count(*) FROM {EV} WHERE delivered"))
    p(
        "c.fanout_took_oldest",
        w.one(
            S0,
            f"SELECT max(event_date) < (SELECT min(event_date) FROM {EV} WHERE NOT delivered)"
            f" FROM {EV} WHERE delivered",
        ),
    )
    hj = EventHandlingJob(w.reg, reg, notifications=EVENT_NOTIFICATIONS, clock=clock)
    hj.dispatch.drain_once()
    p(
        "c.default_handling_batch",
        w.one(S0, f"SELECT count(*) FROM {NT} WHERE state = 'SUCCEEDED'"),
    )

    w.clean()
    CURRENT = 0
    MAX = 0
    slow = Rec("slow", WidgetCreatedEvent, "slow")
    reg_slow = EventHandlerRegistry([slow])
    for i in range(1, 4):
        w.event(S0, i, "WidgetCreatedEvent", T0, widget_payload(i))
    for i in range(4, 6):
        w.event(S1, i, "WidgetCreatedEvent", T0, widget_payload(i))
    w.fanout(reg_slow, clock).drain_once()
    w.handling(reg_slow, clock).drain_once()
    p("c.max_concurrent_handlers", MAX)
    p("c.slow_calls", len(slow.received))

    w.clean()
    rec = Rec("rec", WidgetCreatedEvent)
    reg_rec = EventHandlerRegistry([rec])
    w.event(S0, 1, "WidgetCreatedEvent", T0 - timedelta(seconds=300), widget_payload(1))
    clock.now_value = T0
    w.fanout(reg_rec, clock).drain_once()
    w.event(S0, 2, "WidgetCreatedEvent", T0 - timedelta(seconds=600), widget_payload(2))
    clock.now_value = T0 + timedelta(seconds=60)
    w.fanout(reg_rec, clock).drain_once()
    w.dump("c.order", S0)
    clock.now_value = T0 + timedelta(seconds=120)
    one = w.handling(reg_rec, clock, batch=1)
    one.drain_once()
    one.drain_once()
    p("c.order.handled", jlist(rec.order))

    w.clean()
    fut = Rec("fut", WidgetCreatedEvent)
    reg_fut = EventHandlerRegistry([fut])
    w.event(S0, 9, "WidgetCreatedEvent", T0 + timedelta(seconds=3600), widget_payload(9))
    clock.now_value = T0
    w.fanout(reg_fut, clock).drain_once()
    w.handling(reg_fut, clock).drain_once()
    p("c.future_event.row", w.state(S0, 9, "fut"))
    p("c.future_event.calls", len(fut.received))


def j_jobs_and_config(w: World) -> None:
    clock = MutableClock(T0)
    reg = EventHandlerRegistry([Rec("ok", WidgetCreatedEvent)])
    fj = EventFanoutJob(w.reg, reg, events=EVENTS, notifications=EVENT_NOTIFICATIONS, clock=clock)
    hj = EventHandlingJob(w.reg, reg, notifications=EVENT_NOTIFICATIONS, clock=clock)
    p("j.fanout.name", fj.name())
    p("j.handling.name", hj.name())
    p("j.fanout.schedule", type(fj.schedule()).__name__)
    p("j.handling.schedule_default", type(hj.schedule()).__name__)
    cfg = LocalEventHandlerConfig.defaults()
    p("j.config.handling_enabled_default", jbool(cfg.handling.enabled))
    p(
        "j.config.defaults_empty",
        jbool(
            all(
                getattr(cfg, n) is None
                for n in (
                    "fanout_poll_delay",
                    "fanout_batch_size",
                    "handling_poll_delay",
                    "handling_batch_size",
                    "handling_max_backoff_cap",
                    "handling_retention_window",
                )
            )
        ),
    )
    p("j.config.batch_zero", err(lambda: LocalEventHandlerConfig.builder().fanout_batch_size(0).build()))
    p(
        "j.config.poll_zero",
        err(lambda: LocalEventHandlerConfig.builder().handling_poll_delay(timedelta(0)).build()),
    )
    p(
        "j.config.retention_negative",
        err(
            lambda: LocalEventHandlerConfig.builder()
            .handling_retention_window(timedelta(seconds=-1))
            .build()
        ),
    )
    p(
        "j.job.blank_name",
        err(
            lambda: EventFanoutJob.builder()
            .registry(w.reg)
            .handlers(reg)
            .events(EVENTS)
            .notifications(EVENT_NOTIFICATIONS)
            .clock(clock)
            .name("  ")
            .build()
        ),
    )
    # The job takes no batch size of its own; the drainer does. Python's nearest form.
    p(
        "j.job.batch_zero",
        err(
            lambda: EventFanout(
                w.reg, reg, events=EVENTS, notifications=EVENT_NOTIFICATIONS, batch_size=0
            )
        ),
    )
    p(
        "j.job.no_clock",
        err(
            lambda: EventFanoutJob.builder()
            .registry(w.reg)
            .handlers(reg)
            .events(EVENTS)
            .notifications(EVENT_NOTIFICATIONS)
            .build()
        ),
    )
    p("j.empty_registry_fanout.any", any_(w.fanout(EventHandlerRegistry(), clock).drain_once()))


def g_registry(w: World) -> None:
    a = Rec("a", WidgetCreatedEvent, aliases=("a-old",))
    b = EventHandlerRegistry.builder
    p("g.same_handler_twice", err(lambda: b().with_handler(a).with_handler(a)))
    p("g.duplicate_name", err(lambda: b().with_handler(a).with_handler(Rec("a", NoteCreatedEvent))))
    p(
        "g.alias_equals_other_name",
        err(lambda: b().with_handler(a).with_handler(Rec("b", NoteCreatedEvent, aliases=("a",)))),
    )
    p(
        "g.name_equals_other_alias",
        err(lambda: b().with_handler(a).with_handler(Rec("a-old", NoteCreatedEvent))),
    )
    p(
        "g.alias_equals_own_name",
        err(lambda: b().with_handler(Rec("c", NoteCreatedEvent, aliases=("c",)))),
    )
    p("g.blank_alias", err(lambda: b().with_handler(Rec("c", NoteCreatedEvent, aliases=(" ",)))))
    p("g.blank_name", err(lambda: b().with_handler(Rec(" ", NoteCreatedEvent))))
    collision = err(lambda: b().with_handler(a).with_handler(Rec("d", Other.WidgetCreatedEvent)))
    p("g.simple_name_collision", collision[: collision.find(" maps")])
    r = (
        b()
        .with_handler(a)
        .with_handler(Rec("n1", NoteCreatedEvent))
        .with_handler(Rec("a2", WidgetCreatedEvent))
        .build()
    )
    p("g.handled_event_types", jlist(r.event_types))
    p("g.subscribers_widget", jlist([h.name() for h in r.subscribers("WidgetCreatedEvent")]))
    p("g.subscribers_unknown", jlist(r.subscribers("Nope")))
    p("g.lookup_alias", r.by_name("a-old").name)
    p("g.lookup_unknown", "null" if r.by_name("zzz") is None else "found")


def main() -> None:
    w = World()
    try:
        for probe in (
            b_backoff_schedule,
            c_batches_concurrency_order,
            e_errors_missing_alias,
            f_fanout_and_happy_path,
            g_registry,
            j_jobs_and_config,
            r_replica,
            t_transactions,
            x_expiry,
        ):
            w.clean()
            try:
                probe(w)
            except Exception as error:  # noqa: BLE001
                p(f"{probe.__name__}.CRASHED", f"{type(error).__name__}: {error}")
    finally:
        w.stop()


if __name__ == "__main__":
    main()
