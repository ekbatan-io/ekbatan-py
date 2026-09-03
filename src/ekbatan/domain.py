"""What a persistable thing is, and how it changes.

Two shapes, and the difference between them is the whole point of the framework:

- a :class:`Model` emits events. Every change worth telling the rest of the system about -
  created, deposited, closed - is attached to the model as it changes, and the framework
  persists those events in the same transaction as the row.
- an :class:`Entity` does not. It is state the persistence layer tracks whose changes have no
  business in the event stream: caches, lookup rows, idempotency markers.

Both are frozen dataclasses. A change produces a new instance, which is what makes "the value
you are holding is the value that will be written" true rather than aspirational.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Self

__all__ = [
    "DELETED",
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


@dataclass(frozen=True, kw_only=True)
class Persistable[ID, S: Enum]:
    """A row, in memory.

    ``created_date`` and ``updated_date`` are left unset until the change is staged; the
    framework stamps them from its own clock. Making the application supply them means every
    unit of work carries two lines that are the same every time and wrong occasionally.
    """

    id: ID
    state: S
    version: int = 1
    created_date: datetime | None = None
    updated_date: datetime | None = None

    @property
    def emits_events(self) -> bool:
        return False

    def evolve(self, **changes: Any) -> Self:
        """This row with some fields changed. The only way to 'mutate' one."""
        return dataclasses.replace(self, **changes)

    def next_version(self) -> Self:
        return dataclasses.replace(self, version=self.version + 1)

    @property
    def is_deleted(self) -> bool:
        return getattr(self.state, "name", str(self.state)) == DELETED


@dataclass(frozen=True, kw_only=True)
class Entity[ID](Persistable[ID, GenericState]):
    """State whose changes are not events.

    Writing one emits nothing - but the unit of work that wrote it is still recorded, because
    the audit trail is of units of work rather than of events.

    Its lifecycle is fixed at active-or-deleted. Something that needs a lifecycle of its own
    almost certainly has state changes worth telling the rest of the system about, which makes
    it a :class:`Model`.
    """

    state: GenericState = GenericState.ACTIVE


@dataclass(frozen=True, kw_only=True)
class Model[ID, S: Enum](Persistable[ID, S]):
    """A row whose changes are worth telling the rest of the system about.

    Events are attached as the model changes and are excluded from equality: two readers of one
    database row are looking at the same wallet whether or not one of them is mid-change.
    """

    events: tuple[ModelEvent, ...] = field(default=(), compare=False, repr=False)

    @property
    def emits_events(self) -> bool:
        return True

    def evolve(self, *, emit: ModelEvent | Iterable[ModelEvent] = (), **changes: Any) -> Self:
        """This model with some fields changed, and whatever it emitted in changing::

            def deposit(self, amount: Decimal) -> "Wallet":
                new_balance = self.balance + amount
                return self.evolve(
                    balance=new_balance,
                    emit=WalletMoneyDeposited(amount=amount, new_balance=new_balance),
                )

        There is no publish call and no outbox in sight, which is the point. The event is a
        fact about the change, written in the same statement's transaction, so there is no
        moment at which one exists without the other.
        """
        emitted = (emit,) if isinstance(emit, ModelEvent) else tuple(emit)
        return dataclasses.replace(self, events=self.events + emitted, **changes)

    def without_events(self) -> Self:
        """This model with nothing attached - what a row read back from the database is.

        Reading a wallet must not hand back the events of whatever wrote it, or the next
        change would re-publish them and a consumer would see one deposit twice.
        """
        return dataclasses.replace(self, events=())
