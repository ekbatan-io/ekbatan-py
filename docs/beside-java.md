# Running beside a Java service

Ekbatan also exists for Java. A Python service built with this framework and a Java service built with Ekbatan for Java can share one database, one Redis and one stream of events. Everything that has to line up for that is on this page.

**If every service you run is written in Python, you can skip this page.** Nothing here changes
how the framework behaves on its own.

The Java framework is [Ekbatan](https://github.com/ekbatan-io/ekbatan), documented at
[ekbatan-io.github.io/ekbatan](https://ekbatan-io.github.io/ekbatan/). The two are built so that
both can work on the same data:

| shared | what lines up |
| --- | --- |
| [the framework's tables](#the-shared-tables) | `events`, `event_notifications`, `scheduled_tasks`: same columns, same values |
| [ids](#ids) | the same uuid text, the same shard bits, the same sort order |
| [event payloads](#event-payloads) | the same JSON, byte for byte |
| [keyed locks](#keyed-locks) | the same lock, on Redis and on the SQL servers |
| [streaming](#streaming-the-events) | the same Debezium transforms, reading the same rows |
| [traces and logs](#traces-and-logs) | the same span, metric and logger names |
| [the config file](#the-config-file) | the same keys under `ekbatan` |

## What to set

Three things. The first is the one people miss.

1. **The schema.** The Java framework keeps its tables in a schema called `eventlog`. This
   framework puts them in no schema unless told to, because a Python service usually owns its
   database outright. To share, pass the schema to each table:

   ```python
   EVENTS = outbox_table(metadata, schema="eventlog")
   EVENT_NOTIFICATIONS = notifications_table(metadata, schema="eventlog")
   ```

   On PostgreSQL `eventlog` is a schema in the same database. On MySQL and MariaDB it is a
   separate database, and the connecting user needs a grant on it - see
   [Multi-database](database/multi-database.md). A Debezium connector then lists
   `eventlog.events` rather than `public.events`.
2. **The namespace.** One service, written in two languages, uses one namespace. Different
   services use different ones.
3. **The shard layout.** Both sides must agree on which group and member numbers mean which
   database, because a sharded id carries those numbers. One `ekbatan.sharding` section can
   describe both - see [the config file](#the-config-file).

And keep time in UTC on both sides, as [Multi-database](database/multi-database.md#always-utc)
recommends. The Java framework's `InstantConverter` (wired per project through jOOQ's forced
types) writes an `Instant` as its UTC time in a column with no zone, and reads it back as UTC.
The Python framework writes whatever time it is given, as it is
([What you give is what is stored](database/multi-database.md#what-you-give-is-what-is-stored)), and its default
clock gives the UTC time with no zone attached - the same value Java writes. So a time written
by one reads back as the same moment in the other, as long as the Python side keeps to naive
UTC.

## The shared tables

These were compared by creating both versions of each table and diffing them, not by reading.

### `events`

| | the same in both? |
| --- | --- |
| column names, order, nullability | **yes** |
| PostgreSQL types | **yes** |
| MySQL types | **yes** (see below) |
| MariaDB `id`, `action_id` | **no**: `UUID` in Java, `CHAR(36)` here (see below) |
| schema | **no, unless you set it**: pass `schema="eventlog"` |
| index names | **no**: `idx_events_action_id` in Java, `ix_events_action_id` here - only a name |

**On MySQL** the Java migration adds `CHARACTER SET ascii` to the uuid columns. That makes the
indexes smaller; the text stored is the same.

**MariaDB's native `UUID` does not get in the way, and that was checked.** Against a table
created by the Java migration, this framework writes a uuid and reads back the same value, and
`CAST(id AS CHAR)` gives the usual hyphenated text. Either table works with either service;
whichever service migrates first decides how the value is stored - 16 bytes, or 36.

The indexes are yours either way: both frameworks declare the table's columns and leave the
indexes to your migration - see [`events`](database/tables/events.md#indexes).

### `event_notifications`

The same columns. The Java migration gives `attempts` a `DEFAULT 0`; this framework always writes
the column, so the default is never used. It matters only if you insert rows by hand.

### `scheduled_tasks`

This is [db-scheduler](https://github.com/kagkarlsson/db-scheduler)'s own table, and both
frameworks keep it as db-scheduler wrote it. The Python scheduler, `ekbatan-py-db-scheduler`, is
a port of db-scheduler and reads and writes the table the same way - so a Java worker and a
Python worker take turns at the same executions.

Two differences, both on PostgreSQL and neither a problem:

- **The time columns are `timestamptz` in the Java migration** and `TIMESTAMP` here. The
  Python scheduler stores its times as given and its default clock gives naive UTC, which is
  right for `TIMESTAMP`. On Java's `timestamptz` table, give the Python scheduler a clock that
  gives aware UTC - one line, shown in
  [`scheduled_tasks`](database/tables/scheduled-tasks.md#if-the-time-columns-are-timestamptz). Without it
  the Python worker logs `TypeError` comparing naive and aware times.
- **Index names and order.** Java names them `execution_time_idx`, `last_heartbeat_idx` and
  `priority_execution_time_idx`, with the priority index `(priority DESC, execution_time ASC)`.
  This framework puts the table name in front of each and gives the priority index no direction.
  Both tables hold the same rows and work with both schedulers; the priority index only matters
  when priority ordering is turned on.

### Migrations

The migrator keeps its history in its own table, `ekbatan_schema_history`, and does not read a
Java service's migration history. Let one service own the migrations of a shared database.

## Ids

**A uuid is stored as hyphenated text** - `CHAR(36)` on MySQL and MariaDB, the native type on
PostgreSQL - which is what the Java framework writes too. Left to itself, SQLAlchemy writes a uuid
on MySQL as `CHAR(32)`, the hex with the hyphens taken out. A Java service writing
`d5143465-787a-...` and a Python service writing `d5143465787a...` could not join on `model_id`,
**and neither would notice**: both queries succeed and return nothing. `uuid_column()` avoids
that.

**A sharded id carries its shard in the same bits** on both sides (the layout is on
[Sharding](database/sharding.md#self-describing-ids-shardedid)), so either service routes the
other's rows without a lookup.

**Ids sort the same way.** Comparing two ids in Python (`<`, `sorted`) gives the order the Java
framework gives: each half of the uuid is read as a signed number. Version 7 ids sort by time
either way. A random `Id[...]` whose first digit is `8` to `f` sorts before one starting `0` to
`7` - in both languages, though not in the database, which compares them unsigned.

## Event payloads

The `payload` column holds the same text, byte for byte, that the Java framework writes for the
same event - checked over 50,000 values. So a Java handler reads an event a Python action wrote,
and the other way round.

- **Keys are camelCase**, in the order the fields are declared: `new_balance` is written
  `newBalance`.
- **`modelId` and `modelName` come first.** The Java handler builds the event straight from the
  payload and refuses one without them.
- **`event_type` is the event class's bare name**, `MoneyDepositedEvent`. A consumer routes on
  it, so give an event the same class name on both sides.
- **Each value is written in the form the matching Java type uses**:

| Python value | Java type | written as |
| --- | --- | --- |
| `Decimal` | `BigDecimal` | a JSON number: `10.50`, `1E+3` |
| `float` | `double` | a number: `0.1`, `1.0E20` |
| aware UTC `datetime` | `Instant` | `"2025-01-01T00:00:00.120Z"` |
| `datetime` with another offset | `OffsetDateTime` | `"2025-01-01T02:00:00+02:00"` |
| `datetime` with no zone | `LocalDateTime` | `"2025-01-01T10:00:00"` |

A `datetime` is written as it is, so a time straight from the default clock - no zone attached -
is a `LocalDateTime` to Java. Where the Java event declares an `Instant`, give the event an aware
UTC value: `clock.now().replace(tzinfo=UTC)`, in your own code.
| `date`, `time`, `timedelta` | `LocalDate`, `LocalTime`, `Duration` | `"2025-01-01"`, `"10:00:00"`, `"PT1.5S"` |
| `bytes` | `byte[]` | base64: `"AQL/"` |
| an `Enum` | an enum | the member's name |
| `Id`, `ShardedId` | `Id`, `ShardedId` | `{"id": "...", "value": "..."}` |

One database bends numbers for both sides alike: MySQL's `JSON` column keeps a number with a
fraction as a double, so `50.00` comes back `50.0` whichever service wrote it.

The full rules for writing and reading a payload are on
[Models and entities](concepts/models-and-entities.md#modelevent).

## Keyed locks

**On Redis**, the lock is stored the way Redisson - the Redis client the Java framework's lock is
built on - stores it: a hash at `ekbatan-lock:<key>` with one field per holder, the expiry set to
`max_hold`, and a `0` published on `redisson_lock__channel:{ekbatan-lock:<key>}` on release. So a
Java service and a Python service on one Redis keep each other out, and a release on one side
wakes a waiter on the other at once.

**On PostgreSQL, MySQL and MariaDB**, each key is hashed to a 64-bit number, and the hash is the
one the Java framework computes, so two services asking for one key really wait on each other. On
MySQL and MariaDB the lock's name is that hash as bare unsigned hex (`813e4046e5b48a46` for
`wallet:abc`); a test holds a lock and checks the server shows the name the Java framework uses.
Both send MySQL the same wait, so its
[rounding to whole seconds](database/keyed-locks.md#mysql-rounds-the-wait-to-whole-seconds) is
the same on both sides too.

## Streaming the events

Debezium and Kafka Connect are Java programs, whatever language wrote the rows. The two
transforms, `OutboxToAvroTransform` and `OutboxToProtobufTransform`, are JARs from the Java
project's `ekbatan-debezium-smt` artifact. A Python service uses the same JARs, pointed at its
`events` table, and gets the same messages on the same topics. Nothing in your own service runs
Java.

The JARs are not on a package index. Take them from the Java project's build and put each in its
own directory under the Connect worker's `plugin.path`. Their full settings are documented on
[the Java project's streaming page](https://ekbatan-io.github.io/ekbatan/reference/events/streaming/).

Because the payload JSON is the same text, an Avro or Protobuf schema written for one producer
encodes the other's rows unchanged, and a consumer in either language reads both. The rest of
the setup is on [Streaming](events/event-streaming.md).

## Traces and logs

**The span, attribute and metric names are the same** - `ekbatan.action.name` and the rest - so a
dashboard or an alert written for one service covers the other. The instrumentation scope is
`io.ekbatan.core` on both sides (each reports its own version), and the meter is
`io.ekbatan.events.localeventhandler`.

**Logger names are the Java class names** that log the same lines, such as
`io.ekbatan.core.action.ActionExecutor`. That is why they do not look like Python module paths: a
logging setting written for the Java service - `io.ekbatan.core.action: DEBUG`, or
`io.ekbatan: WARN` - applies here unchanged. The full list is on
[Observability](runtime/observability.md#loggers).

While a migration runs, its log lines carry `ekbatanShard` and `ekbatanTarget`, the same keys the
Java migrator puts in its logging context. Write `%(ekbatanShard)s` in a format string where the
Java pattern writes `%X{ekbatanShard}`.

## The config file

The `ekbatan` section of `ekbatan.yaml` uses the same keys as the Java framework's
`application.yml`, so one shard layout can describe both:

- keys in kebab-case or camelCase alike (`default-shard` or `defaultShard`);
- durations as ISO-8601 text, such as `PT10S` or `PT5M`, which the Java side reads too;
- `${DB_PASSWORD}` and `${DB_PORT:5432}` for environment variables, a syntax both sides read.

The details are on [Config files](database/config-files.md).

## See also

- [Framework tables](database/tables.md) - each shared table in full
- [Sharding](database/sharding.md) - the id layout
- [Keyed locks](database/keyed-locks.md) - the providers
- [Streaming](events/event-streaming.md) - Debezium and the topics
