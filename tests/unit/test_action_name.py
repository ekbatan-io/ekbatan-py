"""An action's name is its class name, as Java's is `actionClass.getSimpleName()`.

It is what `action_name` holds on every outbox row, and what the logs and traces say. Java has no
way to change it, and neither has this: an override of `name` is refused where the class is
defined, rather than writing rows under one name while the class has another.
"""

from __future__ import annotations

import pytest

from ekbatan.action import Action
from ekbatan.aio import AsyncAction


def test_an_action_that_overrides_its_name_is_refused_where_it_is_defined() -> None:
    with pytest.raises(
        TypeError,
        match=r"Renamed overrides name: an action's name is its class name, as in Java - it is "
        r"what action_name holds\. To change it, rename the class\.",
    ):

        class Renamed(Action[None, None]):
            @property
            def name(self) -> str:
                return "SomethingElse"

            def perform(self, principal: str | None, params: None) -> None:
                return None


def test_an_async_action_that_overrides_its_name_is_refused_too() -> None:
    with pytest.raises(TypeError, match="Renamed overrides name"):

        class Renamed(AsyncAction[None, None]):
            name = "SomethingElse"

            async def perform(self, principal: str | None, params: None) -> None:
                return None


def test_an_action_has_no_name_to_override() -> None:
    class Plain(Action[None, None]):
        def perform(self, principal: str | None, params: None) -> None:
            return None

    assert not hasattr(Plain, "name"), "no name to override: the class name is the name"
