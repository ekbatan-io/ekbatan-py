"""Reacting to your own events, in your own process.

The minimal end of the spectrum: no broker, no connector, no second runtime. A unit of work
commits, fan-out turns each committed event into work for every subscriber, and dispatch runs
them. Delivery is at-least-once, which is the only guarantee anything on this side of a
database can honestly make.

A handler is written against an :class:`EventEnvelope` and nothing else. That is deliberate:
the day the handler outgrows in-process dispatch, the same body has to compile against a broker
consumer, and it can only do that if everything it needed arrived in the envelope.

A handler runs after the action that emitted its event has committed - later, on dispatch's own
threads, never inside the action. Its ``handle`` may be ``async def`` (:class:`AsyncEventHandler`),
to await an ``ekbatan.aio`` executor: dispatch then runs it on its handler loop, and decides
success, retry and expiry from it exactly as for a sync one.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, Self, runtime_checkable

from .._builder import FieldBuilder
from ..domain import ModelEvent
from ..errors import ValidationError

__all__ = [
    "AnyEventHandler",
    "AsyncEventHandler",
    "EventEnvelope",
    "EventEnvelopeBuilder",
    "EventHandler",
    "EventHandlerRegistry",
    "EventHandlerRegistryBuilder",
]


@dataclass(frozen=True, kw_only=True, slots=True)
class EventEnvelope[E: ModelEvent]:
    """One delivery: the typed event, and the context of the unit of work that produced it."""

    event: E

    #: The outbox row's id. Stable across retries, so it is the idempotency key a handler
    #: should use when its side effect is not naturally repeatable.
    event_id: uuid.UUID

    namespace: str
    action_id: uuid.UUID
    action_name: str

    #: The producing unit of work's input. Where a handler reads a value the domain event
    #: deliberately does not carry - a delivery address has no business inside a
    #: money-deposited event.
    action_params: dict[str, Any]

    started_date: datetime
    completion_date: datetime
    event_date: datetime
    model_id: str | None
    model_type: str | None

    #: Which attempt this is, starting at 1. Present so a handler can log it, not so it can
    #: decide to give up - that is the retention window's job.
    attempt: int = 1

    @classmethod
    def builder(cls) -> EventEnvelopeBuilder[E]:
        return EventEnvelopeBuilder()


@runtime_checkable
class EventHandler[E: ModelEvent](Protocol):
    """Something that reacts to one event type."""

    @property
    def name(self) -> str:
        """A cluster-stable identifier, stored on every notification row this handler creates.

        Not a label: renaming it creates a new subscription, and every row queued under the old
        name is stranded. Keep it stable across class and module renames, and use
        :attr:`aliases` when you do have to change it.
        """
        ...

    @property
    def event_type(self) -> type[E]:
        """The event this subscribes to. Subscription is by type; a handler that receives
        everything and filters in its own body is one that will eventually forget to."""
        ...

    def handle(self, envelope: EventEnvelope[E]) -> None:
        """Do the work. Raising records a failure and schedules a retry."""
        ...


@runtime_checkable
class AsyncEventHandler[E: ModelEvent](Protocol):
    """A handler whose ``handle`` is a coroutine - for one that awaits an ``ekbatan.aio``
    executor. The same ``name``, ``event_type`` and ``aliases`` as :class:`EventHandler`.

    It runs on dispatch's handler loop (``ekbatan.events.HandlerLoop``), a batch at a time; what
    it awaits must belong to that loop. Raising records a failure and schedules a retry, as for a
    sync handler.
    """

    @property
    def name(self) -> str: ...

    @property
    def event_type(self) -> type[E]: ...

    async def handle(self, envelope: EventEnvelope[E]) -> None: ...


#: Either kind; a registry holds both.
type AnyEventHandler = EventHandler[Any] | AsyncEventHandler[Any]


class EventHandlerRegistry:
    """Which handlers subscribe to what.

    Each handler is checked as Java's ``EventHandlerRegistry.Builder.withHandler`` checks it, in
    the same order. Names and aliases share one lookup table, as Java's ``handlersByLookupName``,
    so a name or alias may appear once across every handler - which is also what refuses the
    same handler registered twice. And an event type's short name may stand for one class
    only, because the outbox stores events by that name.
    """

    __slots__ = ("_by_event", "_by_lookup", "_types_by_short_name")

    def __init__(self, handlers: object = ()) -> None:
        #: Java's ``handlersByLookupName``: every name and every alias, each once.
        self._by_lookup: dict[str, AnyEventHandler] = {}
        #: Java's ``eventTypesBySimpleName``.
        self._types_by_short_name: dict[str, type] = {}
        self._by_event: dict[str, list[AnyEventHandler]] = {}
        for handler in handlers:  # type: ignore[attr-defined]
            self.add(handler)

    @classmethod
    def builder(cls) -> EventHandlerRegistryBuilder:
        return EventHandlerRegistryBuilder()

    def add(self, handler: AnyEventHandler) -> EventHandlerRegistry:
        """Java's ``withHandler``, check for check."""
        if handler is None:
            raise ValidationError("handler cannot be None")
        name = handler.name
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("handler.name cannot be blank")
        event_type = handler.event_type
        if event_type is None:
            raise ValidationError("handler.event_type cannot be None")
        short_name = getattr(event_type, "__name__", None)
        if not isinstance(short_name, str) or not short_name.strip():
            raise ValidationError("handler.event_type.__name__ cannot be blank")

        self._refuse_a_taken_lookup_name(name)

        previous = self._types_by_short_name.get(short_name)
        if previous is not None and previous is not event_type:
            raise ValidationError(
                f"Event type simple name collision: {short_name} maps to both "
                f"{_qualified(previous)} and {_qualified(event_type)}"
            )

        self._types_by_short_name[short_name] = event_type
        self._by_lookup[name] = handler
        aliases = getattr(handler, "aliases", ())
        if aliases is None:
            raise ValidationError("handler.aliases cannot be None")
        for alias in aliases:
            if not isinstance(alias, str) or not alias.strip():
                raise ValidationError("handler.aliases cannot contain blank values")
            if alias == name:
                raise ValidationError(f"Handler alias duplicates canonical name: {name}")
            self._refuse_a_taken_lookup_name(alias)
            self._by_lookup[alias] = handler
        self._by_event.setdefault(short_name, []).append(handler)
        return self

    def _refuse_a_taken_lookup_name(self, lookup_name: str) -> None:
        if lookup_name in self._by_lookup:
            raise ValidationError(
                f"Handler name or alias already registered: {lookup_name} "
                "(handler names and aliases must be globally unique)"
            )

    @property
    def event_types(self) -> list[str]:
        """The event type names anything currently subscribes to.

        Fan-out selects on this, which is why an event with no subscribers stays undelivered
        rather than being marked done and thrown away - deployments routinely add a handler
        after the events it wants already exist.
        """
        return sorted(self._by_event)

    def subscribers(self, event_type: str) -> list[AnyEventHandler]:
        return list(self._by_event.get(event_type, ()))

    def by_name(self, name: str) -> AnyEventHandler | None:
        """Resolve a queued row's handler name, following a rename if one was declared."""
        return self._by_lookup.get(name)

    def __len__(self) -> int:
        return sum(len(handlers) for handlers in self._by_event.values())

    def __repr__(self) -> str:
        return f"EventHandlerRegistry({len(self)} handler(s), {len(self._by_event)} event type(s))"


