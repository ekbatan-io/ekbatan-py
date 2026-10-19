"""Fan-out, dispatch and retry write the clock's own value - nothing converts it.

The clock is frozen, so every `now()` is one object; each check captures what reached SQLAlchemy
and the driver and asks for that very object. The handlers run sync and async.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import event, select

from fixtures.actions import (
    CreateWalletAction,
    CreateWalletParams,
    DepositMoneyAction,
    DepositParams,
)
from fixtures.executing import run
from fixtures.handlers import failing, succeeding
from fixtures.schema import EVENT_NOTIFICATIONS

HANDLER_KINDS = ("sync", "async")

#: Naive UTC with microseconds - what `SystemClock().now()` gives.
PRECISE = datetime(2026, 1, 1, 10, 0, 0, 123456)


def _flat(parameters: Any) -> list[Any]:
    if isinstance(parameters, dict):
        return list(parameters.values())
    if isinstance(parameters, (list, tuple)):
        return [v for p in parameters for v in _flat(p)]
    return [parameters]


def _watch(registry: Any) -> tuple[list[tuple[str, Any, Any]], Any]:
    engine = registry.manager(registry.default_shard).primary
    seen: list[tuple[str, Any, Any]] = []

    def hook(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool):
        seen.append(
            (statement.lstrip().split()[0].upper(), context.compiled_parameters, parameters)
        )

    event.listen(engine, "before_cursor_execute", hook)
    return seen, lambda: event.remove(engine, "before_cursor_execute", hook)


def _deposited(executor: Any, wallets: Any) -> None:
    wallet = run(
        executor,
        CreateWalletAction(executor.clock),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("100")),
    )
    run(
        executor,
        DepositMoneyAction(wallets, executor.clock),
        DepositParams(wallet_id=wallet.id, amount=Decimal("1"), recipient="ada@example.com"),
    )


def test_fan_out_and_dispatch_write_the_clocks_own_value(
    executor, wallets, pipeline, registry, clock, dialect
):
    clock.pause_at(PRECISE)
    fanout, dispatch = pipeline(succeeding("audit"))
    _deposited(executor, wallets)

    seen, stop = _watch(registry)
    try:
        assert fanout.drain_once() == 1
        dispatch.drain_once()
    finally:
        stop()

    writes = [(compiled, sent) for verb, compiled, sent in seen if verb in ("INSERT", "UPDATE")]
    assert any(v is PRECISE for compiled, _ in writes for v in _flat(compiled)), (
        "the clock's value is handed to SQLAlchemy as it is"
    )
    if dialect != "sqlite":  # SQLAlchemy writes SQLite's DATETIME as text
        assert any(v is PRECISE for _, sent in writes for v in _flat(sent)), "and to the driver"

    with registry.default.connection(readonly=True) as conn:
        rows = conn.execute(select(EVENT_NOTIFICATIONS)).mappings().all()
    assert rows and all(r["updated_date"] == PRECISE for r in rows)
    assert all(r["updated_date"].tzinfo is None for r in rows), "read back with no zone added"


def test_a_retry_time_is_the_clocks_value_plus_the_backoff_as_it_is(
    executor, wallets, pipeline, registry, clock
):
    clock.pause_at(PRECISE)
    flaky = failing("flaky", times=1)
    fanout, dispatch = pipeline(flaky, max_backoff=timedelta(seconds=60))
    _deposited(executor, wallets)
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
