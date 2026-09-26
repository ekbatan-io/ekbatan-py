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
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, Self, runtime_checkable

from sqlalchemy import Connection, Table, func, insert, select

from .._builder import FieldBuilder
from .._json import canonical
from .._tracing import span
from ..domain import ModelEvent
from ..errors import ValidationError
from ..persistence import DatabaseRegistry
from ..sharding import ShardIdentifier

__all__ = [
    "EventEntity",
    "EventEntityBuilder",
    "EventPersister",
    "SingleTableJsonEventPersister",
]

#: The columns an outbox table must have - one per :class:`EventEntity` field.
OUTBOX_COLUMNS = (
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


@dataclass(frozen=True, kw_only=True, slots=True, eq=False)
class EventEntity:
    """One row of ``eventlog.events`` - Java's ``EventEntity``.

    Written by :class:`SingleTableJsonEventPersister`, read back by the fan-out job. A sentinel
    row, written for a unit of work that emitted nothing, has ``model_id``, ``model_type``,
    ``event_type`` and ``payload`` all ``None``.

    Equal by ``id`` alone, as Java's is.
    """

    id: uuid.UUID
    namespace: str
    action_id: uuid.UUID
    action_name: str
    action_params: Mapping[str, Any]
    started_date: datetime
    completion_date: datetime
    model_id: str | None = None
    model_type: str | None = None
    event_type: str | None = None
    payload: Mapping[str, Any] | None = None
    event_date: datetime
    delivered: bool = False

    def __post_init__(self) -> None:
        for name in (
            "id",
            "action_id",
            "action_name",
            "action_params",
            "started_date",
            "completion_date",
            "event_date",
        ):
            if getattr(self, name) is None:
                raise ValidationError(f"{name} cannot be None")
        # Not blank, and deliberately not the identifier rule the executor enforces. This type
        # is also how rows are read back out, so a stricter rule would make rows written under
        # an older, looser rule unreadable - and wedge the fan-out job on that row for good.
        if not isinstance(self.namespace, str) or not self.namespace.strip():
            raise ValidationError("namespace cannot be blank")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EventEntity):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)

    @classmethod
    def builder(cls) -> EventEntityBuilder:
        return EventEntityBuilder()

    @classmethod
    def create_event_entity(
        cls,
        *,
        id: uuid.UUID,
        namespace: str,
        action_id: uuid.UUID,
        action_name: str,
        action_params: Mapping[str, Any],
        started_date: datetime,
        completion_date: datetime,
        model_id: str | None,
        model_type: str | None,
        event_type: str | None,
        payload: Mapping[str, Any] | None,
        event_date: datetime,
    ) -> EventEntityBuilder:
        """Java's ``createEventEntity(...)``: every field but ``delivered``, as a builder."""
        return (
            cls.builder()
            .id(id)
            .namespace(namespace)
            .action_id(action_id)
            .action_name(action_name)
            .action_params(action_params)
            .started_date(started_date)
            .completion_date(completion_date)
            .model_id(model_id)
            .model_type(model_type)
            .event_type(event_type)
            .payload(payload)
            .event_date(event_date)
        )

    @classmethod
    def from_row(cls, row: Any) -> EventEntity:
        """One read back from the table."""
        return cls(**{name: getattr(row, name) for name in OUTBOX_COLUMNS})

    def to_row(self) -> dict[str, Any]:
        """The values to insert, keyed by column."""
        return {name: getattr(self, name) for name in OUTBOX_COLUMNS}


class EventEntityBuilder(FieldBuilder[EventEntity]):
    """Fluent builder for :class:`EventEntity`. Obtain via :meth:`EventEntity.builder`."""

    __slots__ = ()
    _target = EventEntity
    _required = (
        "id",
        "namespace",
        "action_id",
        "action_name",
        "action_params",
        "started_date",
        "completion_date",
        "event_date",
    )

    def id(self, id: uuid.UUID) -> Self:
        return self._set("id", id)

    def namespace(self, namespace: str) -> Self:
        return self._set("namespace", namespace)

    def action_id(self, action_id: uuid.UUID) -> Self:
        return self._set("action_id", action_id)

    def action_name(self, action_name: str) -> Self:
        return self._set("action_name", action_name)

    def action_params(self, action_params: Mapping[str, Any]) -> Self:
        return self._set("action_params", action_params)

    def started_date(self, started_date: datetime) -> Self:
        return self._set("started_date", started_date)

    def completion_date(self, completion_date: datetime) -> Self:
        return self._set("completion_date", completion_date)

    def model_id(self, model_id: str | None) -> Self:
        return self._set("model_id", model_id)

    def model_type(self, model_type: str | None) -> Self:
        return self._set("model_type", model_type)

    def event_type(self, event_type: str | None) -> Self:
        return self._set("event_type", event_type)

    def payload(self, payload: Mapping[str, Any] | None) -> Self:
        return self._set("payload", payload)

    def event_date(self, event_date: datetime) -> Self:
        return self._set("event_date", event_date)

    def delivered(self, delivered: bool) -> Self:
        return self._set("delivered", delivered)


def _require_outbox_columns(table: Table) -> Table:
    missing = [c for c in OUTBOX_COLUMNS if c not in table.c]
    if missing:
        raise ValidationError(f"{table.name} is missing outbox column(s): {missing}")
    return table


