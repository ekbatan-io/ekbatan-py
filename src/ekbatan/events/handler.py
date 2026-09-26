"""Reacting to your own events, in your own process.

The minimal end of the spectrum: no broker, no connector, no second runtime. A unit of work
commits, fan-out turns each committed event into work for every subscriber, and dispatch runs
them. Delivery is at-least-once, which is the only guarantee anything on this side of a
database can honestly make.

A handler is written against an :class:`EventEnvelope` and nothing else. That is deliberate:
the day the handler outgrows in-process dispatch, the same body has to compile against a broker
consumer, and it can only do that if everything it needed arrived in the envelope.
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


class EventHandlerRegistry:
    """Which handlers subscribe to what."""

    __slots__ = ("_aliases", "_by_event", "_by_name")

    def __init__(self, handlers: object = ()) -> None:
        self._by_name: dict[str, EventHandler[Any]] = {}
        self._by_event: dict[str, list[EventHandler[Any]]] = {}
        self._aliases: dict[str, str] = {}
        for handler in handlers:  # type: ignore[attr-defined]
            self.add(handler)

    @classmethod
    def builder(cls) -> EventHandlerRegistryBuilder:
        return EventHandlerRegistryBuilder()

    def add(self, handler: EventHandler[Any]) -> EventHandlerRegistry:
        name = handler.name
        if not name or not name.strip():
            raise ValidationError(f"{type(handler).__name__} has a blank name")
        existing = self._by_name.get(name)
        if existing is not None and existing is not handler:
            raise ValidationError(
                f"two handlers are both called {name!r}: {type(existing).__name__} and "
                f"{type(handler).__name__}. The name is a subscription identity, so they would "
                f"share one queue and each drain the other's work."
            )
        self._by_name[name] = handler
        self._by_event.setdefault(handler.event_type.__name__, []).append(handler)
        for alias in getattr(handler, "aliases", ()):
            self._aliases[alias] = name
        return self

    @property
    def event_types(self) -> list[str]:
        """The event type names anything currently subscribes to.

        Fan-out selects on this, which is why an event with no subscribers stays undelivered
        rather than being marked done and thrown away - deployments routinely add a handler
        after the events it wants already exist.
        """
        return sorted(self._by_event)

    def subscribers(self, event_type: str) -> list[EventHandler[Any]]:
        return list(self._by_event.get(event_type, ()))

    def by_name(self, name: str) -> EventHandler[Any] | None:
        """Resolve a queued row's handler name, following a rename if one was declared."""
        return self._by_name.get(name) or self._by_name.get(self._aliases.get(name, ""))

    def __len__(self) -> int:
        return len(self._by_name)

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
        self._handlers: list[EventHandler[Any]] = []
        # Added to as each handler arrives, only so it is refused on the line that added it.
        self._checked = EventHandlerRegistry()

    def with_handler(self, handler: EventHandler[Any]) -> Self:
        if handler is None:
            raise ValidationError("handler cannot be None")
        self._checked.add(handler)
        self._handlers.append(handler)
        return self

    def with_handlers(self, handlers: Iterable[EventHandler[Any]]) -> Self:
        if handlers is None:
            raise ValidationError("handlers cannot be None")
        for handler in handlers:
            self.with_handler(handler)
        return self

    def build(self) -> EventHandlerRegistry:
        return EventHandlerRegistry(self._handlers)
