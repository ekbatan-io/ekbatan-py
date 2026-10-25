"""Reacting to your own events, in your own process.

The minimal end of the spectrum: no broker, no connector, no second runtime. A unit of work
commits, fan-out turns each committed event into work for every subscriber, and dispatch runs
them. Delivery is at-least-once, which is the only guarantee anything on this side of a
database can honestly make.

A handler is written against an :class:`EventEnvelope` and nothing else. That is deliberate:
the day the handler outgrows in-process dispatch, the same body has to compile against a broker
consumer, and it can only do that if everything it needed arrived in the envelope.

A handler runs after the action that emitted its event has committed - later, on dispatch's own
threads, never inside the action. It extends :class:`EventHandler` - Java's ``EventHandler``,
with its ``name()``, ``aliases()``, ``event_type()`` and ``handle()`` - or
:class:`AsyncEventHandler`, whose ``handle()`` is ``async def`` to await an ``ekbatan.aio``
executor: dispatch then runs it on its handler loop, and decides success, retry and expiry from
it exactly as for a sync one.
"""

from __future__ import annotations

import inspect
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Any, Self

from .._builder import FieldBuilder
from ..domain import ModelEvent
from ..errors import ValidationError
from ..time import Instant

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
class EventEnvelope[E: ModelEvent[Any]]:
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

    started_date: Instant
    completion_date: Instant
    event_date: Instant
    model_id: str | None
    model_type: str | None

    #: Which attempt this is, starting at 1. Present so a handler can log it, not so it can
    #: decide to give up - that is the retention window's job.
    attempt: int = 1

    @classmethod
    def builder(cls) -> EventEnvelopeBuilder[E]:
        return EventEnvelopeBuilder()


class _HandlerCore[E: ModelEvent[Any]](ABC):
    """Both kinds of handler but for ``handle``: Java's ``EventHandler`` methods, in its order.

    Private - a handler extends :class:`EventHandler`, or :class:`AsyncEventHandler` for an
    ``async def handle()``.
    """

    @abstractmethod
    def name(self) -> str:
        """A cluster-stable identifier, stored on every notification row this handler creates.

        Not a label: renaming it creates a new subscription, and every row queued under the old
        name is stranded. Keep it stable across class and module renames, and use
        :meth:`aliases` when you do have to change it.
        """

    def aliases(self) -> AbstractSet[str]:
        """Earlier names this handler still answers to, so the rows queued under a name it was
        renamed from still find it. None unless overridden, as Java's ``Set.of()``."""
        return frozenset()

    @abstractmethod
    def event_type(self) -> type[E]:
        """The event this subscribes to. Subscription is by type; a handler that receives
        everything and filters in its own body is one that will eventually forget to."""


class EventHandler[E: ModelEvent[Any]](_HandlerCore[E]):
    """Reacts to one event type - Java's ``EventHandler``, extended rather than implemented:
    write ``name()``, ``event_type()`` and ``handle()``; ``aliases()`` is empty unless overridden.

    Runs on dispatch's threads, after the action that emitted the event has committed.
    """

    @abstractmethod
    def handle(self, envelope: EventEnvelope[E]) -> None:
        """Do the work. Raising records a failure and schedules a retry."""


class AsyncEventHandler[E: ModelEvent[Any]](_HandlerCore[E]):
    """An :class:`EventHandler` whose ``handle()`` is ``async def`` - for one that awaits an
    ``ekbatan.aio`` executor. The same ``name()``, ``aliases()`` and ``event_type()``.

    It runs on dispatch's handler loop (``ekbatan.events.HandlerLoop``), a batch at a time; what
    it awaits must belong to that loop. Raising records a failure and schedules a retry, as for a
    sync handler.
    """

    @abstractmethod
    async def handle(self, envelope: EventEnvelope[E]) -> None:
        """Do the work, awaited. Raising records a failure and schedules a retry."""


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
        """Java's ``withHandler``, check for check.

        A handler extends :class:`EventHandler` or :class:`AsyncEventHandler`, as a Java handler
        implements ``EventHandler``; anything else is refused, as Java's compiler refuses it. So
        is a ``handle()`` of the other kind - an ``async def`` on an :class:`EventHandler`, a plain
        ``def`` on an :class:`AsyncEventHandler` - because dispatch runs a handler by its base.
        """
        if handler is None:
            raise ValidationError("handler cannot be None")
        _refuse_a_handler_of_no_kind(handler)
        name = handler.name()
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("handler.name() cannot be blank")
        event_type = handler.event_type()
        if event_type is None:
            raise ValidationError("handler.event_type() cannot be None")
        short_name = getattr(event_type, "__name__", None)
        if not isinstance(short_name, str) or not short_name.strip():
            raise ValidationError("handler.event_type().__name__ cannot be blank")

        self._refuse_a_taken_lookup_name(name)

        previous = self._types_by_short_name.get(short_name)
        if previous is not None and previous is not event_type:
            raise ValidationError(
                f"Event type simple name collision: {short_name} maps to both "
                f"{_qualified(previous)} and {_qualified(event_type)}"
            )

        self._types_by_short_name[short_name] = event_type
        self._by_lookup[name] = handler
        aliases = handler.aliases()
        if aliases is None:
            raise ValidationError("handler.aliases() cannot be None")
        for alias in aliases:
            if not isinstance(alias, str) or not alias.strip():
                raise ValidationError("handler.aliases() cannot contain blank values")
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


class EventEnvelopeBuilder[E: ModelEvent[Any]](FieldBuilder[EventEnvelope[E]]):
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

    def started_date(self, started_date: Instant) -> Self:
        return self._set("started_date", started_date)

    def completion_date(self, completion_date: Instant) -> Self:
        return self._set("completion_date", completion_date)

    def event_date(self, event_date: Instant) -> Self:
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


def _refuse_a_handler_of_no_kind(handler: object) -> None:
    """A handler is one of the two kinds, by its base, and its ``handle()`` is that kind's."""
    what = type(handler).__qualname__
    if not isinstance(handler, EventHandler | AsyncEventHandler):
        raise TypeError(
            f"{what} is not an event handler: extend EventHandler, or AsyncEventHandler for an "
            f"async def handle()"
        )
    awaited = inspect.iscoroutinefunction(type(handler).handle)
    if isinstance(handler, EventHandler) and awaited:
        raise TypeError(
            f"{what} extends EventHandler, but its handle() is async def: extend "
            f"AsyncEventHandler instead"
        )
    if isinstance(handler, AsyncEventHandler) and not awaited:
        raise TypeError(
            f"{what} extends AsyncEventHandler, but its handle() is not async def: extend "
            f"EventHandler instead"
        )
