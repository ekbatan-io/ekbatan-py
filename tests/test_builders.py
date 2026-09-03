"""Builders and copy(), the long form of a change.

`evolve` is the short form and is what the reference application uses. The builder is here for
the same reason it is in the Java implementation: a change touching several fields reads better
named than positional, and `copy()` is how you start from a row you already have.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

import pytest
from examples.wallet.model import Wallet, WalletMoneyDeposited, WalletState

from ekbatan.domain import Builder, Entity, GenericState, Model, ModelEvent
from ekbatan.errors import ValidationError
from ekbatan.ids import Id


def a_wallet(balance: str = "100.00") -> Wallet:
    return Wallet.open(
        id=Id[Wallet].random(), owner_id=uuid4(), currency="EUR", balance=Decimal(balance)
    )


def test_copy_starts_from_the_row_you_have():
    wallet = a_wallet()
    same = wallet.copy().build()

    assert same == wallet
    assert same is not wallet
    assert same.events == wallet.events, "the events come across too"


def test_the_builder_sets_any_field_of_the_target():
    wallet = a_wallet()
    changed = (
        wallet.copy().balance(Decimal("250.00")).currency("USD").state(WalletState.CLOSED).build()
    )

    assert changed.balance == Decimal("250.00")
    assert changed.currency == "USD"
    assert changed.state is WalletState.CLOSED
    assert changed.id == wallet.id
    assert wallet.balance == Decimal("100.00"), "the original is untouched"


def test_versions_move_through_the_builder():
    wallet = a_wallet()
    assert wallet.copy().with_initial_version().build().version == 1
    assert wallet.copy().increase_version().build().version == 2
    assert wallet.copy().increase_version().increase_version().build().version == 3
    assert wallet.copy().version(9).build().version == 9


def test_events_are_attached_through_the_builder():
    wallet = a_wallet()
    deposited = WalletMoneyDeposited(amount=Decimal("1"), new_balance=Decimal("101"))

    changed = wallet.copy().balance(Decimal("101")).with_event(deposited).build()

    assert [e.event_type for e in changed.events] == ["WalletCreated", "WalletMoneyDeposited"]
    assert changed.copy().events(()).build().events == ()


def test_a_builder_refuses_a_field_the_target_does_not_have():
    wallet = a_wallet()
    with pytest.raises(AttributeError, match="no field 'nonsense'"):
        wallet.copy().nonsense("x")
    with pytest.raises(ValidationError, match="no field"):
        wallet.copy().set(nonsense="x")


def test_a_builder_can_be_started_from_nothing():
    built = (
        Builder(Wallet)
        .id(uuid4())
        .state(WalletState.OPENED)
        .owner_id(uuid4())
        .currency("GBP")
        .balance(Decimal("5.00"))
        .with_initial_version()
        .build()
    )
    assert built.currency == "GBP"
    assert built.version == 1
    assert built.events == ()


def test_an_entity_has_a_builder_too_and_no_timestamps():
    class Step(Enum):
        COMPLETE = "COMPLETE"
        REFUND = "REFUND"
        DELETED = "DELETED"

    @dataclass(frozen=True, kw_only=True)
    class TransferStep(Entity[UUID, Step]):
        transfer_id: UUID

    step = TransferStep(id=uuid4(), state=Step.COMPLETE, transfer_id=uuid4())
    moved = step.copy().state(Step.REFUND).increase_version().build()

    assert moved.state is Step.REFUND
    assert moved.version == 2
    assert not hasattr(moved, "created_date"), "an entity is state, not history"
    assert not moved.emits_events


def test_an_entity_defaults_its_lifecycle_but_a_model_does_not():
    @dataclass(frozen=True, kw_only=True)
    class Marker(Entity[UUID]):
        note: str

    marker = Marker(id=uuid4(), state=GenericState.ACTIVE, note="x")
    assert marker.state is GenericState.ACTIVE
    assert marker.evolve(state=GenericState.DELETED).is_deleted


def test_evolve_and_the_builder_agree():
    wallet = a_wallet()
    event = WalletMoneyDeposited(amount=Decimal("1"), new_balance=Decimal("101"))

    short = wallet.evolve(balance=Decimal("101"), emit=event)
    long = wallet.copy().balance(Decimal("101")).with_event(event).build()

    assert short == long
    assert [e.event_type for e in short.events] == [e.event_type for e in long.events]


def test_a_model_carries_timestamps_and_an_entity_does_not():
    assert "created_date" in Model.__dataclass_fields__
    assert "events" in Model.__dataclass_fields__
    assert "created_date" not in Entity.__dataclass_fields__
    assert "events" not in Entity.__dataclass_fields__
    assert set(Entity.__dataclass_fields__) == {"id", "state", "version"}


def test_events_stay_out_of_equality():
    wallet = a_wallet()
    with_event = (
        wallet.copy()
        .with_event(WalletMoneyDeposited(amount=Decimal("0"), new_balance=Decimal("100.00")))
        .build()
    )

    assert with_event == wallet, (
        "two readers of one row are looking at the same wallet, mid-change or not"
    )


def test_an_event_carries_no_identity():
    assert set(ModelEvent.__dataclass_fields__) == set(), (
        "which model an event is about is known by the model it is attached to"
    )


def test_with_event_can_be_called_as_many_times_as_the_change_needs():
    wallet = a_wallet()
    built = (
        wallet.copy()
        .with_event(WalletMoneyDeposited(amount=Decimal("10"), new_balance=Decimal("110")))
        .with_event(WalletMoneyDeposited(amount=Decimal("5"), new_balance=Decimal("115")))
        .balance(Decimal("115"))
        .build()
    )

    assert [e.event_type for e in built.events] == [
        "WalletCreated",
        "WalletMoneyDeposited",
        "WalletMoneyDeposited",
    ], "each call appends; the last one does not replace the others"
    assert [e.amount for e in built.events[1:]] == [Decimal("10"), Decimal("5")], (
        "and they keep the order they were attached in, which is the order they reach the outbox"
    )


def test_domain_methods_chain_and_their_events_accumulate():
    wallet = a_wallet()
    closed = wallet.deposit(Decimal("50")).withdraw(Decimal("20")).close()

    assert closed.balance == Decimal("130.00")
    assert closed.state is WalletState.CLOSED
    assert [e.event_type for e in closed.events] == [
        "WalletCreated",
        "WalletMoneyDeposited",
        "WalletMoneyWithdrawn",
        "WalletClosed",
    ]


def test_copy_from_takes_every_field():
    wallet = a_wallet()
    assert Builder(Wallet).copy_from(wallet).build() == wallet


def test_the_inherited_copy_carries_fields_the_framework_never_heard_of():
    """The whole reason copy() is not written per model."""

    @dataclass(frozen=True, kw_only=True)
    class ShardedWallet(Model[UUID, WalletState]):
        owner_id: UUID
        currency: str
        balance: Decimal
        aliases: tuple[str, ...] = ()

    wallet = ShardedWallet(
        id=uuid4(),
        state=WalletState.OPENED,
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("100.00"),
        aliases=("main", "eu"),
    )
    changed = wallet.copy().balance(Decimal("150.00")).build()

    assert changed.balance == Decimal("150.00")
    assert changed.aliases == ("main", "eu"), (
        "a field added to the model must not need copy() edited to survive a change"
    )
    assert changed.owner_id == wallet.owner_id
