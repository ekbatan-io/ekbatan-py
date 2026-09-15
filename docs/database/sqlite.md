# SQLite

**No Java counterpart - SQLite is not a dialect there at all.**

It is supported here, it is what the test suite runs on by default, and it is **not a production
dialect**. This page is mostly about why both halves of that are true.

## Quick reference

| | |
| --- | --- |
| driver | the standard library's `sqlite3` |
| url | `sqlite:///path/to.db`, or `sqlite://` for memory |
| extra | none needed |
| uuid | `CHAR(32)` - hex, no hyphens |
| json | `JSON` - text, with SQLAlchemy doing the encoding |
| timestamps | `DATETIME` |
| keyed lock | `InProcessKeyedLockProvider` only |
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

## What it is good for

- the inner loop, and the unit tests;
- an example that should run with no setup -
  [`wallet_service_sqlite`](../../ekbatan-py-examples/wallet_service_sqlite) is exactly that;
- a single-process tool or an internal utility that will never have a second instance.

For that last case it genuinely works. Use `InProcessKeyedLockProvider`, accept that the
scheduler is not cluster-exclusive because there is no cluster, and you have a working
event-sourced service in one file with no infrastructure.

## The claim this framework makes

That it works is a claim about **PostgreSQL, MySQL and MariaDB**. CI runs all three on every
push, plus PostgreSQL again under a non-UTC process. SQLite passing is necessary and not
sufficient.

## See also

- [Multiple dialects](multi-database.md) - where the four disagree
- [`wallet_service_sqlite`](../../ekbatan-py-examples/wallet_service_sqlite) - no container, no
  web stack
