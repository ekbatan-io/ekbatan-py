"""Area B parity probe (Python twin of ParityBActionsProbeTest.java).

Two PostgreSQL containers (global 0:0, mexico 1:0), the same tables Java's postgres-sharded
module migrates (eventlog.events + wallets), and the same scenarios, printing the same PROBE keys.

Run: cd ekbatan-py && uv run --no-sync python parity-probes/B-actions/probe_actions.py
"""

from __future__ import annotations

import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))

from sqlalchemy import MetaData, create_engine, text  # noqa: E402
from testcontainers.community.postgres import PostgresContainer  # noqa: E402

from ekbatan.action import (  # noqa: E402
    ActionExecutor,
    ActionRegistry,
    ExecutionConfiguration,
    RetryConfig,
    SingleTableJsonEventPersister,
)
from ekbatan.action.action import Action  # noqa: E402
from ekbatan.clock import SystemClock, VirtualClock  # noqa: E402
from ekbatan.config import (  # noqa: E402
    ShardGroupConfig,
    ShardingConfig,
    ShardMemberConfig,
)
from ekbatan.domain import Model, ModelEvent  # noqa: E402
from ekbatan.errors import CrossShardError, StaleRecordError  # noqa: E402
from ekbatan.ids import ShardedId  # noqa: E402
from ekbatan.persistence import DatabaseRegistry  # noqa: E402
from ekbatan.repository import RepositoryRegistry  # noqa: E402
from ekbatan.schema import outbox_table  # noqa: E402
from ekbatan.sharding import EmbeddedBitsShardingStrategy, ShardIdentifier  # noqa: E402
from ekbatan.testing import ActionSpec  # noqa: E402
from fixtures.domain import Wallet, WalletCreatedEvent, WalletMoneyDepositedEvent  # noqa: E402
from fixtures.logins import data_source  # noqa: E402
from fixtures.repositories import NotificationRepository, WalletRepository  # noqa: E402
from fixtures.schema import NOTIFICATIONS, WALLETS  # noqa: E402
from fixtures.domain import Notification  # noqa: E402

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)
AUSTRALIA = ShardIdentifier(2, 0)

# Java's postgres-sharded migrations, plus the two nullable columns the suite's Wallet fixture
# maps (aliases, reward_points). They play no part in any probe.
DDL = """
CREATE SCHEMA IF NOT EXISTS eventlog;
CREATE TABLE eventlog.events (
    id UUID PRIMARY KEY,
    namespace VARCHAR(255) NOT NULL,
    action_id UUID NOT NULL,
    action_name VARCHAR(255) NOT NULL,
    action_params JSONB NOT NULL,
    started_date TIMESTAMP NOT NULL,
    completion_date TIMESTAMP NOT NULL,
    model_id VARCHAR(255),
    model_type VARCHAR(255),
    event_type VARCHAR(255),
    payload JSONB,
    event_date TIMESTAMP NOT NULL,
    delivered BOOLEAN NOT NULL
);
CREATE INDEX idx_events_action_id ON eventlog.events(action_id);
CREATE TABLE wallets (
    id UUID PRIMARY KEY,
    version BIGINT NOT NULL,
    state VARCHAR(24) NOT NULL,
    owner_id UUID NOT NULL,
    currency CHAR(3) NOT NULL,
    balance DECIMAL(19, 4) NOT NULL,
    created_date TIMESTAMP NOT NULL,
    updated_date TIMESTAMP NOT NULL,
    aliases JSONB,
    reward_points INT
);
"""

EVENTS = outbox_table(MetaData(), schema="eventlog")

registry: DatabaseRegistry
wallet_repo: WalletRepository
executor: ActionExecutor
engines: dict[ShardIdentifier, Any] = {}
clock = SystemClock()
ATTEMPTS = [0]


def p(key: str, value: Any) -> None:
    print(f"PROBE {key}: {fmt(value)}", flush=True)


def fmt(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, list):
        return "[" + ", ".join(fmt(v) for v in value) + "]"
    return str(value)


def sh(s: ShardIdentifier) -> str:
    return f"{s.group}:{s.member}"


def new_wallet(shard: ShardIdentifier) -> Wallet:
    return Wallet.open(
        id=ShardedId[Wallet].generate(shard),
        owner_id=uuid.uuid4(),
        currency="EUR",
        balance=Decimal("10"),
        created_date=clock.now(),
    )


# ---------------------------------------------------------------------- actions


@dataclass(frozen=True)
class CountryParams:
    country_code: str


@dataclass(frozen=True)
class NoParams:
    pass


@dataclass(frozen=True)
class IdParams:
    wallet_id: uuid.UUID


SEEN: list[Any] = [None]
THROWN: list[Any] = [None]
STAGED_IDS: list[uuid.UUID] = []
INNER_ID: list[Any] = [None]


class CreateAction(Action[CountryParams, Wallet]):
    def perform(self, principal, params):
        SEEN[0] = "null" if principal is None else principal
        shard = {"MX": MEXICO, "AU": AUSTRALIA}.get(params.country_code, GLOBAL)
        return self.plan.add(new_wallet(shard))


