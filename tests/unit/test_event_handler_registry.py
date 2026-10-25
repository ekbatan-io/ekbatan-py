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
from ekbatan.events import AsyncEventHandler, EventEnvelope, EventHandler, EventHandlerRegistry


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


class Handler(EventHandler[Any]):
    """Answers with whatever it was given, so a check can be handed a bad value."""

    def __init__(self, name: Any, event_type: Any, aliases: Any = frozenset()) -> None:
        self._name = name
        self._event_type = event_type
        self._aliases = aliases

    def name(self) -> Any:
        return self._name

    def aliases(self) -> Any:
        return self._aliases

    def event_type(self) -> Any:
        return self._event_type

    def handle(self, envelope: EventEnvelope[Any]) -> None:
        pass


def registry(*handlers: Any) -> EventHandlerRegistry:
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
    with pytest.raises(ValidationError, match=r"handler\.name\(\) cannot be blank"):
        registry(Handler(name, FooEvent))


def test_a_missing_event_type_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"handler\.event_type\(\) cannot be None"):
        registry(Handler("alpha", None))


def test_no_handler_is_refused() -> None:
    with pytest.raises(ValidationError, match="handler cannot be None"):
        EventHandlerRegistry.builder().with_handler(None)  # type: ignore[arg-type]


def test_aliases_of_none_are_refused() -> None:
    with pytest.raises(ValidationError, match=r"handler\.aliases\(\) cannot be None"):
        registry(Handler("alpha", FooEvent, None))


@pytest.mark.parametrize("alias", [None, "", "  "])
def test_a_blank_alias_is_refused(alias: Any) -> None:
    with pytest.raises(ValidationError, match=r"handler\.aliases\(\) cannot contain blank values"):
        registry(Handler("alpha", FooEvent, [alias]))


# -- a handler extends a base, with Java's methods (Dracula 71) ----------------------------------


class Plain(EventHandler[FooEvent]):
    def name(self) -> str:
        return "plain"

    def event_type(self) -> type[FooEvent]:
        return FooEvent

    def handle(self, envelope: EventEnvelope[FooEvent]) -> None:
        pass


def test_aliases_default_to_empty_and_an_override_is_honoured() -> None:
    """Java's `aliases()` is a default method returning `Set.of()`; so is this one."""

    class Renamed(Plain):
        def name(self) -> str:
            return "renamed"

        def aliases(self) -> frozenset[str]:
            return frozenset({"plain-before"})

    plain, renamed = Plain(), Renamed()
    assert plain.aliases() == frozenset()

    built = registry(plain, renamed)
    assert built.by_name("plain") is plain
    assert built.by_name("renamed") is renamed
    assert built.by_name("plain-before") is renamed, "a row queued under the old name finds it"


def test_a_handler_that_extends_neither_base_is_refused() -> None:
    """Every member a handler has, and no base: Java's compiler refuses a class that does not
    implement `EventHandler`, and the registry refuses this one."""

    class Shaped:
        def name(self) -> str:
            return "shaped"

        def event_type(self) -> type[FooEvent]:
            return FooEvent

        def handle(self, envelope: EventEnvelope[FooEvent]) -> None:
            pass

    with pytest.raises(
        TypeError,
        match=r"Shaped is not an event handler: extend EventHandler, or AsyncEventHandler for an "
        r"async def handle\(\)",
    ):
        registry(Shaped())  # type: ignore[arg-type]


def test_a_handle_of_the_other_kind_is_refused() -> None:
    """Dispatch runs a handler by its base, so a `handle()` of the other kind is refused."""

    class AwaitsButSync(EventHandler[FooEvent]):
        def name(self) -> str:
            return "awaits"

        def event_type(self) -> type[FooEvent]:
            return FooEvent

        async def handle(self, envelope: EventEnvelope[FooEvent]) -> None:  # type: ignore[override]
            pass

    class PlainButAsync(AsyncEventHandler[FooEvent]):
        def name(self) -> str:
            return "plain"

        def event_type(self) -> type[FooEvent]:
            return FooEvent

        def handle(self, envelope: EventEnvelope[FooEvent]) -> None:  # type: ignore[override]
            pass

    with pytest.raises(
        TypeError,
        match=r"AwaitsButSync extends EventHandler, but its handle\(\) "
        r"is async def: extend AsyncEventHandler instead",
    ):
        registry(AwaitsButSync())
    with pytest.raises(
        TypeError,
        match=r"PlainButAsync extends AsyncEventHandler, but its handle\(\) "
        r"is not async def: extend EventHandler instead",
    ):
        registry(PlainButAsync())  # type: ignore[arg-type]
