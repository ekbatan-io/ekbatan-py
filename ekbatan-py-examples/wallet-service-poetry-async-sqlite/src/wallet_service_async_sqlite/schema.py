"""The tables.

Written by hand here so the whole example reads in one place. In a real project this file is
generated - `python -m ekbatan.schemagen` starts a container, runs the migrations into it and
reflects the result - so the Python and the database cannot drift.
`fastapi_wallet_rest_pg` is the example that does it that way.

The framework's own tables are built rather than transcribed: their shape is a contract that a
Debezium connector or another service binds to, so there is one definition to be right about.
"""

from __future__ import annotations

from sqlalchemy import CHAR, BigInteger, Column, MetaData, Numeric, String, Table, Text

from ekbatan.schema import (
    notifications_table,
    outbox_table,
    scheduled_tasks_table,
    timestamp_column,
    uuid_column,
)

metadata = MetaData()

WALLETS = Table(
    "wallets",
    metadata,
    Column("id", uuid_column(), primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("state", String(24), nullable=False),
    Column("owner_id", uuid_column(), nullable=False),
    Column("currency", CHAR(3), nullable=False),
    Column("balance", Numeric(19, 4), nullable=False),
    Column("created_date", timestamp_column(), nullable=False),
    Column("updated_date", timestamp_column(), nullable=False),
)

ALERTS = Table(
    "alerts",
    metadata,
    Column("id", uuid_column(), primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("state", String(24), nullable=False),
    Column("wallet_id", uuid_column(), nullable=False),
    Column("recipient", String(255), nullable=False),
    Column("message", Text, nullable=False),
)

#: The outbox, the local handler's queue, and the scheduler's table.
EVENTS = outbox_table(metadata)
EVENT_NOTIFICATIONS = notifications_table(metadata)
SCHEDULED_TASKS = scheduled_tasks_table(metadata)

__all__ = ["ALERTS", "EVENTS", "EVENT_NOTIFICATIONS", "SCHEDULED_TASKS", "WALLETS", "metadata"]