class DepositAction(Action[IdParams, Wallet]):
    def perform(self, principal, params):
        w = wallet_repo.get_by_id(ShardedId[Wallet](params.wallet_id))
        return self.plan.update(w.deposit(Decimal("5")))


class CreateAndDepositAction(Action[NoParams, Wallet]):
    def perform(self, principal, params):
        return self.plan.add(new_wallet(GLOBAL).deposit(Decimal("5")))


class TouchAction(Action[IdParams, Wallet]):
    def perform(self, principal, params):
        w = wallet_repo.get_by_id(ShardedId[Wallet](params.wallet_id))
        return self.plan.update(w.copy().build())


class NothingAction(Action[NoParams, Any]):
    def perform(self, principal, params):
        return None


class ProbeEx(RuntimeError):
    pass


class ProbeSubEx(ProbeEx):
    pass


class ThrowAction(Action[str, Wallet]):
    def perform(self, principal, kind):
        ATTEMPTS[0] += 1
        self.plan.add(new_wallet(GLOBAL))
        if kind == "runtime":
            e: BaseException = RuntimeError("boom")
        elif kind == "stale":
            e = StaleRecordError("stale")
        elif kind == "probe":
            e = ProbeEx("probe")
        elif kind == "probesub":
            e = ProbeSubEx("probesub")
        elif kind == "wrapped":
            try:
                raise RuntimeError("wrapped") from StaleRecordError("inner")
            except RuntimeError as wrapped:
                e = wrapped
        elif kind == "illegalarg":
            e = ValueError("bad")
        else:
            raise AssertionError(kind)
        THROWN[0] = e
        raise e


class StaleUpdateAction(Action[IdParams, Wallet]):
    def perform(self, principal, params):
        ATTEMPTS[0] += 1
        w = wallet_repo.get_by_id(ShardedId[Wallet](params.wallet_id))
        return self.plan.update(w.copy().version(999).build())


class FreshPlanAction(Action[NoParams, Wallet]):
    def perform(self, principal, params):
        ATTEMPTS[0] += 1
        n = ATTEMPTS[0]
        w = self.plan.add(new_wallet(GLOBAL))
        STAGED_IDS.append(w.id.value)
        if n == 1:
            raise StaleRecordError("first attempt")
        return w


class TwoShardAction(Action[CountryParams, Wallet]):
    def perform(self, principal, params):
        if params.country_code == "mx-first":
            self.plan.add(new_wallet(MEXICO))
            return self.plan.add(new_wallet(GLOBAL))
        g = self.plan.add(new_wallet(GLOBAL))
        self.plan.add(new_wallet(MEXICO))
        return g


class PartialAction(Action[IdParams, Wallet]):
    def perform(self, principal, params):
        ATTEMPTS[0] += 1
        g = self.plan.add(new_wallet(GLOBAL))
        mx = wallet_repo.get_by_id(ShardedId[Wallet](params.wallet_id))
        self.plan.update(mx.copy().version(999).build())
        return g


class NullParamsAction(Action[NoParams, Wallet]):
    def perform(self, principal, params):
        return self.plan.add(new_wallet(GLOBAL))


class ScalarParamsAction(Action[str, Wallet]):
    def perform(self, principal, params):
        return self.plan.add(new_wallet(GLOBAL))


class DoubleStageAction(Action[str, Wallet]):
    def perform(self, principal, kind):
        ATTEMPTS[0] += 1
        w = new_wallet(GLOBAL)
        if kind == "add-add":
            self.plan.add(w)
            self.plan.add(w)
        elif kind == "add-update":
            self.plan.add(w)
            self.plan.update(w)
        elif kind == "update-update":
            self.plan.update(w)
            self.plan.update(w)
        return w


class OuterA:
    @dataclass(frozen=True, kw_only=True)
    class Dup(ModelEvent["Wallet"]):
        pass


class OuterB:
    @dataclass(frozen=True, kw_only=True)
    class Dup(ModelEvent["Wallet"]):
        pass

    class UnregisteredAction(Action[NoParams, Any]):
        def perform(self, principal, params):
            return None


class DupEventAction(Action[CountryParams, Wallet]):
    def perform(self, principal, params):
        w = new_wallet(GLOBAL)
        cls = OuterA.Dup if params.country_code == "a" else OuterB.Dup
        return self.plan.add(w.copy().with_event(cls(model_id=str(w.id))).build())


class ReadOwnAction(Action[NoParams, str]):
    def perform(self, principal, params):
        w = self.plan.add(new_wallet(GLOBAL))
        return fmt(wallet_repo.find_by_id(w.id) is not None)


class OuterAction(Action[NoParams, Wallet]):
    def perform(self, principal, params):
        mine = self.plan.add(new_wallet(GLOBAL))
        inner = executor.execute(CreateAction, CountryParams("DE"), principal=principal)
        INNER_ID[0] = inner.id
        p("s29.outer_plan_still_has_its_add", len(self.plan.additions(Wallet)))
        raise RuntimeError(f"outer fails after inner {mine.id}")


