"""Writing one shard's slice of a plan.

The rows first, then the events they produced, both on the connection the caller has a
transaction open on. That ordering and that single connection are the whole of rule 4 - there
is no observable moment where a change is committed and its events are not - so they live in
one named place with one test, rather than inline in the executor among the retries and the
span bookkeeping.

The transaction is the caller's. This opens none, commits none, and rolls back none: handed a
connection, it writes on it. That is what makes the guarantee impossible to break by accident
from the outside - a persister that opened its own would be the dual write this framework
exists to remove.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from ..domain import Model, ModelEvent, Persistable
from ..errors import ValidationError
from ..persistence import Transaction
from ..repository import AnyRepository, RepositoryRegistry
from .outbox import EventPersister
from .plan import PersistableChanges

__all__ = ["ChangePersister"]

LOG = logging.getLogger("io.ekbatan.core.action.persister.ChangePersister")


class _ChangePersisterCore:
    """What to write and in which order - shared by :class:`ChangePersister` and
    ``ekbatan.aio``'s ``AsyncChangePersister``, which differ only in awaiting each write."""

    __slots__ = ("outbox", "repositories")

    def __init__(self, repositories: RepositoryRegistry, outbox: Any) -> None:
        if not len(repositories):
            raise ValidationError("at least one repository is required")
        self.repositories = repositories
        self.outbox = outbox

    def repository_for(self, model_type: type) -> AnyRepository:
        """The repository that writes this model, or a refusal naming it.

        Reached through here as well as directly, because the executor needs a repository to
        route a row long before it needs one to write it, and both should fail the same way.
        """
        return self.repositories.repository(model_type)

    def _writes(
        self, changes: Mapping[type, PersistableChanges]
    ) -> tuple[list[ModelEvent], list[tuple[Any, list[Any], list[Any]]]]:
        """The events to record, and per model type its repository, additions and updates.

        In the plan's order, which is the order the rows are written in: models first, then
        the events they produced, all on one connection.
        """
        events: list[ModelEvent] = []
        LOG.debug(
            "Persisting %d additions, %d updates",
            sum(len(c.additions) for c in changes.values()),
            sum(len(c.updates) for c in changes.values()),
        )
        writes: list[tuple[Any, list[Any], list[Any]]] = []
        for model_type, staged in changes.items():
            repo = self.repository_for(model_type)
            if issubclass(model_type, Model):
                events += _events_of(staged.additions.values())
                events += _events_of(staged.updates.values())
            writes.append((repo, list(staged.additions.values()), list(staged.updates.values())))
        return events, writes


class ChangePersister(_ChangePersisterCore):
    """Flushes staged changes to storage, inside a transaction somebody else opened."""

    __slots__ = ()

    outbox: EventPersister

    def __init__(self, repositories: RepositoryRegistry, outbox: EventPersister) -> None:
        super().__init__(repositories, outbox)

    def persist(
        self,
        tx: Transaction,
        changes: Mapping[type, PersistableChanges],
        *,
        namespace: str,
        action_name: str,
        params: Any,
        started: datetime,
        completed: datetime,
        action_id: uuid.UUID,
    ) -> None:
        """Write the rows, then the events, inside ``tx``.

        The shard is not a separate argument: the transaction is already on one, and two ways
        to say which would eventually disagree.
        """
        events, writes = self._writes(changes)
        for repo, additions, updates in writes:
            # The no-result variants deliberately. What was staged is already in hand and the
            # events came off it, so nothing here reads a written row back - asking the
            # database to return every one of them would be paid on every unit of work for
            # nothing. A caller who wants the rows back calls the repository itself.
            if additions:
                repo.add_all_no_result(additions)
            if updates:
                repo.update_all_no_result(updates)

        self.outbox.persist_action_events(
            namespace,
            action_name,
            started,
            completed,
            params,
            events,
            tx.shard,
            action_id,
        )
        LOG.debug("Persisted events for action %s [actionEventId=%s]", action_name, action_id)


def _events_of(models: Iterable[Persistable[Any, Any]]) -> list[ModelEvent]:
    """Every attached event, as it was constructed.

    Each already carries its model's id and name - its constructor required them - so nothing
    is filled in here, as nothing is in Java's ``SingleTableJsonEventPersister``, which writes
    ``event.modelId`` and ``event.modelName`` straight to their columns.
    """
    return [event for model in models for event in _attached(model)]


def _attached(model: Persistable[Any, Any]) -> Sequence[Any]:
    return getattr(model, "events", ())
