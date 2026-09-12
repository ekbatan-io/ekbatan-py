"""Handlers for the wallet application.

Each reads only its envelope, which is what would let it move into a broker consumer later
without its body changing. A handler that reached into a repository or a transaction is one
whose deployment discovers, all at once, that it cannot.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ekbatan import ActionExecutor
from ekbatan.events import EventEnvelope

from .actions import NotifyAction, NotifyParams
from .domain import WalletClosedEvent, WalletId, WalletMoneyDepositedEvent


class RecordingHandler:
    """Records what it was handed. The shape every other handler here is a variation on."""

    def __init__(self, name: str, event_type: type, on_call: Callable[[], None] | None = None):
        self._name = name
        self._event_type = event_type
        self._on_call = on_call
        self.received: list[EventEnvelope[Any]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
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
    name: str = "recorder", event_type: type = WalletMoneyDepositedEvent
) -> RecordingHandler:
    return RecordingHandler(name, event_type)


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


class MoneyDepositedEventHandler:
    """Chains into another unit of work - the shape a saga step has.

    Reads the recipient off the producing action's parameters, because a delivery address has
    no business inside a money-deposited event.
    """

    def __init__(self, executor: ActionExecutor) -> None:
        self.executor = executor
        self.calls = 0

    name = "wallet-deposit-notification"
    event_type = WalletMoneyDepositedEvent

    def handle(self, envelope: EventEnvelope[WalletMoneyDepositedEvent]) -> None:
        self.calls += 1
        self.executor.execute(
            NotifyAction(),
            NotifyParams(
                wallet_id=WalletId.of(envelope.model_id or ""),
                kind="MONEY_DEPOSITED",
                recipient=str(envelope.action_params.get("recipient", "")),
                message=f"deposited {envelope.event.amount}, balance {envelope.event.new_balance}",
            ),
        )


def on_close(name: str = "closer") -> RecordingHandler:
    return RecordingHandler(name, WalletClosedEvent)