class PlanProbeAction(Action[NoParams, str]):
    def expose_plan(self):
        return self.plan

    def perform(self, principal, params):
        out = ["none"]

        def body():
            try:
                self.plan  # noqa: B018
                out[0] = "ok"
            except Exception as e:  # noqa: BLE001
                out[0] = type(e).__name__

        t = threading.Thread(target=body)
        t.start()
        t.join()
        return out[0]


LATCH = {"count": 0, "event": threading.Event(), "lock": threading.Lock()}


class LatchDepositAction(Action[IdParams, Wallet]):
    def perform(self, principal, params):
        with LATCH["lock"]:
            ATTEMPTS[0] += 1
        w = wallet_repo.get_by_id(ShardedId[Wallet](params.wallet_id))
        with LATCH["lock"]:
            LATCH["count"] += 1
            if LATCH["count"] >= 2:
                LATCH["event"].set()
        LATCH["event"].wait(10)
        return self.plan.update(w.deposit(Decimal("1")))


class ReAddAction(Action[IdParams, Wallet]):
    def perform(self, principal, params):
        ATTEMPTS[0] += 1
        w = wallet_repo.get_by_id(ShardedId[Wallet](params.wallet_id))
        return self.plan.add(w.copy().version(1).build())


class UpdateThenAddAction(Action[IdParams, Wallet]):
    def perform(self, principal, params):
        w = wallet_repo.get_by_id(ShardedId[Wallet](params.wallet_id))
        self.plan.update(w.deposit(Decimal("1")))
        return self.plan.add(new_wallet(GLOBAL))


class ThreeAddsAction(Action[NoParams, list]):
    def perform(self, principal, params):
        return [str(self.plan.add(new_wallet(GLOBAL)).id.value) for _ in range(3)]


class SpyPersister:
    """Wraps the default persister; records shard order and what is visible mid-transaction."""

    def __init__(self, inner):
        self.inner = inner
        self.order: list[str] = []
        self.fail = False
        self.during_other = "?"
        self.during_tx = "?"

    def persist_action_events(self, namespace, action_name, started_date, completion_date,
                              action_params, model_events, shard, action_event_id):
        self.order.append(sh(shard))
        # A connection of its own, from an engine outside the framework: another client.
        with engines[shard].connect() as other:
            self.during_other = str(other.execute(text("select count(*) from wallets")).scalar_one())
        tx = registry.manager(shard).current()
        self.during_tx = str(tx.execute(text("select count(*) from wallets")).scalar_one())
        self.inner.persist_action_events(namespace, action_name, started_date, completion_date,
                                         action_params, model_events, shard, action_event_id)
        if self.fail:
            raise RuntimeError("persister fails")


# ---------------------------------------------------------------------- helpers


def scalar(shard: ShardIdentifier, sql: str) -> Any:
    with engines[shard].connect() as c:
        return c.execute(text(sql)).scalar_one()


def wallet_repository(s: ShardIdentifier) -> int:
    return scalar(s, "select count(*) from wallets")


def outbox(s: ShardIdentifier) -> int:
    return scalar(s, "select count(*) from eventlog.events")


def truncate() -> None:
    for s in (GLOBAL, MEXICO):
        with engines[s].begin() as c:
            c.execute(text("truncate table wallets, eventlog.events"))


def truncate_events(s: ShardIdentifier) -> None:
    with engines[s].begin() as c:
        c.execute(text("truncate table eventlog.events"))


ROWS_SQL = """
select id::text as id, namespace, action_id::text as action_id, action_name,
 action_params::text as params, started_date::text as started,
 completion_date::text as completed, model_id, model_type, event_type,
 case when payload is null then null when jsonb_typeof(payload) = 'object' then (payload - 'modelId' - 'ownerId' - 'owner_id')::text else 'JSON ' || jsonb_typeof(payload) end as payload, (payload is null) as payload_sql_null, event_date::text as evdate,
 delivered, substring(id::text, 15, 1) as idv, substring(action_id::text, 15, 1) as aidv,
 (started_date <= completion_date) as started_le,
 (event_date = completion_date) as ev_eq,
 abs(extract(epoch from (started_date - (now() at time zone 'utc')))) < 30 as utc_close
 from eventlog.events order by ctid
"""


def rows(s: ShardIdentifier) -> list[dict[str, Any]]:
    with engines[s].connect() as c:
        return [dict(r._mapping) for r in c.execute(text(ROWS_SQL))]


def describe_one(prefix: str, r: dict[str, Any]) -> None:
    p(prefix + ".namespace", r["namespace"])
    p(prefix + ".action_name", r["action_name"])
    p(prefix + ".action_params", r["params"])
    p(prefix + ".model_type", r["model_type"])
    p(prefix + ".event_type", r["event_type"])
    p(prefix + ".payload_minus_ids", r["payload"])
    p(prefix + ".delivered", r["delivered"])
    p(prefix + ".id_uuid_version", r["idv"])
    p(prefix + ".action_id_uuid_version", r["aidv"])
    p(prefix + ".started_le_completion", r["started_le"])
    p(prefix + ".event_date_eq_completion", r["ev_eq"])
    p(prefix + ".started_is_utc_now", r["utc_close"])
    p(prefix + ".model_id_null", r["model_id"] is None)
    p(prefix + ".payload_sql_null", r["payload_sql_null"])


