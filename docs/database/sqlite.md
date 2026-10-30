# SQLite

Running the framework on SQLite. It needs no server, which makes it good for tests, examples and
small tools - and it is **not a production database**. This page is mostly about why both halves
of that are true.

## Quick reference

| | |
| --- | --- |
| driver | the standard library's `sqlite3` |
| async driver | aiosqlite - the `sqlite-async` extra, for `ekbatan.aio` |
| url | `sqlite:///path/to.db`, or `sqlite://` for memory |
| extra | none needed |
| uuid | `CHAR(32)` - hex, no hyphens |
| json | `JSON` - text, with SQLAlchemy doing the encoding |
| timestamps | `DATETIME` |
| keyed lock | none of its own — use `RedisKeyedLockProvider` |
| `SKIP LOCKED` | **no** |
| `RETURNING` | yes, 3.35+ |

## Why it is here

Because it needs no server. A test suite that needs a database server running before it does
anything is a suite people stop running. On SQLite, tests run on a fresh clone with no Docker -
this framework's own unit tests take about a second - and that is worth a great deal.

It also checks the framework's own SQL honestly: the integration tests that run against the
other three databases run against SQLite too.

## Why it is not a production dialect

**No `SKIP LOCKED`.** This is the one that settles it. The job scheduler relies on it to let a
waiting reader skip a row another instance has claimed. Without it, two workers polling the same
table queue up behind each other, or collide. In one process that is invisible; in a cluster it is
wrong.

**No real concurrency.** One writer at a time, for the whole database. The framework's
optimistic locking still works correctly; it is the throughput that is missing.

**Uuids lose their hyphens.** They are stored as `CHAR(32)`, the hex form SQLAlchemy uses where a
database has no native uuid type. The values are the same uuids and round-trip correctly, but they do not look
like what PostgreSQL shows, and a hand-written query comparing against a hyphenated literal finds
nothing.

**Timestamps have no fractional-second guarantee** the way `DATETIME(6)` does, and the outbox's
only ordering key is a timestamp.

**Types are only advice.** SQLite accepts a string in an integer column, so a mapping bug that
PostgreSQL rejects loudly can pass here and fail in production. That is why passing on SQLite
proves less than it seems to - and why this framework is tested on all three server databases on
every change, not on SQLite alone.

## Migration order

Nothing to set up first. SQLite has one namespace per file: no `CREATE SCHEMA`, no init script,
and no user to grant anything to. `ATTACH` is the nearest thing to a second schema, and it works
per connection rather than per database, so leave every table name unqualified. The framework's
two event tables are plain `events` and `event_notifications` here: ekbatan maps its `eventlog`
onto the file by itself.

```
migrations/
  V0001__eventlog.sql           # events + event_notifications
  V0002__scheduled_tasks.sql    # only if you run distributed jobs or in-process handlers
  V0003__wallets.sql            # your domain tables
```

Keep the files split anyway, for the same reason as on the other databases: the first two change
when you upgrade the framework, the third when your domain changes.

## Framework tables

Three, and only the first is always required. Each table's page has its full DDL, the map is on
[Framework tables](tables.md), and `emit_ddl(metadata, "sqlite")` prints the DDL for
your own migration.

| Table | Needed when | Page |
|---|---|---|
| `events` | always | [`events`](tables/events.md) |
| `event_notifications` | you handle events in-process | [`event_notifications`](tables/event-notifications.md) |
| `scheduled_tasks` | you run distributed jobs, or in-process handlers | [`scheduled_tasks`](tables/scheduled-tasks.md) |

What the column helpers in `ekbatan.schema` become on this database:

| helper | this dialect |
|---|---|
| `uuid_column()` | `CHAR(32)` |
| `json_column()` | `JSON` |
| `timestamp_column()` | `DATETIME` |

`CHAR(32)` is the hex digits with the hyphens taken out, so SQLite is the one dialect whose
stored ids do not match the others' text.

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
| `id` | `CHAR(32)` | primary key |
| `version` | `BIGINT NOT NULL` | optimistic locking - required, no default |
| `state` | `VARCHAR(24) NOT NULL` | marks soft-deleted rows: `DELETED`, rather than a `deleted_at` column |
| `created_date` | `DATETIME NOT NULL` | **required** when you build the row |
| `updated_date` | `DATETIME NOT NULL` | falls back to `created_date`; yours to move |

## What it is good for

- the edit-and-run loop while you develop, and unit tests;
- an example that should run with no setup -
  [`wallet-service-uv-sqlite`](../../ekbatan-py-examples/wallet-service-uv-sqlite) is exactly that;
- a single-process tool or an internal utility that will never have a second instance.

For that last case it really works. The scheduler cannot keep a job to one instance across a
cluster, but there is no cluster - and what you get is a working service, outbox and event
handlers included, in one file with no infrastructure.

Keyed locks are the one thing it cannot give you on its own: SQLite has no advisory locks, and
there is no in-process provider. If you need them, use `RedisKeyedLockProvider`
(`AsyncRedisKeyedLockProvider` in async code). Optimistic locking needs nothing and is always on.

## The claim this framework makes

That it works is a claim about **PostgreSQL, MySQL and MariaDB**, and all three are tested on
every change. Passing on SQLite is necessary, but not enough.

## See also

- [Multiple dialects](multi-database.md) - where the four disagree
- [`wallet-service-uv-sqlite`](../../ekbatan-py-examples/wallet-service-uv-sqlite) - no container, no
  web stack
- [`wallet-service-uv-async-sqlite`](../../ekbatan-py-examples/wallet-service-uv-async-sqlite) - the
  same, async, on aiosqlite
