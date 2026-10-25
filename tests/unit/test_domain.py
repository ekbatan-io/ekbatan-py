"""Builders and copy(), the long form of a change.

`evolve` is the short form and is what the reference application uses. The builder is here for
the same reason it is in the Java implementation: a change touching several fields reads better
named than positional, and `copy()` is how you start from a row you already have.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Self
from uuid import UUID, uuid4

import pytest

if TYPE_CHECKING:
    # For the type checker only - so it does not exist while the tests run. See
    # test_a_class_whose_field_types_cannot_be_read_is_refused.
    from fractions import Fraction

from ekbatan.domain import Entity, EntityBuilder, GenericState, Model, ModelBuilder, ModelEvent
from ekbatan.errors import EkbatanError, ValidationError
from ekbatan.ids import Id, ShardedId
from fixtures.domain import (
    NOW,
    Notification,
    Wallet,
    WalletClosedEvent,
    WalletMoneyDepositedEvent,
    WalletState,
)


def a_wallet(balance: str = "100.00") -> Wallet:
    return Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal(balance),
        created_date=NOW,
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
    deposited = WalletMoneyDepositedEvent(
        model_id="wallet-1", amount=Decimal("1"), new_balance=Decimal("101")
    )

    changed = wallet.copy().balance(Decimal("101")).with_event(deposited).build()

    assert [e.event_type for e in changed.events] == [
        "WalletCreatedEvent",
        "WalletMoneyDepositedEvent",
    ]
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
        .created_date(NOW)
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

        def validate(self) -> None:
            pass  # no rules beyond its types

    step = TransferStep(id=uuid4(), state=Step.COMPLETE, version=1, transfer_id=uuid4())
    # Defined inside a test, so there is nowhere to put a generated builder. `EntityBuilder`
    # by name is the escape hatch `copy()` names when it refuses.
    moved = (
        EntityBuilder(TransferStep).copy_base(step).state(Step.REFUND).increase_version().build()
    )

    assert moved.state is Step.REFUND
    assert moved.version == 2
    assert not hasattr(moved, "created_date"), "an entity is state, not history"
    assert not moved.is_model


def test_an_entity_defaults_its_lifecycle_but_a_model_does_not():
    @dataclass(frozen=True, kw_only=True)
    class Marker(Entity[UUID]):
        note: str

        def validate(self) -> None:
            pass  # no rules beyond its types

    marker = Marker(id=uuid4(), state=GenericState.ACTIVE, version=1, note="x")
    assert marker.state is GenericState.ACTIVE
    assert marker.evolve(state=GenericState.DELETED).is_deleted


def test_evolve_and_the_builder_agree():
    wallet = a_wallet()
    event = WalletMoneyDepositedEvent(
        model_id="wallet-1", amount=Decimal("1"), new_balance=Decimal("101")
    )

    short = wallet.evolve(balance=Decimal("101"), emit=event)
    long = wallet.copy().balance(Decimal("101")).with_event(event).build()

    assert short == long
    assert [e.event_type for e in short.events] == [e.event_type for e in long.events]


def test_a_model_carries_timestamps_and_an_entity_does_not():
    import dataclasses

    model_fields = {f.name for f in dataclasses.fields(Model)}
    entity_fields = {f.name for f in dataclasses.fields(Entity)}
    assert "created_date" in model_fields
    assert "events" in model_fields
    assert "created_date" not in entity_fields
    assert "events" not in entity_fields
    assert entity_fields == {"id", "state", "version"}


def test_events_stay_out_of_equality():
    wallet = a_wallet()
    with_event = (
        wallet.copy()
        .with_event(
            WalletMoneyDepositedEvent(
                model_id="wallet-1", amount=Decimal("0"), new_balance=Decimal("100.00")
            )
        )
        .build()
    )

    assert with_event == wallet, (
        "two readers of one row are looking at the same wallet, mid-change or not"
    )


# -- ModelEvent: Java's ModelEventTest, case for case ---------------------------------------------


@dataclass(frozen=True, kw_only=True)
class _TestEvent(ModelEvent["String"]):
    pass


class _Object:
    pass


@dataclass(frozen=True, kw_only=True)
class _DetailedEvent(ModelEvent[_Object]):
    detail: str


def test_constructor_sets_model_id_and_model_name():
    event = _TestEvent(model_id="model-123")

    assert event.model_id == "model-123"
    assert event.model_name == "String"


def test_model_name_is_the_class_simple_name():
    """``ModelEvent[_Object]`` gives the bare name, as ``getSimpleName()`` does - and a string
    argument gives the same thing, because events are declared before their model."""
    event = _DetailedEvent(model_id="id-1", detail="some detail")

    assert event.model_name == "_Object"
    assert event.detail == "some detail"


def test_constructor_rejects_a_missing_model_id():
    with pytest.raises(TypeError, match="model_id"):
        _TestEvent()  # type: ignore[call-arg]


def test_constructor_rejects_none_model_id():
    with pytest.raises(ValidationError, match="model_id cannot be None or blank"):
        _TestEvent(model_id=None)  # type: ignore[arg-type]


def test_constructor_rejects_blank_model_id():
    with pytest.raises(ValidationError, match="model_id cannot be None or blank"):
        _TestEvent(model_id="")


def test_constructor_rejects_whitespace_model_id():
    with pytest.raises(ValidationError, match="model_id cannot be None or blank"):
        _TestEvent(model_id="   ")


def test_an_event_that_names_no_model_is_refused():
    """Java's ``new ModelEvent<Object>("id", null) {}``. There it is the only constructor, so an
    event that names no model does not compile; here it is refused where the class is defined -
    at import - rather than at its first event (see test_model_event_names_its_model.py)."""
    with pytest.raises(TypeError, match="model class cannot be None"):

        @dataclass(frozen=True, kw_only=True)
        class Unnamed(ModelEvent):  # type: ignore[type-arg]
            pass


def test_model_name_cannot_be_passed():
    """It comes off the class line, as Java's comes off ``Wallet.class`` - so there is no way to
    pass one that disagrees with it."""
    with pytest.raises(TypeError, match="model_name"):
        _TestEvent(model_id="id", model_name="Other")  # type: ignore[call-arg]


def test_a_subclass_inherits_the_model_it_names():
    @dataclass(frozen=True, kw_only=True)
    class MoreDetailed(_DetailedEvent):
        more: int

    assert MoreDetailed(model_id="id", detail="d", more=1).model_name == "_Object"


def test_the_identity_fields_are_in_the_payload_under_javas_spelling():
    """They are columns of their own *and* payload keys, and the duplication is deliberate.

    Java's local event handler deserialises this payload straight into the handler's event class
    (`objectMapper.treeToValue(notification.payload, handler.eventType())`), and every event
    class there carries a `@JsonCreator` taking `@JsonProperty("modelId")` which it hands to
    `ModelEvent`'s constructor - where `Validate.notBlank` refuses a null. A payload written
    without them does not deserialise on the Java side at all.

    `modelId`, not `model_id`: the key is Java's, because Java is the one reading it.
    """
    wallet = a_wallet()
    event = WalletMoneyDepositedEvent(
        model_id=str(wallet.id), amount=Decimal("10"), new_balance=Decimal("110")
    )

    assert list(event.payload()) == ["modelId", "modelName", "amount", "newBalance"]
    assert event.payload()["modelId"] == str(wallet.id)
    assert event.payload()["modelName"] == "Wallet"


def test_a_payload_written_by_either_port_rebuilds_a_complete_event():
    """The round trip that makes a shared eventlog work in both directions.

    ``modelName`` is in the payload and is not passed back in - Java's ``@JsonCreator`` accepts
    it and ignores it in favour of ``Wallet.class``, and this does the same."""
    from ekbatan._json import canonical, decode

    wallet = a_wallet()
    event = WalletMoneyDepositedEvent(
        model_id=str(wallet.id), amount=Decimal("10"), new_balance=Decimal("110")
    )

    rebuilt = decode(WalletMoneyDepositedEvent, canonical(event.payload()))

    assert rebuilt.amount == Decimal("10")
    assert rebuilt.model_id == str(wallet.id), "read back from Java's `modelId` key"
    assert rebuilt.model_name == "Wallet"


def test_with_event_can_be_called_as_many_times_as_the_change_needs():
    wallet = a_wallet()
    built = (
        wallet.copy()
        .with_event(
            WalletMoneyDepositedEvent(
                model_id="wallet-1", amount=Decimal("10"), new_balance=Decimal("110")
            )
        )
        .with_event(
            WalletMoneyDepositedEvent(
                model_id="wallet-1", amount=Decimal("5"), new_balance=Decimal("115")
            )
        )
        .balance(Decimal("115"))
        .build()
    )

    assert [e.event_type for e in built.events] == [
        "WalletCreatedEvent",
        "WalletMoneyDepositedEvent",
        "WalletMoneyDepositedEvent",
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
        "WalletCreatedEvent",
        "WalletMoneyDepositedEvent",
        "WalletMoneyWithdrawnEvent",
        "WalletClosedEvent",
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

        def validate(self) -> None:
            pass  # no rules beyond its types

    wallet = ShardedWallet(
        id=uuid4(),
        state=WalletState.OPENED,
        version=1,
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("100.00"),
        aliases=("main", "eu"),
        created_date=NOW,
    )
    changed = ModelBuilder(ShardedWallet).copy_base(wallet).balance(Decimal("150.00")).build()

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
        ("created_date", None, "created_date cannot be null"),
        # The application's own fields, which Java validates one `Validate.notNull` at a time
        # in every model's constructor. A dataclass can enumerate them, so the rule is stated
        # once in the base and covers the fields an author would have forgotten too.
        ("owner_id", None, "owner_id cannot be null"),
        ("currency", None, "currency cannot be null"),
        ("balance", None, "balance cannot be null"),
    ],
)
def test_a_model_refuses_to_be_built_without_its_invariants(field, value, message):
    fields = {
        "id": ShardedId[Wallet].random(),
        "state": WalletState.OPENED,
        "version": 1,
        "owner_id": uuid4(),
        "currency": "EUR",
        "balance": Decimal("1.00"),
        field: value,
    }
    with pytest.raises(ValidationError, match=message):
        Wallet(**{"created_date": NOW, **fields})


def test_an_optional_field_accepts_none_and_a_required_one_does_not():
    """Java's `Optional<T>` versus `T`, with one sentinel instead of two.

    There, `Optional.empty()` means absent and a null `Optional` *reference* is a bug the type
    system cannot exclude. Here absence **is** `None`, so there is no fourth case to guard
    against: the annotation says whether `None` belongs, and that is the whole rule.
    """

    @dataclass(frozen=True, kw_only=True)
    class Tagged(Wallet):
        note: str | None = None
        label: str = "unset"

    common = {
        "id": ShardedId[Wallet].random(),
        "state": WalletState.OPENED,
        "version": 1,
        "created_date": NOW,
        "owner_id": uuid4(),
        "currency": "EUR",
        "balance": Decimal("1.00"),
    }
    assert Tagged(**common, note=None).note is None, "`str | None` takes it"

    with pytest.raises(ValidationError, match="label cannot be null"):
        Tagged(**common, label=None)


def test_a_class_whose_field_types_cannot_be_read_is_refused():
    """The None checks are read off the annotations, so a name the class's module cannot see
    while the program runs - here one imported only under TYPE_CHECKING - is refused when the
    class is first made, naming it. It used to switch every None check of the class off, the
    base's included, without a word."""

    @dataclass(frozen=True, kw_only=True)
    class Unreadable(Wallet):
        ratio: Fraction | None = None

    with pytest.raises(TypeError, match=r"Unreadable's field types cannot be read .*Fraction"):
        Unreadable(
            id=ShardedId[Wallet].random(),
            state=WalletState.OPENED,
            version=1,
            created_date=NOW,
            owner_id=None,  # refused, were the checks on
            currency="EUR",
            balance=Decimal("1.00"),
        )


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
        "id": Id[Notification].random(),
        "state": GenericState.ACTIVE,
        "version": 1,
        "wallet_id": ShardedId[Wallet].random(),
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
            id=ShardedId[Wallet].random(),
            state=WalletState.OPENED,
            version=1,
            owner_id=uuid4(),
            currency="EUR",
            balance=Decimal("1.00"),
            events=None,
            created_date=NOW,
        )


