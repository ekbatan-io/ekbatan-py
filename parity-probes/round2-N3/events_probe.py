"""Parity round 2, area N3: events, jobs, metrics - the Python twin of ParityN3EventsProbeTest.java.

Run: cd ekbatan-py && uv run --no-sync python parity-probes/round2-N3/events_probe.py
"""

from __future__ import annotations

from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

SPANS = InMemorySpanExporter()
_tp = TracerProvider()
_tp.add_span_processor(SimpleSpanProcessor(SPANS))
otel_trace.set_tracer_provider(_tp)
METRICS = InMemoryMetricReader()
otel_metrics.set_meter_provider(MeterProvider(metric_readers=[METRICS]))

import logging  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

MARK = "n3MarkerPw7731q"
SCEN = ["setup"]
RECORDS: list[tuple[str, str, str, str, str]] = []
ALL_TEXT: list[str] = []
OTHER: dict[str, int] = {}
LEVELS = {50: "CRITICAL", 40: "ERROR", 30: "WARN", 20: "INFO", 10: "DEBUG"}


class Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            msg = str(record.msg)
        exc, tb = "-", ""
        if record.exc_info and record.exc_info[1] is not None:
            exc = type(record.exc_info[1]).__name__
            tb = "".join(traceback.format_exception(*record.exc_info))
        ALL_TEXT.append(f"{SCEN[0]} {record.name} {msg} {tb}")
        lvl = LEVELS.get(record.levelno, str(record.levelno))
        if record.name.startswith("io.ekbatan") or (
            record.name.startswith("db_scheduler") and record.levelno >= logging.INFO
        ):
            RECORDS.append((SCEN[0], lvl, record.name, msg, exc))
        elif record.levelno >= logging.INFO:
            key = f"{SCEN[0]} {lvl} {'.'.join(record.name.split('.')[:3])}"
            OTHER[key] = OTHER.get(key, 0) + 1


logging.getLogger().addHandler(Capture())
logging.getLogger().setLevel(logging.DEBUG)

from db_scheduler.task.schedule import FixedDelay  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

from ekbatan.config import (  # noqa: E402
    DataSourceConfig,
    ShardGroupConfig,
    ShardingConfig,
    ShardMemberConfig,
)
from ekbatan.distributedjobs import DistributedJob, job_registry  # noqa: E402
from ekbatan.domain import ModelEvent  # noqa: E402
from ekbatan.events import EventDispatch, EventFanout, EventHandlerRegistry  # noqa: E402
from ekbatan.events import EventHandler  # noqa: E402
from ekbatan.persistence import DatabaseRegistry, DataSource  # noqa: E402
from ekbatan.sharding import ShardIdentifier  # noqa: E402
from fixtures.schema import EVENT_NOTIFICATIONS, EVENTS, metadata  # noqa: E402

S0 = ShardIdentifier(0, 0)
T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
FMT = "%Y-%m-%d %H:%M:%S"


def p(key: str, value: Any) -> None:
    print(f"PROBE {key}: {value}", flush=True)


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now_value = now

    def now(self) -> datetime:
        return self.now_value


@dataclass(frozen=True, kw_only=True)
class WidgetCreatedEvent(ModelEvent["Widget"]):
    name: str
    color: str


class Fatal(BaseException):
    """The nearest thing to a Java `Error`."""


class Rec(EventHandler[Any]):
    def __init__(self, name: str, mode: str) -> None:
        self._name, self.mode = name, mode

    def name(self) -> str:
        return self._name

    def event_type(self) -> type:
        return WidgetCreatedEvent

    def handle(self, envelope: Any) -> None:
        if self.mode == "fail":
            raise RuntimeError("handler boom")
        if self.mode == "error":
            raise Fatal("error boom")


def uid(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012d}"


def ts(d: datetime) -> str:
    return "'" + d.strftime(FMT) + "'"


ENGINE: Any = None
SOURCE: Any = None


def ex(sql: str) -> None:
    with ENGINE.begin() as c:
        c.exec_driver_sql(sql)


