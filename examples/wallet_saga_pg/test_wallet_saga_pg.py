"""The saga, its compensation, and its replay.

    uv run pytest examples/wallet_saga_pg

Java's `WalletSagaIntegrationTest` covers the same three: the happy path, the compensation
paths, and that a replayed delivery is a no-op.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from ekbatan.clock import SystemClock

from .actions import (
    CompleteTransferAction,
    CompleteTransferParams,
    InitiateTransferAction,
    OpenWalletAction,
    OpenWalletParams,
    TransferParams,
    TransferResult,
)
from .app import SagaService, build
from .model import StepName, Wallet, WalletId
from .schema import EVENTS


@pytest.fixture
def service(tmp_path: Path) -> Iterator[SagaService]:
    # A *running* clock, not a frozen one. The event trail below is read in `event_date` order,
    # and the outbox has no ordinal column - so under a frozen clock every step ties and the
    # order falls back to a random uuid. Each saga step is a separate unit of work at a
    # different instant, which is exactly what makes the trail readable; freezing time would
    # be testing an order neither port promises.
    built = build(f"sqlite:///{tmp_path / 'saga.db'}", clock=SystemClock())
    try:
        yield built
    finally:
        built.close()


def a_wallet(service: SagaService, balance: str = "100.00", currency: str = "EUR") -> Wallet:
    return service.executor.execute(
        OpenWalletAction,
        OpenWalletParams(owner_id=uuid4(), currency=currency, opening_balance=Decimal(balance)),
    )


def transfer(service: SagaService, source: Wallet, target: WalletId, amount: str) -> TransferResult:
    result: TransferResult = service.executor.execute(
        InitiateTransferAction,
        TransferParams(from_wallet_id=source.id, to_wallet_id=target, amount=Decimal(amount)),
    )
    return result


def events_on(service: SagaService, wallet_id: WalletId) -> list[str]:
    """The event trail of one aggregate, in order - what an audit query would read."""
    with service.registry.default.connection(readonly=True) as conn:
        rows = conn.execute(
            select(EVENTS.c.event_type)
            .where(EVENTS.c.model_id == str(wallet_id.value))
            .where(EVENTS.c.event_type.is_not(None))
            .order_by(EVENTS.c.event_date, EVENTS.c.id)
        ).scalars()
        return [row for row in rows]


def test_a_transfer_moves_the_money_in_two_commits(service: SagaService) -> None:
    source, destination = a_wallet(service, "100.00"), a_wallet(service, "0.00")

    result = transfer(service, source, destination.id, "30.00")

    # Step one has committed, on its own: the money has left and has not arrived.
    assert service.wallets.get(source.id).balance == Decimal("70.0000")
    assert service.wallets.get(destination.id).balance == Decimal("0.0000")

    service.deliver_pending_events()

    assert service.wallets.get(source.id).balance == Decimal("70.0000")
    assert service.wallets.get(destination.id).balance == Decimal("30.0000")
    assert service.steps.steps_of(result.transfer_id) == [StepName.CREDIT]


def test_the_source_carries_the_whole_story(service: SagaService) -> None:
    """Choreography leaves its state in the event log rather than in a coordinator."""
    source, destination = a_wallet(service, "100.00"), a_wallet(service, "0.00")

    transfer(service, source, destination.id, "30.00")
    service.deliver_pending_events()

    assert events_on(service, source.id) == ["WalletOpenedEvent", "TransferInitiatedEvent"]
    assert events_on(service, destination.id) == ["WalletOpenedEvent", "TransferCompletedEvent"]


def test_a_closed_destination_is_compensated_not_lost(service: SagaService) -> None:
    """The headline case. The debit already committed, so the money is put back by a *new*
    action with its own commit - forward-only compensation."""
    source, destination = a_wallet(service, "100.00"), a_wallet(service, "0.00")
    service.wallets.update(service.wallets.get(destination.id).close())

    result = transfer(service, source, destination.id, "30.00")
    assert service.wallets.get(source.id).balance == Decimal("70.0000"), "debited"

    service.deliver_pending_events()

    assert service.wallets.get(source.id).balance == Decimal("100.0000"), "and made whole"
    assert service.wallets.get(destination.id).balance == Decimal("0.0000"), "untouched"
    assert service.steps.steps_of(result.transfer_id) == [StepName.CREDIT, StepName.REFUND], (
        "both steps ran - the credit step *attempted* and failed, and the refund compensated it"
    )


def test_the_failure_and_the_refund_land_on_the_source_s_trail(service: SagaService) -> None:
    """`TransferFailedEvent` is emitted on the source, not the destination - the destination may be
    closed, or may not exist at all. Reading one aggregate tells the whole story."""
    source, destination = a_wallet(service, "100.00"), a_wallet(service, "0.00")
    service.wallets.update(service.wallets.get(destination.id).close())

    transfer(service, source, destination.id, "30.00")
    service.deliver_pending_events()

    assert events_on(service, source.id) == [
        "WalletOpenedEvent",
        "TransferInitiatedEvent",
        "TransferFailedEvent",
        "TransferRefundedEvent",
    ]


def test_a_mismatched_currency_is_refused_the_same_way(service: SagaService) -> None:
    source = a_wallet(service, "100.00", "EUR")
    destination = a_wallet(service, "0.00", "USD")

    transfer(service, source, destination.id, "30.00")
    service.deliver_pending_events()

    assert service.wallets.get(source.id).balance == Decimal("100.0000")
    assert "TransferRefundedEvent" in events_on(service, source.id)


def test_a_destination_that_does_not_exist_is_refused_the_same_way(service: SagaService) -> None:
    """There is no aggregate to fail on at the far end, which is exactly why the failure event
    goes on the source."""
    source = a_wallet(service, "100.00")

    transfer(service, source, WalletId(uuid4()), "30.00")
    service.deliver_pending_events()

    assert service.wallets.get(source.id).balance == Decimal("100.0000")
    assert "TransferRefundedEvent" in events_on(service, source.id)


def test_a_replayed_delivery_does_not_credit_twice(service: SagaService) -> None:
    """Local delivery is at-least-once *by design*, so the saga has to survive a repeat.

    The second `CompleteTransferAction` stages the same `(transfer_id, CREDIT)` marker, and
    the unique
    constraint refuses the insert, and the credit beside it is rolled back with it. Invoked
    directly rather than by duplicating a notification row, because what is being tested is the
    action's idempotency and not the dispatcher's bookkeeping.
    """
    # The driver's error, not one of the framework's. Ekbatan classifies what *it* decides -
    # a lost version check is a `StaleRecordError`, a bad argument a `ValidationError` - and
    # deliberately does not translate a constraint the application itself declared. So an
    # application using a unique index for idempotency catches the database's error, which is
    # also the one carrying which constraint fired.
    from sqlalchemy.exc import IntegrityError

    source, destination = a_wallet(service, "100.00"), a_wallet(service, "0.00")
    result = transfer(service, source, destination.id, "30.00")
    service.deliver_pending_events()

    assert service.wallets.get(destination.id).balance == Decimal("30.0000")

    replay = CompleteTransferParams(
        transfer_id=result.transfer_id,
        from_wallet_id=source.id,
        to_wallet_id=destination.id,
        amount=Decimal("30.00"),
        currency="EUR",
    )
    with pytest.raises(IntegrityError):
        service.executor.execute(CompleteTransferAction, replay)

    assert service.wallets.get(destination.id).balance == Decimal("30.0000"), (
        "the credit was rolled back with the marker that refused"
    )
    assert service.steps.steps_of(result.transfer_id) == [StepName.CREDIT], "still one"


def test_a_transfer_larger_than_the_balance_never_starts(service: SagaService) -> None:
    """Refused by the domain, in step one, before any event exists - so there is nothing to
    compensate and no saga to run."""
    from ekbatan.errors import ValidationError

    source, destination = a_wallet(service, "10.00"), a_wallet(service, "0.00")

    with pytest.raises(ValidationError, match="cannot transfer"):
        transfer(service, source, destination.id, "30.00")

    assert service.wallets.get(source.id).balance == Decimal("10.0000")
    assert events_on(service, source.id) == ["WalletOpenedEvent"]


def test_every_step_is_its_own_unit_of_work(service: SagaService) -> None:
    """Three commits in the failing case, two in the happy one - each independently durable,
    which is the property that lets the steps live on different databases later."""
    source, destination = a_wallet(service, "100.00"), a_wallet(service, "0.00")
    service.wallets.update(service.wallets.get(destination.id).close())

    before = _action_ids(service)
    transfer(service, source, destination.id, "30.00")
    service.deliver_pending_events()

    started = _action_ids(service) - before
    assert len(started) == 3, "initiate, complete-but-failed, refund - three transactions"


def _action_ids(service: SagaService) -> set[UUID]:
    with service.registry.default.connection(readonly=True) as conn:
        return set(conn.execute(select(EVENTS.c.action_id)).scalars())
