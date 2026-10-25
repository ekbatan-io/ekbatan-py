# Architecture map

A compact orientation. `AGENTS.md` remains the source of truth.

## The core idea

A persistence and action framework. It stores state changes and domain events atomically, so the
database can act as a reliable outbox.

An **action** declares intent. During `perform()` it reads repositories and stages immutable
snapshots on a **plan**. After `perform()` returns, the **executor** opens the transaction and
writes the domain rows and the event rows together.

## The layout

```
src/ekbatan/
  domain.py          Model, Entity, ModelEvent, the builders
  ids.py             Id, ShardedId, TypedValue
  sharding.py        ShardIdentifier, the strategies, the bit layout
  repository.py      Repository, ModelRepository, EntityRepository, RepositoryRegistry
  persistence.py     DatabaseRegistry, TransactionManager, Transaction
  config.py          DataSourceConfig, ShardConfig, ShardingConfig
  schema.py          the framework's tables, the column helpers, Converter/converted()/sql_type()
  errors.py          the exception hierarchy
  clock.py           Clock, SystemClock, VirtualClock
  migrations.py      SQL migrations on yoyo: files, checks, history, one call per layout
  schemagen/         table definitions from a migrated database: _config (pyproject, forced
                     types: name, converter, sqlalchemy-type), _types (the type table; an
                     unknown type is sql_type("...")), _reflect, _render, _records (the
                     typed `<Table>Record` beside each table)
  codegen.py         typed builders, generated per model
  streaming.py       ActionEvent - the consumer-side view of an outbox row
  accounting.py      statement counting, so cost claims are checkable
  action/            action, plan, executor, registry, persister, outbox
  events/            handler, fanout, dispatch, jobs, notification, config
  locks/             base, sql, redis, inprocess, _holder
  distributedjobs/   DistributedJob, JobRegistry, JobsConfig
db-scheduler/        a separate distribution: the port of kagkarlsson/db-scheduler
ekbatan-py-examples/ seven examples x uv/pip/Poetry: 21 standalone projects
website/             Astro. The only part that does not run through uv
```

## Invariants

- Domain objects are frozen dataclasses. A mutation returns a new instance.
- Only a `Model` emits events. An `Entity` does not.
- Every persistable carries a `version`, and every update checks it.
- Reads filter soft-deleted rows once, in the framework.
- Every repository writes `to_record` / `from_record`, typed by the table's record - a `dict`
  subclass schemagen writes beside the table (`WalletsRecord` beside `WALLETS`), with a
  keyword `__init__`, `__getitem__` overloads for mypy only, and a `column_<name>()` method per
  column (`EKB-0485`); repositories are `ModelRepository[Wallet, WalletsRecord,
  UUID]` - the key is the type the id column holds, as Java's `DB_ID`: a read by id takes
  `wallet.id.value`, and a forced type converting the id column makes the typed id the key
  (`EKB-0541`). `columns_by_field_name` /
  `model_by_field_name` are the by-name mapping, chosen in a line, never a default.
- `to_record` builds the record by keyword - `WalletsRecord(id=..., version=..., ...)`; a column
  whose name is no Python name has a sanitized keyword (`class_=`, `order_date=`, `_2fa=`), and
  one the database fills defaults to `UNSET` (left out of the record and the INSERT). The INSERT
  is given that very dict. `from_record` reads `record.column_x()` (the preferred way;
  `record["x"]` is fine too, equally type-checked); the framework hands it the record class on every read path.
- The ambient transaction is a `ContextVar`, so it is per-thread and per-task.
- Opening a transaction inside another one opens a **second** transaction on a second
  connection, as Java's does (`EKB-0301`). Repository calls inside a block join it; nested blocks
  do not. Do not nest.
- Cross-shard is refused unless asked for, because atomic-across-databases does not exist.

## Where the two ports differ

`DIVERGENCE.md` is the complete list. The ones that bite most often:

- no virtual threads, so fan-out, dispatch and the lock watchdog are sequential or thread-backed;
- the framework is synchronous, and blocking is not free under asyncio;
- retry matching is by `isinstance` here and exact by class in Java;
- this port has no graceful shutdown on a signal, and no pool leak detection.
