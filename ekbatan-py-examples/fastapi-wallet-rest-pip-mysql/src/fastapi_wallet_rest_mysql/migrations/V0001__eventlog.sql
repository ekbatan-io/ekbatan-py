-- The framework's outbox: `events`, and `event_notifications` for the in-process handlers.
-- Generated once with ekbatan.migrations.emit_ddl(metadata, "mysql") and pasted, rather
-- than built from schema.py at migration time: a migration has to mean the same thing forever.
-- An applied migration is never edited; a change goes in the next file.

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
