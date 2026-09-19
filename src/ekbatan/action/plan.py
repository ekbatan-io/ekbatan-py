"""The staging area.

A unit of work does not write as it goes. It says what it wants written, on a plan, and the
executor commits the whole plan in one transaction after the unit of work returns. That is what
makes "it either all happened or none of it did" true without the application arranging it.

Staging is not writing. A unit of work that stages a change and then raises has written
nothing - which is the property that distinguishes a staging area from a lazily-flushed
session, and the one most worth testing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, cast

from ..clock import Clock, to_storage
from ..domain import Model, Persistable
from ..errors import ValidationError

__all__ = ["ActionPlan", "PersistableChanges"]


@dataclass(slots=True)
class PersistableChanges:
    """What is staged for one model type, keyed by id so a row staged twice stays one row."""

    additions: dict[Any, Persistable[Any, Any]] = field(default_factory=dict)
    updates: dict[Any, Persistable[Any, Any]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.additions or self.updates)

    @property
    def all(self) -> list[Persistable[Any, Any]]:
        return [*self.additions.values(), *self.updates.values()]


class ActionPlan:
    """Accumulates one unit of work's intended changes.

    Created fresh for every invocation, including every retry attempt, so attempts are
    independent. Not thread-safe on purpose: it belongs to the invocation, and a plan mutated
    from work handed to a pool is a data race with a correct-looking traceback.
    """

    __slots__ = ("_by_type", "_clock")

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._by_type: dict[type, PersistableChanges] = {}

    # ------------------------------------------------------------------ staging

    def add[P: Persistable[Any, Any]](self, model: P) -> P:
        """Stage an insert. Returns the row you passed, as Java's ``add`` does.

        Nothing is filled in here any more. ``created_date`` is required at construction and
        ``updated_date`` falls back to it, so the row handed over is already the row that will
        be written - which is why this can hand back the same object rather than a copy.
        """
        changes = self._changes(type(model))
        self._refuse_if_staged(changes, model)
        changes.additions[model.id] = model
        return model

    def add_all[P: Persistable[Any, Any]](self, models: Iterable[P]) -> list[P]:
        return [self.add(m) for m in models]

    def update[P: Persistable[Any, Any]](self, model: P) -> P:
        """Stage an update. Returns the row as it will be written - version already moved.

        Handing back the incremented copy is what lets the rest of the unit of work read
        forward instead of reasoning about what the database is going to do later.
        """
        changes = self._changes(type(model))
        self._refuse_if_staged(changes, model)
        stamped = self._stamp_updated(model)
        # Staged at the version it was read at; the repository moves it when it writes, under
        # `WHERE version = ?`. The caller gets the moved copy, so the rest of the unit of work
        # reads forward instead of reasoning about what the database will do later.
        changes.updates[stamped.id] = stamped
        return stamped.next_version()

    def update_all[P: Persistable[Any, Any]](self, models: Iterable[P]) -> list[P]:
        return [self.update(m) for m in models]

    def _refuse_if_staged(self, changes: PersistableChanges, model: Persistable[Any, Any]) -> None:
        """One row, staged once.

        A unit of work is one write per row, so staging the same row twice cannot mean two
        writes - and the second call would have to either overwrite the first silently or
        invent a version the database has never held. Both are worse than saying so.

        The first value handed back is what makes it plain: after ``a = plan.update(x)``, a
        second staging would leave ``a`` claiming a version it no longer has and a balance
        nobody is going to write. Compose the changes on the model instead, and stage the
        result once - which is also how it reads best.
        """
        staged = model.id in changes.additions or model.id in changes.updates
        if not staged:
            return
        name = type(model).__name__
        raise ValidationError(
            f"{name} {model.id} is already staged in this unit of work. A unit of work writes "
            f"each row once, so two changes to one row have to be composed before they are "
            f"staged - `wallet.deposit(10).withdraw(5)`, then one plan.update - rather than "
            f"staged one after another."
        )

    # ------------------------------------------------------------------ reading

    def changes(self) -> Mapping[type, PersistableChanges]:
        """A read-only view of what is staged, by model type.

        A view rather than the map itself: a caller holding the real one can empty the plan, and
        the executor would then commit a unit of work that had quietly become a no-op.
        """
        return MappingProxyType(self._by_type)

    def additions[P: Persistable[Any, Any]](self, model_type: type[P]) -> Sequence[P]:
        changes = self._by_type.get(model_type)
        return cast("list[P]", list(changes.additions.values())) if changes else []

    def updates[P: Persistable[Any, Any]](self, model_type: type[P]) -> Sequence[P]:
        changes = self._by_type.get(model_type)
        return cast("list[P]", list(changes.updates.values())) if changes else []

    def __bool__(self) -> bool:
        return any(self._by_type.values())

    def __repr__(self) -> str:
        staged = sum(len(c.additions) + len(c.updates) for c in self._by_type.values())
        return f"ActionPlan({staged} staged)"

    # ---------------------------------------------------------------- internals

    def _changes(self, model_type: type) -> PersistableChanges:
        return self._by_type.setdefault(model_type, PersistableChanges())

    def _stamp_updated[P: Persistable[Any, Any]](self, model: P) -> P:
        """Mark when the row changed. An entity has no timestamps, and neither has the version
        moved yet - the repository does that as it writes, under the optimistic lock."""
        if not isinstance(model, Model):
            return model
        return model.evolve(updated_date=to_storage(self._clock.now()))