def event(n: int, date: datetime) -> None:
    payload = '{"modelId":"m-%d","modelName":"Widget","name":"name-%d","color":"red"}' % (n, n)
    ex(
        "INSERT INTO events (id, namespace, action_id, action_name, action_params, started_date,"
        " completion_date, model_id, model_type, event_type, payload, event_date, delivered) VALUES"
        f" ('{uid(n)}', 'ns', '{uid(1000 + n)}', 'SomeAction', '{{\"k\":\"v\"}}', {ts(date)}, {ts(date)},"
        f" 'm-{n}', 'Widget', 'WidgetCreatedEvent', '{payload}', {ts(date)}, FALSE)"
    )


def notif(n: int, event_n: int, handler: str, event_date: datetime, next_retry: datetime) -> None:
    payload = '{"modelId":"m-%d","modelName":"Widget","name":"n","color":"red"}' % event_n
    ex(
        "INSERT INTO event_notifications (id, event_id, handler_name, namespace, action_id, action_name,"
        " action_params, started_date, completion_date, model_id, model_type, event_type, payload,"
        " event_date, state, attempts, next_retry_at, created_date, updated_date) VALUES"
        f" ('{uid(500 + n)}', '{uid(event_n)}', '{handler}', 'ns', '{uid(1000 + event_n)}',"
        f" 'SomeAction', '{{}}', {ts(event_date)}, {ts(event_date)}, 'm-{event_n}', 'Widget',"
        f" 'WidgetCreatedEvent', '{payload}', {ts(event_date)}, 'PENDING', 0, {ts(next_retry)},"
        f" {ts(event_date)}, {ts(event_date)})"
    )


def scenario(name: str, body) -> None:
    SPANS.clear()
    SCEN[0] = name
    try:
        body()
        p(name + ".outcome", "ok")
    except BaseException as t:  # noqa: BLE001
        p(name + ".outcome", f"{type(t).__name__}: {t}")
    time.sleep(0.1)
    p(name + ".spans", len(SPANS.get_finished_spans()))
    i = 0
    for r in RECORDS:
        if r[0] != name:
            continue
        p(f"{name}.log.{i}", f"{r[1]} {r[2]} | {r[3]}".replace("\n", "\\n") + f" | exc={r[4]}")
        i += 1


def dump_metrics() -> None:
    out: dict[str, str] = {}
    data = METRICS.get_metrics_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                kind = type(m.data).__name__
                head = f"{m.name} scope={sm.scope.name} type={kind} unit={m.unit} desc={m.description}"
                if kind == "Sum":
                    head += (
                        f" monotonic={str(m.data.is_monotonic).lower()}"
                        f" temporality={m.data.aggregation_temporality.name}"
                    )
                    for pt in m.data.data_points:
                        a = "{" + ", ".join(f"{k}={v}" for k, v in sorted(pt.attributes.items())) + "}"
                        out[f"{m.name} {a}"] = f"value={pt.value}"
                elif kind == "Histogram":
                    head += f" temporality={m.data.aggregation_temporality.name}"
                    for pt in m.data.data_points:
                        a = "{" + ", ".join(f"{k}={v}" for k, v in sorted(pt.attributes.items())) + "}"
                        out[f"{m.name} {a}"] = f"count={pt.count} boundaries={len(pt.explicit_bounds)}"
                out[f"{m.name} (instrument)"] = head
                if MARK in m.to_json():
                    p("secret.metric_with_marker", m.name)
    for k in sorted(out):
        p("metric." + k, out[k])


class Job(DistributedJob):
    def __init__(self, name, schedule, body) -> None:
        self._name, self._schedule, self._body = name, schedule, body

    def name(self) -> str:
        return self._name

    def schedule(self):
        return self._schedule

    def execute(self, ctx) -> None:
        self._body()


def boom() -> None:
    raise ValueError("job boom")