class EventEnvelopeBuilder[E: ModelEvent](FieldBuilder[EventEnvelope[E]]):
    """Fluent builder for :class:`EventEnvelope`. Obtain via :meth:`EventEnvelope.builder`.

    The framework builds envelopes itself; this is for a test that hands one to a handler. The
    fields Java refuses as null are refused here, and ``model_id`` / ``model_type`` default to
    ``None`` as Java's do.
    """

    __slots__ = ()
    _target = EventEnvelope
    _required = (
        "event",
        "event_id",
        "namespace",
        "action_id",
        "action_name",
        "action_params",
        "started_date",
        "completion_date",
        "event_date",
    )
    _defaults = {"model_id": None, "model_type": None}  # noqa: RUF012 - read, never mutated

    def event(self, event: E) -> Self:
        return self._set("event", event)

    def event_id(self, event_id: uuid.UUID) -> Self:
        return self._set("event_id", event_id)

    def namespace(self, namespace: str) -> Self:
        return self._set("namespace", namespace)

    def action_id(self, action_id: uuid.UUID) -> Self:
        return self._set("action_id", action_id)

    def action_name(self, action_name: str) -> Self:
        return self._set("action_name", action_name)

    def action_params(self, action_params: dict[str, Any]) -> Self:
        return self._set("action_params", action_params)

    def started_date(self, started_date: datetime) -> Self:
        return self._set("started_date", started_date)

    def completion_date(self, completion_date: datetime) -> Self:
        return self._set("completion_date", completion_date)

    def event_date(self, event_date: datetime) -> Self:
        return self._set("event_date", event_date)

    def model_id(self, model_id: str | None) -> Self:
        return self._set("model_id", model_id)

    def model_type(self, model_type: str | None) -> Self:
        return self._set("model_type", model_type)

    def attempt(self, attempt: int) -> Self:
        return self._set("attempt", attempt)


class EventHandlerRegistryBuilder:
    """Fluent builder for :class:`EventHandlerRegistry`. Obtain via
    :meth:`EventHandlerRegistry.builder`.

    Each handler is checked as it is added, as Java's ``withHandler`` checks it, so a refusal
    points at the line that added the handler rather than at ``build()``.
    """

    __slots__ = ("_checked", "_handlers")

    def __init__(self) -> None:
        self._handlers: list[AnyEventHandler] = []
        # Added to as each handler arrives, only so it is refused on the line that added it.
        self._checked = EventHandlerRegistry()

    def with_handler(self, handler: AnyEventHandler) -> Self:
        self._checked.add(handler)
        self._handlers.append(handler)
        return self

    def with_handlers(self, handlers: Iterable[AnyEventHandler]) -> Self:
        if handlers is None:
            raise ValidationError("handlers cannot be None")
        for handler in handlers:
            self.with_handler(handler)
        return self

    def build(self) -> EventHandlerRegistry:
        return EventHandlerRegistry(self._handlers)


def _qualified(cls: type) -> str:
    """Java's ``Class.getName()``: the module and the class, to tell two same-named apart."""
    return f"{cls.__module__}.{cls.__qualname__}"
