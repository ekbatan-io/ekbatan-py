"""Java's `ActionSpecTest`, case for case, then the cases only Python needs.

The fixtures are Java's: `TestModel` and `TestProduct`, and the five actions - create, create
both, delete, rename (an update that emits nothing) and one that always fails. Every failure
message is asserted word for word, because a test helper's message is its user interface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Self

import pytest

from ekbatan import Action, ActionSpec, Clock, VirtualClock
from ekbatan.domain import Model, ModelBuilder, ModelEvent
from ekbatan.ids import Id

# -- Java's fixtures ----------------------------------------------------------------------------


class TestState(Enum):
    __test__ = False
    ACTIVE = "ACTIVE"
    DELETED = "DELETED"


class TestModelBuilder(ModelBuilder["TestModel", Id["TestModel"], TestState]):
    __test__ = False

    def name(self, value: str) -> Self:
        return self.set(name=value)


class TestProductBuilder(ModelBuilder["TestProduct", Id["TestProduct"], TestState]):
    __test__ = False

    def sku(self, value: str) -> Self:
        return self.set(sku=value)


@dataclass(frozen=True, kw_only=True)
class TestCreatedEvent(ModelEvent["TestModel"]):
    __test__ = False
    name: str


@dataclass(frozen=True, kw_only=True)
class TestDeletedEvent(ModelEvent["TestModel"]):
    __test__ = False


@dataclass(frozen=True, kw_only=True)
class TestProductCreatedEvent(ModelEvent["TestProduct"]):
    __test__ = False
    sku: str


@dataclass(frozen=True, kw_only=True)
class TestModel(Model[Id["TestModel"], TestState, TestModelBuilder]):
    __test__ = False
    name: str


@dataclass(frozen=True, kw_only=True)
class TestProduct(Model[Id["TestProduct"], TestState, TestProductBuilder]):
    __test__ = False
    sku: str


def a_model(name: str, created: datetime) -> TestModel:
    """``testModel().id(Id.random(...)).state(ACTIVE)...withInitialVersion().build()``."""
    return (
        TestModelBuilder()
        .id(Id[TestModel].random())
        .state(TestState.ACTIVE)
        .name(name)
        .created_date(created)
        .with_initial_version()
        .build()
    )


@dataclass(frozen=True)
class CreateParams:
    name: str


@dataclass(frozen=True)
class CreateBothParams:
    name: str
    sku: str


@dataclass(frozen=True)
class RenameParams:
    new_name: str


@dataclass(frozen=True)
class FailingParams:
    message: str


class CreateAction(Action[CreateParams, TestModel]):
    def perform(self, principal: str | None, params: CreateParams) -> TestModel:
        model_id = Id[TestModel].random()
        return self.plan.add(
            TestModelBuilder()
            .id(model_id)
            .state(TestState.ACTIVE)
            .name(params.name)
            .created_date(self.clock.instant())
            .with_initial_version()
            .with_event(TestCreatedEvent(model_id=str(model_id), name=params.name))
            .build()
        )


class CreateBothAction(Action[CreateBothParams, TestModel]):
    def perform(self, principal: str | None, params: CreateBothParams) -> TestModel:
        product_id = Id[TestProduct].random()
        self.plan.add(
            TestProductBuilder()
            .id(product_id)
            .state(TestState.ACTIVE)
            .sku(params.sku)
            .created_date(self.clock.instant())
            .with_initial_version()
            .with_event(TestProductCreatedEvent(model_id=str(product_id), sku=params.sku))
            .build()
        )
        model_id = Id[TestModel].random()
        return self.plan.add(
            TestModelBuilder()
            .id(model_id)
            .state(TestState.ACTIVE)
            .name(params.name)
            .created_date(self.clock.instant())
            .with_initial_version()
            .with_event(TestCreatedEvent(model_id=str(model_id), name=params.name))
            .build()
        )


class DeleteAction(Action[None, TestModel]):
    def __init__(self, clock: Clock, existing: TestModel) -> None:
        super().__init__(clock)
        self.existing = existing

    def perform(self, principal: str | None, params: None) -> TestModel:
        return self.plan.update(
            self.existing.copy()
            .state(TestState.DELETED)
            .with_event(TestDeletedEvent(model_id=str(self.existing.id)))
            .build()
        )


class RenameAction(Action[RenameParams, TestModel]):
    """An update that emits nothing - the case `assert_no_events` exists for."""

    def __init__(self, clock: Clock, existing: TestModel) -> None:
        super().__init__(clock)
        self.existing = existing

    def perform(self, principal: str | None, params: RenameParams) -> TestModel:
        return self.plan.update(self.existing.copy().name(params.new_name).build())


class FailingAction(Action[FailingParams, None]):
    """Java's throws `IllegalArgumentException`; `ValueError` is Python's."""

    def perform(self, principal: str | None, params: FailingParams) -> None:
        raise ValueError(params.message)


def message(text: str) -> str:
    """``hasMessageContaining``, for `pytest.raises(match=...)`."""
    return re.escape(text)


JAN_1 = datetime(2025, 1, 1, tzinfo=UTC)


@pytest.fixture
def clock() -> VirtualClock:
    return VirtualClock()


@pytest.fixture
def existing(clock: VirtualClock) -> TestModel:
    return a_model("test", clock.instant())


# -- assert_added -------------------------------------------------------------------------------


def test_assert_added_single_with_verifier(clock):
    clock.pause_at(JAN_1)

    def verify(model: TestModel) -> None:
        assert model.name == "my-wallet"
        assert model.state is TestState.ACTIVE
        assert model.created_date == JAN_1, "an aware UTC instant, as Java's Instant"

    (
        ActionSpec.of(CreateAction(clock))
        .with_principal("test-user")
        .execute(CreateParams("my-wallet"))
        .assert_added(TestModel, verify)
        .assert_emitted(TestCreatedEvent)
        .assert_no_updates()
    )


def test_assert_added_with_count(clock):
    ActionSpec.of(CreateAction(clock)).with_principal("test-user").execute(
        CreateParams("test")
    ).assert_added(TestModel, 1)


def test_assert_added_with_count_and_verifier(clock):
    def verify(models: list[TestModel]) -> None:
        assert len(models) == 1
        assert models[0].name == "test"

    ActionSpec.of(CreateAction(clock)).with_principal("test-user").execute(
        CreateParams("test")
    ).assert_added(TestModel, 1, verify)


def test_assert_added_fails_when_count_mismatch(clock):
    assert_ = (
        ActionSpec.of(CreateAction(clock)).with_principal("test-user").execute(CreateParams("test"))
    )

    with pytest.raises(
        AssertionError, match=message("Expected 2 addition(s) of TestModel but found 1")
    ):
        assert_.assert_added(TestModel, 2)


# -- assert_updated -----------------------------------------------------------------------------


def test_assert_updated_single_with_verifier(clock):
    existing = a_model("to-delete", clock.instant())

    (
        ActionSpec.of(DeleteAction(clock, existing))
        .with_principal("admin")
        .execute(None)
        .assert_updated(TestModel, lambda model: model.state is TestState.DELETED)
        .assert_emitted(TestDeletedEvent)
        .assert_no_additions()
    )


def test_assert_updated_with_count(clock, existing):
    ActionSpec.of(DeleteAction(clock, existing)).with_principal("admin").execute(
        None
    ).assert_updated(TestModel, 1)


def test_assert_updated_fails_when_no_updates(clock):
    assert_ = (
        ActionSpec.of(CreateAction(clock)).with_principal("test-user").execute(CreateParams("test"))
    )

    with pytest.raises(
        AssertionError, match=message("Expected 1 update(s) of TestModel but found 0")
    ):
        assert_.assert_updated(TestModel, 1)


# -- assert_emitted -----------------------------------------------------------------------------


def test_assert_emitted_with_verifier(clock):
    ActionSpec.of(CreateAction(clock)).with_principal("test-user").execute(
        CreateParams("inspected")
    ).assert_emitted(TestCreatedEvent, lambda event: event.name == "inspected")


def test_assert_emitted_with_count(clock):
    ActionSpec.of(CreateAction(clock)).with_principal("test-user").execute(
        CreateParams("test")
    ).assert_emitted(TestCreatedEvent, 1)


def test_assert_emitted_with_count_and_verifier(clock):
    ActionSpec.of(CreateBothAction(clock)).with_principal("test-user").execute(
        CreateBothParams("model", "SKU-001")
    ).assert_emitted(TestCreatedEvent, 1, lambda events: events[0].name == "model")


def test_assert_emitted_fails_when_no_events(clock, existing):
    assert_ = ActionSpec.of(DeleteAction(clock, existing)).with_principal("admin").execute(None)

    with pytest.raises(
        AssertionError,
        match=message("Expected at least 1 event of TestCreatedEvent but none were emitted"),
    ):
        assert_.assert_emitted(TestCreatedEvent)


# -- assert_not_emitted -------------------------------------------------------------------------


def test_assert_not_emitted_passes_when_event_absent(clock):
    ActionSpec.of(CreateAction(clock)).with_principal("test-user").execute(
        CreateParams("test")
    ).assert_not_emitted(TestDeletedEvent)


def test_assert_not_emitted_fails_when_event_present(clock):
    assert_ = (
        ActionSpec.of(CreateAction(clock)).with_principal("test-user").execute(CreateParams("test"))
    )

    with pytest.raises(
        AssertionError, match=message("Expected no events of TestCreatedEvent but found 1")
    ):
        assert_.assert_not_emitted(TestCreatedEvent)


# -- assert_no_additions / assert_no_updates / assert_no_events / assert_no_changes -------------


def test_assert_no_additions_passes_on_update_only(clock, existing):
    ActionSpec.of(DeleteAction(clock, existing)).with_principal("admin").execute(
        None
    ).assert_no_additions()


def test_assert_no_additions_fails_when_additions_exist(clock):
    assert_ = (
        ActionSpec.of(CreateAction(clock)).with_principal("test-user").execute(CreateParams("test"))
    )

    with pytest.raises(AssertionError, match=message("Expected no additions but found 1")):
        assert_.assert_no_additions()


def test_assert_no_updates_passes_on_create_only(clock):
    ActionSpec.of(CreateAction(clock)).with_principal("test-user").execute(
        CreateParams("test")
    ).assert_no_updates()


def test_assert_no_updates_fails_when_updates_exist(clock, existing):
    assert_ = ActionSpec.of(DeleteAction(clock, existing)).with_principal("admin").execute(None)

    with pytest.raises(AssertionError, match=message("Expected no updates but found 1")):
        assert_.assert_no_updates()


def test_assert_no_events_passes_when_no_events(clock):
    existing = a_model("old-name", clock.instant())

    (
        ActionSpec.of(RenameAction(clock, existing))
        .with_principal("admin")
        .execute(RenameParams("new-name"))
        .assert_no_events()
        .assert_updated(TestModel, lambda model: model.name == "new-name")
    )


def test_assert_no_events_fails_when_events_exist(clock):
    assert_ = (
        ActionSpec.of(CreateAction(clock)).with_principal("test-user").execute(CreateParams("test"))
    )

    with pytest.raises(AssertionError, match=message("Expected no events but found 1")):
        assert_.assert_no_events()


def test_assert_no_changes_passes_for_unrelated_type(clock):
    ActionSpec.of(CreateAction(clock)).with_principal("test-user").execute(
        CreateParams("test")
    ).assert_no_changes_of(TestProduct)


def test_assert_no_changes_fails_when_changes_exist(clock):
    assert_ = (
        ActionSpec.of(CreateAction(clock)).with_principal("test-user").execute(CreateParams("test"))
    )

    with pytest.raises(AssertionError):
        assert_.assert_no_changes()


# -- assert_no_additions_of / assert_no_updates_of / assert_no_changes_of -----------------------


def test_assert_no_additions_of_passes_for_unrelated_type(clock):
    ActionSpec.of(CreateAction(clock)).with_principal("test-user").execute(
        CreateParams("test")
    ).assert_no_additions_of(TestProduct)


def test_assert_no_additions_of_fails_when_additions_of_type_exist(clock):
    assert_ = (
        ActionSpec.of(CreateBothAction(clock))
        .with_principal("test-user")
        .execute(CreateBothParams("model", "SKU-001"))
    )

    with pytest.raises(
        AssertionError, match=message("Expected no additions of TestProduct but found 1")
    ):
        assert_.assert_no_additions_of(TestProduct)


def test_assert_no_updates_of_passes_for_unrelated_type(clock, existing):
    ActionSpec.of(DeleteAction(clock, existing)).with_principal("admin").execute(
        None
    ).assert_no_updates_of(TestProduct)


def test_assert_no_changes_of_passes_for_unrelated_type(clock):
    """Java's body for this case asserts the two additions rather than calling
    `assertNoChangesOf`; ported as written."""
    (
        ActionSpec.of(CreateBothAction(clock))
        .with_principal("test-user")
        .execute(CreateBothParams("model", "SKU-001"))
        .assert_added(TestModel, 1)
        .assert_added(TestProduct, 1)
    )


def test_assert_no_changes_of_fails_when_changes_of_type_exist(clock):
    assert_ = (
        ActionSpec.of(CreateBothAction(clock))
        .with_principal("test-user")
        .execute(CreateBothParams("model", "SKU-001"))
    )

    with pytest.raises(
        AssertionError, match=message("Expected no additions of TestModel but found 1")
    ):
        assert_.assert_no_changes_of(TestModel)


# -- assert_total_additions / assert_total_updates ----------------------------------------------


def test_assert_total_additions_counts_across_types(clock):
    (
        ActionSpec.of(CreateBothAction(clock))
        .with_principal("test-user")
        .execute(CreateBothParams("model", "SKU-001"))
        .assert_total_additions(2)
        .assert_total_updates(0)
    )


def test_assert_total_additions_fails_on_mismatch(clock):
    assert_ = (
        ActionSpec.of(CreateBothAction(clock))
        .with_principal("test-user")
        .execute(CreateBothParams("model", "SKU-001"))
    )

    with pytest.raises(AssertionError, match=message("Expected 1 total addition(s) but found 2")):
        assert_.assert_total_additions(1)


def test_assert_total_updates_counts_across_types(clock, existing):
    (
        ActionSpec.of(DeleteAction(clock, existing))
        .with_principal("admin")
        .execute(None)
        .assert_total_updates(1)
        .assert_total_additions(0)
    )


# -- assert_result ------------------------------------------------------------------------------


def test_assert_result(clock):
    def verify(result: TestModel) -> None:
        assert result.name == "result-check"
        assert result.version == 1

    ActionSpec.of(CreateAction(clock)).with_principal("test-user").execute(
        CreateParams("result-check")
    ).assert_result(verify)


# -- assert_throws ------------------------------------------------------------------------------


def test_assert_throws_passes_on_expected_exception(clock):
    ActionSpec.of(FailingAction(clock)).with_principal("test-user").assert_throws(
        ValueError, FailingParams("boom")
    )


def test_assert_throws_with_verifier(clock):
    ActionSpec.of(FailingAction(clock)).with_principal("test-user").assert_throws(
        ValueError, FailingParams("boom"), lambda ex: str(ex) == "boom"
    )


def test_assert_throws_fails_when_no_exception(clock):
    with pytest.raises(
        AssertionError, match=message("Expected ValueError to be thrown but nothing was thrown")
    ):
        ActionSpec.of(CreateAction(clock)).with_principal("test-user").assert_throws(
            ValueError, CreateParams("test")
        )


def test_assert_throws_fails_when_wrong_exception_type(clock):
    """Java's `IllegalStateException` against an `IllegalArgumentException`."""
    with pytest.raises(AssertionError, match=message("Expected RuntimeError but got ValueError")):
        ActionSpec.of(FailingAction(clock)).with_principal("test-user").assert_throws(
            RuntimeError, FailingParams("boom")
        )


