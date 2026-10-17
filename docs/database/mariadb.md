# MariaDB

Close enough to MySQL to share a driver and most of a page, and different in four ways that have
each cost real time.

## Quick reference

```
uv add "ekbatan[mariadb]"
```

| | |
| --- | --- |
| driver | PyMySQL |
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

Three, and only the first is always required. The DDL is generated rather than transcribed -
[Framework tables](tables.md) has it per dialect, and `emit_ddl(metadata, "mariadb")` prints it
for your own migration.

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

`JSON` here is `LONGTEXT` with a check constraint, which is why reflection has to be
corrected rather than trusted - see [schemagen](schemagen.md).

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
| `id` | `CHAR(36)` | primary key |
| `version` | `BIGINT NOT NULL` | optimistic locking - required, no default |
| `state` | `VARCHAR(24) NOT NULL` | soft-delete discriminator; `DELETED` rather than a `deleted_at` column |
| `created_date` | `DATETIME(6) NOT NULL` | **required** when you build the row |
| `updated_date` | `DATETIME(6) NOT NULL` | falls back to `created_date`; yours to move |

## The scheme has to say `mariadb`

**This is the trap, and it is not cosmetic.** A MariaDB container hands back a `mysql://` url:

```python
MySqlContainer("mariadb:11.4").get_connection_url()   # -> mysql://test:test@localhost:59994/test
```

SQLAlchemy picks its DDL, its version reporting and its `SKIP LOCKED` support off the **scheme**,
and this framework picks its keyed-lock provider off `engine.dialect.name`. Leave it as `mysql`
and everything works - while testing MySQL behaviour against a MariaDB server, which is the worst
of both outcomes.

Rewrite it to `mariadb+pymysql://`. The test suite does, and the one time it did not, "the
MariaDB suite" was exercising the MySQL code path.

## Migration order

MariaDB has no schemas, so an `eventlog` namespace has to be its own **database**. The connecting
user usually only has rights on the main one, so the cross-database `GRANT`s have to come from an
init script that runs as root, before anything else connects.

```
mariadb_init.sql              # root-only GRANTs, mounted into the container at startup
migrations/
  V0000__eventlog_database.sql  # CREATE DATABASE IF NOT EXISTS eventlog
  V0001__eventlog.sql           # events + event_notifications + their indexes
  V0002__scheduled_tasks.sql    # only if you run distributed jobs or in-process handlers
  V0003__wallets.sql            # your domain tables
```

All of that is only needed if you want the framework's tables in a namespace of their own. This
port defaults to the connected database, where none of it applies - see
[Schema or database](multi-database.md#schema-vs-database).

### `mariadb_init.sql`

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
    image: mariadb:11.4
    volumes:
      - ./mariadb_init.sql:/docker-entrypoint-initdb.d/mariadb_init.sql:ro
```

## MariaDB-specific gotchas

**Native UUID exists, and this port does not emit it.** MariaDB 10.7+ has a real 16-byte `UUID`
type; Java's DDL uses it. SQLAlchemy does not render it, so `uuid_column()` gives `CHAR(36)`.

**Interop is unaffected, and that was checked rather than assumed.** Against a table created with
Java's `id UUID` DDL, this port writes a uuid and reads back an identical value, and
`CAST(id AS CHAR)` gives the canonical hyphenated text. Either DDL works with either port. The
difference is 16 bytes against 36.

**`JSON` is `LONGTEXT` underneath.** MariaDB has no separate JSON storage - it is `LONGTEXT` with
a CHECK constraint, and the driver reports it that way. That is invisible when you *declare* the
column with `json_column()`, and very visible when something **reflects** it: a reflected JSON
column comes back as text. [`schemagen`](schemagen.md) forces it back, and that forcing is the
reason it exists.

Java has the same problem and solves it the same way, with a jOOQ `ForcedType` carrying
`withName("JSON")`.

**A reflected bigint carries a display width.** MariaDB reports `BIGINT(display_width=20)`.
Emitted verbatim into a generated module, it does not even import - `TypeError: BIGINT() takes no
arguments`. `schemagen` normalises it.

**`GET_LOCK` honours sub-second timeouts here** - unlike MySQL, which rounds them to whole seconds.
That is why there is a separate provider class for a backend that issues identical statements.
[The measurements](keyed-locks.md#mysql-rounds-the-wait-to-whole-seconds).

**No partial indexes**, same as MySQL. The predicate column goes in the index.

**`DATETIME(6)`, not `TIMESTAMP`**, and not bare `DATETIME`. Same reasoning as MySQL.

**One schema change per migration file**, and **`TZ=UTC` on the server**. Same as MySQL.

## See also

- [MySQL](mysql.md) - most of this, with the differences called out
- [schemagen](schemagen.md) - the normalisation that makes reflection usable here
- [`fastapi-wallet-rest-uv-mariadb`](../../ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb)
