"""Round 2, area N4: errors and the testing helper (Python twin of ParityN4ProbeTest.java).

Two PostgreSQL containers (global 0:0, mexico 1:0) with the tables Java's postgres-sharded
module migrates, and the same failures as the Java probe, printing the same PROBE keys.

Run: cd ekbatan-py && uv run --no-sync python parity-probes/round2-N4/probe_errors.py
"""

from __future__ import annotations

import sys
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from sqlalchemy import MetaData, create_engine, text  # noqa: E402
from testcontainers.community.postgres import PostgresContainer  # noqa: E402

from ekbatan import errors as E  # noqa: E402
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
    DataSourceConfig,
    ShardGroupConfig,
    ShardingConfig,
    ShardMemberConfig,
)
from ekbatan.ids import ShardedId  # noqa: E402
from ekbatan.locks import PostgresKeyedLockProvider  # noqa: E402
from ekbatan.persistence import DatabaseRegistry, DataSource  # noqa: E402
from ekbatan.schema import outbox_table  # noqa: E402
from ekbatan.sharding import EmbeddedBitsShardingStrategy, ShardIdentifier  # noqa: E402
from ekbatan.testing import ActionSpec  # noqa: E402
from fixtures.domain import Wallet  # noqa: E402
from fixtures.logins import data_source  # noqa: E402
from fixtures.repositories import WalletRepository  # noqa: E402
from fixtures.schema import WALLETS  # noqa: E402

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)
AUSTRALIA = ShardIdentifier(2, 0)
DEAD_URL = "postgresql+psycopg://test:test@127.0.0.1:58599/nowhere"

DDL = """
CREATE SCHEMA IF NOT EXISTS eventlog;
CREATE TABLE eventlog.events (
    id UUID PRIMARY KEY, namespace VARCHAR(255) NOT NULL, action_id UUID NOT NULL,
    action_name VARCHAR(255) NOT NULL, action_params JSONB NOT NULL,
    started_date TIMESTAMP NOT NULL, completion_date TIMESTAMP NOT NULL,
    model_id VARCHAR(255), model_type VARCHAR(255), event_type VARCHAR(255), payload JSONB,
    event_date TIMESTAMP NOT NULL, delivered BOOLEAN NOT NULL
);
CREATE TABLE wallets (
    id UUID PRIMARY KEY, version BIGINT NOT NULL, state VARCHAR(24) NOT NULL,
    owner_id UUID NOT NULL, currency CHAR(3) NOT NULL, balance DECIMAL(19, 4) NOT NULL,
    created_date TIMESTAMP NOT NULL, updated_date TIMESTAMP NOT NULL,
    aliases JSONB, reward_points INT
);
"""

EVENTS = outbox_table(MetaData(), schema="eventlog")
clock = SystemClock()
engines: dict[ShardIdentifier, Any] = {}
ATTEMPTS = [0]
FIXED_ID: list[Any] = [None]


def p(key: str, value: Any) -> None:
    if value is True:
        value = "true"
    elif value is False:
        value = "false"
    elif value is None:
        value = "null"
    print(f"PROBE {key}: {value}", flush=True)


def catching(fn) -> BaseException | None:
    try:
        fn()
        return None
    except BaseException as t:  # noqa: BLE001
        return t


def name(t: BaseException | None) -> str:
    return "none" if t is None else type(t).__name__


def chain(t: BaseException | None) -> str:
    out = []
    while t is not None:
        out.append(type(t).__name__)
        t = t.__cause__
    return " <- ".join(out) or "none"


def one_line(t: BaseException | None) -> str:
    return "none" if t is None else str(t).splitlines()[0][:220] if str(t) else ""


def report(key: str, t: BaseException | None) -> None:
    p(f"{key}.type", name(t))
    p(f"{key}.msg", one_line(t))
    p(f"{key}.chain", chain(t))
    p(f"{key}.caught_by_framework_base", isinstance(t, E.EkbatanError))


def new_wallet(shard: ShardIdentifier, wallet_id: ShardedId | None = None) -> Wallet:
    return Wallet.open(
        id=wallet_id or ShardedId[Wallet].generate(shard),
        owner_id=uuid.uuid4(),
        currency="EUR",
        balance=Decimal("10"),
        created_date=clock.now(),
    )


def count(shard: ShardIdentifier, table: str) -> int:
    with engines[shard].connect() as c:
        return c.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def truncate() -> None:
    for e in engines.values():
        with e.begin() as c:
            c.execute(text("TRUNCATE wallets, eventlog.events"))


# ---------------------------------------------------------------------- actions


@dataclass(frozen=True)
class CountryParams:
    country_code: str


@dataclass(frozen=True)
class NoParams:
    pass


class Opaque:
    """A value with no fields: Java's `new Object()`."""


