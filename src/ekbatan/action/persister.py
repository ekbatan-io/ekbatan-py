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

from ..domain import Model, Persistable
from ..errors import ValidationError
from ..persistence import Transaction
from ..repository import Repository
from .outbox import EmittedEvent, EventPersister, OutboxRecord
from .plan import PersistableChanges

__all__ = ["ChangePersister"]

LOG = logging.getLogger("ekbatan.action")


class ChangePersister:
    """Flushes staged changes to storage, inside a transaction somebody else opened."""

    __slots__ = ("_by_model", "outbox")

    def __init__(
        self, repositories: Iterable[Repository[Any, Any]], outbox: EventPersister
    ) -> None:
        self._by_model: dict[type, Repository[Any, Any]] = {
            repo.model_type: repo for repo in repositories
        }
        if not self._by_model:
            raise ValidationError("at least one repository is required")
        self.outbox = outbox

    def repository_for(self, model_type: type) -> Repository[Any, Any]:
        """The repository that writes this model, or a refusal naming it.

        Checked wherever the lookup happens rather than only where the write does: the caller
        needs the repository to route the row long before it needs it to write the row, and a
        missing one found there is a null dereference with nothing in it about the type.
        """
        try:
            return self._by_model[model_type]
        except KeyError:
            raise ValidationError(
                f"no repository was given for {model_type.__name__}, so the change staged for "
                f"it cannot be written. Pass one to ActionExecutor(repositories=[...])."
            ) from None

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
        events: list[EmittedEvent] = []

        for model_type, staged in changes.items():
            repo = self.repository_for(model_type)
            if issubclass(model_type, Model):
                events += _events_of(staged.additions.values())
                events += _events_of(staged.updates.values())
            # The no-result variants deliberately. What was staged is already in hand and the
            # events came off it, so nothing here reads a written row back - asking the
            # database to return every one of them would be paid on every unit of work for
            # nothing. A caller who wants the rows back calls the repository itself.
            if staged.additions:
                repo.add_all_no_result(list(staged.additions.values()))
            if staged.updates:
                repo.update_all_no_result(list(staged.updates.values()))

        LOG.debug(
            "persisting %d addition(s) and %d update(s) on shard %s",
            sum(len(c.additions) for c in changes.values()),
            sum(len(c.updates) for c in changes.values()),
            tx.shard,
        )

        self.outbox.persist(
            tx.connection,
            OutboxRecord(
                namespace=namespace,
                action_id=action_id,
                action_name=action_name,
                action_params=params,
                started_date=started,
                completion_date=completed,
                events=events,
                shard=tx.shard,
            ),
        )


def _events_of(models: Iterable[Persistable[Any, Any]]) -> list[EmittedEvent]:
    return [
        EmittedEvent(str(model.id), type(model).__name__, event)
        for model in models
        for event in _attached(model)
    ]


def _attached(model: Persistable[Any, Any]) -> Sequence[Any]:
    return getattr(model, "events", ())
