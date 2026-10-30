# `scheduled_tasks`

The table distributed jobs run on. Optional: needed only if you use distributed jobs - or the
in-process event handler, whose fan-out and dispatch *are* jobs too.

**Needed when:** you use distributed jobs, or the in-process event handler.
**Written by:** db-scheduler.
**Built by:** `ekbatan.schema.scheduled_tasks_table(metadata, name="scheduled_tasks", schema=None)`.

This table is **owned by [db-scheduler](https://github.com/kagkarlsson/db-scheduler)**, the
scheduling library the jobs run on, not by this framework. The job registry is a thin layer over
db-scheduler and does not wrap the table in a model. The schema here is db-scheduler's own,
unchanged.

The scheduler comes with `ekbatan` as a dependency: `ekbatan-py-db-scheduler`, a Python version
of db-scheduler, published by this project, released together with `ekbatan` and pinned to the
matching version.

## DDL

### PostgreSQL

```sql
CREATE TABLE scheduled_tasks (
    task_name            VARCHAR(100) NOT NULL,
    task_instance        VARCHAR(100) NOT NULL,
    task_data            BYTEA,
    execution_time       TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    picked               BOOLEAN      NOT NULL,
    picked_by            VARCHAR(50),
    last_success         TIMESTAMP WITHOUT TIME ZONE,
    last_failure         TIMESTAMP WITHOUT TIME ZONE,
    consecutive_failures INTEGER,
    last_heartbeat       TIMESTAMP WITHOUT TIME ZONE,
    version              BIGINT       NOT NULL,
    priority             SMALLINT,
    PRIMARY KEY (task_name, task_instance)
);

CREATE INDEX scheduled_tasks_execution_time_idx ON scheduled_tasks (execution_time);
CREATE INDEX scheduled_tasks_last_heartbeat_idx ON scheduled_tasks (last_heartbeat);
CREATE INDEX scheduled_tasks_priority_execution_time_idx
    ON scheduled_tasks (priority, execution_time);
```

### MySQL and MariaDB

The same table, with `BLOB` for `BYTEA` and `DATETIME(6)` for the timestamps:

```sql
CREATE TABLE scheduled_tasks (
    task_name            VARCHAR(100) NOT NULL,
    task_instance        VARCHAR(100) NOT NULL,
    task_data            BLOB,
    execution_time       DATETIME(6)  NOT NULL,
    picked               BOOL         NOT NULL,
    picked_by            VARCHAR(50),
    last_success         DATETIME(6),
    last_failure         DATETIME(6),
    consecutive_failures INTEGER,
    last_heartbeat       DATETIME(6),
    version              BIGINT       NOT NULL,
    priority             SMALLINT,
    PRIMARY KEY (task_name, task_instance)
);

CREATE INDEX scheduled_tasks_execution_time_idx ON scheduled_tasks (execution_time);
CREATE INDEX scheduled_tasks_last_heartbeat_idx ON scheduled_tasks (last_heartbeat);
CREATE INDEX scheduled_tasks_priority_execution_time_idx
    ON scheduled_tasks (priority, execution_time);
```

`DATETIME(6)` is the thing to get right here: the scheduler compares `execution_time` and
`last_heartbeat` against times that are not whole seconds, and a bare `DATETIME` cuts them to
whole seconds.

The three indexes are the same on every database; [Indexes](#indexes) says what the priority one
is for.

## Columns

| column | null | meaning |
| --- | :---: | --- |
| `task_name` | no | which job |
| `task_instance` | no | which instance of it. For a recurring job there is exactly one, named `recurring` |
| `task_data` | yes | the serialised payload, for a job that carries one |
| `execution_time` | no | when it is next due |
| `picked` | no | whether some instance has claimed it |
| `picked_by` | yes | which one |
| `last_success`, `last_failure` | yes | the last outcomes |
| `consecutive_failures` | yes | how many runs in a row have failed; the backoff is based on it |
| `last_heartbeat` | yes | **the liveness signal.** An execution whose heartbeat stops is declared dead and run again elsewhere |
| `version` | no | optimistic locking, the same idea as everywhere else |
| `priority` | yes | which due execution to take first |

`task_name` plus `task_instance` is the primary key, and that key is all it takes to run each job
on only one instance across the cluster: a recurring job is one row, so only one instance can
hold it.

## The heartbeat is why the jobs pool is separate

`last_heartbeat` is written by a background thread, over and over, for every execution this
instance holds. If that thread cannot get a connection, the heartbeat stops, and the execution is
declared dead and **run again somewhere else** - while it is still running here.

That is why jobs get a pool of their own (see [Wiring](../../wiring.md)), with **at least
`workers + 3` connections**: one for each worker, plus one for each of the scheduler's three
loops - polling for due work, writing heartbeats, and finding dead executions. A job body holding
the last connection starves the heartbeat.
db-scheduler warns at startup when the pool is too small.

## Dialect types

The columns, their order, nullability and the primary key are db-scheduler's, on every database.
The time columns are `TIMESTAMP WITHOUT TIME ZONE` on PostgreSQL and `DATETIME(6)` on MySQL and
MariaDB.

### If the time columns are `timestamptz`

An edge case. The scheduler writes and reads its times exactly as it is given them, as the rest
of the framework does. Its default clock gives the current UTC time with no zone attached - the
right kind for the `TIMESTAMP` columns above. But a table created by some other migration may
declare the PostgreSQL time columns `timestamptz` instead. Those hand back times *with* a zone,
so give the scheduler a clock that has one too:

```python
from datetime import UTC, datetime


class AwareUtcClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


registry = (
    job_registry()
    .data_source(jobs_data_source)
    .with_job(job)
    .customize_scheduler(lambda scheduler: scheduler.clock(AwareUtcClock()))
    .build()
)
```

This is the scheduler's own clock, from db-scheduler, not Ekbatan's `Clock`. That is why its one
method is `now()`, where Ekbatan's `Clock` has `instant()`.

Without it, the scheduler compares its zone-less clock with the zoned times PostgreSQL hands back,
and Python refuses. Tried with a one-second job on a `timestamptz` table: the job still ran, but
every restart logged `TypeError: can't compare offset-naive and offset-aware datetimes` while
matching the recurring job to its row. The scheduler's other comparisons of a stored time with
the clock - purging rows of unknown tasks, for one - fail the same way. With the clock above,
there is no error.

Keep the database session in UTC either way. A **naive** datetime - one with no zone - written
into a `timestamptz` column is read by PostgreSQL in the *session's* time zone. Measured, writing
`2026-09-15 12:00:00`:

| session timezone | naive value lands as | aware value lands as |
| --- | --- | --- |
| `UTC` | `12:00:00 UTC` | `12:00:00 UTC` |
| `America/New_York` | **`16:00:00 UTC`** | `12:00:00 UTC` |

Four hours off, silently, and every execution fires at the wrong time. This is why the framework's
tables use `TIMESTAMP` without a zone everywhere else. If you write to this table yourself, write
the same kind of time the scheduler's clock gives.

## Indexes

### Index names and the priority ordering

Each index name starts with the table's name - `scheduled_tasks_execution_time_idx` and so on -
so the names stay unique when one schema holds two schedulers, each with its own table.

The scheduler takes due executions highest priority first, then oldest first
(`priority DESC, execution_time ASC`). The index is `(priority, execution_time)`, with no
direction: at this table's size the difference could not be measured.

## See also

- [Framework tables](../tables.md) - the map
- [Wiring](../../wiring.md) - the three pools, and why this one is separate
