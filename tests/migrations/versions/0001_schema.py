"""The suite's schema, applied the way a deployment applies one.

Java's integration tests run Flyway over `V0001__create__events.sql`; this is that. It matters
for one reason beyond fidelity: **indexes are not in `ekbatan.schema`** and cannot be, because a
single declaration cannot be `WHERE delivered = FALSE` on PostgreSQL and a plain composite on
MySQL. They live wherever the table is created - here, and in each example's migrations - which
is where Java puts them too.

The table *shapes* still come from the metadata. Those are the contract other ports and CDC
connectors bind to, and transcribing them into DDL by hand is how the two drift.

Revision ID: 0001
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

#: What the fan-out job asks for: undelivered rows of a type somebody subscribes to. On
#: PostgreSQL the predicate goes in the index, so it holds the backlog rather than the archive -
#: measured at 56 kB against 3,528 kB on 500,000 rows with a 1% backlog. MySQL and MariaDB have
#: no partial indexes, so the predicate column goes into the index instead.
_UNDELIVERED = {
    "postgres": "CREATE INDEX ix_events_undelivered ON events (event_type, event_date)"
    " WHERE delivered = FALSE",
    "other": "CREATE INDEX ix_events_undelivered ON events (delivered, event_type, event_date)",
}

#: What dispatch asks for: notifications still to deliver. SUCCEEDED and EXPIRED rows pile up
#: and are never selected, so on PostgreSQL they stay out of the index.
_DUE = {
    "postgres": "CREATE INDEX ix_event_notifications_due ON event_notifications (next_retry_at)"
    " WHERE state IN ('PENDING', 'FAILED')",
    "other": "CREATE INDEX ix_event_notifications_due ON event_notifications"
    " (state, next_retry_at)",
}


def upgrade() -> None:
    from fixtures.schema import metadata

    bind = op.get_bind()
    metadata.create_all(bind)

    dialect = "postgres" if bind.dialect.name == "postgresql" else "other"
    op.execute("CREATE INDEX ix_events_action_id ON events (action_id)")
    op.execute(_UNDELIVERED[dialect])
    op.execute(_DUE[dialect])


def downgrade() -> None:
    from fixtures.schema import metadata

    metadata.drop_all(op.get_bind())