def test_the_events_a_model_was_built_with_cannot_change_underneath_it():
    """A frozen model holding the caller's list is not frozen.

    Append to that list afterwards and the model gains an event it was never built with, which
    the persister then writes - in a transaction that had already decided what it was writing.
    Java's constructor says ``List.copyOf(...)`` for this; here the tuple is the copy.
    """
    attached = [
        WalletMoneyDepositedEvent(
            model_id="wallet-1", amount=Decimal("1"), new_balance=Decimal("2")
        )
    ]
    wallet = Wallet(
        id=ShardedId[Wallet].random(),
        state=WalletState.OPENED,
        version=1,
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
        events=attached,
        created_date=NOW,
    )

    attached.append(
        WalletMoneyDepositedEvent(
            model_id="wallet-1", amount=Decimal("999"), new_balance=Decimal("999")
        )
    )

    assert isinstance(wallet.events, tuple)
    assert len(wallet.events) == 1, "the model kept what it was built with"
    with pytest.raises(AttributeError):
        wallet.events.append(WalletClosedEvent(model_id="wallet-1"))


def test_a_typed_value_refuses_to_wrap_nothing():
    """Java's TypedValue constructor validates it too.

    ``ShardedId[Wallet](None)`` would compare equal to every other empty ``ShardedId``, hash with
    them, and reach the database as a null in the primary key.
    """
    with pytest.raises(ValidationError, match="ShardedId value cannot be null"):
        ShardedId[Wallet](None)


