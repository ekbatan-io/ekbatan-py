"""The wallet application's units of work.

Each is short on purpose. Read one and you should be able to say what it does to the business
without knowing anything about this framework - no transaction, no flush, no publish, no
outbox. That is the bar these are here to demonstrate.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from ekbatan import AbstractRepository, Action, Clock
from ekbatan.aio import AsyncAction
from ekbatan.errors import ValidationError
from ekbatan.ids import Id, ShardedId
from modes import twin

from .domain import Notification, Wallet


class WalletCreateAction(Action["WalletCreateAction.Params", Wallet]):
    @dataclass(frozen=True, kw_only=True)
    class Params:
        owner_id: UUID
        currency: str
        initial_balance: Decimal
        wallet_id: ShardedId[Wallet] | None = None

    def perform(self, principal: str | None, params: Params) -> Wallet:
        return self.plan.add(
            Wallet.open(
                id=params.wallet_id or ShardedId[Wallet].random(),
                owner_id=params.owner_id,
                currency=params.currency,
                balance=params.initial_balance,
                created_date=self.clock.instant(),
            )
        )


class WalletDepositMoneyAction(Action["WalletDepositMoneyAction.Params", Wallet]):
    @dataclass(frozen=True, kw_only=True)
    class Params:
        wallet_id: ShardedId[Wallet]
        amount: Decimal
        recipient: str

    def __init__(
        self, wallet_repository: AbstractRepository[Wallet, Any, UUID], clock: Clock
    ) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    def perform(self, principal: str | None, params: Params) -> Wallet:
        wallet = self.wallet_repository.get_by_id(params.wallet_id.value)
        return self.plan.update(wallet.deposit(params.amount))


class WalletCloseAction(Action["WalletCloseAction.Params", Wallet]):
    @dataclass(frozen=True, kw_only=True)
    class Params:
        wallet_id: ShardedId[Wallet]

    def __init__(
        self, wallet_repository: AbstractRepository[Wallet, Any, UUID], clock: Clock
    ) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    def perform(self, principal: str | None, params: Params) -> Wallet:
        wallet = self.wallet_repository.get_by_id(params.wallet_id.value)
        closed = wallet.close()
        # An already-closed wallet comes back unchanged, so there is nothing to stage - and
        # therefore no event. The invocation is still recorded.
        return self.plan.update(closed) if closed is not wallet else wallet


class WalletTransferMoneyAction(Action["WalletTransferMoneyAction.Params", tuple[Wallet, Wallet]]):
    """Two models, one transaction. Both balances move or neither does, and nothing in here
    arranges that."""

    @dataclass(frozen=True, kw_only=True)
    class Params:
        from_wallet_id: ShardedId[Wallet]
        to_wallet_id: ShardedId[Wallet]
        amount: Decimal

    def __init__(
        self, wallet_repository: AbstractRepository[Wallet, Any, UUID], clock: Clock
    ) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    def perform(self, principal: str | None, params: Params) -> tuple[Wallet, Wallet]:
        source = self.wallet_repository.get_by_id(params.from_wallet_id.value)
        destination = self.wallet_repository.get_by_id(params.to_wallet_id.value)
        if source.currency != destination.currency:
            raise ValidationError(
                f"cannot transfer {source.currency} into a {destination.currency} wallet"
            )
        return (
            self.plan.update(source.withdraw(params.amount)),
            self.plan.update(destination.deposit(params.amount)),
        )


class CreateNotificationAction(Action["CreateNotificationAction.Params", Notification]):
    """Writes an entity. Emits nothing - and is still recorded, as one sentinel row."""

    @dataclass(frozen=True, kw_only=True)
    class Params:
        wallet_id: ShardedId[Wallet]
        kind: str
        recipient: str
        message: str | None = None

    def perform(self, principal: str | None, params: Params) -> Notification:
        return self.plan.add(
            Notification.create(
                id=Id[Notification].random(),
                wallet_id=params.wallet_id,
                kind=params.kind,
                recipient=params.recipient,
                message=params.message,
            )
        )


class WalletDepositThenFailAction(Action[WalletDepositMoneyAction.Params, Wallet]):
    """Stages a real change and then raises.

    No business has one. It exists because "staging is not writing" cannot be observed any
    other way.
    """

    def __init__(
        self, wallet_repository: AbstractRepository[Wallet, Any, UUID], clock: Clock
    ) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    def perform(self, principal: str | None, params: WalletDepositMoneyAction.Params) -> Wallet:
        wallet = self.wallet_repository.get_by_id(params.wallet_id.value)
        self.plan.update(wallet.deposit(params.amount))
        raise ValidationError("changed my mind")


# ---------------------------------------------------------------- the same, on ekbatan.aio
#
# Twins of the actions above, for the tests that run each scenario on the sync executor and on
# the async one. Each body is its sync twin's with `await` on the reads, and each keeps its sync
# twin's name, so both write the same `action_name` and one assertion covers both - what `name`
# is for: a name kept stable across a rename.


class TwinAction[P, R](AsyncAction[P, R]):
    """An async twin, named as its sync twin is: the class name without ``Async``."""

    @property
    def name(self) -> str:
        return type(self).__name__.removeprefix("Async")


@twin(WalletCreateAction)
class AsyncWalletCreateAction(TwinAction[WalletCreateAction.Params, Wallet]):
    async def perform(self, principal: str | None, params: WalletCreateAction.Params) -> Wallet:
        return self.plan.add(
            Wallet.open(
                id=params.wallet_id or ShardedId[Wallet].random(),
                owner_id=params.owner_id,
                currency=params.currency,
                balance=params.initial_balance,
                created_date=self.clock.instant(),
            )
        )


@twin(WalletDepositMoneyAction)
class AsyncWalletDepositMoneyAction(TwinAction[WalletDepositMoneyAction.Params, Wallet]):
    def __init__(self, wallet_repository: Any, clock: Clock) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    async def perform(
        self, principal: str | None, params: WalletDepositMoneyAction.Params
    ) -> Wallet:
        wallet = await self.wallet_repository.get_by_id(params.wallet_id.value)
        return self.plan.update(wallet.deposit(params.amount))


@twin(WalletCloseAction)
class AsyncWalletCloseAction(TwinAction[WalletCloseAction.Params, Wallet]):
    def __init__(self, wallet_repository: Any, clock: Clock) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    async def perform(self, principal: str | None, params: WalletCloseAction.Params) -> Wallet:
        wallet = await self.wallet_repository.get_by_id(params.wallet_id.value)
        closed = wallet.close()
        return self.plan.update(closed) if closed is not wallet else wallet


@twin(WalletTransferMoneyAction)
class AsyncWalletTransferMoneyAction(
    TwinAction[WalletTransferMoneyAction.Params, tuple[Wallet, Wallet]]
):
    def __init__(self, wallet_repository: Any, clock: Clock) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    async def perform(
        self, principal: str | None, params: WalletTransferMoneyAction.Params
    ) -> tuple[Wallet, Wallet]:
        source = await self.wallet_repository.get_by_id(params.from_wallet_id.value)
        destination = await self.wallet_repository.get_by_id(params.to_wallet_id.value)
        if source.currency != destination.currency:
            raise ValidationError(
                f"cannot transfer {source.currency} into a {destination.currency} wallet"
            )
        return (
            self.plan.update(source.withdraw(params.amount)),
            self.plan.update(destination.deposit(params.amount)),
        )


@twin(CreateNotificationAction)
class AsyncCreateNotificationAction(TwinAction[CreateNotificationAction.Params, Notification]):
    async def perform(
        self, principal: str | None, params: CreateNotificationAction.Params
    ) -> Notification:
        return self.plan.add(
            Notification.create(
                id=Id[Notification].random(),
                wallet_id=params.wallet_id,
                kind=params.kind,
                recipient=params.recipient,
                message=params.message,
            )
        )


@twin(WalletDepositThenFailAction)
class AsyncWalletDepositThenFailAction(TwinAction[WalletDepositMoneyAction.Params, Wallet]):
    def __init__(self, wallet_repository: Any, clock: Clock) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    async def perform(
        self, principal: str | None, params: WalletDepositMoneyAction.Params
    ) -> Wallet:
        wallet = await self.wallet_repository.get_by_id(params.wallet_id.value)
        self.plan.update(wallet.deposit(params.amount))
        raise ValidationError("changed my mind")