def err(t: BaseException | None) -> str:
    return "none" if t is None else type(t).__name__


def catching(fn) -> BaseException | None:
    try:
        fn()
        return None
    except BaseException as t:  # noqa: BLE001
        return t


def msg(t: BaseException | None) -> str:
    return "passed" if t is None else f"{type(t).__name__} / {t}"


def build_executor(actions, *, outbox_persister=None, exec_clock=None, namespace="parity.actions",
                   repos=None):
    return ActionExecutor(
        namespace=namespace,
        registry=registry,
        repositories=repos if repos is not None else [wallet_repo],
        actions=actions,
        outbox=outbox_persister or SingleTableJsonEventPersister(registry, EVENTS),
        clock=exec_clock or clock,
    )


# ---------------------------------------------------------------------- the probe


def probe() -> None:
    global registry, wallet_repo, executor
    alice = "alice"

    actions = ActionRegistry([
        CreateAction(clock), DepositAction(clock), CreateAndDepositAction(clock),
        TouchAction(clock), NothingAction(clock), ThrowAction(clock), StaleUpdateAction(clock),
        FreshPlanAction(clock), TwoShardAction(clock), PartialAction(clock),
        NullParamsAction(clock), ScalarParamsAction(clock), DoubleStageAction(clock),
        DupEventAction(clock), ReadOwnAction(clock), OuterAction(clock), PlanProbeAction(clock),
        LatchDepositAction(clock), ReAddAction(clock), UpdateThenAddAction(clock),
        ThreeAddsAction(clock),
    ])
    executor = build_executor(actions)
    no_retry = ExecutionConfiguration.no_retry()
    cross = ExecutionConfiguration(allow_cross_shard=True)

    # S1 add
    truncate()
    created = executor.execute(CreateAction, CountryParams("DE"), principal=alice)
    p("s1.result_version", created.version)
    p("s1.result_events_attached", len(created.events))
    p("s1.wallets_global", wallet_repository(GLOBAL))
    p("s1.wallets_mexico", wallet_repository(MEXICO))
    p("s1.outbox_global", outbox(GLOBAL))
    p("s1.outbox_mexico", outbox(MEXICO))
    r1 = rows(GLOBAL)[0]
    describe_one("s1.row", r1)
    p("s1.row.model_id_is_wallet_id", str(created.id.value) == r1["model_id"])
    p("s1.principal_seen", SEEN[0])
    p("s1.db_version", scalar(GLOBAL, "select version from wallets"))
    executor.execute(CreateAction, CountryParams("DE"), principal=None)
    p("s40.null_principal_seen", SEEN[0])
    p("s40.outbox_has_principal_column", scalar(
        GLOBAL,
        "select count(*) from information_schema.columns where table_schema='eventlog'"
        " and table_name='events' and column_name like '%principal%'"))

    # S2 update
    truncate()
    w2 = executor.execute(CreateAction, CountryParams("DE"), principal=alice)
    before_updated = scalar(GLOBAL, "select updated_date::text from wallets")
    truncate_events(GLOBAL)
    dep = executor.execute(DepositAction, IdParams(w2.id.value), principal=alice)
    p("s2.result_version", dep.version)
    with engines[GLOBAL].connect() as c:
        wr = c.execute(text("select version, balance::text, updated_date::text from wallets")).one()
    p("s2.db_version", wr[0])
    p("s2.db_balance", wr[1])
    p("s2.updated_date_unchanged", before_updated == wr[2])
    r2 = rows(GLOBAL)
    p("s2.outbox_rows", len(r2))
    p("s2.event_type", r2[0]["event_type"])
    p("s2.payload_minus_ids", r2[0]["payload"])
    p("s2.action_params", r2[0]["params"])

    # S3 create + deposit composed before staging
    truncate()
    executor.execute(CreateAndDepositAction, NoParams(), principal=alice)
    r3 = rows(GLOBAL)
    p("s3.event_types_in_insert_order", [r["event_type"] for r in r3])
    p("s3.db_version", scalar(GLOBAL, "select version from wallets"))
    p("s3.same_action_id", len({r["action_id"] for r in r3}) == 1)

    # S4 update with no new event
    truncate()
    w4 = executor.execute(CreateAction, CountryParams("DE"), principal=alice)
    truncate_events(GLOBAL)
    t4 = executor.execute(TouchAction, IdParams(w4.id.value), principal=alice)
    r4 = rows(GLOBAL)
    p("s4.outbox_rows", len(r4))
    p("s4.event_type", r4[0]["event_type"])
    p("s4.payload", r4[0]["payload"])
    p("s4.payload_sql_null", r4[0]["payload_sql_null"])
    p("s4.model_type", r4[0]["model_type"])
    p("s4.result_version", t4.version)
    p("s4.db_version", scalar(GLOBAL, "select version from wallets"))

    # S5 empty plan
    truncate()
    res5 = executor.execute(NothingAction, NoParams(), principal=alice)
    p("s5.result", res5)
    p("s5.outbox_global", outbox(GLOBAL))
    p("s5.outbox_mexico", outbox(MEXICO))
    describe_one("s5.row", rows(GLOBAL)[0])

    # S6 perform throws
    truncate()
    ATTEMPTS[0] = 0
    e6 = catching(lambda: executor.execute(ThrowAction, "runtime", principal=alice))
    p("s6.caller_exception", err(e6))
    p("s6.same_instance", e6 is THROWN[0])
    p("s6.attempts", ATTEMPTS[0])
    p("s6.wallets", wallet_repository(GLOBAL))
    p("s6.outbox", outbox(GLOBAL))

    # S7 perform throws StaleRecord directly
    ATTEMPTS[0] = 0
    e7 = catching(lambda: executor.execute(ThrowAction, "stale", principal=alice))
    p("s7.caller_exception", err(e7))
    p("s7.attempts", ATTEMPTS[0])

    # S8 optimistic-lock conflict in persist, default retry
    truncate()
    w8 = executor.execute(CreateAction, CountryParams("DE"), principal=alice)
    truncate_events(GLOBAL)
    ATTEMPTS[0] = 0
    t0 = time.monotonic()
    e8 = catching(lambda: executor.execute(StaleUpdateAction, IdParams(w8.id.value), principal=alice))
    ms = (time.monotonic() - t0) * 1000
    p("s8.caller_exception", err(e8))
    p("s8.attempts", ATTEMPTS[0])
    p("s8.elapsed_ge_100ms", ms >= 100)
    p("s8.elapsed_lt_1000ms", ms < 1000)
    p("s8.outbox", outbox(GLOBAL))
    p("s8.db_version", scalar(GLOBAL, "select version from wallets"))

    # S9 noRetry
    ATTEMPTS[0] = 0
    e9 = catching(lambda: executor.execute(StaleUpdateAction, IdParams(w8.id.value),
                                           principal=alice, config=no_retry))
    p("s9.caller_exception", err(e9))
    p("s9.attempts", ATTEMPTS[0])

    # S10 with_retry replaces the default for the same class
    ATTEMPTS[0] = 0
    three = (ExecutionConfiguration.builder()
             .with_retry(RetryConfig(on=StaleRecordError, max_retries=3, delay=0)).build())
    catching(lambda: executor.execute(StaleUpdateAction, IdParams(w8.id.value),
                                      principal=alice, config=three))
    p("s10.attempts", ATTEMPTS[0])

    # S11 exact class match
    probe_cfg = (ExecutionConfiguration.builder()
                 .with_retry(RetryConfig(on=ProbeEx, max_retries=2, delay=0)).build())
    ATTEMPTS[0] = 0
    catching(lambda: executor.execute(ThrowAction, "probe", principal=alice, config=probe_cfg))
    p("s11.exact_attempts", ATTEMPTS[0])
    ATTEMPTS[0] = 0
    catching(lambda: executor.execute(ThrowAction, "probesub", principal=alice, config=probe_cfg))
    p("s11.subclass_attempts", ATTEMPTS[0])
    ATTEMPTS[0] = 0
    catching(lambda: executor.execute(ThrowAction, "stale", principal=alice, config=probe_cfg))
    p("s11.default_dropped_when_other_added", ATTEMPTS[0])

    # S12 cause chain
    ATTEMPTS[0] = 0
    e12 = catching(lambda: executor.execute(ThrowAction, "wrapped", principal=alice))
    p("s12.attempts", ATTEMPTS[0])
    p("s12.caller_exception", err(e12))

    # S13 fresh plan per retry
    truncate()
    ATTEMPTS[0] = 0
    STAGED_IDS.clear()
    w13 = executor.execute(FreshPlanAction, NoParams(), principal=alice)
    p("s13.attempts", ATTEMPTS[0])
    p("s13.wallets", wallet_repository(GLOBAL))
    p("s13.result_is_second_attempt", w13.id.value == STAGED_IDS[1])
    p("s13.outbox", outbox(GLOBAL))

    # S14 cross-shard rejected
    truncate()
    e14 = catching(lambda: executor.execute(TwoShardAction, CountryParams("mx-first"), principal=alice))
    p("s14.caller_exception", err(e14))
    if isinstance(e14, CrossShardError):
        p("s14.active_shard", sh(e14.active_shard))
        p("s14.requested_shard", sh(e14.requested_shard))
    p("s14.wallets_global", wallet_repository(GLOBAL))
    p("s14.wallets_mexico", wallet_repository(MEXICO))
    p("s14.outbox_global", outbox(GLOBAL))
    p("s14.outbox_mexico", outbox(MEXICO))

    # S15 cross-shard allowed, with a spy persister to see commit order
    truncate()
    spy = SpyPersister(SingleTableJsonEventPersister(registry, EVENTS))
    spy_exec = build_executor(actions, outbox_persister=spy)
    spy_exec.execute(TwoShardAction, CountryParams("mx-first"), principal=alice, config=cross)
    p("s15.commit_order", spy.order)
    p("s15.wallets_global", wallet_repository(GLOBAL))
    p("s15.wallets_mexico", wallet_repository(MEXICO))
    p("s15.outbox_global", outbox(GLOBAL))
    p("s15.outbox_mexico", outbox(MEXICO))
    g15 = rows(GLOBAL)[0]
    m15 = rows(MEXICO)[0]
    p("s15.same_action_id", g15["action_id"] == m15["action_id"])
    p("s15.same_started_date", g15["started"] == m15["started"])
    gw = scalar(GLOBAL, "select id::text from wallets")
    mw = scalar(MEXICO, "select id::text from wallets")
    p("s15.global_row_is_global_wallet", gw == g15["model_id"])
    p("s15.mexico_row_is_mexico_wallet", mw == m15["model_id"])

    # S16 partial commit, no retry
    truncate()
    mx16 = executor.execute(CreateAction, CountryParams("MX"), principal=alice)
    insert_mx = ("insert into wallets (id, version, state, owner_id, currency, balance,"
                 " created_date, updated_date) values (cast(:id as uuid), 1, 'OPENED',"
                 " gen_random_uuid(), 'MXN', 1, now(), now())")
    truncate()
    with engines[MEXICO].begin() as c:
        c.execute(text(insert_mx), {"id": str(mx16.id.value)})
    ATTEMPTS[0] = 0
    no_retry_cross = ExecutionConfiguration.no_retry(allow_cross_shard=True)
    e16 = catching(lambda: executor.execute(PartialAction, IdParams(mx16.id.value),
                                            principal=alice, config=no_retry_cross))
    p("s16.caller_exception", err(e16))
    p("s16.attempts", ATTEMPTS[0])
    p("s16.wallets_global", wallet_repository(GLOBAL))
    p("s16.wallets_mexico", wallet_repository(MEXICO))
    p("s16.outbox_global", outbox(GLOBAL))
    p("s16.outbox_mexico", outbox(MEXICO))

    # S17 partial commit, default retry on
    truncate()
    with engines[MEXICO].begin() as c:
        c.execute(text(insert_mx), {"id": str(mx16.id.value)})
    ATTEMPTS[0] = 0
    e17 = catching(lambda: executor.execute(PartialAction, IdParams(mx16.id.value),
                                            principal=alice, config=cross))
    p("s17.caller_exception", err(e17))
    p("s17.attempts", ATTEMPTS[0])
    p("s17.wallets_global", wallet_repository(GLOBAL))
    p("s17.outbox_global", outbox(GLOBAL))

    # S18 unknown shard falls back to default
    truncate()
    executor.execute(CreateAction, CountryParams("AU"), principal=alice)
    p("s18.wallets_global", wallet_repository(GLOBAL))
    p("s18.outbox_global", outbox(GLOBAL))
    p("s18.outbox_mexico", outbox(MEXICO))

    # S19 unregistered action
    e19 = catching(lambda: executor.execute(OuterB.UnregisteredAction, NoParams(), principal=alice))
    p("s19.caller_exception", err(e19))

    # S20 null params
    truncate()
    e20 = catching(lambda: executor.execute(NullParamsAction, None, principal=alice))
    p("s20.caller_exception", err(e20))
    p("s20.wallets", wallet_repository(GLOBAL))
    p("s20.outbox", outbox(GLOBAL))
    if outbox(GLOBAL):
        p("s20.stored_action_params", rows(GLOBAL)[0]["params"])

    # S21 scalar params
    truncate()
    e21 = catching(lambda: executor.execute(ScalarParamsAction, "MX", principal=alice))
    p("s21.caller_exception", err(e21))
    p("s21.wallets", wallet_repository(GLOBAL))

    # S23-25 staging one row twice
    for kind in ("add-add", "add-update", "update-update"):
        truncate()
        ATTEMPTS[0] = 0
        e = catching(lambda kind=kind: executor.execute(DoubleStageAction, kind, principal=alice))
        p(f"s23.{kind}.caller_exception", err(e))
        p(f"s23.{kind}.attempts", ATTEMPTS[0])
        p(f"s23.{kind}.wallets", wallet_repository(GLOBAL))

    # S26 event simple-name collision
    truncate()
    e26a = catching(lambda: executor.execute(DupEventAction, CountryParams("a"), principal=alice))
    e26b = catching(lambda: executor.execute(DupEventAction, CountryParams("b"), principal=alice))
    p("s26.first", err(e26a))
    p("s26.second", err(e26b))
    p("s26.wallets", wallet_repository(GLOBAL))
    e26c = catching(lambda: executor.execute(DupEventAction, CountryParams("a"), principal=alice))
    p("s26.first_again", err(e26c))

    # S27 transaction visibility
    truncate()
    spy.order.clear()
    spy_exec.execute(CreateAction, CountryParams("DE"), principal=alice)
    p("s27.during_other_connection_sees_wallet", spy.during_other)
    p("s27.during_in_transaction_sees_wallet", spy.during_tx)
    p("s27.after_other_connection_sees_wallet", wallet_repository(GLOBAL))
    truncate()
    spy.fail = True
    e27 = catching(lambda: spy_exec.execute(CreateAction, CountryParams("DE"), principal=alice))
    spy.fail = False
    p("s27.persister_failure_exception", err(e27))
    p("s27.persister_failure_wallets", wallet_repository(GLOBAL))
    p("s27.persister_failure_outbox", outbox(GLOBAL))

    # S28 a read inside perform does not see the staged add
    truncate()
    p("s28.read_sees_staged", executor.execute(ReadOwnAction, NoParams(), principal=alice))

    # S29 nested execute
    truncate()
    e29 = catching(lambda: executor.execute(OuterAction, NoParams(), principal=alice))
    p("s29.outer_exception", err(e29))
    p("s29.wallets", wallet_repository(GLOBAL))
    p("s29.inner_wallet_committed", wallet_repo.find_by_id(INNER_ID[0]) is not None)
    p("s29.outbox_rows", outbox(GLOBAL))

    # S30 plan outside execution and on another thread
    p("s30.plan_outside_execute", err(catching(lambda: PlanProbeAction(clock).expose_plan())))
    p("s30.plan_on_new_thread", executor.execute(PlanProbeAction, NoParams(), principal=alice))

    # S31 concurrency: two deposits on one wallet
    truncate()
    w31 = executor.execute(CreateAction, CountryParams("DE"), principal=alice)
    ATTEMPTS[0] = 0
    errs: list[str] = []

    def deposit():
        try:
            executor.execute(LatchDepositAction, IdParams(w31.id.value), principal=alice)
        except Exception as e:  # noqa: BLE001
            errs.append(err(e))

    threads = [threading.Thread(target=deposit) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    p("s31.errors", errs)
    p("s31.attempts", ATTEMPTS[0])
    p("s31.final_balance", scalar(GLOBAL, "select balance::text from wallets"))
    p("s31.final_version", scalar(GLOBAL, "select version from wallets"))

    # S32 fixed clock
    truncate()
    fixed = VirtualClock(datetime(2026, 1, 1, 10, 0, 0, 123456, tzinfo=UTC))
    fixed_exec = build_executor(ActionRegistry([NothingAction(fixed)]), exec_clock=fixed)
    fixed_exec.execute(NothingAction, NoParams(), principal=alice)
    r32 = rows(GLOBAL)[0]
    p("s32.started_date", r32["started"])
    p("s32.completion_date", r32["completed"])
    p("s32.event_date", r32["evdate"])
    e32 = catching(lambda: build_executor(ActionRegistry([NothingAction(SystemClock())]),
                                          exec_clock=fixed))
    p("s32.action_clock_differs_from_executor_clock", err(e32))

    # S33 duplicate primary key
    truncate()
    w33 = executor.execute(CreateAction, CountryParams("DE"), principal=alice)
    ATTEMPTS[0] = 0
    e33 = catching(lambda: executor.execute(ReAddAction, IdParams(w33.id.value), principal=alice))
    p("s33.caller_exception", err(e33))
    p("s33.attempts", ATTEMPTS[0])

    # S34 namespace validation
    candidates = [None, "", " ", "my-service", "a.b_c", "_x.y1", "1abc", "a..b", "a.", "abc\n",
                  "café"]
    for i, ns in enumerate(candidates):
        e = catching(lambda ns=ns: build_executor(actions, namespace=ns))
        p(f"s34.ns{i}", "accepted" if e is None else "refused")

    # S35 RetryConfig validation
    p("s35.negative_max_retries", err(catching(lambda: RetryConfig(max_retries=-1, delay=0))))
    p("s35.negative_delay", err(catching(lambda: RetryConfig(max_retries=1, delay=-0.001))))
    p("s35.zero_retries", err(catching(lambda: RetryConfig(max_retries=0, delay=0))))

    # S36 two instances of one action type
    e36 = catching(lambda: ActionRegistry.builder()
                   .with_action(CreateAction(clock)).with_action(CreateAction(clock)).build())
    p("s36.duplicate_registration", err(e36))

    # S37 executor builder without an event persister
    e37 = catching(lambda: ActionExecutor.builder().namespace("parity.actions")
                   .database_registry(registry)
                   .repository_registry(RepositoryRegistry([wallet_repo]))
                   .action_registry(actions).clock(clock).build())
    p("s37.no_event_persister", err(e37))

    # S38 no repository for a staged type
    e38a = catching(lambda: build_executor(actions, repos=RepositoryRegistry([])))
    p("s38.empty_repository_registry", err(e38a))
    other_repo = NotificationRepository(Notification, NOTIFICATIONS, registry, clock=clock)
    other_exec = build_executor(actions, repos=[other_repo])
    truncate()
    e38 = catching(lambda: other_exec.execute(CreateAction, CountryParams("DE"), principal=alice))
    p("s38.unknown_type_exception", err(e38))
    p("s38.outbox", outbox(GLOBAL))

    # S42 grouping order: update staged before add
    truncate()
    w42 = executor.execute(CreateAction, CountryParams("DE"), principal=alice)
    truncate_events(GLOBAL)
    executor.execute(UpdateThenAddAction, IdParams(w42.id.value), principal=alice)
    p("s42.event_types_in_insert_order", [r["event_type"] for r in rows(GLOBAL)])
    truncate()
    ids = executor.execute(ThreeAddsAction, NoParams(), principal=alice)
    got = [r["model_id"] for r in rows(GLOBAL)]
    p("s42.three_adds_insert_order_is_staging_order", got == ids)

    # S43 execute inside a transaction the caller opened, which then rolls back
    truncate()

    def outer_tx():
        with registry.manager(GLOBAL).transaction():
            executor.execute(CreateAction, CountryParams("DE"), principal=alice)
            raise RuntimeError("caller rolls back")

    e43 = catching(outer_tx)
    p("s43.caller_exception", err(e43))
    p("s43.wallets_after_outer_rollback", wallet_repository(GLOBAL))
    p("s43.outbox_after_outer_rollback", outbox(GLOBAL))

    # S39 ActionSpec
    truncate()
    spec = ActionSpec.of(CreateAction(clock)).with_principal(alice)
    a = spec.execute(CountryParams("DE"))
    p("s39.pass_chain", err(catching(lambda: a.assert_added(Wallet, 1)
                                      .assert_emitted(WalletCreatedEvent)
                                      .assert_no_updates()
                                      .assert_total_additions(1)
                                      .assert_not_emitted(WalletMoneyDepositedEvent))))
    p("s39.added_count_wrong", msg(catching(lambda: a.assert_added(Wallet, 2))))
    p("s39.emitted_missing", msg(catching(lambda: a.assert_emitted(WalletMoneyDepositedEvent))))
    p("s39.updated_one_missing", msg(catching(lambda: a.assert_updated(Wallet, lambda w: None))))
    p("s39.no_changes", msg(catching(a.assert_no_changes)))
    p("s39.total_additions_wrong", msg(catching(lambda: a.assert_total_additions(3))))
    p("s39.emitted_base_class_count", msg(catching(lambda: a.assert_emitted(ModelEvent, 1))))
    p("s39.added_base_class", msg(catching(lambda: a.assert_added(Model, 1))))
    p("s39.no_events", msg(catching(a.assert_no_events)))
    p("s39.not_emitted_present", msg(catching(lambda: a.assert_not_emitted(WalletCreatedEvent))))

    def failing_verifier(w):
        raise AssertionError("verifier says no")

    p("s39.verifier_failure", err(catching(lambda: a.assert_added(Wallet, failing_verifier))))

    def result_verifier(w):
        if w.version != 1:
            raise AssertionError("bad")

    p("s39.result_verifier_gets_result", err(catching(lambda: a.assert_result(result_verifier))))
    p("s39.nothing_persisted", wallet_repository(GLOBAL))
    p("s39.principal_seen", SEEN[0])
    throw_spec = ActionSpec.of(ThrowAction(clock))
    p("s39.throws_subclass_ok", err(catching(lambda: throw_spec.assert_throws(Exception, "illegalarg"))))
    p("s39.throws_wrong_type", msg(catching(lambda: throw_spec.assert_throws(LookupError, "illegalarg"))))
    p("s39.throws_nothing", msg(catching(lambda: ActionSpec.of(CreateAction(clock))
                                         .assert_throws(Exception, CountryParams("DE")))))

    def throws_verifier(e):
        raise AssertionError("verifier ran")

    p("s39.throws_verifier_runs", err(catching(
        lambda: throw_spec.assert_throws(ValueError, "illegalarg", throws_verifier))))
    p("s39.plan_outside", err(catching(lambda: PlanProbeAction(clock).expose_plan())))
    # Python only (Dracula 74): a verifier returning a falsy value fails.
    p("s39.py_only.verifier_returning_zero", err(catching(lambda: a.assert_result(lambda w: 0))))


def main() -> None:
    global registry, wallet_repo
    containers = [PostgresContainer("postgres:latest", driver="psycopg") for _ in range(2)]
    try:
        for c in containers:
            c.start()
        urls = {GLOBAL: containers[0].get_connection_url(), MEXICO: containers[1].get_connection_url()}
        for shard, url in urls.items():
            engines[shard] = create_engine(url)
            with engines[shard].begin() as conn:
                conn.exec_driver_sql(DDL)
        groups = [
            ShardGroupConfig.builder().group(s.group).name(f"group-{s.group}").with_member(
                ShardMemberConfig.builder().member(s.member).name(f"db{s.group}")
                .primary_config(data_source(url, pool_size=10)).build()
            ).build()
            for s, url in urls.items()
        ]
        config = ShardingConfig.builder().default_shard(GLOBAL).groups(groups).build()
        registry = DatabaseRegistry.from_config(config)
        wallet_repo = WalletRepository(
            Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
        )
        probe()
        registry.close()
        for e in engines.values():
            e.dispose()
    finally:
        for c in containers:
            c.stop()


if __name__ == "__main__":
    main()
