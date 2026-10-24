# PostgreSQL

The cleanest fit. Native UUID, native JSONB, partial indexes, `SKIP LOCKED` and `RETURNING` -
every feature the framework wants, without a workaround.

## Quick reference

```
uv add "ekbatan[postgres]"
```

| | |
| --- | --- |
| driver | psycopg 3 |
| async driver | asyncpg - the `postgres-async` extra, for `ekbatan.aio` |
| url | `postgresql+psycopg://host:5432/db` |
| login | `username=` and `password=`, never in the url |
| uuid | `UUID`, native |
| json | `JSONB` |
| timestamps | `TIMESTAMP WITHOUT TIME ZONE`, UTC |
| keyed lock | `PostgresKeyedLockProvider`, `pg_advisory_lock` |
| `SKIP LOCKED` | yes |
| `RETURNING` | yes |
| tested against | `postgres:17-alpine` |

## Migration order

The framework's two event tables live in a schema of their own, `eventlog`, inside the connected
database: `eventlog.events` and `eventlog.event_notifications`. The first migration creates the
schema inline - `CREATE SCHEMA IF NOT EXISTS eventlog;` - and then the tables in it. Nothing passes
the tables to the framework: the executor and the event jobs name them themselves.

```
migrations/
  V0001__eventlog.sql           # events + event_notifications + their indexes
  V0002__scheduled_tasks.sql    # only if you run distributed jobs or in-process handlers
  V0003__wallets.sql            # your domain tables
```

Three files rather than one because they change for different reasons: the first two move
when you upgrade the framework, the third whenever your domain does.

No init script. The connecting user normally has create-schema rights already, which is the
whole of why PostgreSQL is the least work of the four.

## Framework tables

Three, and only the first is always required. The DDL is generated rather than transcribed -
[Framework tables](tables.md) has it per dialect, and `emit_ddl(metadata, "postgresql")` prints it
for your own migration.

| Table | Needed when | Page |
|---|---|---|
| `events` | always | [`events`](tables/events.md) |
| `event_notifications` | you handle events in-process | [`event_notifications`](tables/event-notifications.md) |
| `scheduled_tasks` | you run distributed jobs, or in-process handlers | [`scheduled_tasks`](tables/scheduled-tasks.md) |

What the column helpers render here:

| helper | this dialect |
|---|---|
| `uuid_column()` | `UUID` |
| `json_column()` | `JSONB` |
| `timestamp_column()` | `TIMESTAMP WITHOUT TIME ZONE` |

The framework declares no indexes; your migration writes them, and on PostgreSQL that means
partial indexes - see the gotcha below.

## Domain tables

Your tables are yours. The repository requires two columns on every one of them and refuses at
construction if either is missing: **`version`**, the optimistic-locking counter, and
**`state`**, the soft-delete discriminator. A `ModelRepository` additionally wants
`created_date` and `updated_date`; an entity's table needs neither, because an entity is state
rather than history. Neither is written by the framework: `created_date` is required when you
build the row, and `updated_date` is yours to move on the changes where it means something.

Reads filter `state != DELETED` for you. Updates carry `WHERE version = ?` and increment it.

| column | type | role |
|---|---|---|
| `id` | `UUID` | primary key |
| `version` | `BIGINT NOT NULL` | optimistic locking - required, no default |
| `state` | `VARCHAR(24) NOT NULL` | soft-delete discriminator; `DELETED` rather than a `deleted_at` column |
| `created_date` | `TIMESTAMP WITHOUT TIME ZONE NOT NULL` | **required** when you build the row |
| `updated_date` | `TIMESTAMP WITHOUT TIME ZONE NOT NULL` | falls back to `created_date`; yours to move |

## Postgres-specific gotchas

**`JSONB`, not `JSON`.** `json_column()` gives you `JSONB`. The binary form is indexable and
compares by value; `JSON` is stored text and compares by bytes, so two payloads that differ only
in key order are unequal.

**Schema, not database.** `eventlog` is a schema inside the connected database:
`CREATE SCHEMA IF NOT EXISTS eventlog;` in your first migration, and no grant - the connecting
user normally has the right. Each database has its own, so shards that are databases on one server
never share event tables - unlike MySQL and MariaDB, where `eventlog` is a database, one per
server. Hand-written SQL names them `eventlog.events` and
`eventlog.event_notifications`.

**Plain `TIMESTAMP`, never `TIMESTAMPTZ`.** Run the server with `TZ=UTC`. CI runs this suite a
second time under `TZ=America/New_York` because that is where a driver inferring a zoned type
shows up. See [Multiple dialects](multi-database.md#always-utc).

**Partial indexes are the reason PostgreSQL suits this workload.** The framework declares no
indexes on its tables, so write the undelivered index in its partial form,
`WHERE delivered = FALSE`: on a 500k-row outbox with a 1% backlog it is
[63x smaller](tables/events.md#the-undelivered-index-and-what-it-costs) than the composite one. The examples' PostgreSQL migrations do this.

**Untyped `NULL` in a multi-row `VALUES`.** PostgreSQL infers `text` for a bare `NULL` and then
refuses to put it in an integer column - *"column reward_points is of type integer but
expression is of type text"*. The framework casts every NULL explicitly in a batch write. You
need the same care in your own multi-row inserts.

**Indexing inside JSONB.** If you query against payload fields, add a GIN index. The framework
does not ship one because most applications do not need it.

**`psycopg`, not `psycopg2`.** The `postgres` extra brings psycopg 3. A url saying
`postgresql+psycopg2` needs a driver this project does not install.

## See also

- [Framework tables](tables.md) - the DDL
- [Multiple dialects](multi-database.md) - where the four disagree
- [`fastapi-wallet-rest-uv-pg`](../../ekbatan-py-examples/fastapi-wallet-rest-uv-pg)
