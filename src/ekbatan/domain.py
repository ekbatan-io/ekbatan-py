"""What a persistable thing is, and how it changes.

Two shapes, and the difference between them is the whole point of the framework:

- a :class:`Model` emits events. Every change worth telling the rest of the system about -
  created, deposited, closed - is attached to the model as it changes, and the framework
  persists those events in the same transaction as the row. It carries timestamps, because
  when a thing happened is part of what happened.
- an :class:`Entity` does not. It is state the persistence layer tracks whose changes have no
  business in the event stream: caches, lookup rows, idempotency markers. Id, state, version,
  and nothing else.

Both are frozen dataclasses. A change produces a new instance, which is what makes "the value
you are holding is the value that will be written" true rather than aspirational, and
:meth:`Persistable.copy` is how you make one::

    return self.copy().with_event(WalletMoneyDeposited(...)).balance(new_balance).build()

Unlike the Java implementation, ``copy()`` is inherited rather than written per model. There it
is abstract and every model implements the same six lines, handing each field back to its
builder, because Java cannot reconstruct a subclass whose shape it does not know. Here the
fields come off the dataclass, so a model that gains one gains it everywhere at once.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Self

from .errors import ValidationError

__all__ = [
    "DELETED",
    "Builder",
    "Entity",
    "GenericState",
    "Model",
    "ModelEvent",
    "Persistable",
]

#: The state value every read path treats as absent. Any state enum may declare it.
DELETED = "DELETED"


class GenericState(Enum):
    """A lifecycle for things that do not have one of their own."""

    ACTIVE = "ACTIVE"
    DELETED = "DELETED"


@dataclass(frozen=True, kw_only=True)
class ModelEvent:
    """Something that happened to a model.

    Carries no identity. Which model this is about, and of what type, is known by the model
    the event was attached to - so the framework fills those columns in at persist time and
    the event itself holds only what happened::

        @dataclass(frozen=True, kw_only=True)
        class WalletMoneyDeposited(ModelEvent):
            amount: Decimal
            new_balance: Decimal

    Not ``WalletMoneyDeposited(wallet_id, amount, new_balance)``. The id is already known, and
    a parameter that can only ever be passed one correct value is a parameter that will one day
    be passed a different one.
    """

    def payload(self) -> dict[str, Any]:
        """The event as it is stored. Override to control the wire shape."""
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}

    @property
    def event_type(self) -> str:
        """The name a consumer routes and deserialises on."""
        return type(self).__name__


class Builder[T]:
    """Accumulates field values, then builds one instance.

    Reached through :meth:`Persistable.copy`, and fluent the whole way::

        wallet.copy().balance(new_balance).with_event(WalletMoneyDeposited(...)).build()

    Every field of the target is a setter, resolved at call time from the dataclass itself.
    Java needs an annotation processor to generate this per class; here one builder covers
    every model and entity there will ever be.
    """

    __slots__ = ("_target", "_values")

    def __init__(self, target: type[T], values: dict[str, Any] | None = None) -> None:
        self._target = target
        self._values: dict[str, Any] = dict(values or {})

    # -- the fields every persistable has, spelled out so a type checker sees them ----------

    def id(self, value: Any) -> Self:
        return self.set(id=value)

    def state(self, value: Any) -> Self:
        return self.set(state=value)

    def version(self, value: int) -> Self:
        return self.set(version=value)

    def with_initial_version(self) -> Self:
        """Start at 1 - what a row being inserted for the first time carries."""
        return self.set(version=1)

    def increase_version(self) -> Self:
        return self.set(version=int(self._values.get("version", 0)) + 1)

    def created_date(self, value: datetime | None) -> Self:
        return self.set(created_date=value)

    def updated_date(self, value: datetime | None) -> Self:
        return self.set(updated_date=value)

    def events(self, value: Iterable[ModelEvent]) -> Self:
        return self.set(events=tuple(value))

    def with_event(self, event: ModelEvent) -> Self:
        """Attach an event. The only way one enters the outbox."""
        return self.set(events=(*self._values.get("events", ()), event))

    def copy_from(self, other: Any) -> Self:
        """Take every field from an existing instance - the framework's and your own.

        This is what makes an inherited ``copy()`` possible. Java has no equivalent, because a
        base class there cannot enumerate a subclass's fields; that is why every Java model
        writes out its own ``copy()``, and why one of them eventually forgets a field.
        """
        for name in _field_names(type(other)):
            self._values[name] = getattr(other, name)
        return self

    # -- everything else ---------------------------------------------------------------------

    def set(self, **changes: Any) -> Self:
        unknown = set(changes) - _field_names(self._target)
        if unknown:
            raise ValidationError(
                f"{self._target.__name__} has no field(s) {sorted(unknown)}; "
                f"it has {sorted(_field_names(self._target))}"
            )
        self._values.update(changes)
        return self

    def __getattr__(self, name: str) -> Callable[[Any], Builder[T]]:
        """Any field of the target, as a fluent setter."""
        if name.startswith("_") or name not in _field_names(self._target):
            raise AttributeError(
                f"{self._target.__name__} has no field {name!r}; "
                f"it has {sorted(_field_names(self._target))}"
            )

        def setter(value: Any) -> Builder[T]:
            self._values[name] = value
            return self

        return setter

    def build(self) -> T:
        return self._target(**self._values)

    def __repr__(self) -> str:
        return f"Builder({self._target.__name__}, {sorted(self._values)})"


def _field_names(target: type) -> frozenset[str]:
    return frozenset(f.name for f in dataclasses.fields(target))


@dataclass(frozen=True, kw_only=True)
class Persistable[ID, S: Enum]:
    """A row, in memory: an identity, a lifecycle state, and an optimistic-locking version."""

    id: ID
    state: S
    version: int = 1

    @property
    def emits_events(self) -> bool:
        return False

    @classmethod
    def builder(cls) -> Builder[Self]:
        """An empty builder for this type - what a constructor would be if it were fluent.

        The counterpart to :meth:`copy`: that one starts from a row you have, this one from
        nothing. Java's generated ``WalletBuilder.wallet()``, without the generation.
        """
        return Builder(cls)

    def copy(self) -> Builder[Self]:
        """A builder pre-loaded with every one of this row's values.

        Inherited, not written per model: the fields come off the dataclass, so a model that
        gains one gains it here too. Override it only to make a copy that deliberately differs
        - dropping a cached field, resetting a counter.
        """
        return Builder(type(self)).copy_from(self)

    def evolve(self, **changes: Any) -> Self:
        """This row with some fields changed.

        The shorthand the framework uses internally - stamping a timestamp, marking a row
        deleted. In a domain model prefer :meth:`copy`, so that every change in the codebase
        reads the same way.
        """
        return dataclasses.replace(self, **changes)

    def next_version(self) -> Self:
        return dataclasses.replace(self, version=self.version + 1)

    @property
    def is_deleted(self) -> bool:
        return getattr(self.state, "name", str(self.state)) == DELETED


@dataclass(frozen=True, kw_only=True)
class Entity[ID, S: Enum = GenericState](Persistable[ID, S]):
    """State whose changes are not events.

    Writing one emits nothing - but the unit of work that wrote it is still recorded, because
    the audit trail is of units of work rather than of events.

    No timestamps: an entity is state, not history. Something that needs to know when it last
    changed almost certainly has changes worth telling the rest of the system about, which
    makes it a :class:`Model`.
    """


@dataclass(frozen=True, kw_only=True)
class Model[ID, S: Enum](Persistable[ID, S]):
    """A row whose changes are worth telling the rest of the system about.

    Events are excluded from equality: two readers of one database row are looking at the same
    wallet whether or not one of them is mid-change.

    ``created_date`` and ``updated_date`` are left unset until the change is staged; the
    framework stamps them from its own clock. Making the application supply them means every
    unit of work carries two lines that are the same every time and wrong occasionally.
    """

    created_date: datetime | None = None
    updated_date: datetime | None = None
    events: tuple[ModelEvent, ...] = field(default=(), compare=False, repr=False)

    @property
    def emits_events(self) -> bool:
        return True

    def evolve(self, *, emit: ModelEvent | Iterable[ModelEvent] = (), **changes: Any) -> Self:
        """This model with some fields changed, and whatever it emitted in changing.

        The shorthand, equivalent to ``copy().with_event(...).…​.build()``. Prefer the builder
        in a domain model; this exists for the framework's own one-field changes.
        """
        emitted = (emit,) if isinstance(emit, ModelEvent) else tuple(emit)
        return dataclasses.replace(self, events=self.events + emitted, **changes)

    def without_events(self) -> Self:
        """This model with nothing attached - what a row read back from the database is.

        Reading a wallet must not hand back the events of whatever wrote it, or the next
        change would re-publish them and a consumer would see one deposit twice.
        """
        return dataclasses.replace(self, events=())