# -- raw accessors ------------------------------------------------------------------------------


def test_result_returns_raw_value(clock):
    assert_ = (
        ActionSpec.of(CreateAction(clock)).with_principal("test-user").execute(CreateParams("raw"))
    )

    result: TestModel = assert_.result()
    assert result.name == "raw"


def test_additions_returns_list(clock):
    assert_ = (
        ActionSpec.of(CreateBothAction(clock))
        .with_principal("test-user")
        .execute(CreateBothParams("model", "SKU-001"))
    )

    assert len(assert_.additions(TestModel)) == 1
    assert len(assert_.additions(TestProduct)) == 1
    assert assert_.additions(TestProduct)[0].sku == "SKU-001"


def test_updates_returns_list(clock, existing):
    assert_ = ActionSpec.of(DeleteAction(clock, existing)).with_principal("admin").execute(None)

    assert len(assert_.updates(TestModel)) == 1
    assert assert_.updates(TestModel)[0].state is TestState.DELETED
    assert assert_.updates(TestProduct) == []


# -- multi-type combined assertions -------------------------------------------------------------


def test_create_both_full_verification(clock):
    june_1 = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
    clock.pause_at(june_1)

    def verify_model(model: TestModel) -> None:
        assert model.name == "widget"
        assert model.created_date == june_1

    (
        ActionSpec.of(CreateBothAction(clock))
        .with_principal("test-user")
        .execute(CreateBothParams("widget", "SKU-999"))
        .assert_added(TestModel, verify_model)
        .assert_added(TestProduct, lambda product: product.sku == "SKU-999")
        .assert_total_additions(2)
        .assert_total_updates(0)
        .assert_emitted(TestCreatedEvent)
        .assert_emitted(TestProductCreatedEvent)
        .assert_not_emitted(TestDeletedEvent)
        .assert_no_updates()
        .assert_result(lambda result: result.name == "widget")
    )


