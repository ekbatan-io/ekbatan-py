"""Parity round 2, area N3 (observability): the Python twin of ParityN3ObsProbeTest.java.

Every log record, span and the marker-password search, for the same scenarios as the Java probe.
One PostgreSQL container (global_db, mexico_db), login `probe` with a marker password.

Run: cd ekbatan-py && uv run --no-sync python parity-probes/round2-N3/obs_probe.py
"""

from __future__ import annotations

# The SDK goes in before anything of ekbatan's is imported, as Java registers it before the
# instrumented classes load.
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
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
import uuid  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from datetime import timedelta  # noqa: E402
from decimal import Decimal  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

MARK = "n3MarkerPw7731q"
SCEN = ["setup"]
RECORDS: list[tuple[str, str, str, str, str]] = []
ALL_TEXT: list[str] = []
OTHER: dict[str, int] = {}
SPAN_HITS = [0]

LEVELS = {50: "CRITICAL", 40: "ERROR", 30: "WARN", 20: "INFO", 10: "DEBUG"}


class Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            msg = str(record.msg)
        exc = "-"
        tb = ""
        if record.exc_info and record.exc_info[1] is not None:
            exc = type(record.exc_info[1]).__name__
            tb = "".join(traceback.format_exception(*record.exc_info))
        extra = " ".join(f"{k}={v}" for k, v in record.__dict__.items() if k.startswith("ekbatan"))
        ALL_TEXT.append(f"{SCEN[0]} {record.name} {msg} {tb} {extra}")
        if record.name.startswith("io.ekbatan"):
            RECORDS.append((SCEN[0], LEVELS.get(record.levelno, str(record.levelno)), record.name, msg, exc))
        elif record.levelno >= logging.INFO:
            top = ".".join(record.name.split(".")[:3])
            key = f"{SCEN[0]} {LEVELS.get(record.levelno)} {top}"
            OTHER[key] = OTHER.get(key, 0) + 1


logging.getLogger().addHandler(Capture())
logging.getLogger().setLevel(logging.DEBUG)

from sqlalchemy import create_engine, text  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

from ekbatan.action import (  # noqa: E402
    ActionExecutor,
    ActionRegistry,
    ExecutionConfiguration,
    RetryConfig,
    SingleTableJsonEventPersister,
)
from ekbatan.action.action import Action  # noqa: E402
from ekbatan.clock import SystemClock  # noqa: E402
from ekbatan.config import (  # noqa: E402
    DataSourceConfig,
    ShardGroupConfig,
    ShardingConfig,
    ShardMemberConfig,
)
from ekbatan.errors import StaleRecordError  # noqa: E402
from ekbatan.ids import ShardedId  # noqa: E402
from ekbatan.locks import PostgresKeyedLockProvider  # noqa: E402
from ekbatan.migrations import migrate  # noqa: E402
from ekbatan.persistence import DatabaseRegistry  # noqa: E402
from ekbatan.schema import outbox_table  # noqa: E402
from ekbatan.sharding import EmbeddedBitsShardingStrategy, ShardIdentifier  # noqa: E402
from fixtures.domain import Wallet  # noqa: E402
from fixtures.repositories import WalletRepository  # noqa: E402
from fixtures.schema import WALLETS  # noqa: E402
from sqlalchemy import MetaData  # noqa: E402

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)
AUSTRALIA = ShardIdentifier(2, 0)
clock = SystemClock()
ATTEMPTS = [0]
registry: DatabaseRegistry
wallet_repo: WalletRepository
executor: ActionExecutor

V1 = """CREATE SCHEMA IF NOT EXISTS eventlog;
CREATE TABLE eventlog.events (
    id UUID PRIMARY KEY, namespace VARCHAR(255) NOT NULL, action_id UUID NOT NULL,
    action_name VARCHAR(255) NOT NULL, action_params JSONB NOT NULL,
    started_date TIMESTAMP NOT NULL, completion_date TIMESTAMP NOT NULL,
    model_id VARCHAR(255), model_type VARCHAR(255), event_type VARCHAR(255), payload JSONB,
    event_date TIMESTAMP NOT NULL, delivered BOOLEAN NOT NULL);
CREATE INDEX idx_events_action_id ON eventlog.events(action_id);
"""
# Java's V0002, plus the two nullable columns the suite's Wallet fixture maps.
V2 = """CREATE TABLE wallets (
    id UUID PRIMARY KEY, version BIGINT NOT NULL, state VARCHAR(24) NOT NULL,
    owner_id UUID NOT NULL, currency CHAR(3) NOT NULL, balance DECIMAL(19, 4) NOT NULL,
    created_date TIMESTAMP NOT NULL, updated_date TIMESTAMP NOT NULL,
    aliases JSONB, reward_points INT);
CREATE INDEX idx_wallets_owner_id ON wallets(owner_id);
"""


def p(key: str, value: Any) -> None:
    print(f"PROBE {key}: {value}", flush=True)


