"""An async unit of work.

The counterpart to :class:`ekbatan.action.Action`, differing in one line - ``perform`` is a
coroutine::

    class WalletDepositMoneyAction(AsyncAction["WalletDepositMoneyAction.Params", Wallet]):
        @dataclass(frozen=True, kw_only=True)
        class Params:
            wallet_id: ShardedId[Wallet]
            amount: Decimal

        def __init__(self, clock: Clock, wallet_repository: WalletRepository) -> None:
            super().__init__(clock)
            self.wallet_repository = wallet_repository

        async def perform(self, principal: str | None, params: Params) -> Wallet:
            wallet = await self.wallet_repository.get_by_id(params.wallet_id.value)
            return self.plan.update(wallet.deposit(params.amount))

The clock, the name and the plan are the sync action's own code. The plan is in the same
context variable, which is per task: two invocations running at once on one event loop each
see their own.
"""

from __future__ import annotations

from ..action.action import _ActionCore

__all__ = ["AsyncAction"]


class AsyncAction[P, R](_ActionCore[P, R]):
    """One unit of business work whose ``perform`` awaits.

    Register it with an :class:`~ekbatan.aio.AsyncActionExecutor`; the sync executor refuses
    it, as this one refuses a sync :class:`~ekbatan.action.Action`.
    """

    __slots__ = ()

    async def perform(self, principal: str | None, params: P) -> R:
        raise NotImplementedError
