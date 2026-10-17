"""`EventHandlerRegistry`: Java's `EventHandlerRegistryBuilderTest`, case for case, and then every
other check Java's `withHandler` makes.

A handler's name - and each alias - is stored on every notification row it is given, and a row
finds its handler again by that name. So Java refuses, at startup, anything that would make one
name mean two things: the same handler twice, two event classes behind one short name, an alias
that is already somebody's name. Python accepted all three without a word (Dracula bug 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from ekbatan.domain import ModelEvent
from ekbatan.errors import ValidationError
from ekbatan.events import EventEnvelope, EventHandlerRegistry


class _Placeholder:
    pass


@dataclass(frozen=True, kw_only=True)
class FooEvent(ModelEvent[_Placeholder]):
    pass


@dataclass(frozen=True, kw_only=True)
class BarEvent(ModelEvent[_Placeholder]):
    pass


class FirstNamespace:
    @dataclass(frozen=True, kw_only=True)
    class CollidingEvent(ModelEvent[_Placeholder]):
        pass


class SecondNamespace:
    @dataclass(frozen=True, kw_only=True)
    class CollidingEvent(ModelEvent[_Placeholder]):
        pass


class Handler:
    def __init__(self, name: Any, event_type: Any, aliases: Any = ()) -> None:
        self.name = name
        self.event_type = event_type
        self.aliases = aliases

    def handle(self, envelope: EventEnvelope[Any]) -> None:
        pass


def registry(*handlers: Handler) -> EventHandlerRegistry:
    return EventHandlerRegistry.builder().with_handlers(handlers).build()


# -- Java's EventHandlerRegistryBuilderTest -----------------------------------------------------


def test_with_handlers_indexes_handlers_by_name_and_event_type() -> None:
    h1, h2, h3 = Handler("alpha", FooEvent), Handler("beta", FooEvent), Handler("gamma", BarEvent)

    built = registry(h1, h2, h3)

    assert built.by_name("alpha") is h1
    assert built.by_name("beta") is h2
    assert built.by_name("gamma") is h3
    assert built.by_name("missing") is None
    assert built.subscribers("FooEvent") == [h1, h2]
    assert built.subscribers("BarEvent") == [h3]
    assert built.subscribers("UnknownEvent") == []
    assert built.event_types == ["BarEvent", "FooEvent"]


def test_with_handler_resolves_aliases_without_using_them_for_fanout() -> None:
    handler = Handler("alpha", FooEvent, ("old-alpha", "older-alpha"))

    built = registry(handler)

    assert built.by_name("alpha") is handler
    assert built.by_name("old-alpha") is handler
    assert built.by_name("older-alpha") is handler
    assert built.subscribers("FooEvent") == [handler]


def test_with_handler_rejects_alias_that_duplicates_canonical_name() -> None:
    with pytest.raises(ValidationError, match="Handler alias duplicates canonical name: alpha"):
        registry(Handler("alpha", FooEvent, ("alpha",)))


def test_with_handler_rejects_alias_collision_with_existing_lookup_name() -> None:
    with pytest.raises(
        ValidationError, match="Handler name or alias already registered: old-alpha"
    ):
        registry(
            Handler("alpha", FooEvent, ("old-alpha",)), Handler("beta", FooEvent, ("old-alpha",))
        )


def test_with_handler_rejects_name_collision_with_existing_alias() -> None:
    with pytest.raises(
        ValidationError, match="Handler name or alias already registered: old-alpha"
    ):
        registry(Handler("alpha", FooEvent, ("old-alpha",)), Handler("old-alpha", FooEvent))


def test_with_handler_rejects_different_event_classes_with_same_simple_name() -> None:
    with pytest.raises(ValidationError) as raised:
        registry(
            Handler("first", FirstNamespace.CollidingEvent),
            Handler("second", SecondNamespace.CollidingEvent),
        )
    message = str(raised.value)
    assert "Event type simple name collision: CollidingEvent" in message
    assert f"{__name__}.FirstNamespace.CollidingEvent" in message
    assert f"{__name__}.SecondNamespace.CollidingEvent" in message


# -- the rest of Java's withHandler -------------------------------------------------------------


def test_the_same_handler_twice_is_refused() -> None:
    """Java refuses it by its name: already registered. It used to give two subscribers here,
    so every event was handled twice."""
    handler = Handler("alpha", FooEvent)
    with pytest.raises(ValidationError, match="Handler name or alias already registered: alpha"):
        registry(handler, handler)


def test_two_handlers_with_one_name_are_refused() -> None:
    with pytest.raises(ValidationError, match="Handler name or alias already registered: alpha"):
        registry(Handler("alpha", FooEvent), Handler("alpha", BarEvent))


def test_one_alias_twice_on_one_handler_is_refused() -> None:
    with pytest.raises(ValidationError, match="Handler name or alias already registered: old"):
        registry(Handler("alpha", FooEvent, ["old", "old"]))


def test_the_same_event_class_for_several_handlers_is_fine() -> None:
    first, second = Handler("first", FooEvent), Handler("second", FooEvent)
    assert registry(first, second).subscribers("FooEvent") == [first, second]


@pytest.mark.parametrize("name", [None, "", "   "])
def test_a_blank_name_is_refused(name: Any) -> None:
    with pytest.raises(ValidationError, match=r"handler\.name cannot be blank"):
        registry(Handler(name, FooEvent))


def test_a_missing_event_type_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"handler\.event_type cannot be None"):
        registry(Handler("alpha", None))


def test_no_handler_is_refused() -> None:
    with pytest.raises(ValidationError, match="handler cannot be None"):
        EventHandlerRegistry.builder().with_handler(None)  # type: ignore[arg-type]


def test_aliases_of_none_are_refused() -> None:
    with pytest.raises(ValidationError, match=r"handler\.aliases cannot be None"):
        registry(Handler("alpha", FooEvent, None))


@pytest.mark.parametrize("alias", [None, "", "  "])
def test_a_blank_alias_is_refused(alias: Any) -> None:
    with pytest.raises(ValidationError, match=r"handler\.aliases cannot contain blank values"):
        registry(Handler("alpha", FooEvent, [alias]))


def test_a_handler_without_aliases_has_none() -> None:
    """Java's `aliases()` defaults to an empty set; here the attribute may be left out."""

    class Plain:
        name = "plain"
        event_type = FooEvent

        def handle(self, envelope: EventEnvelope[Any]) -> None:
            pass

    handler = Plain()
    assert registry(handler).by_name("plain") is handler  # type: ignore[arg-type]
