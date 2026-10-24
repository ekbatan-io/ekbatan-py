"""Fan-out, dispatch and retry write the clock's own value - nothing converts it.

The clock is frozen, so every `instant()` is one object; each check captures what reached SQLAlchemy
and the driver and asks for that very object. The handlers run sync and async.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import event, select

from fixtures.actions import (
    WalletCreateAction,
    WalletDepositMoneyAction,
)
from fixtures.executing import run
from fixtures.handlers import failing, succeeding
from fixtures.schema import EVENT_NOTIFICATIONS

HANDLER_KINDS = ("sync", "async")

#: Naive UTC with microseconds - what `SystemClock().instant()` gives.
PRECISE = datetime(2026, 1, 1, 10, 0, 0, 123456)


def _flat(parameters: Any) -> list[Any]:
    if isinstance(parameters, dict):
        return list(parameters.values())
    if isinstance(parameters, (list, tuple)):
        return [v for p in parameters for v in _flat(p)]
    return [parameters]


def _watch(registry: Any) -> tuple[list[tuple[str, Any, Any]], Any]:
    engine = registry.manager(registry.default_shard).primary.sqlalchemy_engine
    seen: list[tuple[str, Any, Any]] = []

    def hook(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool):
        seen.append(
            (statement.lstrip().split()[0].upper(), context.compiled_parameters, parameters)
        )

    event.listen(engine, "before_cursor_execute", hook)
    return seen, lambda: event.remove(engine, "before_cursor_execute", hook)


def _deposited(executor: Any, wallet_repository: Any) -> None:
    wallet = run(
        executor,
        WalletCreateAction(executor.clock),
        WalletCreateAction.Params(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("100")),
    )
    run(
        executor,
        WalletDepositMoneyAction(wallet_repository, executor.clock),
        WalletDepositMoneyAction.Params(
            wallet_id=wallet.id, amount=Decimal("1"), recipient="ada@example.com"
        ),
    )


def test_fan_out_and_dispatch_write_the_clocks_own_value(
    executor, wallet_repository, pipeline, registry, clock, dialect
):
    clock.pause_at(PRECISE)
    fanout, dispatch = pipeline(succeeding("audit"))
    _deposited(executor, wallet_repository)

    seen, stop = _watch(registry)
    try:
        assert fanout.drain_once() == 1
        dispatch.drain_once()
    finally:
        stop()

    inserted = [c for verb, compiled, _ in seen if verb == "INSERT" for c in compiled]
    updated = [c for verb, compiled, _ in seen if verb == "UPDATE" for c in compiled]
    queued = [c for c in inserted if "next_retry_at" in c]
    outcomes = [c for c in updated if "updated_date" in c]
    assert queued and outcomes
    for c in queued:  # fan-out: the notification's own times, from the clock
        assert c["created_date"] is PRECISE and c["updated_date"] is PRECISE, c
    for c in outcomes:  # dispatch: the outcome's time, from the clock
        assert c["updated_date"] is PRECISE, c
    if dialect != "sqlite":  # SQLAlchemy writes SQLite's DATETIME as text
        sent = [v for verb, _, params in seen if verb == "UPDATE" for v in _flat(params)]
        assert any(v is PRECISE for v in sent), "and on to the driver as it is"

    with registry.default.connection(readonly=True) as conn:
        rows = conn.execute(select(EVENT_NOTIFICATIONS)).mappings().all()
    assert rows and all(r["updated_date"] == PRECISE for r in rows)
    assert all(r["updated_date"].tzinfo is None for r in rows), "read back with no zone added"


def test_a_retry_time_is_the_clocks_value_plus_the_backoff_as_it_is(
    executor, wallet_repository, pipeline, registry, clock
):
    clock.pause_at(PRECISE)
    flaky = failing("flaky", times=1)
    fanout, dispatch = pipeline(flaky, max_backoff=timedelta(seconds=60))
    _deposited(executor, wallet_repository)
    fanout.drain_once()

    dispatch.drain_once()
    with registry.default.connection(readonly=True) as conn:
        (row,) = conn.execute(select(EVENT_NOTIFICATIONS)).mappings().all()
    assert row["state"] == "FAILED"
    assert row["updated_date"] == PRECISE
    assert row["next_retry_at"] > PRECISE and row["next_retry_at"].tzinfo is None
    assert row["next_retry_at"] - PRECISE <= timedelta(seconds=60), "no zone offset added"

    clock.advance(timedelta(seconds=61))
    dispatch.drain_once()
    assert flaky.calls == 2, "due times compare with the naive clock"
