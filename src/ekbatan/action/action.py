"""A unit of work.

Subclass :class:`Action`, take whatever it needs in ``__init__``, and write ``perform``. The
plan is reachable as ``self.plan`` rather than passed in, so the body reads as domain work and
nothing else::

    class DepositMoney(Action[DepositParams, Wallet]):
        def __init__(self, wallets: Repository[Wallet, UUID]) -> None:
            self.wallets = wallets

        def perform(self, params: DepositParams) -> Wallet:
            wallet = self.wallets.get(params.wallet_id)
            return self.plan.update(wallet.deposit(params.amount))

An instance is shared across every concurrent invocation, so it must hold no mutable state
beyond what its constructor sets. The per-invocation state - the plan - is not on the instance
at all; it lives in a context variable the executor binds, which is per-thread and per-async
task at once.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from datetime import datetime
from typing import Any

from ..clock import Clock, SystemClock
from ..errors import EkbatanError
from .plan import ActionPlan

__all__ = ["Action", "current_clock", "current_plan", "current_principal"]

_plan: ContextVar[ActionPlan | None] = ContextVar("ekbatan_plan", default=None)
_principal: ContextVar[str | None] = ContextVar("ekbatan_principal", default=None)
_clock: ContextVar[Clock | None] = ContextVar("ekbatan_clock", default=None)


def current_plan() -> ActionPlan:
    """The plan of the invocation running on this thread or task."""
    plan = _plan.get()
    if plan is None:
        raise EkbatanError(
            "there is no unit of work here. A plan only exists inside "
            "ActionExecutor.execute(...); staging a change outside one would have nothing to "
            "commit it. If this was called from a thread you started inside an action, note "
            "that the plan does not travel - join the work first, then stage from the "
            "invoking thread."
        )
    return plan


def current_principal() -> str | None:
    """Who asked for this, if anyone said."""
    return _principal.get()


def current_clock() -> Clock:
    """The clock this invocation is running against.

    The executor's, so a frozen clock in a test freezes the whole unit of work - the model's
    timestamps, the outbox row's, and anything the action reads for itself. An action calling
    ``datetime.now()`` directly is an action that cannot be tested at a fixed instant.
    """
    clock = _clock.get()
    return clock if clock is not None else SystemClock()


class Action[P, R]:
    """One unit of business work."""

    @property
    def name(self) -> str:
        """What lands in ``action_name``. Override only to keep a name stable across a
        rename - it is written into every row this action has ever produced."""
        return type(self).__name__

    @property
    def plan(self) -> ActionPlan:
        return current_plan()

    @property
    def principal(self) -> str | None:
        return current_principal()

    @property
    def clock(self) -> Clock:
        """The executor's clock. Use it rather than reaching for the wall clock."""
        return current_clock()

    def now(self) -> datetime:
        """Shorthand for ``self.clock.now()``."""
        return self.clock.now()

    def perform(self, params: P) -> R:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{self.name}()"


class _Binding:
    """Binds the per-invocation state for the duration of one ``perform``."""

    __slots__ = ("_clock", "_plan", "_principal", "_tokens")

    def __init__(self, plan: ActionPlan, principal: str | None, clock: Clock) -> None:
        self._plan = plan
        self._principal = principal
        self._clock = clock
        self._tokens: list[Token[Any]] = []

    def __enter__(self) -> ActionPlan:
        self._tokens = [
            _plan.set(self._plan),
            _principal.set(self._principal),
            _clock.set(self._clock),
        ]
        return self._plan

    def __exit__(self, *exc: object) -> None:
        _clock.reset(self._tokens[2])
        _principal.reset(self._tokens[1])
        _plan.reset(self._tokens[0])
