-- The framework's outbox: `events`, and `event_notifications` for the in-process handlers.
-- Generated once with ekbatan.migrations.emit_ddl(metadata, "postgresql") and pasted, rather
-- than built from schema.py at migration time: a migration has to mean the same thing forever.
-- An applied migration is never edited; a change goes in the next file.

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
);

CREATE INDEX ix_event_notifications_due ON event_notifications (next_retry_at)
    WHERE state IN ('PENDING', 'FAILED');

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
);

CREATE INDEX ix_events_undelivered ON events (event_type, event_date)
    WHERE delivered = FALSE;

CREATE INDEX ix_events_action_id ON events (action_id);