# -- only in Python: a verifier cannot pass by accident -----------------------------------------
#
# Java's verifier is a `Consumer`: it asserts inside itself and returns nothing. A Python lambda
# cannot hold an `assert`, so it returns its verdict instead - and a port that ignored the return
# value would let `lambda m: m.name == "wrong"` pass whatever the name was.


def test_a_lambda_that_returns_false_fails(clock):
    assert_ = (
        ActionSpec.of(CreateAction(clock))
        .with_principal("test-user")
        .execute(CreateParams("right"))
    )

    with pytest.raises(AssertionError, match="did not satisfy the verifier"):
        assert_.assert_added(TestModel, lambda model: model.name == "wrong")


@pytest.mark.parametrize("falsy", [False, 0, "", []])
def test_any_falsy_verdict_fails_not_only_false(clock, falsy):
    """A lambda returning `0` or `""` has not said yes either."""
    assert_ = (
        ActionSpec.of(CreateAction(clock)).with_principal("test-user").execute(CreateParams("x"))
    )

    with pytest.raises(AssertionError, match="did not satisfy the verifier"):
        assert_.assert_result(lambda _: falsy)


def test_every_verifying_method_checks_the_verdict(clock, existing):
    """Each of the seven places a verifier is taken, handed one that says no."""
    no = lambda _: False  # noqa: E731
    created = ActionSpec.of(CreateAction(clock)).with_principal("u").execute(CreateParams("x"))
    deleted = ActionSpec.of(DeleteAction(clock, existing)).with_principal("u").execute(None)
    for check in (
        lambda: created.assert_added(TestModel, no),
        lambda: created.assert_added(TestModel, 1, no),
        lambda: deleted.assert_updated(TestModel, no),
        lambda: deleted.assert_updated(TestModel, 1, no),
        lambda: created.assert_emitted(TestCreatedEvent, no),
        lambda: created.assert_emitted(TestCreatedEvent, 1, no),
        lambda: created.assert_result(no),
    ):
        with pytest.raises(AssertionError, match="did not satisfy the verifier"):
            check()
    with pytest.raises(AssertionError, match="did not satisfy the verifier"):
        ActionSpec.of(FailingAction(clock)).assert_throws(ValueError, FailingParams("boom"), no)


