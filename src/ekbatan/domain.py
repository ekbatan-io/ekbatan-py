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
from typing import Any, ClassVar, Self

from .errors import ValidationError

__all__ = [
    "DELETED",
    "Entity",
    "EntityBuilder",
    "GenericState",
    "Model",
    "ModelBuilder",
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


class EntityBuilder[T, ID = Any, S = Any]:
    """Accumulates field values, then builds one instance.

    Parameterised by what it builds and by that model's id and state types, so ``id`` and
    ``state`` are typed here rather than re-declared by every subclass. A generated builder
    then only has to describe the fields its own model added::

        class WalletBuilder(ModelBuilder[Wallet, WalletId, WalletState]):
            builds = Wallet

            def balance(self, value: Decimal) -> Self: ...

    Reached through :meth:`Persistable.copy` or :meth:`Persistable.builder`, and fluent the
    whole way::

        wallet.copy().balance(new_balance).with_event(MoneyDeposited(...)).build()

    Every field of the target is a setter, resolved at call time from the dataclass itself, so
    one builder covers every model there will ever be. What that cannot do is tell a type
    checker what a setter takes - see :mod:`ekbatan.codegen`, which generates exactly that and
    nothing else.
    """

    __slots__ = ("_target", "_values")

    #: Set by a subclass so it needs no constructor argument.
    builds: ClassVar[type[Any] | None] = None

    def __init__(self, target: type[T] | None = None, values: dict[str, Any] | None = None) -> None:
        resolved = target or type(self).builds
        if resolved is None:
            raise ValidationError(
                f"{type(self).__name__} does not know what it builds; pass the type, or "
                f"subclass it with `builds = YourModel`"
            )
        self._target: type[T] = resolved
        self._values: dict[str, Any] = dict(values or {})

    # -- what every persistable has -----------------------------------------------------------

    def id(self, value: ID) -> Self:
        return self.set(id=value)

    def state(self, value: S) -> Self:
        return self.set(state=value)

    def version(self, value: int) -> Self:
        return self.set(version=value)

    def with_initial_version(self) -> Self:
        """Start at 1 - what a row being inserted for the first time carries."""
        return self.set(version=1)

    def increase_version(self) -> Self:
        return self.set(version=int(self._values.get("version", 0)) + 1)

    def copy_base(self, other: Any) -> Self:
        """Take every field from an existing instance - the framework's and your own.

        Java's ``copyBase`` copies the six fields the base class knows about, and each model
        then writes its own ``copy()`` to hand over the rest. Here the fields come off the
        dataclass, so this one copies all of them and ``copy()`` can be inherited - which is
        also why a model that gains a field does not have to remember to copy it.
        """
        for name in _field_names(type(other)):
            self._values[name] = getattr(other, name)
        return self

    # -- everything else ----------------------------------------------------------------------

    def set(self, **changes: Any) -> Self:
        unknown = set(changes) - _field_names(self._target)
        if unknown:
            raise ValidationError(
                f"{self._target.__name__} has no field(s) {sorted(unknown)}; "
                f"it has {sorted(_field_names(self._target))}"
            )
        self._values.update(changes)
        return self

    def __getattr__(self, name: str) -> Callable[[Any], EntityBuilder[T, ID, S]]:
        """Any field of the target, as a fluent setter."""
        if name.startswith("_") or name not in _field_names(self._target):
            raise AttributeError(
                f"{self._target.__name__} has no field {name!r}; "
                f"it has {sorted(_field_names(self._target))}"
            )

        def setter(value: Any) -> EntityBuilder[T, ID, S]:
            self._values[name] = value
            return self

        return setter

    def build(self) -> T:
        return self._target(**self._values)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._target.__name__}, {sorted(self._values)})"


