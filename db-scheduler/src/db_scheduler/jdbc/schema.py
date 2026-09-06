"""`scheduled_tasks`, exactly as db-scheduler defines it.

Not approximately. A Java service and a Python service coordinating through one table means the
columns, the types and the indexes have to be the same, so this is a transcription of the
`postgresql_tables.sql` shipped with db-scheduler rather than a design.

The two that carry the whole scheme:

    version    the optimistic lock. Every state change - pick, reschedule, heartbeat - carries
               `WHERE version = ?`, which is what makes exactly one instance win a slot.
    picked +   together they say "somebody is on this, and was alive at that moment", which is
    last_heartbeat  what lets another instance take over work whose owner died.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    SmallInteger,
    Table,
    Text,
)

__all__ = ["DEFAULT_TABLE_NAME", "scheduled_tasks_table"]

DEFAULT_TABLE_NAME = "scheduled_tasks"


def scheduled_tasks_table(metadata: MetaData, name: str = DEFAULT_TABLE_NAME) -> Table:
    """The table. `name` is settable, as db-scheduler's `tableName` is."""
    return Table(
        name,
        metadata,
        Column("task_name", Text, nullable=False),
        Column("task_instance", Text, nullable=False),
        # Bytes, not JSON: the column has to hold whatever serializer the application chose,
        # and db-scheduler's default writes Java-serialized or JSON bytes into it.
        Column("task_data", LargeBinary, nullable=True),
        # Zone-less UTC. Postgres' own schema says `timestamp with time zone`, but the value
        # stored is the instant either way, and a naive UTC column is the one shape every
        # supported dialect agrees on - MySQL has no zoned timestamp at all.
        Column("execution_time", DateTime, nullable=False),
        Column("picked", Boolean, nullable=False),
        Column("picked_by", Text, nullable=True),
        Column("last_success", DateTime, nullable=True),
        Column("last_failure", DateTime, nullable=True),
        Column("consecutive_failures", Integer, nullable=True, default=0),
        Column("last_heartbeat", DateTime, nullable=True),
        Column("version", BigInteger, nullable=False),
        Column("priority", SmallInteger, nullable=True),
        PrimaryKeyConstraint("task_name", "task_instance"),
        Index(f"{name}_execution_time_idx", "execution_time"),
        Index(f"{name}_last_heartbeat_idx", "last_heartbeat"),
        # The index the priority poll reads. Without it, ordering by priority then time is a
        # sort of the whole table on every poll.
        Index(f"{name}_priority_execution_time_idx", "priority", "execution_time"),
        extend_existing=True,
    )
