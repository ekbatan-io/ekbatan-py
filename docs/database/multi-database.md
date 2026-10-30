# Multiple dialects

PostgreSQL, MySQL and MariaDB are supported. SQLite is supported for tests and is **not** a
production dialect - see [SQLite](sqlite.md) for exactly why. This page is what differs between
the databases, and what to write so the same code runs on each.

Most of the framework works the same on every database. The differences sit in one place: **the
column types in your migrations.** Get those right and the rest follows. Three column helpers do
that work.

## Column-type cheatsheet

Three helpers cover the columns whose type differs between databases. Each is a SQLAlchemy type
that already knows how to render itself on each database, so you pick the helper, not the
database.

```python
from ekbatan.schema import json_column, timestamp_column, uuid_column
```

| helper | PostgreSQL | MySQL | MariaDB | SQLite |
| --- | --- | --- | --- | --- |
| `uuid_column()` | `UUID` | `CHAR(36)` | `CHAR(36)` | `CHAR(32)` |
| `timestamp_column()` | `TIMESTAMP WITHOUT TIME ZONE` | `DATETIME(6)` | `DATETIME(6)` | `DATETIME` |
| `json_column()` | `JSONB` | `JSON` | `JSON` | `JSON` |

Everything else is ordinary SQLAlchemy - `String(n)`, `Numeric(p, s)`, `Integer`, `BigInteger`,
`Boolean` - and already renders correctly everywhere.

In a SQL migration, write the type from this table. Where you declare a table in Python - the
metadata you give `emit_ddl`, or a `schema.py` you write yourself - use the helper rather than a
raw type. [`schemagen`](schemagen.md) also turns the columns it reads back into these helpers.
That is what lets a schema generated on one database work on another.

### Why MySQL needs `CHARACTER SET ascii` on uuid columns

A uuid written as text is 36 plain ASCII characters: 32 hex digits and four hyphens. Under
MySQL's default character set, `utf8mb4`, a `CHAR(36)` reserves **four bytes per character** -
144 bytes per index entry where 36 would do. On a primary key that is read on every request, that
is four times the index pages to read and to keep in memory, for nothing.

`uuid_column()` does not set the character set, because that belongs to your table's DDL, not to
a column type. Set it in the migration:

```sql
id CHAR(36) CHARACTER SET ascii NOT NULL
```

MariaDB has the same default and needs the same fix. PostgreSQL's native `UUID` is 16 bytes and
has no character set to get wrong. MariaDB 10.7+ has a native `UUID` too, but this framework still
writes `CHAR(36)` there - see below.

### Why MySQL uuid columns are `CHAR(36)`-shaped, not `BINARY(16)`

`BINARY(16)` is half the size, and the obvious optimisation. This framework does not use it,
because the outbox is read by more than this framework: `psql` at a prompt, a Debezium connector,
a service in another language on the same database, a support engineer. A `BINARY(16)`
`model_id` is unreadable to every one of them, and cannot be joined to any column that is not
also `BINARY(16)`.

The same reason rules out MariaDB's native `UUID` type. It is stored as 16 bytes, reads back as a
type MySQL does not have, and produces rows that a MySQL replica of the same schema cannot
receive.

