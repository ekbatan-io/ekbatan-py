"""`scheduled_tasks`, as db-scheduler defines it on each database.

Not one schema. db-scheduler ships a different DDL file per dialect, and the differences are not
cosmetic - transcribing the Postgres one and hoping is how you get a table MySQL refuses to
create. This produces the right thing for each:

    task_name        text        (pg)   varchar(100)   (mysql, mariadb)
    task_instance    text        (pg)   varchar(100)
    picked_by        text        (pg)   varchar(50)
    execution_time   timestamp   (pg)   datetime(6)

The two that bite:

`task_name` and `task_instance` are the primary key, and **MySQL cannot index a TEXT column
without a key length** - `varchar(100)` is what db-scheduler uses, so it is what this uses.

`datetime(6)`, not `datetime`. MySQL's default DATETIME has second precision and silently
truncates. Two executions scheduled 300ms apart would land on the same instant, and the
ordering that decides which runs first would come down to whatever the storage engine felt
like.

Timestamps come in two shapes, and db-scheduler supports both:

    persist_timestamp_in_utc=True    zone-less columns everywhere. Portable, and what
                                     db-scheduler recommends - it warns on MySQL when you do
                                     not use it.
    persist_timestamp_in_utc=False   zoned columns where the dialect has them: `timestamptz` on
                                     Postgres. This is db-scheduler's *default*, so it is the
                                     shape of a table created by a Java instance that was not
                                     told otherwise.

The flag only picks the column type. Times are written exactly as given and read back as the
driver returns them, so the clock has to suit the columns: naive UTC (the default clock) for
zone-less ones, aware UTC for zoned ones. A naive value written into a `timestamptz` column is
interpreted in the session's timezone - keep the session in UTC.
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
    String,
    Table,
)
from sqlalchemy.dialects import mysql

__all__ = ["DEFAULT_TABLE_NAME", "scheduled_tasks_table"]

DEFAULT_TABLE_NAME = "scheduled_tasks"

#: db-scheduler's own column widths. A task name is an identifier, not prose.
TASK_NAME_LENGTH = 100
TASK_INSTANCE_LENGTH = 100
PICKED_BY_LENGTH = 50


def _timestamp(persist_timestamp_in_utc: bool) -> DateTime:
    """An instant, to the microsecond, on every dialect.

    `DATETIME(fsp=6)` on MySQL and MariaDB because their default DATETIME keeps whole seconds
    and drops the rest without saying so. MySQL has no zoned type at all, so it gets the
    zone-less column either way - which is precisely why db-scheduler warns there.
    """
    base = DateTime(timezone=not persist_timestamp_in_utc)
    return base.with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def scheduled_tasks_table(
    metadata: MetaData,
    name: str = DEFAULT_TABLE_NAME,
    *,
    persist_timestamp_in_utc: bool = True,
) -> Table:
    """The table. `name` is settable, as db-scheduler's `tableName` is.

    `persist_timestamp_in_utc` must match the repository reading it, and whatever created the
    table in the first place.
    """
    return Table(
        name,
        metadata,
        Column("task_name", String(TASK_NAME_LENGTH), nullable=False),
        Column("task_instance", String(TASK_INSTANCE_LENGTH), nullable=False),
        # Bytes, not JSON: the column holds whatever serializer the application chose, and
        # db-scheduler writes serialized bytes into a blob.
        Column("task_data", LargeBinary, nullable=True),
        Column("execution_time", _timestamp(persist_timestamp_in_utc), nullable=False),
        Column("picked", Boolean, nullable=False),
        Column("picked_by", String(PICKED_BY_LENGTH), nullable=True),
        Column("last_success", _timestamp(persist_timestamp_in_utc), nullable=True),
        Column("last_failure", _timestamp(persist_timestamp_in_utc), nullable=True),
        Column("consecutive_failures", Integer, nullable=True, default=0),
        Column("last_heartbeat", _timestamp(persist_timestamp_in_utc), nullable=True),
        Column("version", BigInteger, nullable=False),
        Column("priority", SmallInteger, nullable=True),
        PrimaryKeyConstraint("task_name", "task_instance"),
        Index(f"{name}_execution_time_idx", "execution_time"),
        Index(f"{name}_last_heartbeat_idx", "last_heartbeat"),
        # The index the priority poll reads. Without it, ordering by priority then time sorts
        # the whole table on every poll.
        Index(f"{name}_priority_execution_time_idx", "priority", "execution_time"),
        extend_existing=True,
    )
