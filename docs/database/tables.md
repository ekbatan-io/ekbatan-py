# Framework tables

An application has two kinds of table:

- **yours** - `wallets`, `orders`, `notifications`;
- **the framework's** - written by the framework or one of its runtime helpers.

This is the map. The individual pages carry the full DDL per dialect, column by column.

| table | needed when | written by | shape |
| --- | --- | --- | --- |
| [`events`](tables/events.md) | you use the default outbox persister, which is the normal setup | the executor, inside the action's transaction | `outbox_table()` |
| [`event_notifications`](tables/event-notifications.md) | optional - only with the in-process event handler | the fan-out job creates rows, the dispatch job updates them | `notifications_table()` |
| [`scheduled_tasks`](tables/scheduled-tasks.md) | optional - only with distributed jobs, or anything built on them | db-scheduler | `scheduled_tasks_table()` |

The in-process event handler is built on distributed jobs - fan-out and dispatch *are* jobs - so
using it means all three tables.

## Do I need each table?

| what you are doing | `events` | `event_notifications` | `scheduled_tasks` |
| --- | :---: | :---: | :---: |
| action persistence only | yes | no | no |
| Debezium or CDC reading committed events | yes | no | no |
| in-process fan-out and handling | yes | yes | yes |
| distributed jobs only | no | no | yes |
| a custom `EventPersister` that writes somewhere else | up to you | no, unless you also handle in-process | only if you use distributed jobs |

The last row is the one that surprises people. The outbox table is the *default* persister's
choice, not the framework's requirement: swap the persister and `events` is yours to shape -
a different retention, a partitioned table, a second column your consumers need. What you cannot
swap is *when* it is written, which is the part that makes it atomic.

## Table pages

- [`events`](tables/events.md) — the committed action and event rows the default
  persister writes. Every deployment has this one
- [`event_notifications`](tables/event-notifications.md) — one row per (event, handler),
  holding what the in-process pipeline has delivered and what it has not
- [`scheduled_tasks`](tables/scheduled-tasks.md) — the scheduler's own table, used by
  distributed jobs and, through them, by the in-process handlers

## Getting them into your database

They are SQLAlchemy `Table` objects, not DDL you copy. Build them into your own metadata:

```python
from sqlalchemy import MetaData
from ekbatan.schema import notifications_table, outbox_table, scheduled_tasks_table

metadata = MetaData()
EVENTS = outbox_table(metadata)
EVENT_NOTIFICATIONS = notifications_table(metadata)
SCHEDULED_TASKS = scheduled_tasks_table(metadata)
```

Each takes `name=` and `schema=`, so a second outbox, or the tables in their own `eventlog`
schema, is one argument:

```python
EVENTS = outbox_table(metadata, schema="eventlog")
```

In practice you do not write them by hand at all - `schemagen` emits them alongside your own
tables, and `migrate()` creates them from your migration files. See
[schemagen](schemagen.md) and [Migrations](migrations.md).

On PostgreSQL a schema is a namespace inside the database. On MySQL and MariaDB "schema" and
"database" are the same thing, so `schema="eventlog"` means a **separate database**, and the
connecting user needs rights to it. Sharing these tables with a Java service needs the schema
set this way - see [Running beside a Java service](../beside-java.md#what-to-set).

In a sharded application every shard gets its own copy of all three. `migrate()` applies the same
migration files to each.

## See also

- [The outbox](../concepts/outbox.md) - why `events` exists
- [Migrations](migrations.md) - how they get created
- [Multiple dialects](multi-database.md) - where the dialects disagree