SQLAlchemy's own default, on a database with no uuid type, is `CHAR(32)`: the hex digits with the
hyphens stripped. This framework overrides that on MySQL and MariaDB, to avoid a quiet bug. Say one
writer stores `d5143465-787a-...` and another stores `d5143465787a...`. A join on `model_id` then
finds nothing, **and neither notices**, because both queries succeed. The hyphenated text is also
what a Java service writes into its events - see
[Running beside a Java service](../beside-java.md#ids).

### Why MariaDB `JSON` needs the type forced

MariaDB stores `JSON` as `LONGTEXT` with a `CHECK` constraint, and its driver reports it that way.
`CREATE TABLE ... JSON` works, but reading the schema back gives you `LONGTEXT`.

This matters only to a tool that reads the schema back. A column you declare as `json_column()` in
your own `schema.py` tells SQLAlchemy its type, so it is not affected. But
[`schemagen`](schemagen.md) reads a real database. On MariaDB it would write `Text` for a column
you declared as JSON, and the generated module would then bind strings where the framework expects
objects.

So `schemagen` does not trust what it reads there. It finds the column's `json_valid` check in
`information_schema` and writes `json_column()`. The same step fixes `BIGINT(display_width=20)`,
which MariaDB reports for a plain bigint and which does not even import. MySQL reports `JSON`
correctly and needs neither fix.

### One table for every dialect

You never choose column types by database in a repository. Each helper carries its per-database
types inside it, through SQLAlchemy's `with_variant()`. `json_column()`, for example, is:

```python
JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
```

`uuid_column()` is SQLAlchemy's `Uuid` with a `CHAR(36)` variant for MySQL and MariaDB. So one
`Table` and one repository serve every database: SQLAlchemy picks the type when it builds each
statement. That is why this page is a short cheatsheet, and not a pattern to repeat in every
repository.

## Always UTC

### What you give is what is stored

**Ekbatan never converts a time.** A `datetime` goes to the database exactly as your code or your
clock gave it, and comes back exactly as the driver returns it. No zone is added, removed or moved
anywhere: not in models, repositories, the outbox, notifications, event JSON or
`timestamp_column()`. Getting time right is your job. The rest of this section shows how.

```python
from ekbatan import SystemClock

now = SystemClock().instant()          # datetime(2026, 10, 19, 8, 30, 0, 123456) - UTC, no zone
wallet = wallet_repository.add(Wallet.create_wallet(..., now).build())
wallet_repository.get_by_id(wallet.id.value).created_date == now   # True: the same value, to the microsecond
```

The default clock gives the current **UTC time with no zone attached**. That is exactly what a
zone-less column holds. So a value reads back equal to what was written, and can be compared with
the next `clock.instant()`. The framework makes that comparison itself, for event retries, due
times and expiry.

A value *with* a zone is passed on as it is, and then the driver decides what is stored. Measured
with `13:30+03:30` written into a zone-less column:

| driver | stored |
| --- | --- |
| psycopg (sync and async) | moved to the session's zone by the server: `10:00` with the session in UTC |
| asyncpg | refused - `DataError` |
| PyMySQL, aiomysql (MySQL, MariaDB) | `13:30`: the digits kept, the offset dropped |
| SQLite (pysqlite, aiosqlite) | `13:30`: the digits kept, the offset dropped |

So keep to naive UTC - no zone attached - as the default clock gives. If you do use aware values,
with a zone - your own `Clock`, `TIMESTAMPTZ` columns - keep them aware everywhere. The framework
compares what your clock gives with what the database returns, and Python refuses to compare a
naive `datetime` with an aware one.

### Time kinds

Python has one `datetime` class for three different kinds of time. `ekbatan.time` gives each kind a
name, so a type checker keeps them apart and an event's JSON says which kind a reader gets:

| meaning | Python label | PostgreSQL column | JSON |
| --- | --- | --- | --- |
| a moment | `Instant` - no zone attached, UTC digits | `TIMESTAMP` | `"2026-03-01T09:30:00.120Z"` |
| a wall date and time | `LocalDateTime` - no zone attached | `TIMESTAMP` | `"2026-03-01T09:30:00.12"` |
| a moment with its offset | `OffsetDateTime` - zone attached | `TIMESTAMPTZ` | `"2026-03-01T13:00:00.12+03:30"` |
| a time of day | `time` | `TIME` | `"09:30:00"` |
| a date | `date` | `DATE` | `"2026-03-01"` |

`Instant` and `LocalDateTime` use the same zone-less column; only the label differs. On MySQL and
MariaDB that column is `DATETIME(6)`. Neither database has a column that keeps an offset.

```python
from dataclasses import dataclass
from datetime import datetime

from ekbatan import ModelEvent
from ekbatan.time import Instant, LocalDateTime


@dataclass(frozen=True, kw_only=True)
class ShiftPlannedEvent(ModelEvent["Shift"]):
    planned_at: Instant        # when it was planned: a moment
    opens_at: LocalDateTime    # "09:00 on 1 March", wherever the shop is


event = ShiftPlannedEvent(
    model_id=str(shift.id),
    planned_at=self.clock.instant(),                         # the clock gives an Instant
    opens_at=LocalDateTime(datetime(2026, 3, 1, 9, 0)),
)
```

- **The labels cost nothing.** They are `typing.NewType`s: a value is a plain `datetime`, and
  `Instant(x)` hands back `x` - it converts nothing and checks nothing. Arithmetic keeps the
  label: `self.clock.instant() + timedelta(days=1)` is still an `Instant`.
- **mypy keeps them apart.** An `Instant` passed where a `LocalDateTime` is expected is an error,
  and so is a plain `datetime` where an `Instant` is expected. Write `Instant(value)` once, where
  the value comes in, to say what it is.
- **Where they come from.** `clock.instant()` (and `self.clock.instant()` in an action) gives an
  `Instant`. A model's `created_date` and `updated_date` are `Instant`s, and so is every time the
  framework writes - the outbox, notifications, retries, jobs. A generated record says which kind
  each column is ([schemagen](schemagen.md#time-columns)).
- **In JSON, the label decides.** In an event, or in an action's params, a `datetime` field is
  written as the kind it is declared, and read back as the same kind: an `Instant` with `Z`, a
  `LocalDateTime` with no zone, an `OffsetDateTime` with its offset. Nothing is converted: an
  `Instant`'s digits are already UTC's and are written as they are.
- **A plain `datetime` field is refused there**, and so is a value of the wrong sort for its kind
  (a zone attached in an `Instant` field). The action fails when it runs, and nothing is saved:

  ```text
  event ShiftPlannedEvent field planned_at is a plain datetime; declare it Instant,
  LocalDateTime or OffsetDateTime (ekbatan.time)
  ```
- **Your own JSON columns keep what you give them.** A `datetime` inside a `dict` written to a
  `json_column()` of your own is written as it is - no zone for a naive one, its offset (`Z` at
  zero) for an aware one - and never refused. The refusal above is only for event payloads and
  action params, whose reader goes by the declared kind. If you write a dataclass with a field
  declared `Instant`, `LocalDateTime` or `OffsetDateTime` into your own column, that field is
  still written as its kind.
- **A union of two kinds is refused.** `Instant | LocalDateTime` fits any `datetime`, so it says
  nothing about which one a value is: the message is `declared as more than one kind (Instant |
  LocalDateTime); pick one`. `Instant | None` is fine.
- **Ruff.** Ruff's `RUF009` flags `Instant(...)` in a dataclass default as a call. The labels hand
  back the value they are given, so tell Ruff they are immutable, in `pyproject.toml`:

  ```toml
  [tool.ruff.lint.flake8-bugbear]
  extend-immutable-calls = [
    "ekbatan.time.Instant",
    "ekbatan.time.LocalDateTime",
    "ekbatan.time.OffsetDateTime",
  ]
  ```

### The recommendation

UTC everywhere is **strongly recommended**. Nothing enforces it - nothing refuses another zone -
but leaving it is where time bugs come from. It has three parts:

1. **In your code, UTC.** Take the time from the framework's clock: `self.clock.instant()` in an
   action, `clock.instant()` elsewhere. It gives UTC with no zone attached. Not `datetime.now()`,
   which is local time. Convert to a person's local time only where you show it to them.
2. **In the columns, no zone.** `TIMESTAMP` (`WITHOUT TIME ZONE`) on PostgreSQL, `DATETIME(6)` on
   MySQL and MariaDB, holding UTC. `timestamp_column()` builds this: a column type and nothing
   more, with microsecond precision on every database.
   - **Not `TIMESTAMPTZ`.** It turns the session's zone into part of every write and read: two
     readers with different session zones disagree about when the thing happened.
   - **Not bare `DATETIME`** on MySQL and MariaDB. It silently cuts to whole seconds, and the
     outbox's only ordering key is a timestamp.
3. **On the database, UTC.** A naive value is stored as written, whatever the session's zone -
   measured on all three databases, with both drivers of each. The database's **own** clock is
   another matter: `NOW()`, `CURRENT_TIMESTAMP` and a column `DEFAULT` follow the session's zone.
   SQL that mixes those with your columns is off by the zone's offset. So run the server in UTC
   (`TZ=UTC` on the container; `timezone = 'UTC'` on PostgreSQL), or set the zone per connection
   in `data_source_properties`:

   ```python
   # PostgreSQL
   DataSourceConfig(url=..., data_source_properties={"options": "-c TimeZone=UTC"})
   # MySQL and MariaDB
   DataSourceConfig(url=..., data_source_properties={"init_command": "SET time_zone = '+00:00'"})
   ```

   Both were measured against a server set to `Asia/Tehran`: the session then reports UTC. The
   same lines work unchanged for `ekbatan.aio`'s drivers. aiomysql reads `init_command` as PyMySQL
   does. asyncpg has no `options` setting, so the framework hands it the `-c` settings as asyncpg's
   own `server_settings`.

One framework table can break this rule without you noticing: `scheduled_tasks`, the table behind
distributed jobs, whose shape is db-scheduler's. Its helper builds zone-less columns, but a table
created by another migration may have `timestamptz` ones on PostgreSQL. See
[that table's page](tables/scheduled-tasks.md) for the measured consequence, and what to do.

## Schema vs database

The framework's two event tables, `events` and `event_notifications`, always live in a schema
called `eventlog`. What a schema is depends on the database.

**On PostgreSQL** a schema is a namespace **inside** the database you connect to, so the tables
are `eventlog.events` and `eventlog.event_notifications`. Your first migration creates it with
`CREATE SCHEMA IF NOT EXISTS eventlog;`, and the connecting user normally has the right to do that.
Every database - so every shard - has its own.

**On MySQL and MariaDB** "schema" and "database" are the same thing. So `eventlog` is a **separate
database** beside yours, and the tables are `eventlog.events` and `eventlog.event_notifications`
there too. Two things follow:

1. Your first migration creates it, in a file of its own before the tables:
   `CREATE DATABASE IF NOT EXISTS eventlog;` (`V0000__create_eventlog_database.sql`). It runs on
   your database's connection, so the login needs the right to create `eventlog` and to use it.
   Root has that right. A login of its own gets it from root, once, with
   `GRANT ALL PRIVILEGES ON eventlog.* TO 'wallet'@'%';`.
2. There is one `eventlog` for the whole server, shared by every database on it. Two shards, or two
   applications, on one server write into the same two tables. The
   [fan-out job](../events/local-event-handler.md) picks rows by event type alone, so where two
   applications use one event name, each one's handlers get the other's events too. To keep shards
   apart, give each one a server of its own.

**On SQLite**, which has no schemas, the two tables are plain `events` and `event_notifications`
in the database file. Ekbatan maps `eventlog` onto the file by itself.

On every database, you never tell the framework where these tables are. The parts that use them -
the executor's default event writer, the fan-out job and the dispatch job - name them themselves.
See [Framework tables](tables.md).

## Partial indexes (PostgreSQL only)

PostgreSQL can index only the rows a `WHERE` picks:

```sql
CREATE INDEX ix_events_undelivered ON eventlog.events (event_type, event_date)
    WHERE delivered = FALSE;
```

MySQL and MariaDB cannot. The portable form puts the column the `WHERE` tests into the index
instead:

```sql
CREATE INDEX ix_events_undelivered ON eventlog.events (delivered, event_type, event_date);
```

The framework's table helpers declare no indexes on the event tables, so this choice is yours, in
your migration. On PostgreSQL use the partial form. On a large outbox the portable one is far
bigger - [measured at 63x](tables/events.md#the-undelivered-index-and-what-it-costs) - and every
insert pays for it. The examples use the partial form on PostgreSQL.

## Why each dialect needs what it needs

What differs between the four databases, and what the framework does about each. Every row was
found by running against a real database, not by reading documentation.

| | |
| --- | --- |
| **`SKIP LOCKED`** | PostgreSQL, MySQL 8+ and MariaDB 10.6+ have it. SQLite does not, which is the main reason it is not a production dialect |
| **`RETURNING`** | PostgreSQL and SQLite support it on writes. MySQL and MariaDB do not, so the framework rebuilds written rows itself there |
| **`GET_LOCK` sub-second timeouts** | MySQL rounds them to whole seconds; MariaDB honours them. [Measured](keyed-locks.md#mysql-rounds-the-wait-to-whole-seconds) |
| **Native UUID** | PostgreSQL always; MariaDB 10.7+; MySQL never |
| **Reflected type names** | MariaDB reports a bigint as `BIGINT(display_width=20)` and JSON as `LONGTEXT`. [`schemagen`](schemagen.md) corrects both |
| **Untyped `NULL` in a multi-row `VALUES`** | PostgreSQL infers `text`, then refuses to insert it into an integer column. The framework casts every NULL explicitly |
| **Schema changes in a transaction** | PostgreSQL and SQLite roll a failed migration back as a whole. MySQL and MariaDB commit each schema change on its own, so keep one per migration file - see [Migrations](migrations.md#applying-and-recording) |

## Adding a new database

Only the databases on this page are supported. Supporting another takes a change to the framework
itself: a driver SQLAlchemy supports, a keyed-lock provider, and the three column helpers taught to
render on it. The framework picks its lock provider and its per-database behaviour from
`engine.dialect.name`.

## See also

- [PostgreSQL](postgresql.md) · [MySQL](mysql.md) · [MariaDB](mariadb.md) · [SQLite](sqlite.md)
- [Framework tables](tables.md) - the DDL, per database
- [schemagen](schemagen.md) - how it reads your database back, and which types it corrects
