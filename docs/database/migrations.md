# Migrations

No Java counterpart as a page - Java uses Flyway and documents it inside its getting-started and
wiring pages. Here it needs one, because the tool is different and because two things had to be
added on top of it.

The migrations themselves are **[Alembic](https://alembic.sqlalchemy.org)**'s - SQLAlchemy's own
migration tool, by the same author. Nothing here reimplements ordering, the revision graph,
transactional DDL, autogenerate or the CLI.

```
uv add ekbatan[migrations]
```

## Alembic as a library, not as a binary

```python
from ekbatan.migrations import migrate

migrate(layout, "migrations")          # every shard, in order, fail-fast
```

There is **no `alembic.ini` and no `env.py`.** The `Config` is built in memory from the same
`ShardingConfig` the framework routes from, so an application migrates itself at startup with no
external binary and no config file to locate.

That is not a stylistic preference. A config file that has to be found is a config file that is
missing in the one environment you did not test, and `env.py` is where a project's database url
usually gets hardcoded twice.

```python
migrate(layout, MIGRATIONS)                        # all shards
migrate(layout, MIGRATIONS, shards=[MEXICO])       # some
migrate(layout, MIGRATIONS, revision="head")       # a target; head is the default
```

It returns the revision each shard is now at.

## Every shard, one at a time

Alembic upgrades one url per `Config`. `migrate` drives one per shard **sequentially**, stopping
at the first failure and naming the shard **and what had already succeeded** - because half a
migrated cluster is a state an operator has to reason about, and the underlying error only names
a connection url.

Shards are visited in **configuration order**, not in the order of the `shards=` argument, so
two callers asking for the same subset get the same sequence. An identifier that is not in the
configuration is **refused**, not skipped: a typo in a shard list should not quietly migrate
nothing.

A shard added to the configuration and forgotten by the migrator is a database that accepts
writes right up until the first one touches a column that is not there. Migrating from the same
configuration the framework routes from is what keeps those two from drifting apart.

## Checksums, which Alembic does not have

This is the one that matters, and it was measured rather than assumed.

Alembic's `alembic_version` table holds **a single row**: the current head. There is no
per-revision history and no record of what a revision *contained*. So editing a revision that has
already been applied is silently accepted - the file and the database disagree, and nothing
anywhere says so. The next environment you deploy to gets the edited version; the one already
running does not; and the difference surfaces weeks later as a column that exists in staging and
not in production.

Flyway refuses that outright. So does this. `migrate` records a hash of each applied revision in
**`ekbatan_revision_checksums`** and verifies every one before it upgrades. An edited revision
raises `MigrationError` naming the revision, rather than proceeding.

If you *meant* to edit it - the revision has only ever run on your laptop - drop the row for that
revision, or drop the table, and run again.

## Writing the first revision

The framework's own tables have to exist. You do not hand-write their DDL:

```python
from sqlalchemy import MetaData
from ekbatan.migrations import emit_ddl
from ekbatan.schema import notifications_table, outbox_table, scheduled_tasks_table

metadata = MetaData()
outbox_table(metadata)
notifications_table(metadata)
scheduled_tasks_table(metadata)

print(emit_ddl(metadata, "postgresql"))
```

Run it, read it, paste it into your first revision, **commit it**. Generated once and then owned
by you, rather than regenerated behind your back on every deploy - which is the difference
between a migration and a `create_all`.

`create_all` has no answer for a column that changed, which is why the examples migrate from
files even though they could create their schema in one line.

### Or, for a test

```python
from ekbatan.migrations import migrate_registry

migrate_registry(registry, metadata)     # create_all, every shard
```

For a throwaway database in a test. Not for anything you will have to change later.

## Writing revisions by hand

Ordinary Alembic revisions. Two things to know, both learned the hard way:

**One statement per `op.execute()`.** PyMySQL rejects a multi-statement script, so a revision
that works on PostgreSQL fails on MySQL with an error that names the syntax rather than the
cause.

**Use `ekbatan.schema`'s column helpers rather than raw types** - `uuid_column()`,
`timestamp_column()`, `json_column()`. They are what make a revision portable, and they are what
`schemagen` forces back when it reflects. `timestamp_column()` in particular pins microsecond
precision, because MySQL's bare `DATETIME` truncates to whole seconds silently.

## Order at startup

Migrate **before** anything opens a pool against a schema that may not be there yet:

```python
if config.run_migrations:
    migrate(layout, MIGRATIONS)

registry = DatabaseRegistry.from_config(layout)
```

Whether an application migrates itself at startup or a deploy step does it first is your call.
The examples do it at startup because it makes them runnable; a large deployment usually wants it
as its own step, which is what `run_migrations` being a setting is for.

## See also

- [schemagen](schemagen.md) - which runs your migrations into a container and reflects the result
- [Framework tables](tables.md) - what has to exist
- [Sharding](sharding.md) - the configuration this reads