def main() -> None:
    global ENGINE, SOURCE
    container = PostgresContainer(
        "postgres:17-alpine", username="probe", password=MARK, dbname="events_db", driver="psycopg"
    )
    container.start()
    try:
        host, port = container.get_container_host_ip(), container.get_exposed_port(5432)
        ENGINE = create_engine(f"postgresql+psycopg://probe:{MARK}@{host}:{port}/events_db", pool_size=8)
        SOURCE = DataSource.from_config(DataSourceConfig(
            url=f"postgresql+psycopg://{host}:{port}/events_db", username="probe", password=MARK, pool_size=8
        ))
        metadata.create_all(ENGINE)
        config = (
            ShardingConfig.builder()
            .default_shard(S0)
            .groups(
                [
                    ShardGroupConfig.builder().group(0).name("g").with_member(
                        ShardMemberConfig.builder().member(0).primary_config(
                            DataSourceConfig(
                                url=f"postgresql+psycopg://{host}:{port}/events_db",
                                username="probe",
                                password=MARK,
                                pool_size=8,
                            )
                        ).build()
                    ).build()
                ]
            )
            .build()
        )
        reg = DatabaseRegistry.from_config(config)
        clock = MutableClock(T0)
        handlers = (
            EventHandlerRegistry.builder()
            .with_handler(Rec("ok-handler", "ok"))
            .with_handler(Rec("fail-handler", "fail"))
            .with_handler(Rec("error-handler", "error"))
            .build()
        )
        fanout = EventFanout(reg, handlers, events=EVENTS, notifications=EVENT_NOTIFICATIONS, clock=clock)
        handling = EventDispatch(
            reg,
            handlers,
            notifications=EVENT_NOTIFICATIONS,
            clock=clock,
            retention=timedelta(days=1),
            max_backoff=timedelta(minutes=5),
        )

        def e1():
            event(1, T0)
            event(2, T0)
            fanout.drain_once()

        scenario("e1_fanout", e1)
        scenario("e2_handle_round", handling.drain_once)

        def e3():
            ex("TRUNCATE event_notifications")
            notif(1, 1, "ok-handler", T0 - timedelta(days=2), T0)
            notif(2, 2, "ok-handler", T0 - timedelta(days=2), T0)
            handling.drain_once()

        scenario("e3_expired_preflight", e3)

        def e4():
            ex("TRUNCATE event_notifications")
            notif(3, 3, "fail-handler", T0 - timedelta(days=1) + timedelta(seconds=1), T0)
            handling.drain_once()

        scenario("e4_expired_postfailure", e4)
        dump_metrics()

        ex("drop table if exists scheduled_tasks")
        ex(
            "create table scheduled_tasks (task_name text not null, task_instance text not null,"
            " task_data bytea, execution_time timestamp with time zone not null, picked BOOLEAN not null,"
            " picked_by text, last_success timestamp with time zone, last_failure timestamp with time zone,"
            " consecutive_failures INT, last_heartbeat timestamp with time zone, version BIGINT not null,"
            " priority SMALLINT, PRIMARY KEY (task_name, task_instance))"
        )
        jobs = (
            job_registry()
            .data_source(SOURCE)
            .register_shutdown_hook(False)
            .poll_interval(timedelta(milliseconds=100))
            .with_job(Job("ok-job", FixedDelay.of(timedelta(hours=1)), lambda: None))
            .with_job(Job("failing-job", FixedDelay.of(timedelta(hours=1)), boom))
            .build()
        )
        scenario("j1_start", jobs.start)
        scenario("j2_run", lambda: time.sleep(2))
        scenario("j3_stop", jobs.stop)

        hits = 0
        for s in ALL_TEXT:
            if MARK in s:
                hits += 1
                parts = s.split(" ")
                p(f"secret.hit.{hits}", parts[0] + " " + parts[1])
        p("secret.log_records_with_marker", hits)
        for k in sorted(OTHER):
            p("library_info_plus." + k, OTHER[k])
        fanout.close() if hasattr(fanout, "close") else None
        handling.close()
        reg.close()
    finally:
        container.stop()


if __name__ == "__main__":
    main()
