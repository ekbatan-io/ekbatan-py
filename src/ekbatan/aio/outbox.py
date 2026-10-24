"""Where an async unit of work's events go.

The rows are built by the sync persister's own code - the same columns, the same sentinel row
for a unit of work that emitted nothing, the same refusal of a payload that is not a JSON object
and of two events sharing one name. Only the insert is awaited, on the transaction the executor
has open on the shard.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import func, insert, select

from ..action.outbox import EventEntity, _SingleTableJson
from ..domain import ModelEvent
from ..errors import ValidationError
from ..schema import _EVENTS
from ..sharding import ShardIdentifier
from ..time import Instant
from .persistence import AsyncDatabaseRegistry

__all__ = [
    "AsyncEventEntityRepository",
    "AsyncEventPersister",
    "AsyncSingleTableJsonEventPersister",
]


class AsyncEventEntityRepository:
    """The outbox table's reads and writes for the async persister - the sync
    :class:`~ekbatan.action.outbox.EventEntityRepository`, awaited. Each joins the transaction
    open on the shard, else commits its statement on its own."""

    __slots__ = ("_registry", "_table")

    def __init__(self, database_registry: AsyncDatabaseRegistry) -> None:
        if database_registry is None:
            raise ValidationError("database_registry cannot be None")
        self._registry = database_registry
        self._table = _EVENTS

    async def count(self, shard: ShardIdentifier) -> int:
        async with self._registry.manager(shard).connection() as conn:
            found = await conn.execute(select(func.count()).select_from(self._table))
            return int(found.scalar_one())

    async def find_all(self, shard: ShardIdentifier) -> list[EventEntity]:
        async with self._registry.manager(shard).connection() as conn:
            return [EventEntity.from_row(r) for r in await conn.execute(select(self._table))]

    async def find_by_action_id(
        self, action_id: uuid.UUID, shard: ShardIdentifier
    ) -> list[EventEntity]:
        table = self._table
        async with self._registry.manager(shard).connection() as conn:
            rows = await conn.execute(select(table).where(table.c.action_id == action_id))
            return [EventEntity.from_row(r) for r in rows]

    async def add_all_no_result(
        self, entities: Collection[EventEntity], shard: ShardIdentifier
    ) -> None:
        if not entities:
            return
        async with self._registry.manager(shard).connection() as conn:
            await conn.execute(insert(self._table), [e.to_row() for e in entities])


@runtime_checkable
class AsyncEventPersister(Protocol):
    """Writes one invocation's events, awaited - :class:`~ekbatan.action.EventPersister`'s
    async twin. Called inside the transaction the state change was written in."""

    async def persist_action_events(
        self,
        namespace: str,
        action_name: str,
        started_date: Instant,
        completion_date: Instant,
        action_params: Any,
        model_events: Sequence[ModelEvent],
        shard: ShardIdentifier,
        action_event_id: uuid.UUID,
    ) -> None: ...


class AsyncSingleTableJsonEventPersister(_SingleTableJson):
    """The default :class:`AsyncEventPersister`: every model event, and a sentinel row for a
    unit of work that emits none, into one table with a JSON payload."""

    __slots__ = ("_event_repository",)

    def __init__(self, database_registry: AsyncDatabaseRegistry) -> None:
        super().__init__()
        self._event_repository = AsyncEventEntityRepository(database_registry)

    async def persist_action_events(
        self,
        namespace: str,
        action_name: str,
        started_date: Instant,
        completion_date: Instant,
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
            await self._event_repository.add_all_no_result(entities, shard)
