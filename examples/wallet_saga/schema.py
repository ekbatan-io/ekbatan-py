"""The tables.

One thing here is load-bearing rather than cosmetic: the unique constraint on
`(transfer_id, step)`. That constraint is what makes the saga safe to replay - see
`TransferStep` in `model.py`, and `CompleteTransferAction` in `actions.py`, which relies on it
rather than on a read-then-check.
"""

from __future__ import annotations

from sqlalchemy import (
    CHAR,
    BigInteger,
    Column,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
)

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

TRANSFER_STEPS = Table(
    "transfer_steps",
    metadata,
    Column("id", uuid_column(), primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("state", String(24), nullable=False),
    Column("transfer_id", uuid_column(), nullable=False),
    Column("step", String(24), nullable=False),
    # The whole idempotency mechanism, in one line. A replayed handler stages the same marker,
    # the insert violates this, and the unit of work - marker *and* the credit beside it - is
    # refused together. Doing it with a read-then-insert instead would lose the race it exists
    # to win: two deliveries can both read "not there" before either writes.
    UniqueConstraint("transfer_id", "step", name="uq_transfer_steps_transfer_step"),
)

EVENTS = outbox_table(metadata)
EVENT_NOTIFICATIONS = notifications_table(metadata)
SCHEDULED_TASKS = scheduled_tasks_table(metadata)

__all__ = [
    "EVENTS",
    "EVENT_NOTIFICATIONS",
    "SCHEDULED_TASKS",
    "TRANSFER_STEPS",
    "WALLETS",
    "metadata",
]
