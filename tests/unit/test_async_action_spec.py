"""`AsyncActionSpec` and `async_run_action`: the sync helpers' checks, for an `async def perform`.

Every check after the run is the sync `ActionAssert`'s own - `test_action_spec.py` covers them
case by case - so what is pinned here is the part that differs: `perform` is awaited, the plan
is bound for it exactly as the async executor binds it, and the clock is the action's.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Self

import pytest

from ekbatan import VirtualClock, current_plan
from ekbatan.aio import AsyncAction, AsyncActionSpec, async_run_action
from ekbatan.domain import Model, ModelBuilder, ModelEvent
from ekbatan.errors import EkbatanError
from ekbatan.ids import Id
from ekbatan.testing import ActionAssert

# -- the fixtures: `test_action_spec.py`'s, cut to what is used here ------------------------------


class TestState(Enum):
    __test__ = False
    ACTIVE = "ACTIVE"


class TestModelBuilder(ModelBuilder["TestModel", Id["TestModel"], TestState]):
    __test__ = False

    def name(self, value: str) -> Self:
        return self.set(name=value)


@dataclass(frozen=True, kw_only=True)
class TestCreatedEvent(ModelEvent["TestModel"]):
    __test__ = False
    name: str


@dataclass(frozen=True, kw_only=True)
class TestModel(Model[Id["TestModel"], TestState, TestModelBuilder]):
    __test__ = False
    name: str

    def validate(self) -> None:
        pass  # no rules beyond its types


@dataclass(frozen=True, kw_only=True)
class TestProduct(Model[Id["TestProduct"], TestState]):
    __test__ = False
    sku: str

    def validate(self) -> None:
        pass  # no rules beyond its types


@dataclass(frozen=True)
class CreateParams:
    name: str


@dataclass(frozen=True)
class FailingParams:
    message: str


JAN_1 = datetime(2025, 1, 1, tzinfo=UTC)


class AsyncCreateAction(AsyncAction[CreateParams, TestModel]):
    async def perform(self, principal: str | None, params: CreateParams) -> TestModel:
        await asyncio.sleep(0)  # a real await, so the plan has to survive a suspension
        model_id = Id[TestModel].random()
        return self.plan.add(
            TestModelBuilder()
            .id(model_id)
            .state(TestState.ACTIVE)
            .name(f"{params.name} by {principal}")
            .created_date(self.clock.instant())
            .with_initial_version()
            .with_event(TestCreatedEvent(model_id=str(model_id), name=params.name))
            .build()
        )


class AsyncFailingAction(AsyncAction[FailingParams, None]):
    async def perform(self, principal: str | None, params: FailingParams) -> None:
        await asyncio.sleep(0)
        raise ValueError(params.message)


def test_execute_awaits_perform_and_hands_back_the_sync_assertions():
    clock = VirtualClock(JAN_1)

    checked = asyncio.run(
        AsyncActionSpec.of(AsyncCreateAction(clock))
        .with_principal("ada")
        .execute(CreateParams(name="widget"))
    )

    assert isinstance(checked, ActionAssert)
    (
        checked.assert_added(TestModel, lambda m: m.name == "widget by ada")
        .assert_added(TestModel, lambda m: m.created_date == JAN_1)
        .assert_emitted(TestCreatedEvent, 1)
        .assert_no_additions_of(TestProduct)
        .assert_no_updates()
        .assert_result(lambda m: m.name == "widget by ada")
    )


def test_a_check_that_does_not_hold_fails_with_the_sync_message():
    checked = asyncio.run(
        AsyncActionSpec.of(AsyncCreateAction(VirtualClock(JAN_1))).execute(CreateParams(name="x"))
    )

    with pytest.raises(AssertionError, match="Expected 2 addition"):
        checked.assert_added(TestModel, 2)


def test_assert_throws_awaits_and_passes_on_the_expected_error():
    spec = AsyncActionSpec.of(AsyncFailingAction(VirtualClock(JAN_1)))

    same = asyncio.run(
        spec.assert_throws(ValueError, FailingParams(message="boom"), lambda e: "boom" in str(e))
    )

    assert same is spec, "chainable, as the sync one is"


def test_assert_throws_fails_when_nothing_or_something_else_is_raised():
    clock = VirtualClock(JAN_1)

    with pytest.raises(AssertionError, match="Expected ValueError to be thrown but nothing"):
        asyncio.run(
            AsyncActionSpec.of(AsyncCreateAction(clock)).assert_throws(
                ValueError, CreateParams(name="x")
            )
        )
    with pytest.raises(AssertionError, match="Expected KeyError but got ValueError"):
        asyncio.run(
            AsyncActionSpec.of(AsyncFailingAction(clock)).assert_throws(
                KeyError, FailingParams(message="boom")
            )
        )


def test_async_run_action_hands_back_what_was_staged_and_leaves_no_plan_behind():
    staged = asyncio.run(
        async_run_action(AsyncCreateAction(VirtualClock(JAN_1)), CreateParams(name="w"))
    )

    assert [m.name for m in staged.added(TestModel)] == ["w by None"]
    assert [type(e) for e in staged.events] == [TestCreatedEvent]
    with pytest.raises(EkbatanError, match="no unit of work here"):
        current_plan()


def test_two_runs_at_once_on_one_loop_each_see_their_own_plan():
    """The plan lives in a ContextVar, which is per task."""

    async def both() -> tuple[object, object]:
        clock = VirtualClock(JAN_1)
        return await asyncio.gather(
            async_run_action(AsyncCreateAction(clock), CreateParams(name="a")),
            async_run_action(AsyncCreateAction(clock), CreateParams(name="b")),
        )

    first, second = asyncio.run(both())

    assert [m.name for m in first.added(TestModel)] == ["a by None"]  # type: ignore[attr-defined]
    assert [m.name for m in second.added(TestModel)] == ["b by None"]  # type: ignore[attr-defined]
