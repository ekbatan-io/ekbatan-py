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
import importlib
import typing
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Self

from .clock import to_storage
from .errors import EkbatanError, ValidationError

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
class ModelEvent[M = Any]:
    """Something that happened to a model, and which model it happened to.

    Declared the way Java declares one - the model it is about on the class line, its id in the
    constructor::

        @dataclass(frozen=True, kw_only=True)
        class WalletMoneyDeposited(ModelEvent["Wallet"]):
            amount: Decimal
            new_balance: Decimal

        WalletMoneyDeposited(model_id=str(self.id), amount=amount, new_balance=new_balance)

    which is Java's ``new WalletMoneyDepositedEvent(id, amount, newBalance)`` with
    ``super(id.getValue().toString(), Wallet.class)`` inside it. The type argument does what
    ``Wallet.class`` does there: it fixes :attr:`model_name` once per event class, so no call
    site passes it. A string, as ``ShardedId["Wallet"]`` is one, because events are declared
    before the model that emits them.

    The persister writes both fields to their columns exactly as the event carries them, as
    Java's does - it does not look at the model the event was attached to.
    """

    #: The model's id, as a string - Java's ``modelId``, and the ``model_id`` column a consumer
    #: correlates on. Required, and refused when blank, as ``Validate.notBlank`` refuses it.
    model_id: str = field(compare=False)

    #: The model class's bare name - Java's ``modelName``, and the ``model_type`` column. Never
    #: passed: it comes off the class line, as Java's comes off ``Wallet.class``.
    model_name: str = field(init=False, compare=False)

    #: Resolved once per event class, from the type argument.
    __model_name__: ClassVar[str | None] = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        named = _declared_model_name(cls)
        if named is not None:
            cls.__model_name__ = named

    def __post_init__(self) -> None:
        name = type(self).__model_name__
        if not name:
            # Java cannot get here: `super(modelId, Wallet.class)` is the only constructor, so
            # an event that names no model does not compile.
            raise ValidationError(
                f"model class cannot be None: {type(self).__name__} does not say which model it "
                f"is about; declare it as "
                f'class {type(self).__name__}(ModelEvent["Wallet"]) - the name is what '
                f"fills the model_type column"
            )
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValidationError(
                f"model_id cannot be None or blank: {type(self).__name__} needs the id of the "
                f"{name} it is about - pass model_id=str(self.id). The model_id column is what a "
                f"consumer correlates the event back on"
            )
        object.__setattr__(self, "model_name", name)

    def payload(self) -> dict[str, Any]:
        """The event as it is stored. Override to control the wire shape.

        The two identity fields are in it, under Java's names - ``modelId`` and ``modelName``,
        not ``model_id`` and ``model_name``. They are also columns of their own, so this is a
        duplication; it is deliberate, and it is the difference between the two ports being able
        to read each other's rows and not.

        Java's local event handler deserialises this payload straight into the handler's event
        class (``objectMapper.treeToValue(notification.payload, handler.eventType())``), and
        every event class there carries a ``@JsonCreator`` taking ``@JsonProperty("modelId")``
        which it hands to ``ModelEvent``'s constructor - where ``Validate.notBlank`` refuses a
        null. A payload written without them therefore does not deserialise on the Java side at
        all: the delivery fails, retries, and expires.

        A CDC consumer reading `ActionEvent` never needs them here, because that type has its
        own `modelId` and `modelType` fields read from the columns. The local handler does.
        """
        return {
            **{
                f.name: getattr(self, f.name)
                for f in dataclasses.fields(self)
                if f.name not in _EVENT_IDENTITY
            },
            "modelId": self.model_id,
            "modelName": self.model_name,
        }

    @property
    def event_type(self) -> str:
        """The name a consumer routes and deserialises on."""
        return type(self).__name__


#: Written into the payload under Java's spelling rather than Python's, so the two ports read
#: each other. Excluded under their own names to avoid carrying both.
_EVENT_IDENTITY = frozenset({"model_id", "model_name"})