def test_is_model_separates_the_two_shapes():
    assert a_wallet().is_model
    assert not Notification.create(
        id=Id[Notification].random(),
        wallet_id=ShardedId[Wallet].random(),
        kind="email",
        recipient="someone@example.com",
    ).is_model


# -- the builder a model hands back --------------------------------------------------------------
#
# Java's annotation processor runs on every compile, so `copy()` there returns a generated
# builder or the class does not compile. There is no compile step here, so the refusal is at
# the call. Generated or hand-written makes no difference: what is required is a builder a type
# checker can read, and `ModelBuilder(X)` by name is the deliberate way out.


def test_a_model_without_a_typed_builder_refuses_both_routes():
    @dataclass(frozen=True, kw_only=True)
    class Bare(Model[UUID, WalletState]):
        note: str

        def validate(self) -> None:
            pass  # no rules beyond its types

    row = Bare(id=uuid4(), state=WalletState.OPENED, version=1, created_date=NOW, note="x")

    for call in (lambda: row.copy(), lambda: Bare.builder()):
        with pytest.raises(EkbatanError, match="has not named one"):
            call()


def test_a_hand_written_builder_satisfies_it_as_well_as_a_generated_one():
    """Named on the class line, which is the whole of the wiring (`EKB-0202`).

    No `copy()`, no `builder()`, and no `builds = ByHand` - the model's own declaration is
    what ties the two together, and `__init_subclass__` reads it back.
    """

    class NoteBuilder(ModelBuilder["ByHand", UUID, WalletState]):
        def note(self, value: str) -> Self:
            return self.set(note=value)

    @dataclass(frozen=True, kw_only=True)
    class ByHand(Model[UUID, WalletState, NoteBuilder]):
        note: str

        def validate(self) -> None:
            pass  # no rules beyond its types

    row = ByHand(id=uuid4(), state=WalletState.OPENED, version=1, created_date=NOW, note="x")
    assert row.copy().note("y").build().note == "y"
    assert (
        ByHand.builder()
        .id(row.id)
        .state(WalletState.OPENED)
        .with_initial_version()
        .created_date(NOW)
        .note("z")
        .build()
        .note
        == "z"
    )
    assert NoteBuilder.builds is ByHand, "wired when the model was defined, not when first used"


