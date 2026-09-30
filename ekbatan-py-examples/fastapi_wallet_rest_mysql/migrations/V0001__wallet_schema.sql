-- The application's tables, and the framework's.
-- Generated once with ekbatan.migrations.emit_ddl(metadata, "mysql") and pasted, rather
-- than built from schema.py at migration time: a migration has to mean the same thing
-- forever, and one that read today's schema.py would change meaning with every column added.
-- To change the schema, edit schema.py, regenerate, and put the difference in the next
-- file - V0002__<what_it_does>.sql. An applied migration is never edited.

CREATE TABLE alerts (
    id CHAR(36) NOT NULL,
    version BIGINT NOT NULL,
    state VARCHAR(24) NOT NULL,
    wallet_id CHAR(36) NOT NULL,
    recipient VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    PRIMARY KEY (id)
);

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
);

CREATE INDEX ix_event_notifications_due ON event_notifications (state, next_retry_at);

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
);

CREATE INDEX ix_events_undelivered ON events (delivered, event_type, event_date);

CREATE INDEX ix_events_action_id ON events (action_id);

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
);

CREATE INDEX scheduled_tasks_last_heartbeat_idx ON scheduled_tasks (last_heartbeat);

CREATE INDEX scheduled_tasks_priority_execution_time_idx
    ON scheduled_tasks (priority, execution_time);

CREATE INDEX scheduled_tasks_execution_time_idx ON scheduled_tasks (execution_time);

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
);
