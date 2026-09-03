"""The framework's own tables, as SQLAlchemy metadata.

Both shapes are contracts, not implementation details: a Debezium connector, a CDC pipeline, or
another language's Ekbatan service binds to these column names and types. Building them here
rather than asking every application to transcribe DDL means there is one definition to be
right about, and migrations can be generated from it.

Two rules that outlive any one dialect:

- **Timestamps carry no zone.** A ``TIMESTAMP WITH TIME ZONE`` shifts values by the session's
  offset and two readers of one row then disagree about when the thing happened.
- **``model_id`` is a string.** Not every model is keyed by a UUID; typing it as one would
  exclude every aggregate keyed by an account number or an ISIN.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Uuid,
)

__all__ = [
    "json_column",
    "notifications_table",
    "outbox_table",
    "scheduled_tasks_table",
    "timestamp_column",
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


def json_column() -> JSON:
    """The type a payload column has: ``jsonb`` on Postgres, ``json`` on MySQL and MariaDB.

    Not text holding JSON. The column type is part of what a consumer binds to, and jsonb is
    queryable where a text column is a string somebody has to parse first. The engine is given
    this library's canonical serialiser, so what lands in it is byte-identical to what the
    other ports write.
    """
    from sqlalchemy.dialects.postgresql import JSONB

    return JSON().with_variant(JSONB(), "postgresql")


def outbox_table(metadata: MetaData, *, name: str = "events", schema: str | None = None) -> Table:
    """``eventlog.events``: one row per emitted event, plus one per unit of work that emitted
    none."""
    return Table(
        name,
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("namespace", String(255), nullable=False),
        Column("action_id", Uuid, nullable=False),
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
        Index(f"ix_{name}_action_id", "action_id"),
        Index(f"ix_{name}_undelivered", "delivered", "event_type", "event_date"),
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
        Column("id", Uuid, primary_key=True),
        Column("event_id", Uuid, nullable=False),
        Column("handler_name", String(255), nullable=False),
        Column("namespace", String(255), nullable=False),
        Column("action_id", Uuid, nullable=False),
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
        Index(f"ix_{name}_due", "state", "next_retry_at"),
        schema=schema,
    )


def scheduled_tasks_table(
    metadata: MetaData, *, name: str = "scheduled_tasks", schema: str | None = None
) -> Table:
    """``scheduled_tasks``: one row per job slot, and the row every instance races for.

    The shape is db-scheduler's, deliberately - a Java service and a Python service can then
    coordinate through one table instead of each running the nightly report.

    ``version`` is what makes the claim atomic: an instance updates the row it read, and the
    one whose update matches a row wins the slot. Everything else here is bookkeeping for
    whoever has to work out later why a job stopped running.
    """
    return Table(
        name,
        metadata,
        Column("task_name", String(255), primary_key=True),
        Column("task_instance", String(255), primary_key=True),
        Column("task_data", LargeBinary),
        Column("execution_time", timestamp_column(), nullable=False),
        Column("picked", Boolean, nullable=False),
        Column("picked_by", String(255)),
        Column("last_success", timestamp_column()),
        Column("last_failure", timestamp_column()),
        Column("consecutive_failures", Integer),
        Column("last_heartbeat", timestamp_column()),
        Column("version", BigInteger, nullable=False),
        Index(f"ix_{name}_execution_time", "execution_time"),
        Index(f"ix_{name}_last_heartbeat", "last_heartbeat"),
        schema=schema,
    )