def new_wallet(shard: ShardIdentifier) -> Wallet:
    return Wallet.open(
        id=ShardedId[Wallet].generate(shard),
        owner_id=uuid.uuid4(),
        currency="EUR",
        balance=Decimal("10"),
        created_date=clock.now(),
    )


@dataclass(frozen=True)
class CountryParams:
    country_code: str


@dataclass(frozen=True)
class IdParams:
    wallet_id: uuid.UUID


@dataclass(frozen=True)
class NoParams:
    pass


class CreateAction(Action[CountryParams, Wallet]):
    def perform(self, principal, params):
        shard = {"MX": MEXICO, "AU": AUSTRALIA}.get(params.country_code, GLOBAL)
        return self.plan.add(new_wallet(shard))


class FailAction(Action[NoParams, Wallet]):
    def perform(self, principal, params):
        self.plan.add(new_wallet(GLOBAL))
        raise RuntimeError("perform boom")


class FreshPlanAction(Action[NoParams, Wallet]):
    def perform(self, principal, params):
        ATTEMPTS[0] += 1
        w = self.plan.add(new_wallet(GLOBAL))
        if ATTEMPTS[0] == 1:
            raise StaleRecordError("first attempt")
        return w


class StaleUpdateAction(Action[IdParams, Wallet]):
    def perform(self, principal, params):
        w = wallet_repo.get_by_id(params.wallet_id)
        return self.plan.update(w.copy().version(999).build())


class TwoShardAction(Action[NoParams, Wallet]):
    def perform(self, principal, params):
        g = self.plan.add(new_wallet(GLOBAL))
        self.plan.add(new_wallet(MEXICO))
        return g


class PartialAction(Action[IdParams, Wallet]):
    def perform(self, principal, params):
        g = self.plan.add(new_wallet(GLOBAL))
        mx = wallet_repo.get_by_id(params.wallet_id)
        self.plan.update(mx.copy().version(999).build())
        return g


def describe(s: Any, by_id: dict[int, str]) -> str:
    attrs = {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in s.attributes.items()}
    events = []
    for e in s.events:
        ea = {k: str(v) for k, v in e.attributes.items() if k != "exception.stacktrace"}
        events.append(e.name + "{" + ", ".join(f"{k}={v}" for k, v in sorted(ea.items())) + "}")
    parent = "root" if s.parent is None else by_id.get(s.parent.span_id, "outside")
    status = s.status.status_code.name
    return (
        f"{s.name} parent={parent} kind={s.kind.name} scope={s.instrumentation_scope.name} "
        f"status={status}:{s.status.description or ''} "
        "attrs={" + ", ".join(f"{k}={v}" for k, v in sorted(attrs.items())) + "} "
        "events=[" + ", ".join(events) + "]"
    )


def scenario(name: str, body) -> None:
    SPANS.clear()
    SCEN[0] = name
    try:
        body()
        p(name + ".outcome", "ok")
    except BaseException as t:  # noqa: BLE001
        p(name + ".outcome", type(t).__name__)
    time.sleep(0.05)
    spans = SPANS.get_finished_spans()
    by_id = {s.context.span_id: s.name for s in spans}
    for i, s in enumerate(spans):
        p(f"{name}.span.{i}", describe(s, by_id))
        if MARK in s.to_json():
            SPAN_HITS[0] += 1
    i = 0
    for r in RECORDS:
        if r[0] != name:
            continue
        p(f"{name}.log.{i}", f"{r[1]} {r[2]} | {r[3]}".replace("\n", "\\n") + f" | exc={r[4]}")
        i += 1


def ds(base: str, db: str) -> DataSourceConfig:
    return DataSourceConfig(url=f"{base}/{db}", username="probe", password=MARK, pool_size=6)


