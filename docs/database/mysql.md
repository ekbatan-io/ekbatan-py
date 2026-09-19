# MySQL

Fully supported, with three things to know: there is no UUID type, there are no partial indexes,
and `GET_LOCK` does something surprising with sub-second timeouts.

## Quick reference

```
uv add ekbatan[mysql]
```

| | |
| --- | --- |
| driver | PyMySQL |
| url | `mysql+pymysql://user:pass@host:3306/db` |
| uuid | `CHAR(36)` |
| json | `JSON` |
| timestamps | `DATETIME(6)`, UTC |
| keyed lock | `MySQLKeyedLockProvider`, `GET_LOCK` |
| `SKIP LOCKED` | yes, 8.0+ |
| `RETURNING` | **no** |
| tested against | `mysql:8.4` |

## Migration order

MySQL has no schemas, so an `eventlog` namespace has to be its own **database**. The connecting
user usually only has rights on the main one, so the cross-database `GRANT`s have to come from an
init script that runs as root, before anything else connects.

```
mysql_init.sql              # root-only GRANTs, mounted into the container at startup
migrations/versions/
  0000_eventlog_database.py # CREATE DATABASE IF NOT EXISTS eventlog
  0001_eventlog.py          # events + event_notifications + their indexes
  0002_scheduled_tasks.py   # only if you run distributed jobs or in-process handlers
  0003_wallets.py           # your domain tables
```

All of that is only needed if you want the framework's tables in a namespace of their own. This
port defaults to the connected database, where none of it applies - see
[Schema or database](multi-database.md#schema-vs-database).

### `mysql_init.sql`

The container runs every `.sql` in `/docker-entrypoint-initdb.d/` as root before it reports ready.
That is the only place this can live: your application connects as the named user, and a user
cannot grant itself anything.

```sql
GRANT ALL PRIVILEGES ON *.* TO 'wallet'@'%';
FLUSH PRIVILEGES;
```

```yaml
services:
  wallet-db:
    image: mysql:8.4
    volumes:
      - ./mysql_init.sql:/docker-entrypoint-initdb.d/mysql_init.sql:ro
```

## Framework tables

Three, and only the first is always required. The DDL is generated rather than transcribed -
[Framework tables](tables.md) has it per dialect, and `emit_ddl(metadata, "mysql")` prints it
for your own revision.

| Table | Needed when | Page |
|---|---|---|
| `events` | always | [`events`](tables/events.md) |
| `event_notifications` | you handle events in-process | [`event_notifications`](tables/event-notifications.md) |
| `scheduled_tasks` | you run distributed jobs, or in-process handlers | [`scheduled_tasks`](tables/scheduled-tasks.md) |

What the column helpers render here:

| helper | this dialect |
|---|---|
| `uuid_column()` | `CHAR(36)` |
| `json_column()` | `JSON` |
| `timestamp_column()` | `DATETIME(6)` |

`DATETIME(6)`, never a bare `DATETIME`: the bare form truncates to whole seconds,
and `event_date` is the outbox's only ordering key.

## Domain tables

Your tables are yours. The repository requires two columns on every one of them and refuses at
construction if either is missing: **`version`**, the optimistic-locking counter, and
**`state`**, the soft-delete discriminator. A `ModelRepository` additionally wants
`created_date` and `updated_date`; an entity's table needs neither, because the framework does
not track timestamps for entities.

Reads filter `state != DELETED` for you. Updates carry `WHERE version = ?` and increment it.

| column | type | role |
|---|---|---|
| `id` | `CHAR(36)` | primary key |
| `version` | `BIGINT NOT NULL` | optimistic locking - required, no default |
| `state` | `VARCHAR(24) NOT NULL` | soft-delete discriminator; `DELETED` rather than a `deleted_at` column |
| `created_date` | `DATETIME(6) NOT NULL` | written once, on insert |
| `updated_date` | `DATETIME(6) NOT NULL` | bumped on every update, alongside `version` |

## MySQL-specific gotchas

**No native UUID.** `uuid_column()` renders `CHAR(36)` - the hyphenated text form, chosen so ids
stay grep-able in query logs and dumps, and so they match what PostgreSQL and MariaDB store as
text. `BINARY(16)` is more compact and harder to debug; it is not the default here or in Java.

**Java writes `CHARACTER SET ascii` on those columns and this port does not.** A uuid is pure
7-bit ASCII, so pinning the charset keeps each character at one byte rather than up to four under
`utf8mb4`, which tightens index locality. It is a storage optimisation, not a format difference -
interop is unaffected - but if you are writing the migration by hand, add it.

**`DATETIME(6)`, not `TIMESTAMP`.** `TIMESTAMP` has a smaller range, implicit
`ON UPDATE CURRENT_TIMESTAMP` behaviour and awkward `NULL` semantics. Reserve it for
db-scheduler's own table. And never bare `DATETIME` - it truncates to whole seconds, silently.

**No partial indexes.** Put the predicate column into the index instead:
`(delivered, event_type, event_date)`. This is what the framework emits everywhere, so on MySQL
the generated index is already the right shape.

**No `RETURNING`.** A write cannot read its own result back in one statement, so the framework
rebuilds written rows itself. That is why `add_no_result` and `update_no_result` exist: on a
large batch where you do not need the objects back, they skip work that is free on PostgreSQL and
is not free here.

**A sub-second lock wait is not a wait.** `GET_LOCK(name, 0.5)` returns **immediately** on MySQL
- it truncates the fraction to zero - while a 1.5-second wait is rounded *up* to two.
[Measured](keyed-locks.md#a-sub-second-wait-is-not-a-wait-on-mysql). A `try_acquire` with a
500 ms wait is a poll.

**`eventlog` would be a separate database.** Only if you want Java's layout. It needs a migration
to create it and an init script to grant rights - the grant cannot be in a migration, because the
migrator connects as the user that needs granting.

**One statement per `op.execute()`.** PyMySQL rejects a multi-statement script, so a revision
that works on PostgreSQL fails here with an error naming the syntax rather than the cause.

**Set the container timezone to UTC.** `TZ=UTC`, or `serverTimezone=UTC` on the url. Without it,
`DATETIME` columns shift silently between write and read.

## See also

- [MariaDB](mariadb.md) - close, and different in ways that matter
- [Framework tables](tables.md) · [Multiple dialects](multi-database.md)
- [`fastapi_wallet_rest_mysql`](../../ekbatan-py-examples/fastapi_wallet_rest_mysql)
