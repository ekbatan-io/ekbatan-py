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

from contextvars import ContextVar
from typing import Any

from ..errors import EkbatanError
from .plan import ActionPlan

__all__ = ["Action", "current_plan", "current_principal"]

_plan: ContextVar[ActionPlan | None] = ContextVar("ekbatan_plan", default=None)
_principal: ContextVar[str | None] = ContextVar("ekbatan_principal", default=None)


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

    def perform(self, params: P) -> R:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{self.name}()"


class _Binding:
    """Binds the per-invocation state for the duration of one ``perform``."""

    __slots__ = ("_plan", "_principal", "_tokens")

    def __init__(self, plan: ActionPlan, principal: str | None) -> None:
        self._plan = plan
        self._principal = principal
        self._tokens: list[Any] = []

    def __enter__(self) -> ActionPlan:
        self._tokens = [_plan.set(self._plan), _principal.set(self._principal)]
        return self._plan

    def __exit__(self, *exc: object) -> None:
        _principal.reset(self._tokens[1])
        _plan.reset(self._tokens[0])
