"""Where an async unit of work's events go.

The rows are built by the sync persister's own code - the same columns, the same sentinel row
for a unit of work that emitted nothing, the same refusal of a payload that is not a JSON object
and of two events sharing one name. Only the insert is awaited, on the transaction the executor
has open on the shard.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import Table, insert

from ..action.outbox import EventEntity, _require_outbox_columns, _SingleTableJson
from ..domain import ModelEvent
from ..errors import ValidationError
from ..sharding import ShardIdentifier
from .persistence import AsyncDatabaseRegistry

__all__ = ["AsyncEventPersister", "AsyncSingleTableJsonEventPersister"]


@runtime_checkable
class AsyncEventPersister(Protocol):
    """Writes one invocation's events, awaited - :class:`~ekbatan.action.EventPersister`'s
    async twin. Called inside the transaction the state change was written in."""

    async def persist_action_events(
        self,
        namespace: str,
        action_name: str,
        started_date: datetime,
        completion_date: datetime,
        action_params: Any,
        model_events: Sequence[ModelEvent],
        shard: ShardIdentifier,
        action_event_id: uuid.UUID,
    ) -> None: ...


class AsyncSingleTableJsonEventPersister(_SingleTableJson):
    """The default :class:`AsyncEventPersister`: every model event, and a sentinel row for a
    unit of work that emits none, into one table with a JSON payload."""

    __slots__ = ("_registry", "_table")

    def __init__(self, database_registry: AsyncDatabaseRegistry, table: Table) -> None:
        super().__init__()
        if database_registry is None:
            raise ValidationError("database_registry cannot be None")
        self._registry = database_registry
        self._table = _require_outbox_columns(table)

    async def persist_action_events(
        self,
        namespace: str,
        action_name: str,
        started_date: datetime,
        completion_date: datetime,
        action_params: Any,
        model_events: Sequence[ModelEvent],
        shard: ShardIdentifier,
        action_event_id: uuid.UUID,
    ) -> None:
        with self._span(action_name, model_events):
            entities = self._entities(
                namespace,
                action_name,
                started_date,
                completion_date,
                action_params,
                model_events,
                action_event_id,
            )
            await self._add_all_no_result(entities, shard)

    async def _add_all_no_result(
        self, entities: Collection[EventEntity], shard: ShardIdentifier
    ) -> None:
        if not entities:
            return
        async with self._registry.manager(shard).connection() as conn:
            await conn.execute(insert(self._table), [e.to_row() for e in entities])