class Money:
    """A plain class with public attributes: Java's class with public final fields."""

    def __init__(self) -> None:
        self.currency = "EUR"
        self.amount = Decimal("5")


@dataclass(frozen=True)
class BadParams:
    thing: Any


class CreateAction(Action[CountryParams, Wallet]):
    def perform(self, principal, params):
        shard = {"MX": MEXICO, "AU": AUSTRALIA}.get(params.country_code, GLOBAL)
        return self.plan.add(new_wallet(shard))


class TwoShardAction(Action[NoParams, None]):
    def perform(self, principal, params):
        self.plan.add(new_wallet(GLOBAL))
        self.plan.add(new_wallet(MEXICO))


class UnregisteredAction(Action[NoParams, None]):
    def perform(self, principal, params):
        return None


class FixedIdAction(Action[NoParams, Wallet]):
    def perform(self, principal, params):
        return self.plan.add(new_wallet(GLOBAL, FIXED_ID[0]))


class BadParamsAction(Action[BadParams, Wallet]):
    def perform(self, principal, params):
        return self.plan.add(new_wallet(GLOBAL))


class StaleThrowAction(Action[NoParams, None]):
    def perform(self, principal, params):
        ATTEMPTS[0] += 1
        raise E.StaleRecordError("probe stale")


class AssertingAction(Action[NoParams, None]):
    def perform(self, principal, params):
        raise AssertionError("assert inside perform")


class PlanLeakAction(Action[NoParams, None]):
    def leak(self):
        return self.plan


def build_executor(registry, repo, actions) -> ActionExecutor:
    return ActionExecutor(
        namespace="parity.n4",
        registry=registry,
        repositories=[repo],
        actions=actions,
        outbox=SingleTableJsonEventPersister(registry, EVENTS),
        clock=clock,
    )


def sharding(urls, policy=None) -> ShardingConfig:
    groups = [
        ShardGroupConfig.builder().group(s.group).name(f"group-{s.group}").with_member(
            ShardMemberConfig.builder().member(s.member).name(f"db{s.group}")
            .primary_config(data_source(url, pool_size=5)).build()
        ).build()
        for s, url in urls.items()
    ]
    b = ShardingConfig.builder().default_shard(GLOBAL).groups(groups)
    if policy:
        b = b.unknown_shard_policy(policy)
    return b.build()


# ---------------------------------------------------------------------- the probe