class EntityBuilder[T, ID = Any, S = Any]:
    """Accumulates field values, then builds one instance.

    Parameterised by what it builds and by that model's id and state types, so ``id`` and
    ``state`` are typed here rather than re-declared by every subclass. A generated builder
    then only has to describe the fields its own model added::

        class WalletBuilder(ModelBuilder[Wallet, ShardedId[Wallet], WalletState]):
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

    #: Set by a subclass so it needs no constructor argument, or filled in by
    #: :meth:`Persistable.__init_subclass__` when the model names this builder on its class
    #: line - whichever happens first.
    builds: ClassVar[type[Any] | None] = None

    #: ``"package.module:Wallet"``, written by the generator. The fallback for the one order
    #: the class line cannot cover: this module imported on its own, before the module that
    #: defines the model. `wallet()` straight out of `builders` is exactly that.
    builds_ref: ClassVar[str | None] = None

    def __init__(self, target: type[T] | None = None, values: dict[str, Any] | None = None) -> None:
        resolved = target or type(self).builds or type(self)._resolve_builds()
        if resolved is None:
            raise ValidationError(
                f"{type(self).__name__} does not know what it builds; pass the type, or "
                f"subclass it with `builds = YourModel`"
            )
        self._target: type[T] = resolved
        self._values: dict[str, Any] = dict(values or {})

    @classmethod
    def _resolve_builds(cls) -> type[Any] | None:
        """Import the module that defines the model, once, and remember the answer."""
        if cls.builds_ref is None:
            return None
        module_name, _, attribute = cls.builds_ref.partition(":")
        module = importlib.import_module(module_name)
        cls.builds = getattr(module, attribute)
        return cls.builds

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


#: Keyed by class and never invalidated - a dataclass's fields are fixed at definition.
_REQUIRED: dict[type, tuple[str, ...]] = {}


def _required(target: type) -> tuple[str, ...]:
    """The fields of ``target`` that may not be ``None``, read off their annotations.

    Java writes this out per class - ``Validate.notNull(builder.ownerId, ...)`` once per field,
    in every model's constructor - because a Java base class cannot enumerate a subclass's
    fields. A dataclass can, so the rule is stated once here and covers the fields an author
    would have forgotten as well as the ones they remembered.

    The mapping from Java is exact, and shorter by one row. ``String currency`` is
    ``currency: str`` and refuses ``None``; ``Optional<String>`` is ``str | None`` and accepts
    it. Java's fourth case - a ``null`` where an ``Optional`` reference belongs - has no
    counterpart, because Python's absence *is* ``None`` rather than a wrapper that can itself
    be missing.

    Cached per class: this is read on every construction, and the answer cannot change.
    """
    cached = _REQUIRED.get(target)
    if cached is not None:
        return cached
    try:
        hints = typing.get_type_hints(target)
    except Exception:  # an annotation naming something only importable under TYPE_CHECKING
        _REQUIRED[target] = ()
        return ()
    names = []
    for declared in dataclasses.fields(target):
        hint = hints.get(declared.name)
        if hint is None or hint is Any:
            continue
        if type(None) in typing.get_args(hint):
            continue  # `T | None`: absence is part of the type
        names.append(declared.name)
    _REQUIRED[target] = tuple(names)
    return _REQUIRED[target]


def _declared_builder(target: type) -> type[EntityBuilder[Any, Any, Any]] | None:
    """The builder named on the class line, or ``None`` if the model did not name one.

    ``class Wallet(Model[ShardedId["Wallet"], WalletState, WalletBuilder])`` puts the builder
    where a checker can already see it - the return type of ``copy()`` is the third parameter -
    and this is the other half: reading the same argument back so the base can build one.

    Java has no counterpart because it needs none. Its ``copy()`` is ``abstract``, so every
    model writes the method out, and writing it is where the narrower return type goes. Here
    the base can fill the method in, because a dataclass knows its own fields - so the type
    has nowhere else to live but the class line.

    The walk is over ``__mro__`` rather than the class's own bases, so a model that subclasses
    another model inherits the answer.
    """
    for klass in target.__mro__:
        for base in getattr(klass, "__orig_bases__", ()):
            args = typing.get_args(base)
            # The third argument, and only if it really is a builder class. Matching on the
            # shape rather than on `get_origin(base) is Model` because this runs while `Model`
            # itself is being created, before the name exists to compare against.
            if len(args) >= 3 and isinstance(args[2], type) and issubclass(args[2], EntityBuilder):
                return args[2]
    return None


def _keep_row_equality(cls: type) -> None:
    """Put the equality a row class inherits on the class itself, before its decorator runs.

    ``@dataclass`` writes ``__eq__`` - and, when frozen, ``__hash__`` - from every field unless the
    class defines them itself; a definition on a base class does not count. So without this, every
    model declared the documented way would quietly compare whole rows. Copying the inherited pair
    into the class's own namespace is what makes the decorator leave it alone.

    A class that writes its own ``__eq__`` keeps it, with whatever hash it gives (Python makes a
    class that defines ``__eq__`` alone unhashable, and that is left to the class). Its subclasses
    inherit that choice rather than having the framework's put back.
    """
    if "__eq__" in vars(cls):
        return
    for klass in cls.__mro__[1:]:
        if "__eq__" in vars(klass):
            cls.__eq__ = vars(klass)["__eq__"]  # type: ignore[method-assign]
            break
    for klass in cls.__mro__[1:]:
        if "__hash__" in vars(klass):
            cls.__hash__ = vars(klass)["__hash__"]  # type: ignore[method-assign]
            break


def _declared_model_name(target: type) -> str | None:
    """The model an event class names on its class line, or ``None`` if it names none.

    ``ModelEvent["Wallet"]`` gives ``"Wallet"``; ``ModelEvent[Wallet]`` gives the class's bare
    name, which is the same string - Java's ``getSimpleName()``. Only this class's own bases
    are read: a subclass of an event that already named its model inherits the answer as a
    class attribute, and one that names a different model overrides it.
    """
    for base in getattr(target, "__orig_bases__", ()):
        if typing.get_origin(base) is not ModelEvent:
            continue
        (arg,) = typing.get_args(base) or (None,)
        if isinstance(arg, str):
            return arg
        if isinstance(arg, typing.ForwardRef):
            return arg.__forward_arg__
        if isinstance(arg, type):
            return arg.__name__
    return None


def _no_typed_builder(target: type, route: str) -> EkbatanError:
    """Java refuses this at compile time; there is no compile step here, so it refuses here.

    An annotation processor runs on every Java build, so `copy()` there returns a
    `WalletBuilder` or the class does not compile. The untyped builder means this port *can*
    run without one - and then a checker sees only `__getattr__`, `.balance("oops")`
    type-checks, and the file the generator exists for is quietly absent.

    Nothing here requires the builder to be *generated*. What is required is that the model
    names a builder a checker can read, and a hand-written one does that as well as a
    generated one - better, where a setter needs a conditional default or a computed field.
    """
    name = target.__name__
    kind = "Model" if issubclass(target, Model) else "Entity"
    return EkbatanError(
        f"{name}.{route} needs a typed builder, and {name} has not named one.\n"
        f"\n"
        f"The untyped builder resolves setters by name, so a checker sees `__getattr__` and "
        f"`.field(wrong_type)` slips through. Write the builder, either way round:\n"
        f"\n"
        f"  generated   python -m ekbatan.codegen {target.__module__} --out <pkg>/builders.py\n"
        f'  by hand     class {name}Builder({kind}Builder["{name}", ...]):\n'
        f"                  def balance(self, value: Decimal) -> Self: "
        f"return self.set(balance=value)\n"
        f"\n"
        f"then name it on {name}'s class line, which is the whole of the wiring:\n"
        f"\n"
        f'  class {name}({kind}[ShardedId["{name}"], {name}State, {name}Builder]):\n'
        f"\n"
        f"`evolve()` needs none of this and is the right call for a one-field change. "
        f"{kind}Builder({name}) stays reachable by name, for a model with nowhere to put a "
        f"builder - a test defining one inline, or the framework's own internals."
    )


@dataclass(frozen=True, kw_only=True)
class Persistable[
    ID,
    S: Enum,
    B: EntityBuilder[Any, Any, Any] = EntityBuilder[Any, Any, Any],
]:
    """A row, in memory: an identity, a lifecycle state, and an optimistic-locking version.

    **Equal when it is the same row at the same version**, as Java's ``Entity.equals`` and
    ``Model.equals`` say: the same class, and the same values for :attr:`_IDENTITY_FIELDS` - id,
    state and version here, and the two timestamps as well on a :class:`Model`. The business
    fields - a balance, a currency - take no part, and the hash is the id's alone. That is how
    Python compares a saved record too: Django by primary key, SQLAlchemy by one object per row,
    and *Architecture Patterns with Python* by the entity's identifier. Comparing every field is
    for value objects, which have no identity of their own.

    It holds however the model is declared. ``@dataclass(frozen=True)`` on a model would write a
    whole-row ``__eq__`` of its own, so each model class is handed this one as it is defined,
    before its decorator runs - and a decorator never overwrites a method the class already has.
    A model that writes its own ``__eq__`` keeps it.
    """

    #: What makes two instances the same row at the same version.
    _IDENTITY_FIELDS: ClassVar[tuple[str, ...]] = ("id", "state", "version")

    id: ID
    state: S
    version: int = 1

    def __eq__(self, other: object) -> bool:
        if other is self:
            return True
        if type(other) is not type(self):
            # Java's `getClass() != o.getClass()`: a subclass is a different kind of row.
            return NotImplemented
        return all(getattr(self, f) == getattr(other, f) for f in self._IDENTITY_FIELDS)

    def __hash__(self) -> int:
        # The id alone, as Java's `hashCode()` is. Hashing every field made any model with a
        # list or dict field - a JSON column - unusable in a set or as a dict key.
        return hash(self.id)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Tell the declared builder what it builds, at the moment the model is defined.

        The class line already says which builder belongs to this model. Without this the
        builder would have to say so as well - ``builds = Wallet`` - and saying it means
        importing the model, which is the import the class line is already making in the other
        direction. One of the two has to give, and the builder is the one with nothing to lose:
        it is generated, and it is the half a checker never needs at runtime.

        Only fills a blank. A hand-written builder that sets ``builds`` itself keeps it.
        """
        super().__init_subclass__(**kwargs)
        declared = _declared_builder(cls)
        if declared is not None and declared.builds is None:
            declared.builds = cls
        _keep_row_equality(cls)

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
        for name in _required(type(self)):
            if getattr(self, name) is None:
                raise ValidationError(f"{name} cannot be null")
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
    def builder(cls) -> B:
        """An empty builder for this type - what a constructor would be if it were fluent.

        The counterpart to :meth:`copy`: that one starts from a row you have, this one from
        nothing. Java's generated ``WalletBuilder.wallet()``.

        Refused unless the entity named its builder on the class line. See
        :func:`_no_typed_builder`.
        """
        declared = _declared_builder(cls)
        if declared is None:
            raise _no_typed_builder(cls, "builder()")
        return typing.cast("B", declared())

    def copy(self) -> B:
        """A builder pre-loaded with every one of this row's values.

        Refused unless the entity named its builder on the class line - a builder a checker can
        read, generated by `ekbatan.codegen` or written by hand. See :func:`_no_typed_builder`.
        """
        declared = _declared_builder(type(self))
        if declared is None:
            raise _no_typed_builder(type(self), "copy()")
        return typing.cast("B", declared().copy_base(self))

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
class Entity[
    ID,
    S: Enum = GenericState,
    B: EntityBuilder[Any, Any, Any] = EntityBuilder[Any, Any, Any],
](Persistable[ID, S, B]):
    """State whose changes are not events.

    Writing one emits nothing - but the unit of work that wrote it is still recorded, because
    the audit trail is of units of work rather than of events.

    No timestamps: an entity is state, not history. Something that needs to know when it last
    changed almost certainly has changes worth telling the rest of the system about, which
    makes it a :class:`Model`.
    """


