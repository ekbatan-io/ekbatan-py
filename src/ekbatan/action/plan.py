"""The staging area.

A unit of work does not write as it goes. It says what it wants written, on a plan, and the
executor commits the whole plan in one transaction after the unit of work returns. That is what
makes "it either all happened or none of it did" true without the application arranging it.

Staging is not writing. A unit of work that stages a change and then raises has written
nothing - which is the property that distinguishes a staging area from a lazily-flushed
session, and the one most worth testing.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from ..clock import Clock, to_storage
from ..domain import Model, Persistable

__all__ = ["ActionPlan", "Changes"]


@dataclass(slots=True)
class Changes:
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
        self._by_type: dict[type, Changes] = {}

    # ------------------------------------------------------------------ staging

    def add[P: Persistable[Any, Any]](self, model: P) -> P:
        """Stage an insert. Returns the row as it will be written, timestamps and all."""
        stamped = self._stamp_new(model)
        self._changes(type(model)).additions[stamped.id] = stamped
        return stamped

    def add_all[P: Persistable[Any, Any]](self, models: Iterable[P]) -> list[P]:
        return [self.add(m) for m in models]

    def update[P: Persistable[Any, Any]](self, model: P) -> P:
        """Stage an update. Returns the row as it will be written - version already moved.

        Handing back the incremented copy is what lets the rest of the unit of work read
        forward instead of reasoning about what the database is going to do later.

        **A row moves forward one version per unit of work, not per call.** Staging the same
        row a second time replaces what is staged and keeps the version already assigned, so
        two changes to one wallet are one write, guarded against the version that was actually
        read. Incrementing again would write ``WHERE version = 2`` against a row still at 1 and
        fail as a phantom conflict - with nothing concurrent anywhere near it.
        """
        changes = self._changes(type(model))
        pending = changes.updates.get(model.id)
        if pending is not None:
            staged = model.evolve(version=pending.version)
        elif model.id in changes.additions:
            # Changed after being staged for insert: still one insert, with the later values.
            inserted = self._stamp_touched(
                model.evolve(version=changes.additions[model.id].version)
            )
            changes.additions[inserted.id] = inserted
            return inserted
        else:
            staged = model.evolve(version=model.version + 1)
        stamped = self._stamp_touched(staged)
        changes.updates[stamped.id] = stamped
        return stamped

    def update_all[P: Persistable[Any, Any]](self, models: Iterable[P]) -> list[P]:
        return [self.update(m) for m in models]

    # ------------------------------------------------------------------ reading

    def changes(self) -> dict[type, Changes]:
        return self._by_type

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

    def _changes(self, model_type: type) -> Changes:
        return self._by_type.setdefault(model_type, Changes())

    def _stamp_new[P: Persistable[Any, Any]](self, model: P) -> P:
        """Fill in the timestamps a model has. An entity has none - it is state, not history."""
        if not isinstance(model, Model):
            return model
        now = to_storage(self._clock.now())
        changes: dict[str, Any] = {}
        if model.created_date is None:
            changes["created_date"] = now
        if model.updated_date is None:
            changes["updated_date"] = changes.get("created_date", now)
        return model.evolve(**changes) if changes else model

    def _stamp_touched[P: Persistable[Any, Any]](self, model: P) -> P:
        """Mark when it changed. An entity has no timestamps, so there is nothing to mark."""
        if not isinstance(model, Model):
            return model
        return model.evolve(updated_date=to_storage(self._clock.now()))
