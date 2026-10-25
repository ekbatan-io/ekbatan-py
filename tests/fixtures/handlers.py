"""Handlers for the wallet application.

Each reads only its envelope, which is what would let it move into a broker consumer later
without its body changing. A handler that reached into a repository or a transaction is one
whose deployment discovers, all at once, that it cannot.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from collections.abc import Set as AbstractSet
from typing import Any

from ekbatan import ActionExecutor
from ekbatan.events import AsyncEventHandler, EventEnvelope, EventHandler
from ekbatan.ids import ShardedId

from .actions import (
    CreateNotificationAction,
)
from .domain import Wallet, WalletClosedEvent, WalletMoneyDepositedEvent


class RecordingHandler(EventHandler[Any]):
    """Records what it was handed. The shape every other handler here is a variation on."""

    def __init__(
        self,
        name: str,
        event_type: type,
        on_call: Callable[[], None] | None = None,
        aliases: Iterable[str] = (),
    ):
        self._name = name
        self._event_type = event_type
        self._on_call = on_call
        self._aliases = frozenset(aliases)
        self.received: list[EventEnvelope[Any]] = []

    def name(self) -> str:
        return self._name

    def aliases(self) -> AbstractSet[str]:
        return self._aliases

    def event_type(self) -> type:
        return self._event_type

    @property
    def calls(self) -> int:
        return len(self.received)

    def handle(self, envelope: EventEnvelope[Any]) -> None:
        self.received.append(envelope)
        if self._on_call is not None:
            self._on_call()


def succeeding(
    name: str = "recorder",
    event_type: type = WalletMoneyDepositedEvent,
    aliases: Iterable[str] = (),
) -> RecordingHandler:
    return RecordingHandler(name, event_type, aliases=aliases)


def failing(name: str = "flaky", times: int | None = None) -> RecordingHandler:
    """Fails its first ``times`` calls, or every call when ``times`` is None."""
    state = {"remaining": times}

    def boom() -> None:
        if state["remaining"] is None:
            raise RuntimeError("handler failed")
        if state["remaining"] > 0:
            state["remaining"] -= 1
            raise RuntimeError("handler failed")

    return RecordingHandler(name, WalletMoneyDepositedEvent, boom)


def fatal(name: str = "exploder") -> RecordingHandler:
    """Raises the failure class an ordinary ``except Exception`` misses.

    In Java it is an Error; in Go a panic; here a BaseException. What it means is the same
    everywhere, and so is the bug it finds: a dispatcher that lets it escape discards the whole
    batch, including siblings that had already succeeded.
    """

    def explode() -> None:
        raise BaseException("something a normal except would not catch")

    return RecordingHandler(name, WalletMoneyDepositedEvent, explode)


class WalletMoneyDepositedEventHandler(EventHandler[WalletMoneyDepositedEvent]):
    """Chains into another unit of work - the shape a saga step has.

    Reads the recipient off the producing action's parameters, because a delivery address has
    no business inside a money-deposited event.
    """

    def __init__(self, executor: ActionExecutor) -> None:
        self.executor = executor
        self.calls = 0

    def name(self) -> str:
        return "wallet-deposit-notification"

    def event_type(self) -> type[WalletMoneyDepositedEvent]:
        return WalletMoneyDepositedEvent

    def handle(self, envelope: EventEnvelope[WalletMoneyDepositedEvent]) -> None:
        self.calls += 1
        self.executor.execute(
            CreateNotificationAction,
            CreateNotificationAction.Params(
                wallet_id=ShardedId[Wallet].of(envelope.model_id or ""),
                kind="MONEY_DEPOSITED",
                recipient=str(envelope.action_params.get("recipient", "")),
                message=f"deposited {envelope.event.amount}, balance {envelope.event.new_balance}",
            ),
        )


def on_close(name: str = "closer") -> RecordingHandler:
    return RecordingHandler(name, WalletClosedEvent)


class AsyncTwin(AsyncEventHandler[Any]):
    """A handler made async for the async half of a run with ``HANDLER_KINDS``: the same name,
    event type and aliases, and an ``async def handle`` that awaits the sync body on a worker
    thread - so dispatch takes the async path (its handler loop) while the body, its counters and
    what it raises stay the sync handler's. A blocking body awaited this way does not hold up the
    loop's other handlers.
    """

    def __init__(self, sync: Any) -> None:
        self.sync = sync

    def name(self) -> str:
        return str(self.sync.name())

    def aliases(self) -> AbstractSet[str]:
        aliases: AbstractSet[str] = self.sync.aliases()
        return aliases

    def event_type(self) -> type:
        event_type: type = self.sync.event_type()
        return event_type

    async def handle(self, envelope: EventEnvelope[Any]) -> None:
        await asyncio.to_thread(self.sync.handle, envelope)


def of_kind(kind: str, handler: Any) -> Any:
    """``handler`` itself for ``"sync"``; its :class:`AsyncTwin` for ``"async"``."""
    return handler if kind == "sync" else AsyncTwin(handler)