@dataclass(frozen=True, kw_only=True)
class Model[
    ID,
    S: Enum,
    B: ModelBuilder[Any, Any, Any] = ModelBuilder[Any, Any, Any],
](Persistable[ID, S, B]):
    """A row whose changes are worth telling the rest of the system about.

    Events are excluded from equality: two readers of one database row are looking at the same
    wallet whether or not one of them is mid-change.

    ``created_date`` is **required**, as Java's ``createdDate`` is: its constructor says
    ``Validate.notNull(builder.createdDate)`` and ``build()`` throws without it. A model is
    a record of something that happened, and when it happened is part of the record rather
    than a column the framework backfills. Pass ``self.clock.now()`` from the action, which
    is what Java's ``clock.instant()`` is doing.

    ``updated_date`` falls back to ``created_date``, again as Java's does - a row that has
    never changed was last updated when it was created.
    """

    #: Java's `Model.equals` adds the two timestamps to id, state and version.
    _IDENTITY_FIELDS: ClassVar[tuple[str, ...]] = (
        "id",
        "state",
        "version",
        "created_date",
        "updated_date",
    )

    created_date: datetime
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

        # `created_date` is required, and `super().__post_init__()` has already refused a null
        # one - its annotation is `datetime`, not `datetime | None`.
        #
        # Java: `.truncatedTo(MICROS)` on the way in, so what the row holds is what the column
        # holds. Here that means UTC with the zone dropped - an aware value stored as-is would
        # not compare equal to the same instant read back.
        object.__setattr__(self, "created_date", to_storage(self.created_date))
        # Java: `builder.updatedDate != null ? ... : this.createdDate`.
        object.__setattr__(
            self,
            "updated_date",
            self.created_date if self.updated_date is None else to_storage(self.updated_date),
        )

    @property
    def is_model(self) -> bool:
        return True

    @classmethod
    def builder(cls) -> B:
        """Refused unless the model named its builder on the class line.

        See :func:`_no_typed_builder`.
        """
        declared = _declared_builder(cls)
        if declared is None:
            raise _no_typed_builder(cls, "builder()")
        return typing.cast("B", declared())

    def copy(self) -> B:
        """A builder that can attach events, because a model can emit them.

        Refused unless the model named its builder on the class line - a builder a checker can
        read, generated by `ekbatan.codegen` or written by hand. See :func:`_no_typed_builder`.
        """
        declared = _declared_builder(type(self))
        if declared is None:
            raise _no_typed_builder(type(self), "copy()")
        return typing.cast("B", declared().copy_base(self))

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
