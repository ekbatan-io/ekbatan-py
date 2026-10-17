# Database

Everything that touches the actual SQL. Domain code goes through a repository and a transaction
manager; this is where shards live, where the framework's tables sit on disk, and where the
dialects disagree.

For the *concept* of the outbox - the atomic state-and-events story - see
[The outbox](../concepts/outbox.md). This section is everything else.

- **[Framework tables](tables.md)** - the table map, and which you actually need
  - **[`events`](tables/events.md)** - the outbox. DDL, columns, sentinel rows, dialect types
  - **[`event_notifications`](tables/event-notifications.md)** - per-handler delivery rows
  - **[`scheduled_tasks`](tables/scheduled-tasks.md)** - db-scheduler's own table
- **[Repositories](repositories.md)** - reads, writes, soft deletion, and your own queries
- **[The transaction manager](transaction-manager.md)** - transactional access outside an action
- **[Keyed locks](keyed-locks.md)** - four backends, the reentrancy contract, and the watchdog
- **[Sharding](sharding.md)** - configuration, the bit layout, strategies, cross-shard rules
- **[Migrations](migrations.md)** - plain SQL files run as Flyway runs them, across every shard,
  with Flyway's history, checksums and lock, so a Java service can share the database
- **[Multiple dialects](multi-database.md)** - the cheatsheet, and where they disagree
  - **[PostgreSQL](postgresql.md)** · **[MySQL](mysql.md)** · **[MariaDB](mariadb.md)** ·
    **[SQLite](sqlite.md)**
- **[Config files](config-files.md)** - the layout read from a YAML, TOML, JSON or INI file, with
  environment variables filled in.
- **[schemagen](schemagen.md)** - table definitions generated from a migrated database, and the
  type forcing that makes them portable

## Interoperating with a Java service

The framework tables are a **cross-language contract**: a Java service and a Python service can
read each other's outbox rows, contend for the same keyed lock and share one `scheduled_tasks`
table. Three things have to line up, and the first is the one people miss:

1. **The schema.** Java puts its tables in `eventlog`. This port defaults to no schema, because a
   Python service usually owns its database outright. Pass it explicitly to match:
   `outbox_table(metadata, schema="eventlog")`.
2. **The column types.** They already agree - see each table page for the verified comparison.
3. **The namespace.** Different services should use different ones; the same service in two
   languages should use the same one.

Where the two genuinely differ, the table pages say so and say whether it matters.
