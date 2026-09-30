# PostgreSQL

The cleanest fit. Native UUID, native JSONB, partial indexes, `SKIP LOCKED` and `RETURNING` -
every feature the framework wants, without a workaround.

## Quick reference

```
uv add ekbatan[postgres]
```

| | |
| --- | --- |
| driver | psycopg 3 |
| url | `postgresql+psycopg://user:pass@host:5432/db` |
| uuid | `UUID`, native |
| json | `JSONB` |
| timestamps | `TIMESTAMP WITHOUT TIME ZONE`, UTC |
| keyed lock | `PostgresKeyedLockProvider`, `pg_advisory_lock` |
| `SKIP LOCKED` | yes |
| `RETURNING` | yes |
| tested against | `postgres:17-alpine` |

## Migration order

PostgreSQL has no separate "create the eventlog database" step: `eventlog`, if you want one at
all, is a schema inside the connected database and the first migration creates it inline. This
port defaults to no schema, so usually there is nothing to create.

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

Partial indexes are the one thing this port does not emit and PostgreSQL would
reward - see the gotcha below.

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
`CREATE SCHEMA IF NOT EXISTS eventlog;` in your first migration, and no init script - the
connecting user normally has the right. Only needed if you want Java's layout; this port defaults
to no schema.

**Plain `TIMESTAMP`, never `TIMESTAMPTZ`.** Run the server with `TZ=UTC`. CI runs this suite a
second time under `TZ=America/New_York` because that is where a driver inferring a zoned type
shows up. See [Multiple dialects](multi-database.md#always-utc).

**Partial indexes are the reason PostgreSQL suits this workload**, and this port does not emit
them. The generated undelivered index is the portable composite one, which is
[63x larger](tables/events.md#the-undelivered-index-and-what-it-costs) on a 500k-row outbox with
a 1% backlog. On a large outbox, drop it and create the partial one yourself.

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
- [`fastapi_wallet_rest_pg`](../../ekbatan-py-examples/fastapi_wallet_rest_pg)