def main() -> None:
    global registry, wallet_repo, executor
    container = PostgresContainer(
        "postgres:17-alpine", username="probe", password=MARK, dbname="global_db", driver="psycopg"
    )
    container.start()
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)
        base = f"postgresql+psycopg://{host}:{port}"
        admin = create_engine(
            f"postgresql+psycopg://probe:{MARK}@{host}:{port}/global_db", isolation_level="AUTOCOMMIT"
        )
        with admin.connect() as c:
            c.execute(text("CREATE DATABASE mexico_db"))
        migrations = Path(tempfile.mkdtemp())
        (migrations / "V0001__create__events.sql").write_text(V1)
        (migrations / "V0002__create_wallets_table.sql").write_text(V2)
        config = (
            ShardingConfig.builder()
            .default_shard(GLOBAL)
            .groups(
                [
                    ShardGroupConfig.builder().group(0).name("global").with_member(
                        ShardMemberConfig.builder().member(0).primary_config(ds(base, "global_db")).build()
                    ).build(),
                    ShardGroupConfig.builder().group(1).name("mexico").with_member(
                        ShardMemberConfig.builder().member(0).primary_config(ds(base, "mexico_db")).build()
                    ).build(),
                ]
            )
            .build()
        )
        scenario("m1_migrate", lambda: migrate(config, migrations))

        def build_registry():
            global registry
            registry = DatabaseRegistry.from_config(config)

        scenario("r1_registry", build_registry)
        wallet_repo = WalletRepository(
            Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
        )
        executor = ActionExecutor(
            namespace="parity.n3",
            registry=registry,
            repositories=[wallet_repo],
            actions=ActionRegistry(
                [
                    CreateAction(clock),
                    FailAction(clock),
                    FreshPlanAction(clock),
                    StaleUpdateAction(clock),
                    TwoShardAction(clock),
                    PartialAction(clock),
                ]
            ),
            outbox=SingleTableJsonEventPersister(registry, outbox_table(MetaData(), schema="eventlog")),
            clock=clock,
        )
        alice = "alice"
        no_retry = ExecutionConfiguration.no_retry()
        cross = ExecutionConfiguration(allow_cross_shard=True)
        no_retry_cross = ExecutionConfiguration.no_retry(allow_cross_shard=True)
        one_retry = ExecutionConfiguration(retry_configs=(RetryConfig(on=StaleRecordError, max_retries=1, delay=0),))

        created: list[Any] = [None]

        def a1():
            created[0] = executor.execute(alice, CreateAction, CountryParams("DE"))

        scenario("a1_success", a1)
        scenario("a1b_no_principal", lambda: executor.execute(None, CreateAction, CountryParams("DE")))

        def a2():
            ATTEMPTS[0] = 0
            executor.execute(alice, FreshPlanAction, NoParams())

        scenario("a2_retry_then_success", a2)
        scenario("a3_perform_fails", lambda: executor.execute(alice, FailAction, NoParams()))
        wid = created[0].id.value
        scenario(
            "a4_persist_fails_no_retry",
            lambda: executor.execute(alice, StaleUpdateAction, IdParams(wid), config=no_retry),
        )
        scenario(
            "a5_retries_exhausted",
            lambda: executor.execute(alice, StaleUpdateAction, IdParams(wid), config=one_retry),
        )
        scenario(
            "a6_cross_shard_allowed",
            lambda: executor.execute(alice, TwoShardAction, NoParams(), config=cross),
        )
        scenario("a7_cross_shard_rejected", lambda: executor.execute(alice, TwoShardAction, NoParams()))
        mx = executor.execute(alice, CreateAction, CountryParams("MX"))
        scenario(
            "a8_partial_commit",
            lambda: executor.execute(alice, PartialAction, IdParams(mx.id.value), config=no_retry_cross),
        )
        scenario("a9_unknown_shard", lambda: executor.execute(alice, CreateAction, CountryParams("AU")))
        scenario("a10_repo_add_all", lambda: wallet_repo.add_all([new_wallet(GLOBAL), new_wallet(GLOBAL)]))
        scenario(
            "a11_repo_add_all_no_result",
            lambda: wallet_repo.add_all_no_result([new_wallet(GLOBAL), new_wallet(GLOBAL)]),
        )

        # Locks (PostgreSQL advisory)
        lock_data_source = create_engine(f"postgresql+psycopg://probe:{MARK}@{host}:{port}/global_db", pool_size=6)
        other_engine = create_engine(f"postgresql+psycopg://probe:{MARK}@{host}:{port}/global_db", pool_size=6)
        locks = PostgresKeyedLockProvider(lock_data_source)
        other = PostgresKeyedLockProvider(other_engine)

        def l1():
            held = locks.acquire("order-1", timedelta(minutes=5))

            def waiter():
                try:
                    got = other.try_acquire("order-1", timedelta(milliseconds=200), timedelta(minutes=5))
                    p("l1_wait_times_out.got", str(got is not None).lower())
                except Exception as e:  # noqa: BLE001
                    p("l1_wait_times_out.error", type(e).__name__)

            t = threading.Thread(target=waiter)
            t.start()
            t.join()
            held.release()

        scenario("l1_wait_times_out", l1)

        def l2():
            locks.acquire("order-2", timedelta(milliseconds=300))
            time.sleep(0.9)

        scenario("l2_hold_limit_expires", l2)

        def l3():
            lease = locks.acquire("order-3", timedelta(minutes=5))
            with admin.connect() as c:
                c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype = 'advisory'"))
            time.sleep(0.2)
            lease.release()

        scenario("l3_release_fails", l3)

        hits = 0
        for s in ALL_TEXT:
            if MARK in s:
                hits += 1
                parts = s.split(" ")
                p(f"secret.hit.{hits}", parts[0] + " " + parts[1])
        p("secret.log_records_scanned", str(len(ALL_TEXT) > 100).lower())
        p("secret.log_records_with_marker", hits)
        p("secret.spans_with_marker", SPAN_HITS[0])
        for k in sorted(OTHER):
            p("library_info_plus." + k, OTHER[k])
        registry.close()
    finally:
        container.stop()


if __name__ == "__main__":
    main()
