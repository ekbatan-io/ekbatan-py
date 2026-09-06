# db-scheduler, in Python

A port of [kagkarlsson/db-scheduler](https://github.com/kagkarlsson/db-scheduler) — persistent,
cluster-friendly scheduling backed by the database you already have. No broker, no separate
worker protocol: a row in a table, claimed by whichever instance gets there first.

It exists because nothing in the Python ecosystem does this. Every candidate was benched against
two processes sharing one database, running one job every second for eight seconds; the correct
answer is eight executions:

| | result |
| --- | --- |
| huey + SqlHuey (± `lock_task`) | 16 — every slot run twice |
| sqlalchemy-celery-beat | 14 — every slot run twice |
| APScheduler 3.11 + SQLAlchemy store | 14 — six slots run twice |
| APScheduler 4.0.0a6 | 12/12 correct, but no failover without Redis or Postgres, and alpha since 2020 |

The reasoning, and the full comparison, is in `../DIVERGENCE.md`.

## Using it

```python
from datetime import timedelta

from sqlalchemy import MetaData, create_engine

from db_scheduler.builder import create
from db_scheduler.jdbc import scheduled_tasks_table
from db_scheduler.task.helper import Tasks
from db_scheduler.task.schedule import Schedules

engine = create_engine("postgresql+psycopg://localhost/app")

# Once, at deploy time - or run db-scheduler's own DDL, which produces the same table.
metadata = MetaData()
scheduled_tasks_table(metadata)
metadata.create_all(engine)

send_email = Tasks.one_time("send-email", dict).execute(
    lambda instance, context: mailer.send(instance.data["to"])
)
nightly = Tasks.recurring("nightly-report", Schedules.cron("0 0 3 * * ?")).execute(
    lambda instance, context: reports.run()
)

scheduler = create(engine).add_tasks(send_email).add_start_tasks(nightly).threads(10).build()
scheduler.start()

# From anywhere, including a process that runs no scheduler at all:
scheduler.schedule_if_not_exists(
    send_email.instance("welcome-42", {"to": "ada@example.com"}), when
)
```

An application server that only ever *creates* work does not need a scheduler — build a
`StandardSchedulerClient` over the same table and let the workers pick the rows up.

Testing is the other half. `ManualScheduler` is the real scheduler with the threads and the
clock taken out, so an hour of retry backoff is one `tick(timedelta(hours=1))`:

```python
from db_scheduler.testhelper import create_manual_scheduler

scheduler = create_manual_scheduler(engine, send_email).clock(SettableClock(now)).start()
scheduler.schedule_if_not_exists(send_email.instance("an-id"), now)
scheduler.run_any_due_executions()      # runs on this thread, and has finished when it returns
```

## Shutting down

`stop()` is bounded, and it does not interrupt a poll that is already running — db-scheduler
makes the same choice, and for the same reason: a poll is mid-conversation with the database,
and cutting it off could leave rows claimed but never handed to a worker. It is allowed to
finish (a query or two), and it stops *claiming* new work the instant shutdown begins.

`stop()` stops polling, waits up to `shutdown_max_wait` for running executions,
then warns and names anything still going. It never blocks past that, and it never keeps the
process alive: the worker threads are daemon threads, because Python has no counterpart to
Java's `Thread.interrupt()` and the interpreter joins every non-daemon thread at exit — so one
handler that ignores shutdown would otherwise hang the process for ever.

An execution abandoned that way is not lost. It is the same state as the machine losing power:
the row stays picked, its heartbeat stops, and the next instance to run dead-execution detection
revives it. Handlers that want to finish cleanly should check
`context.scheduler_state.is_shutting_down`.

## Threads, and forking

Everything here runs on threads. The library never creates a process: three loop threads (poll,
heartbeat, dead-execution detection) plus a worker pool of `threads(n)`.

Build the `Scheduler` **after** any `fork()` — under gunicorn `--preload`, uwsgi, or a manual
fork. Threads do not survive a fork, so a scheduler built beforehand has no workers in the
child, and the database connections it inherited cannot be shared across the fork either. This
is the same rule SQLAlchemy has for engines, for the same reason. A pool that finds itself in a
forked child refuses work with an explanatory error rather than silently accepting executions
nobody will ever run.

## Sizing the connection pool

Give the engine **more connections than the scheduler has worker threads**. The heartbeat is how
a running execution proves it is alive; if every connection is held by a task body, it cannot
write, and another instance declares the execution dead and runs it again.

Measured with three workers each holding a connection: at capacity 3 *every* execution was
stolen and run twice; at capacity 4, none were. `threads(n)` warns at startup if the pool cannot
outnumber it — including with SQLAlchemy's default pool of 15, where `threads(20)` looks
perfectly reasonable and is not.

## Limits worth knowing

`task_data` is a `blob` on MySQL and MariaDB, as it is in every db-scheduler DDL file — so
payloads over ~64KB are **rejected** there while Postgres accepts them. The failure is loud
(`DataError`), never a silent truncation, but it is a way for something that works on a Postgres
laptop to fail on a MySQL production database. Keep task data small and point at the payload
rather than carrying it.

Task names and instance ids are `varchar(100)`. Postgres and MySQL reject anything longer;
SQLite does not enforce `varchar` lengths at all and stores the whole thing. Nothing truncates
silently on any of them.

## Self-contained

Its own `pyproject.toml`, its own dependencies, its own tests. It does not import from `ekbatan`
and `ekbatan` does not import from it. The table it writes is db-scheduler's `scheduled_tasks`,
so a Java service and a Python service coordinate through one row per task.

## Layout

    src/db_scheduler/          the library, mirroring the Java package structure
    tests/unit/                no database
    tests/integration/         Testcontainers: postgres, mysql, mariadb
    scripts/ledger.py          counts docs/FILE-LEDGER.md
    docs/FILE-LEDGER.md        every Java source file, and where it went
    docs/PORTING.md            the plan, the deliberate differences, the verification status
    docs/java-api-inventory.md   every public Java signature, machine-extracted
    docs/java-test-inventory.md  every Java test method, machine-extracted

## What is ported

All 159 Java source files. 133 ported, 26 recorded as not-applicable with a reason — mostly the
JDBC plumbing SQLAlchemy already is, and the Gson/Jackson bindings the JSON serializer replaces.
`python scripts/ledger.py` counts it.

## Running the tests

    pytest                          # sqlite, about three seconds
    pytest --dialects=postgres      # one container
    pytest --dialects=all           # sqlite, postgres, mysql, mariadb - the same assertions
    ruff check . && mypy

| dialect | result |
| --- | --- |
| SQLite | 732 passed, 6 skipped |
| PostgreSQL 17 | 734 passed, 4 skipped |
| MySQL 8.4 (LTS) | 734 passed, 4 skipped |
| MySQL 26.7 (current) | 734 passed, 4 skipped |
| MariaDB 11.4 | 732 passed, 6 skipped |

Every integration test runs twice per dialect, once for each timestamp shape - zone-less UTC
columns, and the zoned columns a Java-created table has. Reading a table in the wrong shape is
an hour's silent drift rather than an error, so both are exercised rather than assumed.

The skips are lock-and-fetch on the dialects that have no `SELECT ... FOR UPDATE SKIP LOCKED`,
plus the test asserting the refusal on those — so the counts differ by exactly the dialects'
real capabilities.
