"""Writing one shard's slice of a plan, awaited.

What to write and in which order - the rows, then the events they produced, on the one
connection the executor has a transaction open on - is the sync
:class:`~ekbatan.action.persister.ChangePersister`'s code. This awaits each write.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from ..action.persister import LOG, _ChangePersisterCore
from ..action.plan import PersistableChanges
from ..repository import RepositoryRegistry
from .outbox import AsyncEventPersister
from .persistence import AsyncTransaction

__all__ = ["AsyncChangePersister"]


class AsyncChangePersister(_ChangePersisterCore):
    """Flushes staged changes inside an async transaction somebody else opened."""

    __slots__ = ()

    outbox: AsyncEventPersister

    def __init__(self, repositories: RepositoryRegistry, outbox: AsyncEventPersister) -> None:
        super().__init__(repositories, outbox)

    async def persist(
        self,
        tx: AsyncTransaction,
        changes: Mapping[type, PersistableChanges],
        *,
        namespace: str,
        action_name: str,
        params: Any,
        started: datetime,
        completed: datetime,
        action_id: uuid.UUID,
    ) -> None:
        """Write the rows, then the events, inside ``tx``."""
        events, writes = self._writes(changes)
        for repo, additions, updates in writes:
            if additions:
                await repo.add_all_no_result(additions)
            if updates:
                await repo.update_all_no_result(updates)

        await self.outbox.persist_action_events(
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
