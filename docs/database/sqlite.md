# SQLite

It is supported here, it is what the test suite runs on by default, and it is **not a production
dialect**. This page is mostly about why both halves of that are true.

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

A test suite that needs a daemon running before it does anything is a suite people stop running.
`uv run pytest` works on a fresh clone with no Docker, in about a second for the unit tests, and
that is worth a great deal.

It is also an honest smoke test of the framework's own SQL: 322 integration tests run against it,
and they are the same tests that run against the other three.

## Why it is not a production dialect

**No `SKIP LOCKED`.** This is the one that settles it. Both the job scheduler and the SQL keyed
lock depend on a waiting reader being able to skip a row another instance has claimed. Without
it, two workers polling the same table serialise against each other or collide. For one process
that is invisible. For a cluster it is wrong.

**No real concurrency.** One writer at a time, database-wide. The framework's optimistic locking
still behaves correctly; it is the throughput that does not exist.

**Uuids lose their hyphens.** `CHAR(32)`, because SQLAlchemy stores the hex form without native
uuid support. The values are the same uuids and round-trip correctly, but they do not look like
what PostgreSQL shows, and a hand-written query comparing against a hyphenated literal finds
nothing.

**Timestamps have no fractional-second guarantee** the way `DATETIME(6)` does, and the outbox's
only ordering key is a timestamp.

**Types are advisory.** SQLite will accept a string in an integer column, so a mapping bug that
PostgreSQL rejects loudly can pass here and fail in production. This is the reason SQLite passing
proves less than it looks like it proves - and the reason CI runs all three real dialects on
every push.

## Migration order

Nothing to order. SQLite has one namespace per file, no `CREATE SCHEMA`, no init script, and no
user to grant anything to. `ATTACH` is the nearest thing to a second schema and it is
per-connection rather than per-database, so leave every table unqualified.

```
migrations/
  V0001__eventlog.sql           # events + event_notifications
  V0002__scheduled_tasks.sql    # only if you run distributed jobs or in-process handlers
  V0003__wallets.sql            # your domain tables
```

Keep the files split anyway. The reason is not this database - it is that the first two move
when you upgrade the framework and the third when your domain does, which is still true here.

## Framework tables

Three, and only the first is always required. The DDL is generated rather than transcribed -
[Framework tables](tables.md) has it per dialect, and `emit_ddl(metadata, "sqlite")` prints it
for your own migration.

| Table | Needed when | Page |
|---|---|---|
| `events` | always | [`events`](tables/events.md) |
| `event_notifications` | you handle events in-process | [`event_notifications`](tables/event-notifications.md) |
| `scheduled_tasks` | you run distributed jobs, or in-process handlers | [`scheduled_tasks`](tables/scheduled-tasks.md) |

What the column helpers render here:

| helper | this dialect |
|---|---|
| `uuid_column()` | `CHAR(32)` |
| `json_column()` | `JSON` |
| `timestamp_column()` | `DATETIME` |

`CHAR(32)` is hex with the hyphens stripped, so SQLite is the one dialect whose ids are
not byte-comparable with the others'.

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
| `id` | `CHAR(32)` | primary key |
| `version` | `BIGINT NOT NULL` | optimistic locking - required, no default |
| `state` | `VARCHAR(24) NOT NULL` | soft-delete discriminator; `DELETED` rather than a `deleted_at` column |
| `created_date` | `DATETIME NOT NULL` | **required** when you build the row |
| `updated_date` | `DATETIME NOT NULL` | falls back to `created_date`; yours to move |

## What it is good for

- the inner loop, and the unit tests;
- an example that should run with no setup -
  [`wallet-service-uv-sqlite`](../../ekbatan-py-examples/wallet-service-uv-sqlite) is exactly that;
- a single-process tool or an internal utility that will never have a second instance.

For that last case it genuinely works: accept that the scheduler is not cluster-exclusive
because there is no cluster, and you have a working event-sourced service in one file with no
infrastructure. Keyed locks are the one thing it cannot give you on its own - SQLite has no
advisory locks, and there is no in-process provider - so use
`RedisKeyedLockProvider` if you need them (`AsyncRedisKeyedLockProvider` in async mode). Optimistic locking needs nothing and is always on.

## The claim this framework makes

That it works is a claim about **PostgreSQL, MySQL and MariaDB**. CI runs all three on every
push, plus PostgreSQL again under a non-UTC process. SQLite passing is necessary and not
sufficient.

## See also

- [Multiple dialects](multi-database.md) - where the four disagree
- [`wallet-service-uv-sqlite`](../../ekbatan-py-examples/wallet-service-uv-sqlite) - no container, no
  web stack
- [`wallet-service-uv-async-sqlite`](../../ekbatan-py-examples/wallet-service-uv-async-sqlite) - the
  same, async, on aiosqlite
