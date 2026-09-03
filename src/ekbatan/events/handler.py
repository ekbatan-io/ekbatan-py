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
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ..domain import ModelEvent
from ..errors import ValidationError

__all__ = ["EventEnvelope", "EventHandler", "EventHandlerRegistry"]


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
