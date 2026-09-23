"""The three steps, and the two ordinary actions around them.

Each step is one unit of work on one aggregate, and each commits on its own. Read them in the
order the saga runs them: `InitiateTransferAction`, then `CompleteTransferAction`, then -
only when the
second could not finish - `RefundTransferAction`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from ekbatan import Action, Clock

from .model import (
    StepName,
    TransferFailedEvent,
    TransferInitiatedEvent,
    TransferStep,
    Wallet,
    WalletId,
)
from .repository import WalletRepository


@dataclass(frozen=True, kw_only=True)
class OpenWalletParams:
    owner_id: UUID
    currency: str
    opening_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class TransferParams:
    from_wallet_id: WalletId
    to_wallet_id: WalletId
    amount: Decimal


@dataclass(frozen=True, kw_only=True)
class CompleteTransferParams:
    """What the handler read off the event, handed back as parameters.

    The event itself is not passed around: an action takes parameters, and keeping it that way
    is what lets the same action be invoked from a test, a REST endpoint or a replay tool
    without any of them holding an `EventEnvelope`.
    """

    transfer_id: UUID
    from_wallet_id: WalletId
    to_wallet_id: WalletId
    amount: Decimal
    currency: str


@dataclass(frozen=True, kw_only=True)
class RefundTransferParams:
    transfer_id: UUID
    from_wallet_id: WalletId
    to_wallet_id: WalletId
    amount: Decimal
    reason: str


@dataclass(frozen=True, kw_only=True)
class TransferResult:
    transfer_id: UUID
    source: Wallet


class OpenWalletAction(Action[OpenWalletParams, Wallet]):
    def perform(self, principal: str | None, params: OpenWalletParams) -> Wallet:
        return self.plan.add(
            Wallet.open(
                owner_id=params.owner_id,
                currency=params.currency,
                balance=params.opening_balance,
                created_date=self.clock.now(),
            )
        )


class InitiateTransferAction(Action[TransferParams, TransferResult]):
    """Step one: debit the source and say so. One aggregate, one transaction.

    Note what it does *not* do: touch the destination. That is the whole point - the two
    wallets may be on different databases tomorrow, and a step that reached for both would have
    to be rewritten when they are.
    """

    def __init__(self, wallets: WalletRepository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    def perform(self, principal: str | None, params: TransferParams) -> TransferResult:
        source = self.wallets.get(params.from_wallet_id)
        debited, transfer_id = source.initiate_transfer_out(
            to=params.to_wallet_id, amount=params.amount
        )
        return TransferResult(transfer_id=transfer_id, source=self.plan.update(debited))


class CompleteTransferAction(Action[CompleteTransferParams, Wallet | None]):
    """Step two: credit the destination, or fail the transfer on the source's trail.

    Both outcomes are ordinary units of work. "Failed" here is a business outcome, not an
    exception: raising would make the handler retry, and retrying a transfer into a closed
    wallet will never start working.
    """

    def __init__(self, wallets: WalletRepository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    def perform(self, principal: str | None, params: CompleteTransferParams) -> Wallet | None:
        # Staged *before* the credit, so the unique constraint decides whether this delivery is
        # the first one. A replay stages the same marker, the insert is refused, and the credit
        # beside it is rolled back with it - which is why the money cannot arrive twice.
        self.plan.add(TransferStep.of(params.transfer_id, StepName.CREDIT))

        arriving = TransferInitiatedEvent(
            model_id=str(params.from_wallet_id),
            transfer_id=params.transfer_id,
            from_wallet_id=params.from_wallet_id.value,
            to_wallet_id=params.to_wallet_id.value,
            amount=params.amount,
            currency=params.currency,
        )

        destination = self.wallets.find(params.to_wallet_id)
        refusal = "destination wallet does not exist" if destination is None else None
        if destination is not None:
            refusal = destination.can_receive(arriving)

        if refusal is not None:
            source = self.wallets.get(params.from_wallet_id)
            self.plan.update(source.mark_transfer_failed(arriving, refusal))
            return None

        assert destination is not None  # narrowed by `refusal is None`
        return self.plan.update(destination.complete_transfer_in(arriving))


class RefundTransferAction(Action[RefundTransferParams, Wallet]):
    """Step three: put the money back.

    A new change with its own commit and its own event, not a rollback of step one - step one
    committed long ago. Forward-only compensation is the only kind available once a
    transaction has ended, and the outbox is what makes it as durable as the debit was.
    """

    def __init__(self, wallets: WalletRepository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    def perform(self, principal: str | None, params: RefundTransferParams) -> Wallet:
        self.plan.add(TransferStep.of(params.transfer_id, StepName.REFUND))

        source = self.wallets.get(params.from_wallet_id)
        failure = TransferFailedEvent(
            model_id=str(params.from_wallet_id),
            transfer_id=params.transfer_id,
            from_wallet_id=params.from_wallet_id.value,
            to_wallet_id=params.to_wallet_id.value,
            amount=params.amount,
            reason=params.reason,
        )
        return self.plan.update(source.refund_transfer(failure))


__all__ = [
    "CompleteTransferAction",
    "CompleteTransferParams",
    "InitiateTransferAction",
    "OpenWalletAction",
    "OpenWalletParams",
    "RefundTransferAction",
    "RefundTransferParams",
    "TransferParams",
    "TransferResult",
]