def probe(urls) -> None:
    registry = DatabaseRegistry.from_config(sharding(urls))
    repo = WalletRepository(Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(),
                            clock=clock)
    actions = ActionRegistry([
        CreateAction(clock), TwoShardAction(clock), FixedIdAction(clock),
        BadParamsAction(clock), StaleThrowAction(clock),
    ])
    executor = build_executor(registry, repo, actions)

    # e1 getById of a missing row
    missing = ShardedId[Wallet].generate(GLOBAL)
    report("e1.get_missing", catching(lambda: repo.get_by_id(missing)))

    # e2 a direct update with a stale version
    truncate()
    w = executor.execute(CreateAction, CountryParams("DE"), principal="alice")
    repo.update(w.deposit(Decimal("1")))
    report("e2.update_stale", catching(lambda: repo.update(w.deposit(Decimal("2")))))

    # e3 unregistered action
    report("e3.unregistered", catching(
        lambda: executor.execute(UnregisteredAction, NoParams(), principal="alice")))

    # e4 the plan reached outside an execution
    report("e4.plan_outside", catching(lambda: PlanLeakAction(clock).leak()))

    # e5 cross-shard refused
    truncate()
    report("e5.cross_shard", catching(
        lambda: executor.execute(TwoShardAction, NoParams(), principal="alice")))

    # e6 unknown shard with policy FAIL, through execute
    fail_registry = DatabaseRegistry.from_config(sharding(urls, "fail"))
    fail_repo = WalletRepository(Wallet, WALLETS, fail_registry,
                                 sharding=EmbeddedBitsShardingStrategy(), clock=clock)
    fail_exec = build_executor(fail_registry, fail_repo, ActionRegistry([CreateAction(clock)]))
    truncate()
    report("e6.fail_policy_execute", catching(
        lambda: fail_exec.execute(CreateAction, CountryParams("AU"), principal="alice")))
    p("e6.wallets_global", count(GLOBAL, "wallets"))
    fail_registry.close()

    # e9 duplicate primary key through execute
    truncate()
    FIXED_ID[0] = ShardedId[Wallet].generate(GLOBAL)
    executor.execute(FixedIdAction, NoParams(), principal="alice")
    report("e9.duplicate_key", catching(
        lambda: executor.execute(FixedIdAction, NoParams(), principal="alice")))

    # e10 params the JSON writer cannot write
    truncate()
    report("e10.unwritable_params", catching(
        lambda: executor.execute(BadParamsAction, BadParams(Opaque()), principal="alice")))
    p("e10.wallets", count(GLOBAL, "wallets"))
    p("e10.outbox", count(GLOBAL, "eventlog.events"))
    with engines[GLOBAL].connect() as c:
        stored = c.execute(text("SELECT action_params::text FROM eventlog.events")).scalars().all()
    p("e10.stored_params", [s.split(" at 0x")[0] + (" at 0x...>" if " at 0x" in s else "")
                            for s in stored])
    truncate()
    executor.execute(BadParamsAction, BadParams(Money()), principal="alice")
    with engines[GLOBAL].connect() as c:
        stored = c.execute(text("SELECT action_params::text FROM eventlog.events")).scalars().all()
    p("e10b.plain_class_with_public_fields.stored_params",
      [s.split(" at 0x")[0] + (" at 0x...>" if " at 0x" in s else "") for s in stored])

    # e12 a retry policy on the framework's base class: is a stale error (a subclass) retried?
    cfg = (ExecutionConfiguration.builder().no_retry()
           .with_retry(RetryConfig(on=E.EkbatanError, max_retries=2, delay=0.001)).build())
    ATTEMPTS[0] = 0
    t12 = catching(lambda: executor.execute(StaleThrowAction, NoParams(), principal="alice",
                                            config=cfg))
    p("e12.policy_on_base_class.attempts", ATTEMPTS[0])
    p("e12.policy_on_base_class.type", name(t12))

    # e13 the stale error built with no arguments, as `new StaleRecordException()`
    t13 = catching(lambda: E.StaleRecordError())
    p("e13.stale_no_arg_constructor", name(t13))

    registry.close()

    # e7 database unreachable: registry build, then a read
    t7build = catching(lambda: DatabaseRegistry.from_config(sharding({GLOBAL: DEAD_URL})))
    p("e7.unreachable.registry_build", name(t7build))
    dead_registry = DatabaseRegistry.from_config(sharding({GLOBAL: DEAD_URL}))
    dead_repo = WalletRepository(Wallet, WALLETS, dead_registry,
                                 sharding=EmbeddedBitsShardingStrategy(), clock=clock)
    started = time.monotonic()
    t7 = catching(lambda: dead_repo.get_by_id(ShardedId[Wallet].generate(GLOBAL)))
    elapsed = time.monotonic() - started
    report("e7.unreachable.read", t7)
    p("e7.unreachable.is_ConnectivityError", isinstance(t7, E.ConnectivityError))
    p("e7.unreachable.seconds_to_fail_rounded_to_5", int(round(elapsed / 5) * 5))
    dead_registry.close()

    # e8 lock backend unreachable
    dead = DataSource.from_config(DataSourceConfig(url="postgresql+psycopg://127.0.0.1:58599/nowhere", username="test", password="test"))
    lock = PostgresKeyedLockProvider.builder().data_source(dead).build()
    t8 = catching(lambda: lock.try_acquire("wallet:1", timedelta(0), timedelta(seconds=5)))
    report("e8.lock_unreachable", t8)
    p("e8.lock_unreachable.key_field", getattr(t8, "key", "none"))

    # e11 VirtualClock advanced while running
    report("e11.vclock_advance_running", catching(lambda: VirtualClock().advance(timedelta(1))))

    # t1 ActionSpec.assertThrows(IllegalState/Validation) when the action fails an assertion
    spec = ActionSpec.of(AssertingAction(clock))
    t1 = catching(lambda: spec.assert_throws(E.ValidationError, NoParams()))
    p("t1.assert_throws_other_type_action_asserts.type", name(t1))
    p("t1.assert_throws_other_type_action_asserts.msg", one_line(t1))
    p("t1.assert_throws_other_type_action_asserts.chain", chain(t1))

    # t2 assertThrows(<broadest exception type>) when the action fails an assertion
    t2 = catching(lambda: spec.assert_throws(Exception, NoParams()))
    p("t2.assert_throws_broadest_action_asserts", "passes" if t2 is None else name(t2))

    # t3 an action built with a null clock, run through ActionSpec
    t3 = catching(lambda: ActionSpec.of(CreateAction(None)).execute(CountryParams("DE")))
    p("t3.null_clock_action.type", name(t3))


def main() -> None:
    containers = [PostgresContainer("postgres:latest", driver="psycopg") for _ in range(2)]
    try:
        for c in containers:
            c.start()
        urls = {GLOBAL: containers[0].get_connection_url(),
                MEXICO: containers[1].get_connection_url()}
        for shard, url in urls.items():
            engines[shard] = create_engine(url)
            with engines[shard].begin() as conn:
                conn.exec_driver_sql(DDL)
        probe(urls)
        for e in engines.values():
            e.dispose()
    finally:
        for c in containers:
            c.stop()


if __name__ == "__main__":
    main()
