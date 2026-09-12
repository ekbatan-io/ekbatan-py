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
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
)

from ekbatan.schema import (
    json_column,
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
    # Two columns that exist only so the repository suite has something to catch with.
    # Java's `Dummy` fixture carries the same pair for the same reasons.
    #
    # `aliases` is a value that can only reach the database through a type mapper - a write
    # path binding from the value's runtime class rather than from the declared field type
    # would discard it, and nothing else here would notice.
    #
    # `reward_points` is nullable on purpose and nothing may default it: a batch write in
    # which every row leaves it null is what exposes an untyped NULL bind, which some drivers
    # refuse outright because they cannot infer the parameter's type.
    Column("aliases", json_column()),
    Column("reward_points", Integer),
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