def test_a_verifier_that_asserts_inside_itself_fails_on_its_own_assertion(clock):
    """The Java shape still works: return nothing, assert inside."""

    def verify(model: TestModel) -> None:
        assert model.name == "wrong", "my own message"

    assert_ = (
        ActionSpec.of(CreateAction(clock))
        .with_principal("test-user")
        .execute(CreateParams("right"))
    )

    with pytest.raises(AssertionError, match="my own message"):
        assert_.assert_added(TestModel, verify)


def test_assert_throws_does_not_mistake_its_own_failure_for_the_actions_error(clock):
    """In Python `AssertionError` is an `Exception`. Expecting `Exception` itself must still
    report "nothing was thrown" rather than catching the report and passing."""
    with pytest.raises(AssertionError, match="nothing was thrown"):
        ActionSpec.of(CreateAction(clock)).assert_throws(Exception, CreateParams("x"))


def test_the_principal_reaches_the_action(clock):
    seen: list[str | None] = []

    class Recording(Action[None, None]):
        def perform(self, principal: str | None, params: None) -> None:
            seen.append(principal)

    ActionSpec.of(Recording(clock)).with_principal("alice").execute(None)
    ActionSpec.of(Recording(clock)).execute(None)

    assert seen == ["alice", None]


def test_events_match_subclasses_and_rows_match_the_exact_class(clock):
    """Java's split, kept: `isInstance` for events, `changes().get(type)` for rows."""
    staged = ActionSpec.of(CreateBothAction(clock)).execute(CreateBothParams("m", "s"))

    staged.assert_emitted(ModelEvent, 2)
    assert staged.additions(Model) == [], "no row is staged under the base class"


def test_plain_assert_emitted_means_at_least_one_not_exactly_one(clock):
    """Java's `assertEmitted(type)` checks `isEmpty()`, not `size() != 1` - the one overload
    that is not exact. Two events match `ModelEvent` here, and that passes."""
    staged = ActionSpec.of(CreateBothAction(clock)).execute(CreateBothParams("m", "s"))

    staged.assert_emitted(ModelEvent)
