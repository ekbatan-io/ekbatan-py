# db-scheduler, in Python

A port of [kagkarlsson/db-scheduler](https://github.com/kagkarlsson/db-scheduler) — persistent,
cluster-friendly scheduling backed by the database you already have.

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

## Self-contained

Its own `pyproject.toml`, its own dependencies, its own tests. It does not import from `ekbatan`
and `ekbatan` does not import from it. The table it writes is db-scheduler's `scheduled_tasks`,
so a Java service and a Python service coordinate through one row per task.

## Layout

    src/db_scheduler/          the library, mirroring the Java package structure
    tests/unit/                no database
    tests/integration/         Testcontainers: postgres, mysql, mariadb
    docs/java-api-inventory.md   every public Java signature, machine-extracted
    docs/java-test-inventory.md  every Java test method, machine-extracted
