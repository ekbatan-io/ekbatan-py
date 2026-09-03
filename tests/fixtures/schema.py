"""The wallet application's tables.

Written by hand here so the example is readable in one place. In a real project this file is
generated from the database - ``sqlacodegen --generator declarative`` - and regenerated when a
migration lands, so the Python and the schema cannot drift.
"""

from __future__ import annotations

from sqlalchemy import (
    CHAR,
    BigInteger,
    Column,
    DateTime,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
)

from ekbatan.schema import (
    notifications_table,
    outbox_table,
    scheduled_tasks_table,
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
    Column("created_date", DateTime, nullable=False),
    Column("updated_date", DateTime, nullable=False),
)

NOTIFICATIONS = Table(
    "notifications",
    metadata,
    Column("id", uuid_column(), primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("state", String(24), nullable=False),
    Column("wallet_id", uuid_column(), nullable=False),
    Column("kind", String(48), nullable=False),
    Column("recipient", String(255), nullable=False),
    Column("message", Text),
)

#: The framework's own tables. Their shape is a contract - a Debezium connector, a CDC
#: pipeline, or another language's Ekbatan service binds to these column names - so they are
#: built rather than transcribed.
EVENTS = outbox_table(metadata)
EVENT_NOTIFICATIONS = notifications_table(metadata)
SCHEDULED_TASKS = scheduled_tasks_table(metadata)
