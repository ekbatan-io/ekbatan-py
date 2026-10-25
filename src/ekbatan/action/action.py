"""A unit of work.

Subclass :class:`Action`, take whatever it needs in ``__init__``, and write ``perform``::

    class WalletDepositMoneyAction(Action["WalletDepositMoneyAction.Params", Wallet]):
        @dataclass(frozen=True, kw_only=True)
        class Params:
            wallet_id: ShardedId[Wallet]
            amount: Decimal

        def __init__(self, clock: Clock, wallet_repository: WalletRepository) -> None:
            super().__init__(clock)
            self.wallet_repository = wallet_repository

        def perform(self, principal: str | None, params: Params) -> Wallet:
            wallet = self.wallet_repository.get_by_id(params.wallet_id.value)
            return self.plan.update(wallet.deposit(params.amount))

Three per-invocation values, and the same three mechanisms Java uses for them:

- **the clock** is a constructor field. Injected once, and the same object the executor holds.
- **the principal** is a parameter of ``perform``. A helper that needs it is passed it, like
  any other value.
- **the plan** is ambient, because it cannot be a parameter: it has to be reachable from a
  repository call several frames down that never mentions it.

An instance is shared across every concurrent invocation, so it must hold no mutable state
beyond what its constructor sets. The plan is not on the instance at all; it lives in a context
variable the executor binds, which is per-thread and per-async-task at once.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from ..clock import Clock
from ..errors import EkbatanError, ValidationError
from .plan import ActionPlan

__all__ = ["Action", "current_plan"]

_plan: ContextVar[ActionPlan | None] = ContextVar("ekbatan_plan", default=None)


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


class _ActionCore[P, R]:
    """What every action is, whatever its ``perform`` waits on - shared by :class:`Action` and
    ``ekbatan.aio``'s ``AsyncAction``: the clock and the ambient plan.

    The two differ in one line, whether ``perform`` is a coroutine, so that line is all each
    of them adds.
    """

    __slots__ = ("clock",)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # An action's name is its class name, as Java's is `actionClass.getSimpleName()`, with no
        # way to change it: it is what `action_name` holds on every outbox row and what the logs
        # and traces say. An override wrote rows under one name while the class had another.
        if "name" in cls.__dict__:
            raise TypeError(
                f"{cls.__qualname__} overrides name: an action's name is its class name, as in "
                f"Java - it is what action_name holds. To change it, rename the class."
            )

    def __init__(self, clock: Clock) -> None:
        """Take the clock the executor was built with, and nothing else time-related.

        Required rather than defaulted, and the reason is the mistake it prevents: an action
        that quietly fell back to the system clock while the executor held a frozen one would
        stamp its own values from a different instant than the row it stages, and the test
        that froze the clock would pass anyway - on the machine it was written on.
        """
        if clock is None:
            raise ValidationError(
                f"{type(self).__name__} was built without a clock. Pass the one the "
                f"ActionExecutor was built with: a unit of work reading a different clock "
                f"than the one stamping its rows cannot be tested at a fixed instant."
            )
        self.clock = clock

    @property
    def plan(self) -> ActionPlan:
        return current_plan()

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


class Action[P, R](_ActionCore[P, R]):
    """One unit of business work."""

    __slots__ = ()

    def perform(self, principal: str | None, params: P) -> R:
        raise NotImplementedError


class _Binding:
    """Binds the plan for the duration of one ``perform``."""

    __slots__ = ("_plan", "_token")

    def __init__(self, plan: ActionPlan) -> None:
        self._plan = plan
        self._token: Token[Any] | None = None

    def __enter__(self) -> ActionPlan:
        self._token = _plan.set(self._plan)
        return self._plan

    def __exit__(self, *exc: object) -> None:
        if self._token is not None:
            _plan.reset(self._token)
