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

from ekbatan.domain import Entity, GenericState, Model
from ekbatan.errors import ValidationError
from fixtures.domain import (
    Notification,
    NotificationId,
    Wallet,
    WalletClosed,
    WalletId,
    WalletMoneyDeposited,
    WalletState,
)


def a_wallet(balance: str = "100.00") -> Wallet:
    return Wallet.open(
        id=WalletId.random(), owner_id=uuid4(), currency="EUR", balance=Decimal(balance)
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
        Wallet.builder()
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
    assert not moved.is_model


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


def test_an_event_is_told_which_model_it_is_about_rather_than_asked():
    """Java's `ModelEvent` takes `modelId` and `modelName` in its constructor; these are the
    same two fields, filled in by the framework instead of by the application."""
    event = WalletMoneyDeposited(amount=Decimal("10"), new_balance=Decimal("110"))

    assert (event.model_id, event.model_name) == ("", ""), "unset until it is attached"

    wallet = a_wallet()
    told = event.about(wallet)

    assert told.model_id == str(wallet.id)
    assert told.model_name == "Wallet"
    assert told.amount == event.amount, "and nothing else about it changed"
    assert event.model_id == "", "the original is untouched - these are frozen"


def test_the_identity_fields_stay_out_of_the_payload_and_out_of_equality():
    """They have columns of their own, which is where a consumer reads them.

    Java leaves them in the payload too, because they are public fields of its base class and
    Jackson serialises them. `ActionEvent` - the contract - calls the payload raw JSON for the
    consumer to deserialise as it likes, and reads `modelId` from the column.
    """
    event = WalletMoneyDeposited(amount=Decimal("10"), new_balance=Decimal("110"))
    told = event.about(a_wallet())

    assert set(told.payload()) == {"amount", "new_balance"}
    assert told == event, "being told what it is about is not a different event"


def test_an_event_attached_to_a_model_with_no_usable_id_is_refused():
    @dataclass(frozen=True, kw_only=True)
    class Anonymous:
        id: str = ""

    with pytest.raises(ValidationError, match="model_id column"):
        WalletMoneyDeposited(amount=Decimal("1"), new_balance=Decimal("1")).about(Anonymous())


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


def test_copy_base_takes_every_field():
    wallet = a_wallet()
    assert Wallet.builder().copy_base(wallet).build() == wallet


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


# -- what a row refuses to be built as ---------------------------------------------------------
#
# Java checks these in Model's and Entity's constructors, and its ModelTest pins each one by
# message. They are here for the same reason they are there: without them the mistake arrives
# as an IntegrityError from inside a batched insert, naming a column and a driver, several
# frames from the line that made it.


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("id", None, "id cannot be null"),
        ("state", None, "state cannot be null"),
        ("version", None, "version cannot be null"),
        ("version", 0, "version must be greater than or equal to 1"),
        ("version", -1, "version must be greater than or equal to 1"),
    ],
)
def test_a_model_refuses_to_be_built_without_its_invariants(field, value, message):
    fields = {
        "id": WalletId.random(),
        "state": WalletState.OPENED,
        "owner_id": uuid4(),
        "currency": "EUR",
        "balance": Decimal("1.00"),
        field: value,
    }
    with pytest.raises(ValidationError, match=message):
        Wallet(**fields)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("id", None, "id cannot be null"),
        ("state", None, "state cannot be null"),
        ("version", 0, "version must be greater than or equal to 1"),
    ],
)
def test_an_entity_refuses_the_same_invariants(field, value, message):
    fields = {
        "id": NotificationId.random(),
        "state": GenericState.ACTIVE,
        "wallet_id": WalletId.random(),
        "kind": "email",
        "recipient": "someone@example.com",
        field: value,
    }
    with pytest.raises(ValidationError, match=message):
        Notification(**fields)


def test_the_version_a_row_starts_at_is_one():
    assert a_wallet().version == 1


def test_a_row_refuses_a_null_event_list():
    with pytest.raises(ValidationError, match="events cannot be null"):
        Wallet(
            id=WalletId.random(),
            state=WalletState.OPENED,
            owner_id=uuid4(),
            currency="EUR",
            balance=Decimal("1.00"),
            events=None,
        )


def test_the_events_a_model_was_built_with_cannot_change_underneath_it():
    """A frozen model holding the caller's list is not frozen.

    Append to that list afterwards and the model gains an event it was never built with, which
    the persister then writes - in a transaction that had already decided what it was writing.
    Java's constructor says ``List.copyOf(...)`` for this; here the tuple is the copy.
    """
    attached = [WalletMoneyDeposited(amount=Decimal("1"), new_balance=Decimal("2"))]
    wallet = Wallet(
        id=WalletId.random(),
        state=WalletState.OPENED,
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
        events=attached,
    )

    attached.append(WalletMoneyDeposited(amount=Decimal("999"), new_balance=Decimal("999")))

    assert isinstance(wallet.events, tuple)
    assert len(wallet.events) == 1, "the model kept what it was built with"
    with pytest.raises(AttributeError):
        wallet.events.append(WalletClosed())


def test_a_typed_value_refuses_to_wrap_nothing():
    """Java's TypedValue constructor validates it too.

    ``WalletId(None)`` compares equal to every other empty WalletId, hashes with them, and
    reaches the database as a null in the primary key.
    """
    with pytest.raises(ValidationError, match="WalletId value cannot be null"):
        WalletId(None)


def test_is_model_separates_the_two_shapes():
    assert a_wallet().is_model
    assert not Notification.create(
        id=NotificationId.random(),
        wallet_id=WalletId.random(),
        kind="email",
        recipient="someone@example.com",
    ).is_model
