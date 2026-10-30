# MariaDB

Running the framework on MariaDB. It is close enough to MySQL to share a driver and most of this
page, and different in four ways that matter: the url scheme, the UUID type, how JSON is stored,
and lock timeouts.

## Quick reference

```
uv add "ekbatan[mariadb]"
```

| | |
| --- | --- |
| driver | PyMySQL |
| async driver | aiomysql - the `mariadb-async` extra, for `ekbatan.aio` |
| url | `mariadb+pymysql://host:3306/db` |
| login | `username=` and `password=`, never in the url |
| uuid | `CHAR(36)` here; MariaDB also has a native `UUID` |
| json | `JSON` - stored as `LONGTEXT` internally |
| timestamps | `DATETIME(6)`, UTC |
| keyed lock | `MariaDBKeyedLockProvider`, `GET_LOCK` |
| `SKIP LOCKED` | yes, 10.6+ |
| `RETURNING` | **no** (the framework does not use MariaDB's non-standard form) |
| tested against | `mariadb:11.4` |

## Framework tables

Three, and only the first is always required. Each table's page has its full DDL, the map is on
[Framework tables](tables.md), and `emit_ddl(metadata, "mariadb")` prints the DDL for
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

`JSON` here is `LONGTEXT` with a check constraint. So when something reads the table's
definition back from the database - reflection - a JSON column looks like text, and has to be
corrected: see [schemagen](schemagen.md).

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

## The scheme has to say `mariadb`

**Write the url as `mariadb+pymysql://`.** This is the trap, and it is not cosmetic: a
`mysql://` url connects to MariaDB too, and everything seems to work. But SQLAlchemy picks its
DDL, its version reporting and its `SKIP LOCKED` support from the url's **scheme**. Leave it as
`mysql` and you are testing MySQL behaviour against a MariaDB server, which is the worst of both.

Tools can hand you the wrong scheme. A MariaDB test container, for one, gives back a `mysql://`
url:

```python
MySqlContainer("mariadb:11.4").get_connection_url()   # -> mysql://test:test@localhost:59994/test
```

Rewrite such a url to `mariadb+pymysql://` before you use it.

## Migration order

The framework's two event tables go in a database of their own, `eventlog`, beside yours: on
MariaDB a schema *is* a database. Your first migration creates it, and the next puts the tables in
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

## MariaDB-specific gotchas

**Native UUID exists, and this framework does not emit it.** MariaDB 10.7 and later have a real
16-byte `UUID` type. SQLAlchemy does not render it, so `uuid_column()` gives `CHAR(36)`.

**A native `UUID` column works too.** In a table declared `id UUID`, the framework writes a uuid
and reads back the same value, and `CAST(id AS CHAR)` gives the usual hyphenated text. The
difference is 16 bytes a value instead of 36.

**`JSON` is `LONGTEXT` underneath.** MariaDB has no separate JSON storage: a `JSON` column is
`LONGTEXT` with a CHECK constraint, and the driver reports it that way. You do not notice when you
*declare* the column with `json_column()`. You do when something **reflects** it: a reflected JSON
column comes back as text.
[`schemagen`](schemagen.md) forces it back to JSON, and that forcing is the reason it
exists.

It recognises a JSON column by its check, `json_valid(...)`. Reflection does not report that
check, so it is read from `information_schema.CHECK_CONSTRAINTS` instead. A rule that matches on
the type name alone never matches here.

**A reflected bigint carries a display width.** MariaDB reports `BIGINT(display_width=20)`.
Written as it is into a generated module, it does not even import (`TypeError: BIGINT() takes no
arguments`). `schemagen` removes the width.

**`GET_LOCK` honours sub-second timeouts here**, unlike MySQL, which rounds them to whole
seconds. That is why MariaDB has a provider class of its own, although it sends the same
statements as MySQL's. [The measurements](keyed-locks.md#mysql-rounds-the-wait-to-whole-seconds).

**No partial indexes**, as on MySQL. The column you would filter on goes first in a composite
index.

**`DATETIME(6)`, not `TIMESTAMP`**, and not bare `DATETIME`. Same reasoning as MySQL.

**One schema change per migration file**, and **`TZ=UTC` on the server**. Same as MySQL.

## See also

- [MySQL](mysql.md) - most of this, with the differences called out
- [schemagen](schemagen.md) - the normalisation that makes reflection usable here
- [`fastapi-wallet-rest-uv-mariadb`](../../ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb)
