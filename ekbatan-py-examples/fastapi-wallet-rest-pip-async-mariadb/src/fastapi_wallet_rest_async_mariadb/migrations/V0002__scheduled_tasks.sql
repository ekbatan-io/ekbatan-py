-- The job scheduler's table. The in-process event handlers run on the scheduler, so it is
-- needed even with no job of your own.
-- Generated once with ekbatan.migrations.emit_ddl(metadata, "mariadb") and pasted, rather
-- than built from schema.py at migration time: a migration has to mean the same thing forever.
-- An applied migration is never edited; a change goes in the next file.

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
