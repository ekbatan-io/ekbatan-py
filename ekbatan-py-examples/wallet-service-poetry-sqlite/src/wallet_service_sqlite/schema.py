"""The tables.

Written by hand here so the whole example reads in one place. In a real project this file is
generated - `python -m ekbatan.schemagen` starts a container, runs the migrations into it and
reflects the result - so the Python and the database cannot drift.
`fastapi_wallet_rest_pg` is the example that does it that way.

Each table has its record beside it - `WALLETS` and `WalletsRecord` - written by hand here as
schemagen writes it: a `TypedDict` with a key per column, typed as the column reads, `| None`
where the column may be NULL. It is what a repository's `to_record` returns and `from_record`
takes, so mypy checks the mapping against the table. To SQLAlchemy it is a plain `dict`.

The framework's own tables are built rather than transcribed: their shape is a contract that a
Debezium connector or another service binds to, so there is one definition to be right about.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TypedDict
from uuid import UUID

from sqlalchemy import CHAR, BigInteger, Column, MetaData, Numeric, String, Table, Text

from ekbatan.schema import (
    notifications_table,
    outbox_table,
    scheduled_tasks_table,
    timestamp_column,
    uuid_column,
)
from ekbatan.time import Instant

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


class WalletsRecord(TypedDict):
    """A row of `wallets`: what `to_record` writes and `from_record` reads."""

    id: UUID
    version: int
    state: str
    owner_id: UUID
    currency: str
    balance: Decimal
    created_date: Instant
    updated_date: Instant


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


class AlertsRecord(TypedDict):
    """A row of `alerts`."""

    id: UUID
    version: int
    state: str
    wallet_id: UUID
    recipient: str
    message: str


#: The outbox, the local handler's queue, and the scheduler's table.
EVENTS = outbox_table(metadata)
EVENT_NOTIFICATIONS = notifications_table(metadata)
SCHEDULED_TASKS = scheduled_tasks_table(metadata)

__all__ = [
    "ALERTS",
    "EVENTS",
    "EVENT_NOTIFICATIONS",
    "SCHEDULED_TASKS",
    "WALLETS",
    "AlertsRecord",
    "WalletsRecord",
    "metadata",
]
