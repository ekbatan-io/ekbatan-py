"""Units of work: a wallet's own, and the transfer saga's three steps.

Each action's parameters are its nested `Params`, written to the outbox as JSON when it runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

from ekbatan import Action, Clock
from ekbatan.ids import Id

from .model import Currency, TransferStep, TransferStepName, Wallet, WalletState
from .repository import TransferStepRepository, WalletRepository


class WalletCreateAction(Action["WalletCreateAction.Params", Wallet]):
    @dataclass(frozen=True, kw_only=True)
    class Params:
        owner_id: UUID
        #: Plain text, as the outbox's JSON writes it: "EUR". The action turns it into a
        #: `Currency`, which refuses anything but three capital letters.
        currency: str
        initial_balance: Decimal

    def perform(self, principal: str | None, params: Params) -> Wallet:
        wallet = Wallet.create_wallet(
            params.owner_id, Currency(params.currency), params.initial_balance, self.clock.instant()
        ).build()
        return self.plan.add(wallet)


class WalletDepositMoneyAction(Action["WalletDepositMoneyAction.Params", Wallet]):
    """A single wallet's deposit - kept for top-ups outside the transfer saga."""

    @dataclass(frozen=True, kw_only=True)
    class Params:
        wallet_id: Id[Wallet]
        amount: Decimal

    def __init__(self, clock: Clock, wallet_repository: WalletRepository) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    def perform(self, principal: str | None, params: Params) -> Wallet:
        wallet = self.wallet_repository.get(params.wallet_id)
        updated = wallet.deposit(params.amount)
        return self.plan.update(updated)


class WalletCloseAction(Action["WalletCloseAction.Params", Wallet]):
    @dataclass(frozen=True, kw_only=True)
    class Params:
        wallet_id: Id[Wallet]

    def __init__(self, clock: Clock, wallet_repository: WalletRepository) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    def perform(self, principal: str | None, params: Params) -> Wallet:
        wallet = self.wallet_repository.get(params.wallet_id)
        closed = wallet.close()
        return self.plan.update(closed)


class InitiateTransferAction(
    Action["InitiateTransferAction.Params", "InitiateTransferAction.Result"]
):
    """Saga step one: debits the source wallet and emits a `TransferInitiatedEvent`.

    The transfer's id is made here; it ties every later event of the saga together. One
    transaction: the source's row and the event commit together, and the in-process fan-out then
    drives step two through `TransferInitiatedEventHandler`.
    """

    @dataclass(frozen=True, kw_only=True)
    class Params:
        from_wallet_id: Id[Wallet]
        to_wallet_id: Id[Wallet]
        amount: Decimal

    @dataclass(frozen=True, kw_only=True)
    class Result:
        transfer_id: UUID
        source_wallet: Wallet

    def __init__(self, clock: Clock, wallet_repository: WalletRepository) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    def perform(self, principal: str | None, params: Params) -> Result:
        transfer_id = uuid4()
        source = self.wallet_repository.get(params.from_wallet_id)
        debited = source.initiate_transfer_out(transfer_id, params.to_wallet_id, params.amount)
        self.plan.update(debited)
        return InitiateTransferAction.Result(transfer_id=transfer_id, source_wallet=debited)


class CompleteTransferAction(Action["CompleteTransferAction.Params", Wallet]):
    """Saga step two: tries to credit the destination wallet, and emits one of two events -
    `TransferCompletedEvent` on the destination when it can, or `TransferFailedEvent` on the
    *source* when the destination is missing, closed, or in another currency, which starts the
    refund through `TransferFailedEventHandler`.

    The step marker makes a replayed delivery of the same event a no-op rather than a second
    credit.
    """

    @dataclass(frozen=True, kw_only=True)
    class Params:
        transfer_id: UUID
        from_wallet_id: Id[Wallet]
        to_wallet_id: Id[Wallet]
        amount: Decimal

    def __init__(
        self,
        clock: Clock,
        wallet_repository: WalletRepository,
        transfer_step_repository: TransferStepRepository,
    ) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository
        self.transfer_step_repository = transfer_step_repository

    def perform(self, principal: str | None, params: Params) -> Wallet:
        if self.transfer_step_repository.exists_step(
            params.transfer_id, TransferStepName.COMPLETE_TRANSFER
        ):
            return self.wallet_repository.get(params.from_wallet_id)
        self.plan.add(
            TransferStep.create(params.transfer_id, TransferStepName.COMPLETE_TRANSFER).build()
        )

        destination = self.wallet_repository.find(params.to_wallet_id)
        if destination is None:
            return self._fail_on_source(params, "destination wallet not found")
        if destination.state is not WalletState.OPENED:
            return self._fail_on_source(params, f"destination wallet is {destination.state.name}")
        source = self.wallet_repository.get(params.from_wallet_id)
        if destination.currency != source.currency:
            return self._fail_on_source(
                params,
                f"currency mismatch: source={source.currency.code} "
                f"destination={destination.currency.code}",
            )

        credited = destination.complete_transfer_in(
            params.transfer_id, params.from_wallet_id, params.amount
        )
        return self.plan.update(credited)

    def _fail_on_source(self, params: Params, reason: str) -> Wallet:
        source = self.wallet_repository.get(params.from_wallet_id)
        marked = source.mark_transfer_failed(
            params.transfer_id, params.to_wallet_id, params.amount, reason
        )
        return self.plan.update(marked)


class RefundTransferAction(Action["RefundTransferAction.Params", Wallet]):
    """The compensation: credits the source wallet back after a failed transfer, and emits a
    `TransferRefundedEvent` that closes the saga, leaving the whole story on the source:
    initiated, failed, refunded.

    Forward-only compensation, not a rollback: the debit committed long ago, and this adds the
    money back. The step marker makes a replayed failure event a no-op rather than a second
    refund.
    """

    @dataclass(frozen=True, kw_only=True)
    class Params:
        transfer_id: UUID
        from_wallet_id: Id[Wallet]
        amount: Decimal

    def __init__(
        self,
        clock: Clock,
        wallet_repository: WalletRepository,
        transfer_step_repository: TransferStepRepository,
    ) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository
        self.transfer_step_repository = transfer_step_repository

    def perform(self, principal: str | None, params: Params) -> Wallet:
        if self.transfer_step_repository.exists_step(
            params.transfer_id, TransferStepName.REFUND_TRANSFER
        ):
            return self.wallet_repository.get(params.from_wallet_id)
        self.plan.add(
            TransferStep.create(params.transfer_id, TransferStepName.REFUND_TRANSFER).build()
        )
        source = self.wallet_repository.get(params.from_wallet_id)
        refunded = source.refund_transfer(params.transfer_id, params.amount)
        return self.plan.update(refunded)