def test_the_escape_hatch_the_refusal_names_actually_works():
    @dataclass(frozen=True, kw_only=True)
    class Inline(Model[UUID, WalletState]):
        note: str

        def validate(self) -> None:
            pass  # no rules beyond its types

    row = Inline(id=uuid4(), state=WalletState.OPENED, version=1, created_date=NOW, note="x")
    assert ModelBuilder(Inline).copy_base(row).note("y").build().note == "y"


# -- equality: the same row at the same version, as Java's ------------------------------------
#
# Java's ModelTest and EntityTest equality cases, then what only Python needs: the decorator on
# every model would otherwise write a whole-row __eq__, and hashing every field made a model with
# a list field unusable in a set.


def a_notification(**overrides) -> Notification:
    from ekbatan.ids import Id

    values = dict(
        id=Id[Notification].random(),
        wallet_id=ShardedId[Wallet].random(),
        kind="deposit",
        recipient="ada@example.com",
    )
    values.update(overrides)
    return Notification.create(**values)


def test_model_equals_same_fields():
    wallet = a_wallet()

    assert wallet == wallet.copy().build()


def test_model_equals_ignores_events():
    wallet = a_wallet()
    with_event = wallet.copy().with_event(WalletClosedEvent(model_id=str(wallet.id))).build()

    assert with_event == wallet


