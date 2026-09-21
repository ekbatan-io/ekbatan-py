"""The indexes a deployment gets, and where they come from.

They are **not** in `ekbatan.schema`, and cannot be: one declaration cannot be
`WHERE delivered = FALSE` on PostgreSQL and a plain composite on MySQL. Java has the same split
and resolves it the same way - its framework declares tables with `DSL.table(DSL.name(...))`
and ships no DDL at all, while `V0001__eventlog.sql` in each application carries the indexes.

So these assert two things: that the framework declares none, and that the migration the suite
runs produces the right one for the dialect it is on.
"""

from __future__ import annotations

from sqlalchemy import MetaData, inspect

from ekbatan.schema import notifications_table, outbox_table, scheduled_tasks_table


def test_the_framework_declares_no_indexes_of_its_own():
    """Physical design belongs to whoever creates the table, which is not the framework."""
    metadata = MetaData()
    events = outbox_table(metadata)
    notifications = notifications_table(metadata)

    assert events.indexes == set(), "the outbox declared an index"
    assert notifications.indexes == set(), "the notifications table declared an index"


def test_the_framework_still_declares_the_shape():
    """The half that *is* a contract: a CDC connector binds to these column names."""
    metadata = MetaData()
    events = outbox_table(metadata)

    assert {"id", "action_id", "event_type", "event_date", "delivered"} <= set(
        events.columns.keys()
    )
    assert scheduled_tasks_table(MetaData()).name == "scheduled_tasks"


def test_the_migration_builds_the_index_this_dialect_can_have(registry):
    """PostgreSQL gets the predicate in the index; the others get it as a column.

    Measured on 500,000 rows with a 1% backlog: 56 kB partial against 3,528 kB composite.
    """
    engine = registry.default.primary
    names = {i["name"] for i in inspect(engine).get_indexes("events")}

    assert "ix_events_undelivered" in names, f"the migration built none: {sorted(names)}"
    assert "ix_events_action_id" in names

    if engine.dialect.name == "postgresql":
        with engine.connect() as conn:
            ddl = conn.exec_driver_sql(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_events_undelivered'"
            ).scalar_one()
        assert "WHERE (delivered = false)" in ddl, f"not partial: {ddl}"


def test_the_notifications_index_skips_what_dispatch_never_asks_for(registry):
    """SUCCEEDED and EXPIRED rows pile up for the retention window and are never selected."""
    engine = registry.default.primary
    names = {i["name"] for i in inspect(engine).get_indexes("event_notifications")}

    assert "ix_event_notifications_due" in names

    if engine.dialect.name == "postgresql":
        with engine.connect() as conn:
            ddl = conn.exec_driver_sql(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_event_notifications_due'"
            ).scalar_one()
        assert "PENDING" in ddl and "FAILED" in ddl, f"not partial: {ddl}"
