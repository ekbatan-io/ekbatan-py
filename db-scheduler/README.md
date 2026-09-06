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
| SQLite | 503 passed, 3 skipped |
| PostgreSQL 17 | 504 passed, 2 skipped |
| MySQL 8.4 | 504 passed, 2 skipped |
| MariaDB 11.4 | 503 passed, 3 skipped |

The skips are lock-and-fetch on the dialects that have no `SELECT ... FOR UPDATE SKIP LOCKED`,
plus the test asserting the refusal on those — so the counts differ by exactly the dialects'
real capabilities.
