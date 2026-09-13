"""the application's tables, and the framework's

Revision ID: 0001
Revises:

Generated once with `ekbatan.migrations.emit_ddl(metadata, "postgresql")` and pasted, rather than
built from `schema.py` at migration time - a revision has to mean the same thing forever, and one
that read today's `schema.py` would change meaning every time a column was added.

One statement per `op.execute`: PyMySQL executes a single statement per call and rejects a
script, where psycopg accepts one.
"""

from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE event_notifications (
            id UUID NOT NULL,
            event_id UUID NOT NULL,
            handler_name VARCHAR(255) NOT NULL,
            namespace VARCHAR(255) NOT NULL,
            action_id UUID NOT NULL,
            action_name VARCHAR(255) NOT NULL,
            action_params JSONB NOT NULL,
            started_date TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            completion_date TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            model_id VARCHAR(255),
            model_type VARCHAR(255),
            event_type VARCHAR(255) NOT NULL,
            payload JSONB,
            event_date TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            state VARCHAR(24) NOT NULL,
            attempts INTEGER NOT NULL,
            next_retry_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            created_date TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_date TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_event_notifications_event_handler UNIQUE (event_id, handler_name)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX ix_event_notifications_due ON event_notifications (state, next_retry_at)
        """
    )

    op.execute(
        """
        CREATE TABLE events (
            id UUID NOT NULL,
            namespace VARCHAR(255) NOT NULL,
            action_id UUID NOT NULL,
            action_name VARCHAR(255) NOT NULL,
            action_params JSONB NOT NULL,
            started_date TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            completion_date TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            model_id VARCHAR(255),
            model_type VARCHAR(255),
            event_type VARCHAR(255),
            payload JSONB,
            event_date TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            delivered BOOLEAN NOT NULL,
            PRIMARY KEY (id)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX ix_events_undelivered ON events (delivered, event_type, event_date)
        """
    )

    op.execute(
        """
        CREATE INDEX ix_events_action_id ON events (action_id)
        """
    )

    op.execute(
        """
        CREATE TABLE scheduled_tasks (
            task_name VARCHAR(100) NOT NULL,
            task_instance VARCHAR(100) NOT NULL,
            task_data BYTEA,
            execution_time TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            picked BOOLEAN NOT NULL,
            picked_by VARCHAR(50),
            last_success TIMESTAMP WITHOUT TIME ZONE,
            last_failure TIMESTAMP WITHOUT TIME ZONE,
            consecutive_failures INTEGER,
            last_heartbeat TIMESTAMP WITHOUT TIME ZONE,
            version BIGINT NOT NULL,
            priority SMALLINT,
            PRIMARY KEY (task_name, task_instance)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX scheduled_tasks_last_heartbeat_idx ON scheduled_tasks (last_heartbeat)
        """
    )

    op.execute(
        """
        CREATE INDEX scheduled_tasks_execution_time_idx ON scheduled_tasks (execution_time)
        """
    )

    op.execute(
        """
        CREATE INDEX scheduled_tasks_priority_execution_time_idx
            ON scheduled_tasks (priority, execution_time)
        """
    )

    op.execute(
        """
        CREATE TABLE transfer_steps (
            id UUID NOT NULL,
            version BIGINT NOT NULL,
            state VARCHAR(24) NOT NULL,
            transfer_id UUID NOT NULL,
            step VARCHAR(24) NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_transfer_steps_transfer_step UNIQUE (transfer_id, step)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE wallets (
            id UUID NOT NULL,
            version BIGINT NOT NULL,
            state VARCHAR(24) NOT NULL,
            owner_id UUID NOT NULL,
            currency CHAR(3) NOT NULL,
            balance NUMERIC(19, 4) NOT NULL,
            created_date TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_date TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            PRIMARY KEY (id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scheduled_tasks")
    op.execute("DROP TABLE IF EXISTS event_notifications")
    op.execute("DROP TABLE IF EXISTS events")
    op.execute("DROP TABLE IF EXISTS transfer_steps")
    op.execute("DROP TABLE IF EXISTS wallets")
