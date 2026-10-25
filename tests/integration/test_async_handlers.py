"""Async event handlers, natively: an ``async def handle`` that awaits an ``ekbatan.aio`` executor.

`test_events.py` runs every dispatch scenario with sync handlers and again with async ones
(`HANDLER_KINDS`). What is here is what only the async kind has:

- a handler awaiting a real `AsyncActionExecutor`, on an async registry that lives on the
  handler loop - built for the handlers, closed on that loop;
- the handlers of one batch running on the one loop at the same time;
- a handler raising what asyncio would let stop the loop - and the loop going on;
- who closes the loop: the dispatch that made it, never one it was handed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from conftest import GLOBAL, NOT_UNDER_GEVENT, _registry, under_gevent
from ekbatan.aio import AsyncActionExecutor, AsyncDatabaseRegistry
from ekbatan.events import (
    AsyncEventHandler,
    EventDispatch,
    EventEnvelope,
    EventFanout,
    EventHandler,
    EventHandlerRegistry,
    HandlerLoop,
)
from ekbatan.ids import ShardedId
from fixtures.actions import (
    AsyncCreateNotificationAction,
    CreateNotificationAction,
    WalletCreateAction,
    WalletDepositMoneyAction,
)
from fixtures.domain import Notification, Wallet, WalletMoneyDepositedEvent
from fixtures.executing import run
from fixtures.handlers import succeeding
from fixtures.repositories import AsyncNotificationRepository
from fixtures.schema import NOTIFICATIONS

pytestmark = pytest.mark.skipif(under_gevent(), reason=NOT_UNDER_GEVENT)


def a_deposit(executor: Any, wallet_repository: Any) -> None:
    wallet = run(
        executor,
        WalletCreateAction(executor.clock),
        WalletCreateAction.Params(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("100")),
    )
    run(
        executor,
        WalletDepositMoneyAction(wallet_repository, executor.clock),
        WalletDepositMoneyAction.Params(
            wallet_id=wallet.id, amount=Decimal("10"), recipient="ada@example.com"
        ),
    )


def wired(registry: Any, clock: Any, *handlers: Any, **options: Any) -> tuple[Any, Any]:
    subscriptions = EventHandlerRegistry(handlers)
    fanout = EventFanout(registry, subscriptions, clock=clock)
    dispatch = EventDispatch(registry, subscriptions, clock=clock, **options)
    return fanout, dispatch


class CountingSync(EventHandler[WalletMoneyDepositedEvent]):
    def __init__(self) -> None:
        self.calls = 0

    def name(self) -> str:
        return "counting-sync"

    def event_type(self) -> type[WalletMoneyDepositedEvent]:
        return WalletMoneyDepositedEvent

    def handle(self, envelope: EventEnvelope[WalletMoneyDepositedEvent]) -> None:
        self.calls += 1


class CountingAsync(AsyncEventHandler[WalletMoneyDepositedEvent]):
    def __init__(self) -> None:
        self.calls = 0

    def name(self) -> str:
        return "counting-async"

    def event_type(self) -> type[WalletMoneyDepositedEvent]:
        return WalletMoneyDepositedEvent

    async def handle(self, envelope: EventEnvelope[WalletMoneyDepositedEvent]) -> None:
        await asyncio.sleep(0)
        self.calls += 1


def test_a_sync_and_an_async_handler_in_the_method_form_both_run_through_dispatch(
    registry, executor, wallet_repository, clock, notifications_in
):
    """Java's shape: a handler extends a base and answers `name()` and `event_type()`. Fan-out
    stores `name()` on each row, dispatch builds the envelope from `event_type()`, and runs each
    by its base - the async one on the handler loop."""
    sync_handler, async_handler = CountingSync(), CountingAsync()
    fanout, dispatch = wired(registry, clock, sync_handler, async_handler)
    try:
        a_deposit(executor, wallet_repository)
        fanout.drain_once()
        assert dispatch.drain_once() == 2

        assert (sync_handler.calls, async_handler.calls) == (1, 1)
        assert notifications_in() == {"SUCCEEDED": 2}
    finally:
        dispatch.close()


class NotifyingHandler(AsyncEventHandler[WalletMoneyDepositedEvent]):
    """Awaits an async executor: the shape an async handler is for."""

    def __init__(self, executor: AsyncActionExecutor) -> None:
        self.executor = executor

    def name(self) -> str:
        return "async-deposit-notification"

    def event_type(self) -> type[WalletMoneyDepositedEvent]:
        return WalletMoneyDepositedEvent

    async def handle(self, envelope: EventEnvelope[WalletMoneyDepositedEvent]) -> None:
        await self.executor.execute(
            None,
            AsyncCreateNotificationAction,
            CreateNotificationAction.Params(
                wallet_id=ShardedId[Wallet].of(envelope.model_id or ""),
                kind="MONEY_DEPOSITED",
                recipient=str(envelope.action_params.get("recipient", "")),
                message=f"deposited {envelope.event.amount}",
            ),
        )


def test_an_async_handler_awaits_an_async_executor_on_the_handler_loop(
    backend, dialect, registry, executor, wallet_repository, clock, notifications_in
):
    if dialect == "sqlite":
        pytest.skip("SQLite takes one writer at a time; two pools over one file contend")
    loop = HandlerLoop()
    # The handlers' own async registry and executor, used only on the handler loop.
    handler_registry = _registry(backend, {GLOBAL: "ekbatan_main"}, kind=AsyncDatabaseRegistry)
    notification_repository = AsyncNotificationRepository(
        Notification, NOTIFICATIONS, handler_registry, clock=clock
    )
    handler_executor = AsyncActionExecutor(
        namespace="example.wallet",
        registry=handler_registry,
        repositories=[notification_repository],
        actions=[AsyncCreateNotificationAction(clock)],
        clock=clock,
    )
    fanout, dispatch = wired(registry, clock, NotifyingHandler(handler_executor), handler_loop=loop)
    try:
        a_deposit(executor, wallet_repository)
        fanout.drain_once()
        assert dispatch.drain_once() == 1

        assert notifications_in() == {"SUCCEEDED": 1}
        with registry.default.connection(readonly=True) as conn:
            written = conn.execute(select(func.count()).select_from(NOTIFICATIONS)).scalar()
        assert written == 1, "the async unit of work the handler awaited committed"
        assert dispatch.handler_loop is loop
    finally:
        dispatch.close()
        assert loop.running, "a loop handed in is its owner's to close"
        loop.run(handler_registry.aclose())  # on the loop its connections belong to
        loop.close()
    assert not loop.running


class Sleeping(AsyncEventHandler[WalletMoneyDepositedEvent]):
    def __init__(self, name: str) -> None:
        self._name = name
        self.loops: list[Any] = []

    def name(self) -> str:
        return self._name

    def event_type(self) -> type[WalletMoneyDepositedEvent]:
        return WalletMoneyDepositedEvent

    async def handle(self, envelope: EventEnvelope[Any]) -> None:
        self.loops.append(asyncio.get_running_loop())
        await asyncio.sleep(0.4)


def test_one_batch_of_async_handlers_runs_on_one_loop_at_once(
    registry, executor, wallet_repository, clock, notifications_in
):
    handlers = [Sleeping(f"sleeper-{n}") for n in range(5)]
    fanout, dispatch = wired(registry, clock, *handlers)
    try:
        a_deposit(executor, wallet_repository)
        fanout.drain_once()
        started = time.monotonic()
        dispatch.drain_once()
        took = time.monotonic() - started

        assert notifications_in() == {"SUCCEEDED": 5}
        assert took < 1.5, f"five 0.4s handlers took {took:.2f}s: they ran one after another"
        assert len({id(h.loops[0]) for h in handlers}) == 1, "all on the one handler loop"
    finally:
        dispatch.close()
    assert not dispatch.handler_loop.running, "the dispatch closed the loop it made"


class Interrupting(AsyncEventHandler[WalletMoneyDepositedEvent]):
    def name(self) -> str:
        return "interrupter"

    def event_type(self) -> type[WalletMoneyDepositedEvent]:
        return WalletMoneyDepositedEvent

    async def handle(self, envelope: EventEnvelope[Any]) -> None:
        raise KeyboardInterrupt("from a handler")


def test_a_handler_raising_keyboard_interrupt_fails_its_row_and_the_loop_goes_on(
    registry, executor, wallet_repository, clock, notifications_in, caplog
):
    """asyncio stops a loop that a task lets a KeyboardInterrupt out of. The handler loop
    carries it back to the delivery thread instead - which records the failure at ERROR, as for
    a sync handler - and the next handler still runs."""
    ok = succeeding("ok")
    fanout, dispatch = wired(registry, clock, Interrupting())
    later_fanout, later_dispatch = wired(registry, clock, ok, handler_loop=dispatch.handler_loop)
    try:
        a_deposit(executor, wallet_repository)
        fanout.drain_once()
        with caplog.at_level(logging.ERROR):
            dispatch.drain_once()
        assert notifications_in() == {"FAILED": 1}
        assert any("threw Error KeyboardInterrupt" in r.getMessage() for r in caplog.records)

        a_deposit(executor, wallet_repository)  # a fresh event, for the second registry
        later_fanout.drain_once()
        later_dispatch.drain_once()
        assert ok.calls == 1, "the loop survived it"
    finally:
        later_dispatch.close()
        dispatch.close()


def test_a_closed_loop_refuses_work_and_closing_twice_is_harmless():
    loop = HandlerLoop()

    async def answer() -> int:
        return 42

    assert loop.run(answer()) == 42
    loop.close()
    loop.close()
    coro = answer()
    with pytest.raises(RuntimeError, match="closed"):
        loop.run(coro)
    coro.close()
    HandlerLoop().close()  # never started


def test_work_cancelled_by_closing_the_loop_reaches_the_caller_as_a_cancellation():
    import threading

    loop = HandlerLoop()
    raised: list[BaseException] = []
    started = threading.Event()

    async def forever() -> None:
        started.set()
        await asyncio.sleep(60)

    def caller() -> None:
        try:
            loop.run(forever())
        except BaseException as error:
            raised.append(error)

    thread = threading.Thread(target=caller)
    thread.start()
    started.wait(5)
    loop.close()
    thread.join(5)
    assert len(raised) == 1 and isinstance(raised[0], asyncio.CancelledError)