def test_model_not_equal_different_id():
    wallet = a_wallet()

    assert wallet.copy().id(ShardedId[Wallet].random()).build() != wallet


def test_model_not_equal_different_version():
    wallet = a_wallet()

    assert wallet.copy().version(wallet.version + 1).build() != wallet


def test_model_hash_code_based_on_id():
    wallet = a_wallet()

    assert hash(wallet) == hash(wallet.id)
    assert hash(wallet.copy().balance(Decimal("1")).version(9).build()) == hash(wallet)


def test_model_not_equal_different_timestamps():
    """Java's `Model.equals` compares `createdDate` and `updatedDate` too."""
    from datetime import timedelta

    wallet = a_wallet()

    later = wallet.updated_date + timedelta(seconds=1)  # type: ignore[operator]
    assert wallet.copy().updated_date(later).build() != wallet


def test_model_equality_ignores_the_business_fields():
    """The difference from comparing every field: the same row at the same version is the same
    wallet, whatever one copy of it says the balance is. Java's rule, and Django's."""
    wallet = a_wallet("10.00")

    assert wallet.copy().balance(Decimal("99.00")).build() == wallet


def test_entity_equals_same_fields():
    note = a_notification()

    assert note == note.copy().build()


def test_entity_not_equal_different_id():
    from ekbatan.ids import Id

    note = a_notification()

    assert note.copy().id(Id[Notification].random()).build() != note


def test_entity_not_equal_different_state():
    note = a_notification()

    assert note.copy().state(GenericState.DELETED).build() != note


def test_entity_not_equal_different_version():
    note = a_notification()

    assert note.copy().version(note.version + 1).build() != note


def test_entity_hash_code_based_on_id():
    note = a_notification()

    assert hash(note) == hash(note.id)


def test_entity_equality_ignores_the_business_fields():
    note = a_notification(recipient="ada@example.com")

    assert note.copy().recipient("bob@example.com").build() == note


