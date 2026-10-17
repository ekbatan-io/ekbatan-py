"""A transfer between two wallets, as a saga.

The problem: moving money from one wallet to another touches two aggregates, and this framework
commits one unit of work to one transaction. Debiting one wallet and crediting the other in a
single transaction would work here - they are on the same database - but it does not survive
the first time the two live on different shards, or the second wallet moves to another service.

So the transfer is three single-aggregate steps, each its own transaction, chained by events:

    InitiateTransferAction  debit the source, emit TransferInitiatedEvent
      -> CompleteTransferAction  credit the destination, emit TransferCompletedEvent
                           ...or, if it cannot, emit TransferFailedEvent on the *source*
      -> RefundTransferAction    credit the source back, emit TransferRefundedEvent

Two ideas carry the whole design:

**Forward-only compensation.** `refund_transfer` is not a rollback. Step one's debit committed
long ago and cannot be undone; the refund is a *new* change with its own commit and its own
event. The outbox makes the compensation exactly as durable as the thing it compensates.

**Choreography, not orchestration.** There is no saga state machine. Each handler knows only
its own next step, and the state of a transfer is the event trail on the source wallet:
`TransferInitiatedEvent -> TransferFailedEvent -> TransferRefundedEvent` reads the whole
story from one
aggregate, which is what an audit query gets to rely on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from ekbatan import Entity, GenericState, Model, ModelEvent, ShardedId
from ekbatan.errors import ValidationError
from ekbatan.ids import Id

from .builders import WalletBuilder


class WalletState(Enum):
    OPENED = "OPENED"
    CLOSED = "CLOSED"


# -- events -------------------------------------------------------------------------------------
#
# Every one of them carries the whole transfer: id, both wallets, the amount. That is what lets
# a handler act on `envelope.event` alone, without reaching back for the action's parameters or
# for another row. Self-describing events are what make choreography possible - a handler that
# had to look things up would need to know where they are.


@dataclass(frozen=True, kw_only=True)
class WalletOpenedEvent(ModelEvent["Wallet"]):
    owner_id: UUID
    currency: str
    opening_balance: Decimal


@dataclass(frozen=True, kw_only=True)
class TransferInitiatedEvent(ModelEvent["Wallet"]):
    transfer_id: UUID
    from_wallet_id: UUID
    to_wallet_id: UUID
    amount: Decimal
    currency: str


@dataclass(frozen=True, kw_only=True)
class TransferCompletedEvent(ModelEvent["Wallet"]):
    transfer_id: UUID
    from_wallet_id: UUID
    to_wallet_id: UUID
    amount: Decimal


@dataclass(frozen=True, kw_only=True)
class TransferFailedEvent(ModelEvent["Wallet"]):
    """Emitted on the **source** wallet, not the destination.

    The destination may be closed, or may not exist at all - there is no aggregate there to
    attach a failure to. Putting it on the source is also what keeps the trail readable from
    one place.
    """

    transfer_id: UUID
    from_wallet_id: UUID
    to_wallet_id: UUID
    amount: Decimal
    reason: str


@dataclass(frozen=True, kw_only=True)
class TransferRefundedEvent(ModelEvent["Wallet"]):
    transfer_id: UUID
    from_wallet_id: UUID
    amount: Decimal


# -- the aggregate ------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Wallet(Model[ShardedId["Wallet"], WalletState, WalletBuilder]):
    owner_id: UUID
    currency: str
    balance: Decimal

    @classmethod
    def open(
        cls,
        *,
        owner_id: UUID,
        currency: str,
        balance: Decimal,
        id: ShardedId[Wallet] | None = None,
        created_date: datetime,
    ) -> Wallet:
        wallet_id = id or ShardedId[Wallet].random()
        return (
            WalletBuilder()
            .id(wallet_id)
            .state(WalletState.OPENED)
            .owner_id(owner_id)
            .currency(currency)
            .balance(balance)
            .with_initial_version()
            .created_date(created_date)
            .with_event(
                WalletOpenedEvent(
                    model_id=str(wallet_id),
                    owner_id=owner_id,
                    currency=currency,
                    opening_balance=balance,
                )
            )
            .build()
        )

    def close(self) -> Wallet:
        if self.state is WalletState.CLOSED:
            return self
        return self.copy().state(WalletState.CLOSED).build()

    # -- step one, on the source ----------------------------------------------------------------

    def initiate_transfer_out(
        self, *, to: ShardedId[Wallet], amount: Decimal
    ) -> tuple[Wallet, UUID]:
        """Debit now, and say so. The money has left; where it is going is the saga's problem.

        Returns the transfer's id alongside the wallet, because every later step is keyed by it
        and the caller needs it to report the transfer back to whoever asked.
        """
        if self.state is not WalletState.OPENED:
            raise ValidationError(f"wallet {self.id} is {self.state.name}; cannot transfer out")
        if amount <= 0:
            raise ValidationError(f"transfer amount must be positive, got {amount}")
        if amount > self.balance:
            raise ValidationError(f"cannot transfer {amount}: balance is {self.balance}")

        transfer_id = uuid4()
        return (
            self.copy()
            .balance(self.balance - amount)
            .with_event(
                TransferInitiatedEvent(
                    model_id=str(self.id),
                    transfer_id=transfer_id,
                    from_wallet_id=self.id.value,
                    to_wallet_id=to.value,
                    amount=amount,
                    currency=self.currency,
                )
            )
            .build()
        ), transfer_id

    # -- step two, on the destination -----------------------------------------------------------

    def complete_transfer_in(self, event: TransferInitiatedEvent) -> Wallet:
        return (
            self.copy()
            .balance(self.balance + event.amount)
            .with_event(
                TransferCompletedEvent(
                    model_id=str(self.id),
                    transfer_id=event.transfer_id,
                    from_wallet_id=event.from_wallet_id,
                    to_wallet_id=event.to_wallet_id,
                    amount=event.amount,
                )
            )
            .build()
        )

    def can_receive(self, event: TransferInitiatedEvent) -> str | None:
        """Why this wallet cannot take the transfer, or None if it can."""
        if self.state is not WalletState.OPENED:
            return f"destination wallet is {self.state.name}"
        if self.currency != event.currency:
            return f"destination holds {self.currency}, transfer is {event.currency}"
        return None

    # -- step two's other outcome, back on the source --------------------------------------------

    def mark_transfer_failed(self, event: TransferInitiatedEvent, reason: str) -> Wallet:
        """A version-only update that carries an event.

        No balance changes here - the money is still gone, and putting it back is step three's
        job. This exists so the failure lands on the source's event trail, which is the only
        place the whole transfer can be read from.
        """
        return (
            self.copy()
            .with_event(
                TransferFailedEvent(
                    model_id=str(self.id),
                    transfer_id=event.transfer_id,
                    from_wallet_id=event.from_wallet_id,
                    to_wallet_id=event.to_wallet_id,
                    amount=event.amount,
                    reason=reason,
                )
            )
            .build()
        )

    # -- step three, the compensation -------------------------------------------------------------

    def refund_transfer(self, event: TransferFailedEvent) -> Wallet:
        """Credit the money back. A new change, not an undo - see the module docstring."""
        return (
            self.copy()
            .balance(self.balance + event.amount)
            .with_event(
                TransferRefundedEvent(
                    model_id=str(self.id),
                    transfer_id=event.transfer_id,
                    from_wallet_id=event.from_wallet_id,
                    amount=event.amount,
                )
            )
            .build()
        )


class StepName(Enum):
    """Named for the *step*, not for how it turned out.

    `CREDIT` is staged by `CompleteTransferAction` whether the credit happened or the transfer was
    failed instead, because what the marker guards is "step two has run for this transfer" -
    and a replay must not re-run it either way.
    """

    CREDIT = "CREDIT"
    REFUND = "REFUND"


@dataclass(frozen=True, kw_only=True)
class TransferStep(Entity[Id["TransferStep"]]):
    """That one step of one transfer has already been done.

    Local delivery is **at least once**: a handler can be invoked twice for the same event, by
    design, because the alternative is losing one. So the steps that move money stage one of
    these first, under a unique constraint on `(transfer_id, step)`. A replay hits the
    constraint and the whole unit of work - marker and credit together - is refused, which is
    why the credit cannot happen twice.

    An entity rather than a model: nobody downstream cares that a marker was written, and it
    emits nothing.
    """

    transfer_id: UUID
    step: StepName

    @classmethod
    def of(cls, transfer_id: UUID, step: StepName) -> TransferStep:
        return cls(
            id=Id[TransferStep].random(),
            state=GenericState.ACTIVE,
            version=1,
            transfer_id=transfer_id,
            step=step,
        )
