"""Running an async unit of work without a database - :mod:`ekbatan.testing`, awaited.

::

    staged = await async_run_action(
        WalletDepositMoneyAction(clock, wallet_repository), WalletDepositMoneyAction.Params(...)
    )
    assert staged.updated(Wallet)[0].balance == Decimal("150.00")

    (await AsyncActionSpec.of(WalletDepositMoneyAction(clock, wallet_repository))
        .execute(WalletDepositMoneyAction.Params(...)))
        .assert_updated(Wallet, lambda w: w.balance == Decimal("150.00"))
        .assert_emitted(WalletMoneyDepositedEvent, 1)

What comes back is the sync helpers' own :class:`~ekbatan.testing.StagedWork` and
:class:`~ekbatan.testing.ActionAssert`: once ``perform`` has finished there is nothing left to
await, so every check is the sync one. The plan is bound exactly as the
:class:`~ekbatan.aio.AsyncActionExecutor` binds it, and nothing is written or committed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Self

from ..action.action import _Binding
from ..action.plan import ActionPlan
from ..testing import ActionAssert, StagedWork, _check
from .action import AsyncAction

__all__ = ["AsyncActionSpec", "async_run_action"]


async def async_run_action[P, R](
    action: AsyncAction[P, R],
    params: P,
    *,
    principal: str | None = None,
) -> StagedWork[R]:
    """Run one async unit of work against a bare plan and report what it staged - the async
    :func:`~ekbatan.testing.run_action`. The plan stamps from the action's own clock."""
    plan = ActionPlan(action.clock)
    with _Binding(plan):
        result = await action.perform(principal, params)
    return StagedWork(result=result, plan=plan, clock=action.clock)


class AsyncActionSpec[P, R]:
    """:class:`~ekbatan.testing.ActionSpec` for an :class:`~ekbatan.aio.AsyncAction`.

    ``execute`` and ``assert_throws`` are awaited, because they run ``perform``; what
    ``execute`` hands back is the sync :class:`~ekbatan.testing.ActionAssert`, chained as usual.
    """

    __slots__ = ("_action", "_principal")

    def __init__(self, action: AsyncAction[P, R]) -> None:
        self._action = action
        self._principal: str | None = None

    @classmethod
    def of(cls, action: AsyncAction[P, R]) -> AsyncActionSpec[P, R]:
        return cls(action)

    def with_principal(self, principal: str | None) -> Self:
        self._principal = principal
        return self

    async def execute(self, params: P) -> ActionAssert[R]:
        return ActionAssert(await async_run_action(self._action, params, principal=self._principal))

    async def assert_throws[E: Exception](
        self,
        exception_type: type[E],
        params: P,
        verifier: Callable[[E], object] | None = None,
    ) -> Self:
        """The action raises ``exception_type`` or a subclass - as the sync one, awaited."""
        name = exception_type.__name__
        try:
            await async_run_action(self._action, params, principal=self._principal)
        except Exception as error:
            if not isinstance(error, exception_type):
                raise AssertionError(f"Expected {name} but got {type(error).__name__}") from error
            if verifier is not None:
                _check(verifier, error, f"the {name} raised")
        else:
            raise AssertionError(f"Expected {name} to be thrown but nothing was thrown")
        return self