class ModelBuilder[T, ID = Any, S = Any](EntityBuilder[T, ID, S]):
    """A builder for a :class:`Model`: everything above, plus timestamps and events.

    Separate from :class:`EntityBuilder` for the same reason ``Model`` is separate from ``Entity`` -
    an entity has neither, and offering setters for fields it does not have would be an error
    waiting for someone to call it.
    """

    __slots__ = ()

    def created_date(self, value: datetime | None) -> Self:
        return self.set(created_date=value)

    def updated_date(self, value: datetime | None) -> Self:
        return self.set(updated_date=value)

    def events(self, value: Iterable[ModelEvent]) -> Self:
        return self.set(events=tuple(value))

    def with_event(self, event: ModelEvent) -> Self:
        """Attach an event. The only way one enters the outbox."""
        return self.set(events=(*self._values.get("events", ()), event))


def _field_names(target: type) -> frozenset[str]:
    return frozenset(f.name for f in dataclasses.fields(target))


@dataclass(frozen=True, kw_only=True)
class Persistable[ID, S: Enum]:
    """A row, in memory: an identity, a lifecycle state, and an optimistic-locking version."""

    id: ID
    state: S
    version: int = 1

    def __post_init__(self) -> None:
        """Refuse a row that could not be written, where it was built.

        Java checks all four in ``Model`` / ``Entity``'s constructor, and the reason survives
        the translation: without them the mistake surfaces as an ``IntegrityError`` from
        inside a batched insert, naming a column and a driver, several frames from the line
        that made it. ``version`` matters most - it is the optimistic-locking primitive, and a
        row inserted at 0 quietly starts one behind whatever reads it back.

        A subclass that defines its own ``__post_init__`` must call ``super().__post_init__()``
        or it loses these; Java's constructor chaining does that for you and Python's does not.
        """
        if self.id is None:
            raise ValidationError("id cannot be null")
        if self.state is None:
            raise ValidationError("state cannot be null")
        if self.version is None:
            raise ValidationError("version cannot be null")
        if self.version < 1:
            raise ValidationError(f"version must be greater than or equal to 1, not {self.version}")

    @property
    def is_model(self) -> bool:
        """Whether this row's changes are events. ``True`` for a :class:`Model` only.

        The persister branches on it to decide whether to look for events at all, which is why
        it is a property of the row rather than an ``isinstance`` check at every call site.
        """
        return False

    @classmethod
    def builder(cls) -> EntityBuilder[Self, ID, S]:
        """An empty builder for this type - what a constructor would be if it were fluent.

        The counterpart to :meth:`copy`: that one starts from a row you have, this one from
        nothing. Java's generated ``WalletBuilder.wallet()``, without the generation.
        """
        return EntityBuilder(cls)

    def copy(self) -> EntityBuilder[Self, ID, S]:
        """A builder pre-loaded with every one of this row's values.

        Inherited, not written per model: the fields come off the dataclass, so a model that
        gains one gains it here too. Override it only to make a copy that deliberately differs
        - dropping a cached field, resetting a counter.
        """
        return EntityBuilder(type(self)).copy_base(self)

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

    def __post_init__(self) -> None:
        """Everything a row is checked for, and a copy of the events.

        The copy is not housekeeping. ``events`` is annotated as a tuple but nothing stops a
        list being passed, and a frozen model holding the caller's list is not frozen: append
        to that list afterwards and the model gains an event it was never built with - which
        the persister then writes, in a transaction that had already decided what it was
        writing. Java's constructor says ``List.copyOf(...)`` for exactly this, and gets an
        unmodifiable list out of it as well.
        """
        super().__post_init__()
        if self.events is None:
            raise ValidationError("events cannot be null")
        if not isinstance(self.events, tuple):
            object.__setattr__(self, "events", tuple(self.events))

    @property
    def is_model(self) -> bool:
        return True

    @classmethod
    def builder(cls) -> ModelBuilder[Self, ID, S]:
        return ModelBuilder(cls)

    def copy(self) -> ModelBuilder[Self, ID, S]:
        """A builder that can attach events, because a model can emit them."""
        return ModelBuilder(type(self)).copy_base(self)

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