def test_a_model_with_a_list_field_can_go_in_a_set():
    """The fixture wallet carries `aliases: list[str]`. Hashed field by field, that raised
    `unhashable type: 'list'` for every wallet; hashed by id, as Java's is, it cannot."""
    wallet = a_wallet()
    tagged = wallet.copy().aliases(["savings"]).build()

    assert len({wallet, tagged}) == 1
    assert {wallet: "x"}[tagged] == "x"


def test_a_model_declared_with_its_own_dataclass_decorator_keeps_this_equality():
    """`@dataclass(frozen=True)` writes a whole-row `__eq__` unless the class defines one, and a
    base class's does not count - so every model is handed the rule as it is defined."""
    assert Wallet.__eq__ is Model.__eq__
    assert Wallet.__hash__ is Model.__hash__
    assert Notification.__eq__ is Entity.__eq__


def test_a_subclass_is_a_different_kind_of_row():
    """Java's `getClass() != o.getClass()`."""

    @dataclass(frozen=True, kw_only=True)
    class SpecialWallet(Wallet):
        pass

    wallet = a_wallet()
    import dataclasses

    fields = {f.name: getattr(wallet, f.name) for f in dataclasses.fields(wallet) if f.init}
    special = SpecialWallet(**fields)

    assert special != wallet
    assert wallet != special


def test_a_model_that_writes_its_own_equality_keeps_it_and_so_do_its_subclasses():
    class ByBalance(Wallet):
        def __eq__(self, other):
            return isinstance(other, Wallet) and self.balance == other.balance

        __hash__ = Wallet.__hash__

    class Child(ByBalance):
        pass

    assert ByBalance.__eq__ is not Model.__eq__
    assert Child.__eq__ is ByBalance.__eq__, "the framework does not put its own back"


# -- a field whose setter was never called (Java: the builder field is still null) -------------


def wallet_builder():
    from fixtures.builders import wallet

    return wallet()


def test_a_builder_never_given_a_version_is_refused_not_started_at_one():
    """Java's ``Validate.notNull(builder.version, "version cannot be null")``: forgetting
    ``with_initial_version()`` is a mistake, not a default."""
    builder = (
        wallet_builder()
        .id(ShardedId[Wallet].random())
        .state(WalletState.OPENED)
        .owner_id(uuid4())
        .currency("EUR")
        .balance(Decimal("1"))
        .created_date(NOW)
    )
    with pytest.raises(ValidationError, match=r"^version cannot be null$"):
        builder.build()


def test_an_entity_builder_never_given_a_version_is_refused():
    builder = EntityBuilder(Notification).set(
        id=Id[Notification].random(),
        state=GenericState.ACTIVE,
        wallet_id=ShardedId[Wallet].random(),
        kind="email",
        recipient="a@b.c",
    )
    with pytest.raises(ValidationError, match=r"^version cannot be null$"):
        builder.build()


def test_increase_version_with_no_version_set_is_refused():
    with pytest.raises(ValidationError, match=r"^version cannot be null$"):
        wallet_builder().increase_version()


@pytest.mark.parametrize(
    ("given", "message"),
    [
        ({}, "id cannot be null"),
        ({"id"}, "state cannot be null"),
        ({"id", "state"}, "created_date cannot be null"),
        ({"id", "state", "created_date"}, "version cannot be null"),
        ({"id", "state", "created_date", "version"}, "owner_id cannot be null"),
        ({"id", "state", "created_date", "version", "owner_id"}, "currency cannot be null"),
    ],
)
def test_a_setter_never_called_is_refused_by_name_in_javas_order(given, message):
    """Java checks ``id``, ``events``, ``state``, ``createdDate``, ``version`` in ``Model``'s
    constructor, then the model's own fields in its own."""
    values = {
        "id": ShardedId[Wallet].random(),
        "state": WalletState.OPENED,
        "created_date": NOW,
        "version": 1,
        "owner_id": uuid4(),
    }
    builder = wallet_builder().set(**{k: v for k, v in values.items() if k in given})
    with pytest.raises(ValidationError, match=f"^{message}$"):
        builder.build()


