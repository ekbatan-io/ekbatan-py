"""The framework's own tables, as SQLAlchemy metadata - **shape only, not physical design.**

Both shapes are contracts, not implementation details: a Debezium connector, a CDC pipeline, or
another language's Ekbatan service binds to these column names and types. Declaring them here
rather than asking every application to transcribe them means there is one definition to be
right about, and migrations can be generated from it.

This is what Java's framework does, and it declares no more than this. Its repositories reach
for the same tables through `DSL.table(DSL.name("eventlog", "events"))` and every column through
`DSL.field(...)` - hand-written references, so a query can be built. There is no `CREATE TABLE`
and no `CREATE INDEX` anywhere in `ekbatan-core`, `ekbatan-events` or `ekbatan-distributed-jobs`.

**So there are no indexes here either, as of `EKB-0215`.** There used to be three, and they were
the reason PostgreSQL went without the partial indexes Java gives it: one declaration cannot be
`WHERE delivered = FALSE` on PostgreSQL and a plain composite on MySQL, so it was the composite
everywhere - measured at 3,528 kB against Java's 56 kB on 500,000 rows with a 1% backlog.
Indexes belong to whoever creates the table, which is the application's migrations, where Java
puts them too: `V0001__eventlog.sql`.

Two rules that outlive any one dialect:

- **Timestamps carry no zone.** A ``TIMESTAMP WITH TIME ZONE`` shifts values by the session's
  offset and two readers of one row then disagree about when the thing happened.
- **``model_id`` is a string.** Not every model is keyed by a UUID; typing it as one would
  exclude every aggregate keyed by an account number or an ISIN.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    CHAR,
    JSON,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.types import TypeDecorator

__all__ = [
    "json_column",
    "notifications_table",
    "outbox_table",
    "scheduled_tasks_table",
    "timestamp_column",
    "uuid_column",
]


def timestamp_column() -> DateTime:
    """The type every timestamp column has: UTC, no zone, microsecond precision.

    The zone is left off deliberately - ``TIMESTAMP WITH TIME ZONE`` shifts values by the
    session's offset, and two readers of one row then disagree about when the thing happened.

    The precision is not decoration. MySQL's bare ``DATETIME`` stores whole seconds and
    silently drops the rest, so a value written at 10:00:00.123456 reads back as 10:00:00 and
    every ordering derived from it is wrong in a way nothing reports.
    """
    from sqlalchemy.dialects import mysql

    return DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


class _CanonicalUuid(TypeDecorator[uuid.UUID]):
    """A uuid stored as its canonical 36-character text, for dialects with no uuid type.

    SQLAlchemy's default for those is ``CHAR(32)`` - the hex digits with the hyphens stripped.
    That round-trips perfectly well within one application and is unreadable to everything
    else: a Java service writing ``d5143465-787a-...`` and a Python service writing
    ``d5143465787a...`` cannot join on ``model_id``, and neither notices, because both queries
    succeed and return nothing.
    """

    impl = CHAR(36)
    cache_ok = True

    def process_bind_param(self, value: object, dialect: object) -> str | None:
        return None if value is None else str(value)

    def process_result_value(self, value: object, dialect: object) -> uuid.UUID | None:
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def uuid_column() -> Uuid[uuid.UUID]:
    """The type a uuid column has: native where the dialect has one, canonical text where not."""
    return Uuid().with_variant(_CanonicalUuid(), "mysql", "mariadb")


def json_column() -> JSON:
    """The type a payload column has: ``jsonb`` on Postgres, ``json`` on MySQL and MariaDB.

    Not text holding JSON. The column type is part of what a consumer binds to, and jsonb is
    queryable where a text column is a string somebody has to parse first. The engine is given
    this library's serialiser, so what lands in it is the text Java's Ekbatan writes.
    """
    from sqlalchemy.dialects.postgresql import JSONB

    return JSON().with_variant(JSONB(), "postgresql")


def outbox_table(metadata: MetaData, *, name: str = "events", schema: str | None = None) -> Table:
    """``eventlog.events``: one row per emitted event, plus one per unit of work that emitted
    none."""
    return Table(
        name,
        metadata,
        Column("id", uuid_column(), primary_key=True),
        Column("namespace", String(255), nullable=False),
        Column("action_id", uuid_column(), nullable=False),
        Column("action_name", String(255), nullable=False),
        # Not null: every row carries the full input of the unit of work that produced it, so
        # one row is a complete audit record without a join - and so a consumer can read a
        # value the domain event deliberately does not carry.
        Column("action_params", json_column(), nullable=False),
        Column("started_date", timestamp_column(), nullable=False),
        Column("completion_date", timestamp_column(), nullable=False),
        Column("model_id", String(255)),
        Column("model_type", String(255)),
        Column("event_type", String(255)),
        Column("payload", json_column()),
        Column("event_date", timestamp_column(), nullable=False),
        # The outbox's only mutable column, and it belongs to the consumer. A producer that
        # flips it loses the event for every consumer that had not yet read it.
        Column("delivered", Boolean, nullable=False),
        schema=schema,
    )


def notifications_table(
    metadata: MetaData, *, name: str = "event_notifications", schema: str | None = None
) -> Table:
    """``eventlog.event_notifications``: one row per (event, subscriber).

    The event's context is denormalised onto it so that dispatch never has to read the events
    table again - and so a handler's envelope is one row.

    Uniqueness on ``(event_id, handler_name)`` is what makes fan-out safe to re-run. Fan-out is
    not transactional with dispatch, so it will pass over rows it has already seen; the
    constraint is what stops that from doubling the work, and it belongs in the schema rather
    than in a fan-out that tries to remember.
    """
    return Table(
        name,
        metadata,
        Column("id", uuid_column(), primary_key=True),
        Column("event_id", uuid_column(), nullable=False),
        Column("handler_name", String(255), nullable=False),
        Column("namespace", String(255), nullable=False),
        Column("action_id", uuid_column(), nullable=False),
        Column("action_name", String(255), nullable=False),
        Column("action_params", json_column(), nullable=False),
        Column("started_date", timestamp_column(), nullable=False),
        Column("completion_date", timestamp_column(), nullable=False),
        Column("model_id", String(255)),
        Column("model_type", String(255)),
        Column("event_type", String(255), nullable=False),
        Column("payload", json_column()),
        Column("event_date", timestamp_column(), nullable=False),
        Column("state", String(24), nullable=False),
        Column("attempts", Integer, nullable=False, default=0),
        Column("next_retry_at", timestamp_column(), nullable=False),
        Column("created_date", timestamp_column(), nullable=False),
        Column("updated_date", timestamp_column(), nullable=False),
        UniqueConstraint("event_id", "handler_name", name=f"uq_{name}_event_handler"),
        schema=schema,
    )


def scheduled_tasks_table(
    metadata: MetaData, *, name: str = "scheduled_tasks", schema: str | None = None
) -> Table:
    """``scheduled_tasks``: db-scheduler's table, defined by db-scheduler.

    This used to be a second, hand-written definition of the same table, and the two had already
    drifted: it declared ``varchar(255)`` keys where db-scheduler uses 100, and it had no
    ``priority`` column at all - which db-scheduler writes on every insert. A row could not have
    been written into it.

    So it delegates. One definition, in the module that owns the table, and a Java service and a
    Python service still coordinate through one shape.

    ``schema`` is applied afterwards because db-scheduler's builder does not take one: Ekbatan
    supports schema-qualified tables and db-scheduler has no need to.
    """
    from db_scheduler.jdbc import scheduled_tasks_table as db_scheduler_table

    table = db_scheduler_table(metadata, name)
    if schema is not None:
        table.schema = schema
    return table
