# MySQL

Running the framework on MySQL. It is fully supported, with three things to know: there is no
UUID type, there are no partial indexes, and `GET_LOCK` rounds a lock wait to whole seconds.

## Quick reference

```
uv add "ekbatan[mysql]"
```

| | |
| --- | --- |
| driver | PyMySQL |
| async driver | aiomysql - the `mysql-async` extra, for `ekbatan.aio` |
| url | `mysql+pymysql://host:3306/db` |
| login | `username=` and `password=`, never in the url |
| uuid | `CHAR(36)` |
| json | `JSON` |
| timestamps | `DATETIME(6)`, UTC |
| keyed lock | `MySQLKeyedLockProvider`, `GET_LOCK` |
| `SKIP LOCKED` | yes, 8.0+ |
| `RETURNING` | **no** |
| tested against | `mysql:8.4` |

## Migration order

The framework's two event tables go in a database of their own, `eventlog`, beside yours: on
MySQL a schema *is* a database. Your first migration creates it, and the next puts the tables in
it:

```
migrations/
  V0000__create_eventlog_database.sql   # CREATE DATABASE IF NOT EXISTS eventlog;
  V0001__eventlog.sql                   # eventlog.events + eventlog.event_notifications + indexes
  V0002__scheduled_tasks.sql            # only if you run distributed jobs or in-process handlers
  V0003__wallets.sql                    # your domain tables
```

The migrations run on your database's connection, so the login needs the right to create
`eventlog` and to use it. Root has it. Any other login needs one grant, given once by root, and
the grant works even before the database exists:

```sql
GRANT ALL PRIVILEGES ON eventlog.* TO 'wallet'@'%';
```

There is one `eventlog` per server, shared by every database on it. Two shards, or two
applications, on one server write into the same two tables. That has a catch: the fan-out job
picks undelivered rows by event type alone, so if two applications on one server use the same
event name, each one's handlers also get the other's events. A shard or an application on a
server of its own has its own `eventlog`.

## Framework tables

Three, and only the first is always required. Each table's page has its full DDL, the map is on
[Framework tables](tables.md), and `emit_ddl(metadata, "mysql")` prints the DDL for
your own migration.

| Table | Needed when | Page |
|---|---|---|
| `events` | always | [`events`](tables/events.md) |
| `event_notifications` | you handle events in-process | [`event_notifications`](tables/event-notifications.md) |
| `scheduled_tasks` | you run distributed jobs, or in-process handlers | [`scheduled_tasks`](tables/scheduled-tasks.md) |

What the column helpers in `ekbatan.schema` become on this database:

| helper | this dialect |
|---|---|
| `uuid_column()` | `CHAR(36)` |
| `json_column()` | `JSON` |
| `timestamp_column()` | `DATETIME(6)` |

`DATETIME(6)`, never a bare `DATETIME`: the bare form cuts times to whole seconds, and
`event_date` is the outbox's only ordering key.

## Domain tables

Your own tables are yours to design, with two required columns. A repository checks for them
when it is created, and refuses a table that lacks either:

- **`version`**, the counter behind optimistic locking;
- **`state`**, which marks a soft-deleted row as `DELETED`.

A model's table (a `ModelRepository`'s) also needs `created_date` and `updated_date`; an
entity's table needs neither. The framework fills in neither date: you give `created_date` when
you build the row, and you move `updated_date` on the changes where it means something.

Reads skip rows whose `state` is `DELETED`, without being asked. Updates carry
`WHERE version = ?` and increment it.

| column | type | role |
|---|---|---|
| `id` | `CHAR(36)` | primary key |
| `version` | `BIGINT NOT NULL` | optimistic locking - required, no default |
| `state` | `VARCHAR(24) NOT NULL` | marks soft-deleted rows: `DELETED`, rather than a `deleted_at` column |
| `created_date` | `DATETIME(6) NOT NULL` | **required** when you build the row |
| `updated_date` | `DATETIME(6) NOT NULL` | falls back to `created_date`; yours to move |

## MySQL-specific gotchas

**No native UUID.** `uuid_column()` renders `CHAR(36)`, the hyphenated text form. It was chosen
so ids stay searchable in query logs and dumps, and read the same as on PostgreSQL and MariaDB.
`BINARY(16)` is more compact but harder to debug, so it is not the default.

**Add `CHARACTER SET ascii` to those columns in your migration.** `uuid_column()` does not set
the character set. A uuid is plain ASCII, so setting it keeps each character at one byte instead
of up to four under `utf8mb4`, which makes the index smaller. The stored text is the same either
way.

**`DATETIME(6)`, not `TIMESTAMP`.** `TIMESTAMP` has a smaller range, an implicit
`ON UPDATE CURRENT_TIMESTAMP` and awkward `NULL` handling, so every table here uses
`DATETIME(6)`, db-scheduler's included. And never a bare `DATETIME`: it silently cuts times to
whole seconds.

**No partial indexes.** Instead, put the column you would filter on first in a composite index:
`(delivered, event_type, event_date)`, as the MySQL examples' migrations do.

**No `RETURNING`.** A write cannot read its own result back in one statement, so the framework
rebuilds written rows itself. That is why `add_no_result` and `update_no_result` exist: on a
large batch where you do not need the objects back, they skip work that is free on PostgreSQL and
is not free here.

**A lock wait is rounded to whole seconds.** MySQL rounds `GET_LOCK`'s timeout to the nearest
second: a 250 ms wait is a single try, 500 ms waits one second, 1.5 s waits two.
[Measured](keyed-locks.md#mysql-rounds-the-wait-to-whole-seconds).

**`eventlog` is a database.** The event tables are in it, one `eventlog` for the whole server (see
[Migration order](#migration-order)). Hand-written SQL names them `eventlog.events` and
`eventlog.event_notifications`.

**One schema change per migration file.** MySQL commits every schema change the moment it runs,
so if a migration fails after one, that change stays. The migrator records the failure and will
not run that migration again until you have put the database right by hand and called
`repair()`. One change per file means a failure never leaves a file half-done. Several statements
in one file are otherwise fine: the migrator splits a file itself, including a procedure's
`BEGIN ... END` body, written without `DELIMITER`.

**Run the server in UTC** - `TZ=UTC` on the container - or put each session on UTC with
`data_source_properties={"init_command": "SET time_zone = '+00:00'"}`. The values Ekbatan writes
do not shift either way, but `NOW()`, `CURRENT_TIMESTAMP` and column defaults follow the server's
zone. See [Always UTC](multi-database.md#always-utc).

## See also

- [MariaDB](mariadb.md) - close, and different in ways that matter
- [Framework tables](tables.md) · [Multiple dialects](multi-database.md)
- [`fastapi-wallet-rest-uv-mysql`](../../ekbatan-py-examples/fastapi-wallet-rest-uv-mysql)
