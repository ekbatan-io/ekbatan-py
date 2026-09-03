"""The wallet application's tables.

Written by hand here so the example is readable in one place. In a real project this file is
generated from the database - ``sqlacodegen --generator declarative`` - and regenerated when a
migration lands, so the Python and the schema cannot drift.
"""

from __future__ import annotations

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    Uuid,
)

metadata = MetaData()

WALLETS = Table(
    "wallets",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("state", String(24), nullable=False),
    Column("owner_id", Uuid, nullable=False),
    Column("currency", CHAR(3), nullable=False),
    Column("balance", Numeric(19, 4), nullable=False),
    Column("created_date", DateTime, nullable=False),
    Column("updated_date", DateTime, nullable=False),
)

NOTIFICATIONS = Table(
    "notifications",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("state", String(24), nullable=False),
    Column("created_date", DateTime, nullable=False),
    Column("updated_date", DateTime, nullable=False),
    Column("wallet_id", Uuid, nullable=False),
    Column("kind", String(48), nullable=False),
    Column("recipient", String(255), nullable=False),
    Column("message", Text),
)

#: The outbox. Its shape is a contract, not an implementation detail - a Debezium connector,
#: a CDC pipeline, or another language's Ekbatan service binds to these column names.
EVENTS = Table(
    "events",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("namespace", String(255), nullable=False),
    Column("action_id", Uuid, nullable=False),
    Column("action_name", String(255), nullable=False),
    Column("action_params", Text, nullable=False),
    Column("started_date", DateTime, nullable=False),
    Column("completion_date", DateTime, nullable=False),
    Column("model_id", String(255)),
    Column("model_type", String(255)),
    Column("event_type", String(255)),
    Column("payload", Text),
    Column("event_date", DateTime, nullable=False),
    Column("delivered", Boolean, nullable=False),
    schema=None,
)