class EventEntityRepository:
    """The outbox table's reads and writes for the persister - Java's package-private
    ``EventEntityRepository`` in the single-table-JSON package.

    Takes the table, where Java's names ``eventlog.events`` itself: the schema is not the same
    on every database here - SQLite has none - so the application declares the table once and
    everything that touches it is handed the same one.
    """

    __slots__ = ("_registry", "_table")

    def __init__(self, database_registry: DatabaseRegistry, table: Table) -> None:
        if database_registry is None:
            raise ValidationError("database_registry cannot be None")
        self._registry = database_registry
        self._table = _require_outbox_columns(table)

    @contextmanager
    def _tx_db_else_db(self, shard: ShardIdentifier) -> Iterator[Connection]:
        """The transaction open on ``shard``, or a connection that commits each statement."""
        with self._registry.manager(shard).connection() as conn:
            yield conn

    def count(self, shard: ShardIdentifier) -> int:
        with self._tx_db_else_db(shard) as conn:
            return int(conn.execute(select(func.count()).select_from(self._table)).scalar_one())

    def find_all(self, shard: ShardIdentifier) -> list[EventEntity]:
        with self._tx_db_else_db(shard) as conn:
            return [EventEntity.from_row(r) for r in conn.execute(select(self._table))]

    def find_by_action_id(self, action_id: uuid.UUID, shard: ShardIdentifier) -> list[EventEntity]:
        table = self._table
        with self._tx_db_else_db(shard) as conn:
            rows = conn.execute(select(table).where(table.c.action_id == action_id))
            return [EventEntity.from_row(r) for r in rows]

    def add_all_no_result(self, entities: Collection[EventEntity], shard: ShardIdentifier) -> None:
        if not entities:
            return
        with self._tx_db_else_db(shard) as conn:
            conn.execute(insert(self._table), [e.to_row() for e in entities])


@runtime_checkable
class EventPersister(Protocol):
    """Writes one invocation's events - Java's ``EventPersister``.

    Called inside the transaction the state change was written in, and the write joins it: the
    shard's open transaction is where :class:`~ekbatan.persistence.TransactionManager` finds it,
    so a replacement writing through the registry cannot break the guarantee it is extending.
    """

    def persist_action_events(
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


class SingleTableJsonEventPersister:
    """Default :class:`EventPersister`: every model event, and a sentinel row for a unit of work
    that emits none, into one table with a JSON payload - Java's
    ``SingleTableJsonEventPersister``.

    Java takes the registry and a Jackson mapper; here the registry and the table, for the
    reason :class:`EventEntityRepository` takes one.
    """

    __slots__ = ("_event_repository", "_types_by_name")

    def __init__(self, database_registry: DatabaseRegistry, table: Table) -> None:
        self._event_repository = EventEntityRepository(database_registry, table)
        self._types_by_name: dict[str, type] = {}

    def persist_action_events(
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
        """One row per event.

        The rows of one invocation all carry that invocation's completion instant in
        ``event_date``, so nothing here distinguishes their order and the key is random - Java's
        ``UUID.randomUUID()``, and the same choice for the same reason. Ordering *within* an
        invocation is not something the outbox promises in either port: there is no ordinal
        column to promise it with, and a table both ports write to cannot be given the guarantee
        by only one of them. A consumer that needs the order needs it added to the schema.
        """
        with span(
            "ekbatan.event.persist",
            {"ekbatan.action.name": action_name, "ekbatan.event.count": len(model_events)},
        ):
            params = _as_json_object(
                canonical(action_params),
                "action_params must be a JSON object; give the action a params dataclass "
                "rather than a bare scalar or list - `Action[DepositParams, Wallet]`, not "
                "`Action[str, Wallet]`. An action with no parameters takes None",
            )
            if not model_events:
                # A unit of work that emitted nothing is still recorded - one row, every event
                # column null. The audit trail is of units of work, not of events; without
                # this, "nothing happened" and "nothing was recorded" are indistinguishable.
                entities = [
                    EventEntity.create_event_entity(
                        id=uuid.uuid4(),
                        namespace=namespace,
                        action_id=action_event_id,
                        action_name=action_name,
                        action_params=params,
                        started_date=started_date,
                        completion_date=completion_date,
                        model_id=None,
                        model_type=None,
                        event_type=None,
                        payload=None,
                        event_date=completion_date,
                    ).build()
                ]
            else:
                entities = [
                    EventEntity.create_event_entity(
                        id=uuid.uuid4(),
                        namespace=namespace,
                        action_id=action_event_id,
                        action_name=action_name,
                        action_params=params,
                        started_date=started_date,
                        completion_date=completion_date,
                        model_id=event.model_id,
                        model_type=event.model_name,
                        event_type=self._event_type(event),
                        payload=_as_json_object(
                            canonical(event.payload()),
                            f"{type(event).__name__}.payload() must give a JSON object; check "
                            f"for an override returning a scalar or a list",
                        ),
                        event_date=completion_date,
                    ).build()
                    for event in model_events
                ]
            self._event_repository.add_all_no_result(entities, shard)

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


def _as_json_object(value: Any, complaint: str) -> Mapping[str, Any]:
    """Refuse anything the column can hold but a consumer cannot read.

    A JSON column accepts a bare scalar or a list quite happily - `"deposit"` and `[1, 2]` are
    both valid JSON - so nothing fails at write time. What fails is the consumer:
    `ActionEvent.action_params` is typed as a mapping in both ports, and a reader doing
    `params["amount"]` gets a `TypeError` from a row written days earlier by someone else.

    `None` is normalised rather than refused, which is the one place this parts company with
    Java. There an action always takes a params object, so a null reaching the check is a
    mistake; here `execute(SomeAction(), None)` is simply how a parameterless action is invoked.
    The rule that makes both coherent: `None` means "no parameters" and has an unambiguous
    empty-object form, while `"MXN"` means "one unnamed parameter" and does not - storing it
    would mean inventing a key that no reader on either side knows to look for.
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationError(f"{complaint}; got {type(value).__name__}")
    return value
