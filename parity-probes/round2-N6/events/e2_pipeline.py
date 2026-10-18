"""N6-C e2: the events docs' claims about the outbox row, fan-out and dispatch, run on SQLite.

    uv run --no-sync python parity-probes/round2-N6/events/e2_pipeline.py
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))

from sqlalchemy import event as sa_event, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

from backends import start  # noqa: E402
from ekbatan.action import ActionExecutor, ActionRegistry  # noqa: E402
from ekbatan.clock import VirtualClock  # noqa: E402
from ekbatan.config import ShardGroupConfig, ShardingConfig, ShardMemberConfig  # noqa: E402
from ekbatan.events import (  # noqa: E402
    EventDispatch, EventFanout, EventHandlerRegistry, EventHandlingJob, HandlingConfig,
    LocalEventHandlerConfig, EventFanoutJob,
)
from ekbatan.ids import ShardedId  # noqa: E402
from ekbatan.persistence import DatabaseRegistry  # noqa: E402
from ekbatan.repository import RepositoryRegistry  # noqa: E402
from ekbatan.sharding import ShardIdentifier  # noqa: E402
from fixtures.actions import (  # noqa: E402
    CreateWalletAction, CreateWalletParams, DepositMoneyAction, DepositParams, NotifyAction,
    NotifyParams,
)
from fixtures.domain import Wallet, WalletCreatedEvent, WalletMoneyDepositedEvent  # noqa: E402
from fixtures.logins import data_source  # noqa: E402
from fixtures.repositories import NotificationRepository, WalletRepository  # noqa: E402
from fixtures.schema import EVENT_NOTIFICATIONS, EVENTS, NOTIFICATIONS, WALLETS, metadata  # noqa: E402

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
S0, S1 = ShardIdentifier(0, 0), ShardIdentifier(1, 0)


def p(k, v):
    print(f"PROBE {k}: {v}", flush=True)


backend = start("sqlite", Path(tempfile.mkdtemp()))


def registry_for(names):
    groups = []
    for shard, name in names.items():
        backend.prepare(name, metadata)
        backend.truncate(name, metadata)
        groups.append(
            ShardGroupConfig.builder().group(shard.group).name(f"g{shard.group}")
            .with_member(ShardMemberConfig.builder().member(0).name(name)
                         .primary_config(data_source(backend.url(name), pool_size=5)).build())
            .build())
    return DatabaseRegistry.from_config(
        ShardingConfig.builder().default_shard(next(iter(names))).groups(groups).build())


reg = registry_for({S0: "n6_main"})
clock = VirtualClock(T0)
wallets = WalletRepository(Wallet, WALLETS, reg, clock=clock)
notes = NotificationRepository(__import__("fixtures.domain", fromlist=["Notification"]).Notification,
                               NOTIFICATIONS, reg, clock=clock)


def make_executor(with_events: bool):
    b = (ActionExecutor.builder().namespace("example.wallet").database_registry(reg)
         .repository_registry(RepositoryRegistry.builder().with_model_repository(wallets)
                              .with_entity_repository(notes).build())
         .action_registry(ActionRegistry.builder().with_action(CreateWalletAction(clock))
                          .with_action(DepositMoneyAction(wallets, clock))
                          .with_action(NotifyAction(clock)).build())
         .clock(clock))
    if with_events:
        from ekbatan.action import SingleTableJsonEventPersister
        b = b.event_persister(SingleTableJsonEventPersister(reg, EVENTS))
    return b.build()


def raw(sql):
    with reg.manager(S0).primary.connect() if hasattr(reg.manager(S0), "primary") else None as c:
        return c.execute(text(sql)).all()


def q(sql):
    eng = reg.engine(S0) if hasattr(reg, "engine") else None
    with reg.manager(S0).transaction() as tx:
        return tx.execute(text(sql)).all()


# --- outbox row (outbox.md:81 default persister, :103-107 JSON; model-event.mdx serialisation) ---
ex = make_executor(with_events=False)  # outbox.md:81 - no events= at all
wid = ShardedId[Wallet].random()
ex.execute(CreateWalletAction, CreateWalletParams(owner_id=uuid4(), currency="EUR",
                                                  initial_balance=Decimal("100.00"), wallet_id=wid))
clock.advance(timedelta(seconds=5)) if hasattr(clock, "advance") else None
ex.execute(DepositMoneyAction, DepositParams(wallet_id=wid, amount=Decimal("10.50"), recipient="a@b"))
ex.execute(NotifyAction, NotifyParams(wallet_id=wid, kind="k", recipient="r"))
rows = q("SELECT action_name, event_type, payload, action_params, delivered, id, event_date FROM events ORDER BY event_date, action_name")
for r in rows:
    p(f"outbox.row.{r[0]}", f"type={r[1]} payload={r[2]} params={r[3]} delivered={r[4]} id={r[5]!r}")
dep = [r for r in rows if r[0] == "DepositMoneyAction"][0]
p("outbox.default_persister_without_events_kw", "wrote rows" if rows else "no rows")
p("outbox.payload_starts_modelId_modelName", str(dep[2]).startswith('{"modelId":') and '"modelName":"Wallet"' in str(dep[2]))
p("outbox.payload_camel_newBalance", '"newBalance"' in str(dep[2]))
sentinel = [r for r in rows if r[0] == "NotifyAction"][0]
p("outbox.sentinel_payload_is_sql_null", sentinel[2] is None)
p("outbox.sqlite_uuid_text", f"{dep[5]!r} len={len(str(dep[5]))}")

# --- decimals/instant text (model-event.mdx) ---
from ekbatan._json import dumps, loads, decode  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from ekbatan.domain import ModelEvent  # noqa: E402


@dataclass(frozen=True, kw_only=True)
class TsEvent(ModelEvent["Wallet"]):
    amount: Decimal
    at: datetime


ev = TsEvent(model_id="m1", amount=Decimal("1E+3"), at=datetime(2025, 1, 1, 0, 0, 0, 120000, tzinfo=UTC))
txt = dumps(ev)
p("json.decimal_and_instant", txt)
back = decode(TsEvent, loads(txt))
p("json.decimal_back", f"{back.amount!r} {back.at!r}")
for bad in ("", None):
    try:
        TsEvent(model_id=bad, amount=Decimal(1), at=T0)
        p(f"modelevent.model_id={bad!r}", "accepted")
    except Exception as e:
        p(f"modelevent.model_id={bad!r}", f"refused {type(e).__name__}")

# --- fan-out (local-event-handler.md:219-239) ---
CUR = MAX = 0
LK = threading.Lock()


class H:
    def __init__(self, name, mode="ok", aliases=()):
        self.name, self.mode, self.aliases = name, mode, aliases
        self.event_type = WalletMoneyDepositedEvent
        self.seen = []

    def handle(self, env):
        global CUR, MAX
        self.seen.append(env)
        with LK:
            CUR += 1; MAX = max(MAX, CUR)
        time.sleep(0.3)
        with LK:
            CUR -= 1
        if self.mode == "fail":
            raise RuntimeError("boom")


ok, bad = H("ok-handler"), H("bad-handler", "fail")
handlers = EventHandlerRegistry([ok, bad])
clock.pause_at(T0 + timedelta(hours=1))  # fan-out runs an hour after the event
fan = EventFanout(reg, handlers, events=EVENTS, notifications=EVENT_NOTIFICATIONS, clock=clock)
n = fan.drain_until_idle()
p("fanout.events_fanned", n)
for r in q("SELECT handler_name, state, attempts, next_retry_at, event_date, created_date FROM event_notifications ORDER BY handler_name"):
    p(f"fanout.row.{r[0]}", f"state={r[1]} attempts={r[2]} next_retry_at={r[3]} event_date={r[4]} created={r[5]}")
for r in q("SELECT action_name, delivered FROM events ORDER BY action_name"):
    p(f"fanout.delivered.{r[0]}", r[1])

# --- dispatch (local-event-handler.md:247-303, 331; event-notifications.md:110) ---
stmts = []


@sa_event.listens_for(Engine, "before_cursor_execute")
def _cap(conn, cursor, statement, params, context, executemany):
    if statement.lstrip().upper().startswith("UPDATE EVENT_NOTIFICATIONS"):
        stmts.append((statement.split("SET")[1].split("WHERE")[0].strip()[:80], params))


retention = timedelta(minutes=4)
disp = EventDispatch(reg, handlers, notifications=EVENT_NOTIFICATIONS, clock=clock,
                     retention=retention)
# Dispatch "now" = event_date + 1h: past a 4-minute retention -> pre-flight expiry for both.
# Put the clock back to just after the event to see real deliveries.
def D(v):
    return v if isinstance(v, datetime) and v.tzinfo else (v.replace(tzinfo=UTC) if isinstance(v, datetime) else datetime.fromisoformat(v).replace(tzinfo=UTC))
dep_date = D([r for r in rows if r[0] == "DepositMoneyAction"][0][6])
clock.pause_at(dep_date + timedelta(seconds=1))
disp.drain_once()
p("dispatch.max_concurrent_handlers_one_event", MAX)
p("dispatch.envelope_tz", f"event_date={ok.seen[0].event_date!r} started={ok.seen[0].started_date!r}")
p("dispatch.envelope_amount_type", type(ok.seen[0].event.amount).__name__)
p("dispatch.envelope_params", ok.seen[0].action_params)
for r in q("SELECT handler_name, state, attempts, next_retry_at FROM event_notifications ORDER BY handler_name"):
    p(f"dispatch.round1.{r[0]}", f"state={r[1]} attempts={r[2]} next_retry_at={r[3]}")
p("dispatch.round1.update_order", [s[0] for s in stmts])
# keep failing until it expires post-failure
for i in range(2, 8):
    stmts.clear()
    nxt = q("SELECT next_retry_at, state FROM event_notifications WHERE handler_name='bad-handler'")[0]
    if nxt[1] != "FAILED":
        break
    t = D(nxt[0])
    clock.pause_at(t)
    disp.drain_once()
    r = q("SELECT state, attempts, next_retry_at FROM event_notifications WHERE handler_name='bad-handler'")[0]
    p(f"dispatch.round{i}.bad-handler", f"now=+{clock.now()-dep_date} state={r[0]} attempts={r[1]} next_retry_at={r[2]}")

# --- missing handler (local-event-handler.md:116-120) ---
logging.basicConfig(level=logging.WARNING, format="LOG %(levelname)s %(name)s %(message)s", stream=sys.stdout)
q("UPDATE event_notifications SET handler_name='ghost', state='PENDING', attempts=0 WHERE handler_name='ok-handler'") if False else None
with reg.manager(S0).transaction() as tx:
    tx.execute(text("UPDATE event_notifications SET handler_name='ghost', state='PENDING', attempts=0, next_retry_at=event_date WHERE handler_name='ok-handler'"))
clock.pause_at(dep_date + timedelta(seconds=2))
disp.drain_once()
r = q("SELECT state, attempts, next_retry_at FROM event_notifications WHERE handler_name='ghost'")[0]
p("ghost.after_round", f"state={r[0]} attempts={r[1]} next_retry_at={r[2]}")
clock.pause_at(dep_date + retention + timedelta(seconds=1))
disp.drain_once()
r = q("SELECT state, attempts FROM event_notifications WHERE handler_name='ghost'")[0]
p("ghost.after_retention", f"state={r[0]} attempts={r[1]}")

# --- jobs (local-event-handler.md:156-162) ---
cfg = LocalEventHandlerConfig(handling_poll_delay=timedelta(seconds=7), handling=HandlingConfig(enabled=False))
j = EventHandlingJob(reg, handlers, notifications=EVENT_NOTIFICATIONS, config=cfg, clock=clock)
p("job.handling.built_without_enabled.schedule", repr(j.schedule()))
j2 = EventHandlingJob(reg, handlers, notifications=EVENT_NOTIFICATIONS, config=cfg, clock=clock, enabled=cfg.handling.enabled)
p("job.handling.enabled_from_config.schedule", repr(j2.schedule()))
f = EventFanoutJob(reg, handlers, events=EVENTS, notifications=EVENT_NOTIFICATIONS, enabled=False)
p("job.fanout.has_enabled_switch", repr(f.schedule()))
p("config.defaults", LocalEventHandlerConfig())
disp.close(); fan.close()

# --- fan-out across shards concurrently? (sharding.md:436) ---
reg2 = registry_for({S0: "n6_s0", S1: "n6_s1"})
from ekbatan.events import repository as evrepo  # noqa: E402
orig = evrepo.EventEntityRepository.find_undelivered
live = {"cur": 0, "max": 0}


def slow(self, shard, types, limit):
    with LK:
        live["cur"] += 1; live["max"] = max(live["max"], live["cur"])
    time.sleep(0.3)
    with LK:
        live["cur"] -= 1
    return orig(self, shard, types, limit)


evrepo.EventEntityRepository.find_undelivered = slow
fan2 = EventFanout(reg2, handlers, events=EVENTS, notifications=EVENT_NOTIFICATIONS, clock=clock)
fan2.drain_once()
p("fanout.two_shards.max_concurrent_reads", live["max"])
fan2.close()
backend.stop()