def test_the_version_range_is_checked_before_the_models_own_fields():
    builder = wallet_builder().set(
        id=ShardedId[Wallet].random(), state=WalletState.OPENED, created_date=NOW, version=0
    )
    with pytest.raises(ValidationError, match="version must be greater than or equal to 1"):
        builder.build()


def test_an_optional_field_never_set_is_none():
    """Java leaves an unchecked field null; here that is a field typed ``X | None``."""

    @dataclass(frozen=True, kw_only=True)
    class Tagged(Wallet):
        note: str | None

    built = (
        ModelBuilder(Tagged)
        .set(
            id=ShardedId[Wallet].random(),
            state=WalletState.OPENED,
            version=1,
            created_date=NOW,
            owner_id=uuid4(),
            currency="EUR",
            balance=Decimal("1"),
        )
        .build()
    )
    assert built.note is None


# -- timestamps are instants (Java's `Instant`) -------------------------------------------------


def test_a_models_timestamps_are_kept_exactly_as_given():
    """No zone added, removed or moved - the model holds what it was built with."""
    from datetime import datetime, timedelta, timezone

    from ekbatan.clock import VirtualClock

    clock = VirtualClock(NOW)
    wallet = Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1"),
        created_date=clock.instant(),
    )
    assert wallet.created_date is clock.instant()
    assert wallet.updated_date is wallet.created_date, "falls back to created_date"
    tehran = NOW.replace(tzinfo=timezone(timedelta(hours=3, minutes=30)))
    moved = wallet.copy().updated_date(tehran).build()
    assert moved.updated_date is tehran, "kept as given, offset and all"
    naive = datetime(2026, 1, 1, 10, 0, 0, 123456)
    plain = wallet.copy().updated_date(naive).build()
    assert plain.updated_date is naive and plain.updated_date.tzinfo is None


def test_an_event_that_overrides_its_type_is_refused_when_it_is_defined():
    """Handlers subscribe under the class name; an event stored under another name was never
    delivered to them, and nothing said so. As in Java, the type is the class name, and only
    that."""
    from dataclasses import dataclass

    import pytest

    from ekbatan import ModelEvent

    with pytest.raises(TypeError, match="overrides event_type: an event's type is its class name"):

        @dataclass(frozen=True, kw_only=True)
        class RenamedEvent(ModelEvent[object]):
            @property
            def event_type(self) -> str:
                return "wallet.renamed"


def test_overriding_event_type_is_a_type_error_to_mypy(tmp_path):
    """The editor and mypy say so before anything runs."""
    from mypy import api

    source = tmp_path / "renamed.py"
    source.write_text(
        "from dataclasses import dataclass\n"
        "from ekbatan import ModelEvent\n\n"
        "@dataclass(frozen=True, kw_only=True)\n"
        "class RenamedEvent(ModelEvent[object]):\n"
        "    @property\n"
        "    def event_type(self) -> str:\n"
        "        return 'wallet.renamed'\n"
    )
    out, _, status = api.run(["--strict", "--no-incremental", str(source)])
    assert status == 1, out
    assert 'Cannot override final attribute "event_type"' in out, out


def test_events_about_different_models_are_never_equal():
    """Equality left the model out, so a deposit into one wallet equalled the same deposit into
    another: a set kept one of them, and `event in plan.events` matched the wrong wallet's."""
    from fixtures.domain import WalletMoneyDepositedEvent

    a = WalletMoneyDepositedEvent(
        model_id="wallet-a", amount=Decimal("10"), new_balance=Decimal("110")
    )
    b = WalletMoneyDepositedEvent(
        model_id="wallet-b", amount=Decimal("10"), new_balance=Decimal("110")
    )
    same = WalletMoneyDepositedEvent(
        model_id="wallet-a", amount=Decimal("10"), new_balance=Decimal("110")
    )

    assert a != b
    assert len({a, b}) == 2
    assert a == same and hash(a) == hash(same)
