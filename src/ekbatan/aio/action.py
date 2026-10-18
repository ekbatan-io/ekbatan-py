"""An async unit of work.

The counterpart to :class:`ekbatan.action.Action`, differing in one line - ``perform`` is a
coroutine::

    class DepositMoneyAction(AsyncAction[DepositParams, Wallet]):
        def __init__(self, wallets: WalletRepository, clock: Clock) -> None:
            super().__init__(clock)
            self.wallets = wallets

        async def perform(self, principal: str | None, params: DepositParams) -> Wallet:
            wallet = await self.wallets.get(params.wallet_id)
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
