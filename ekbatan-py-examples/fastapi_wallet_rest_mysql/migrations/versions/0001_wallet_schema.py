"""the wallet schema, and the framework's own tables

Revision ID: 0001
Revises:

Generated for **MySQL** with `ekbatan.migrations.emit_ddl(metadata, "mysql")` and pasted,
rather than built from `schema.py` at migration time. That matters: a revision has to mean the
same thing forever. One that read today's `schema.py` would quietly change meaning every time a
column was added, and the database and the file would disagree with nothing to say so - which is
the failure the checksum guard exists to catch, and it cannot catch a file that never changed.

So: edit `schema.py`, regenerate, and paste the *difference* into a new revision.

**One statement per `op.execute`**, and not for tidiness. PyMySQL executes a single statement per
call and rejects a script; psycopg accepts one. A migration written as one multi-statement block
therefore works on PostgreSQL and fails on MySQL and MariaDB with a syntax error pointing at the
second `CREATE TABLE` - which is the kind of difference that makes these three separate projects
rather than one with a different url.
"""

from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE alerts (
            id CHAR(36) NOT NULL,
            version BIGINT NOT NULL,
            state VARCHAR(24) NOT NULL,
            wallet_id CHAR(36) NOT NULL,
            recipient VARCHAR(255) NOT NULL,
            message TEXT NOT NULL,
            PRIMARY KEY (id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE event_notifications (
            id CHAR(36) NOT NULL,
            event_id CHAR(36) NOT NULL,
            handler_name VARCHAR(255) NOT NULL,
            namespace VARCHAR(255) NOT NULL,
            action_id CHAR(36) NOT NULL,
            action_name VARCHAR(255) NOT NULL,
            action_params JSON NOT NULL,
            started_date DATETIME(6) NOT NULL,
            completion_date DATETIME(6) NOT NULL,
            model_id VARCHAR(255),
            model_type VARCHAR(255),
            event_type VARCHAR(255) NOT NULL,
            payload JSON,
            event_date DATETIME(6) NOT NULL,
            state VARCHAR(24) NOT NULL,
            attempts INTEGER NOT NULL,
            next_retry_at DATETIME(6) NOT NULL,
            created_date DATETIME(6) NOT NULL,
            updated_date DATETIME(6) NOT NULL,
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
            id CHAR(36) NOT NULL,
            namespace VARCHAR(255) NOT NULL,
            action_id CHAR(36) NOT NULL,
            action_name VARCHAR(255) NOT NULL,
            action_params JSON NOT NULL,
            started_date DATETIME(6) NOT NULL,
            completion_date DATETIME(6) NOT NULL,
            model_id VARCHAR(255),
            model_type VARCHAR(255),
            event_type VARCHAR(255),
            payload JSON,
            event_date DATETIME(6) NOT NULL,
            delivered BOOL NOT NULL,
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
            task_data BLOB,
            execution_time DATETIME(6) NOT NULL,
            picked BOOL NOT NULL,
            picked_by VARCHAR(50),
            last_success DATETIME(6),
            last_failure DATETIME(6),
            consecutive_failures INTEGER,
            last_heartbeat DATETIME(6),
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
        CREATE INDEX scheduled_tasks_priority_execution_time_idx
            ON scheduled_tasks (priority, execution_time)
        """
    )

    op.execute(
        """
        CREATE INDEX scheduled_tasks_execution_time_idx ON scheduled_tasks (execution_time)
        """
    )

    op.execute(
        """
        CREATE TABLE wallets (
            id CHAR(36) NOT NULL,
            version BIGINT NOT NULL,
            state VARCHAR(24) NOT NULL,
            owner_id CHAR(36) NOT NULL,
            currency CHAR(3) NOT NULL,
            balance NUMERIC(19, 4) NOT NULL,
            created_date DATETIME(6) NOT NULL,
            updated_date DATETIME(6) NOT NULL,
            PRIMARY KEY (id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scheduled_tasks")
    op.execute("DROP TABLE IF EXISTS event_notifications")
    op.execute("DROP TABLE IF EXISTS events")
    op.execute("DROP TABLE IF EXISTS alerts")
    op.execute("DROP TABLE IF EXISTS wallets")
