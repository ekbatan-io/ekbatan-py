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
- **[Migrations](migrations.md)** - plain SQL files on yoyo, across every shard, with the lock,
  checks and history yoyo alone does not have
- **[Multiple dialects](multi-database.md)** - the cheatsheet, and where they disagree
  - **[PostgreSQL](postgresql.md)** · **[MySQL](mysql.md)** · **[MariaDB](mariadb.md)** ·
    **[SQLite](sqlite.md)**
- **[Config files](config-files.md)** - the layout read from a YAML, TOML, JSON or INI file, with
  environment variables filled in.
- **[schemagen](schemagen.md)** - table definitions generated from a migrated database, and the
  type forcing that makes them portable

## Sharing the tables with a Java service

A Java service built on Ekbatan for Java can share these tables, the keyed lock and the event
stream with a Python one. What has to line up - the schema, the namespace, the shard layout - is
on [Running beside a Java service](../beside-java.md).
