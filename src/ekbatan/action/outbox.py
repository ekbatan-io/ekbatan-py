"""Where events go, and what a row of them looks like.

The outbox is a table this framework writes in the same transaction as the state change. It is
never something an application calls. If a port ever requires business code to hand an event to
something, it has become an outbox library and stopped being this.

The default store is one table with a JSON payload, which is the shape a Debezium connector
reads without help. An application needing another - encrypted payloads, a split layout, a
different retention - supplies its own :class:`EventPersister` and everything else, atomicity
included, is unchanged.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import Connection, Table, insert

from .._json import canonical
from .._tracing import span
from ..domain import ModelEvent
from ..errors import ValidationError
from ..sharding import ShardIdentifier, ordered_uuid

__all__ = ["EventPersister", "OutboxRecord", "SingleTableJsonEventPersister"]


@dataclass(frozen=True, kw_only=True, slots=True)
class OutboxRecord:
    """Everything one invocation produced, ready to be written."""

    namespace: str
    action_id: uuid.UUID
    action_name: str
    action_params: Any
    started_date: datetime
    completion_date: datetime
    events: Sequence[ModelEvent]
    shard: ShardIdentifier


@runtime_checkable
class EventPersister(Protocol):
    """Writes one invocation's events, on the connection the state change was written on.

    Being handed the connection is the whole contract: the seam is inside the transaction, not
    around it, so a replacement cannot accidentally break the guarantee it is extending.
    """

    def persist(self, conn: Connection, record: OutboxRecord) -> None: ...


class SingleTableJsonEventPersister:
    """One table, JSON payloads. The default."""

    __slots__ = ("_table", "_types_by_name")

    def __init__(self, table: Table) -> None:
        missing = [
            c
            for c in (
                "id",
                "namespace",
                "action_id",
                "action_name",
                "action_params",
                "started_date",
                "completion_date",
                "model_id",
                "model_type",
                "event_type",
                "payload",
                "event_date",
                "delivered",
            )
            if c not in table.c
        ]
        if missing:
            raise ValidationError(f"{table.name} is missing outbox column(s): {missing}")
        self._table = table
        self._types_by_name: dict[str, type] = {}

    def persist(self, conn: Connection, record: OutboxRecord) -> None:
        with span(
            "ekbatan.event.persist",
            {
                "ekbatan.action.name": record.action_name,
                "ekbatan.event.count": len(record.events),
            },
        ):
            self._write(conn, record)

    def _write(self, conn: Connection, record: OutboxRecord) -> None:
        """One row per event, keyed by a time-ordered id.

        The id is a version 7 UUID, and that is load-bearing rather than tidy. Every row of one
        invocation shares an ``event_date`` - it is the invocation's completion instant - so
        that column alone cannot order them, and a consumer reading a transfer back would be
        free to see the deposit before the withdrawal. Ordering by ``(event_date, id)`` settles
        it, because ``uuid7`` is monotonic within a process; ordering by a version 4 id settles
        nothing. It also stops the primary key scattering writes across the index, which is the
        argument :mod:`ekbatan.sharding` already makes for every other id in the framework.

        Java mints ``UUID.randomUUID()`` here, so a Java-written batch is unordered within an
        invocation on any database that does not happen to return rows in insertion order. The
        column type is identical either way, so the two remain readable to each other.
        """
        params = canonical(record.action_params)
        common = {
            "namespace": record.namespace,
            "action_id": record.action_id,
            "action_name": record.action_name,
            "action_params": params,
            "started_date": record.started_date,
            "completion_date": record.completion_date,
            "event_date": record.completion_date,
            "delivered": False,
        }

        if not record.events:
            # A unit of work that emitted nothing is still recorded - one row, every event
            # column null. The audit trail is of units of work, not of events; without this,
            # "nothing happened" and "nothing was recorded" are indistinguishable afterwards.
            rows = [
                {
                    **common,
                    "id": ordered_uuid(),
                    "model_id": None,
                    "model_type": None,
                    "event_type": None,
                    "payload": None,
                }
            ]
        else:
            rows = [
                {
                    **common,
                    "id": ordered_uuid(),
                    "model_id": event.model_id,
                    "model_type": event.model_name,
                    "event_type": self._event_type(event),
                    "payload": canonical(event.payload()),
                }
                for event in record.events
            ]

        conn.execute(insert(self._table), rows)

    def _event_type(self, event: ModelEvent) -> str:
        """The name a consumer routes and deserialises on - so two different events may not
        share one.

        Stored under one name they are indistinguishable downstream, and the failure lands on
        the consumer weeks later as a payload that will not parse. Better to refuse here.
        """
        name = event.event_type
        cls = type(event)
        previous = self._types_by_name.setdefault(name, cls)
        if previous is not cls:
            raise ValidationError(
                f"event type name collision: {name!r} is used by both "
                f"{previous.__module__}.{previous.__qualname__} and "
                f"{cls.__module__}.{cls.__qualname__}. A consumer routes on this name and "
                f"cannot tell them apart."
            )
        return name
