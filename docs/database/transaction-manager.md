# The transaction manager

A transaction manager runs one database transaction on one shard. **Business operations** -
anything that changes a domain object and emits events - go through the executor. This is for the
other work that needs a transaction: an admin script backfilling a column, a startup hook
reconciling two tables, a multi-step read that does not fit an action, batch maintenance with no
event worth emitting.

The executor uses the same transaction manager inside. Each transaction runs on one connection,
and commits when the block ends, or rolls back if the block fails. While the block runs, any
repository you call joins the transaction, without being handed the connection.

## What it owns

You rarely build one yourself. Get it from the registry:

```python
tm = registry.default                  # the default shard
tm = registry.manager(MEXICO_SHARD)    # a specific one
```

Each one owns four things, for exactly one shard:

- **Its connection pools** — the primary's `DataSource`, and the replica's if that shard has one.
  A data source is one database's connection pool, built from its config with
  `DataSource.from_config`. The SQLAlchemy engine behind it is `data_source.sqlalchemy_engine`. A
  manager accepts only data sources (a plain SQLAlchemy `Engine` is refused), and its `close()`
  closes the data sources it was given.
- **A dialect** - which kind of database it is: `postgres`, `mysql`, `mariadb` or `sqlite`. It is
  read from the data source's url, unless you pass `dialect=`. The dialect decides which
  keyed-lock provider is used, whether `RETURNING` is available, and how some column types are
  written. That is why it belongs to the manager, not to each call.
- **A `ShardIdentifier`** — `ShardIdentifier(0, 0)` when you are not sharding. It labels the
  OpenTelemetry span and is what `DatabaseRegistry` routes by.
- **A `ContextVar`** holding the open transaction's connection while a `tm.transaction()` block
  runs. It is a `ContextVar` rather than a thread-local, so the connection follows an `await`, and
  a `ThreadPoolExecutor` does not pick it up by accident.

In async mode the registry is an `AsyncDatabaseRegistry` and each manager an
`AsyncTransactionManager`, from `ekbatan.aio`: the same four things, with `AsyncDataSource` pools,
and every block below is `async with`. A cancelled task rolls its transaction back like any
other failure.

## Opening a transaction

Open a block with `tm.transaction()`. This backfill finds the wallets that have no currency,
fixes them, and writes an audit row, all in one transaction:

**Sync**

```python
with tm.transaction() as tx:
    orphans = tx.execute(
        select(WALLETS.c.id).where(WALLETS.c.currency.is_(None)).with_for_update()
    ).scalars().all()

    if orphans:
        tx.execute(update(WALLETS).where(WALLETS.c.id.in_(orphans)).values(currency="EUR"))
        tx.execute(insert(AUDIT_LOG).values(at=clock.instant(), note="currency backfill",
                                            affected=len(orphans)))
```

**Async**

```python
async with tm.transaction() as tx:
    found = await tx.execute(
        select(WALLETS.c.id).where(WALLETS.c.currency.is_(None)).with_for_update()
    )
    orphans = found.scalars().all()

    if orphans:
        await tx.execute(update(WALLETS).where(WALLETS.c.id.in_(orphans)).values(currency="EUR"))
        await tx.execute(insert(AUDIT_LOG).values(at=clock.instant(), note="currency backfill",
                                                  affected=len(orphans)))
```

Both writes commit together or neither does. That is the same atomicity an action gives you,
without the rest of what an action does: no plan, no outbox row, no retry on a lost version race.

`clock.instant()` is written exactly as it is: UTC with no zone attached, which is what the
zone-less `at` column holds. Nothing converts it on the way in or out
([What you give is what is stored](multi-database.md#what-you-give-is-what-is-stored)).

Besides `tx.execute`, as above, there is `tx.rows(statement)`, for a statement whose rows become
objects. It has the same `fetch(mapper)` methods a repository uses, described in
[Repositories](repositories.md#examples). `tx.connection` is the `Db` underneath, for everything else SQLAlchemy
offers.

As with any `with` block, values you compute inside are still there after it. An exception of
any kind that leaves the block rolls the transaction back on the way out.

## Do not nest

The framework does not simulate nested transactions or savepoints. A `tm.transaction()` inside
another one on the same `tm` does not fail loudly. It opens a *second* transaction, on a *second*
connection from the pool, which commits on its own. That is almost never what you want. Do all the
work inside one outer block.

What the inner block gets:

- it does not see the outer block's uncommitted rows;
- its commit stands even if the outer block then fails, and its failure rolls back only its own
  writes;
- inside it, `tx_db` and `tx_db_else_db` find the inner transaction; after it, the outer one
  again;
- a write to a row the outer block has locked waits for a lock its own thread holds: until the
  database's lock timeout, or forever where there is none. On SQLite, any inner write after an
  outer one waits for the file lock and fails with "database is locked".

An action run inside a transaction you opened is the same case: the executor opens its own
transaction, so the action commits on its own and stays committed if your block then rolls back.

## What the block does, step by step

1. Borrow a connection from the **primary** pool.
2. Store it in the current context, for this manager, where repositories will find it.
3. `BEGIN`, then run your block.
4. Normal return: `COMMIT`, release the connection.
5. Exception: `ROLLBACK`, release, re-raise.

**Edge case: a rollback that fails.** If the `ROLLBACK` itself fails, the connection is closed
instead of going back to the pool. Closing it makes the database discard whatever was still
pending.

This is why the framework begins, commits and rolls back by hand, rather than through
SQLAlchemy's `with conn.begin()`. That context manager has no way to report that **the rollback
itself** failed. SQLAlchemy would return the connection to the pool and hand it to the next
caller, still carrying whatever the rollback did not undo.

Nor can such a connection be saved by turning autocommit back on. On a connection whose
transaction may still be open, that **is itself a commit**: psycopg sends an explicit `COMMIT`, and MySQL and MariaDB send
`SET autocommit=1`, which they document as an implicit commit. It would commit the very
transaction that just failed to roll back.

**A failed commit alone does not close the connection.** It is handled like any other failure:
the framework sends a `ROLLBACK`, and if that works, the connection is clean and goes back to the
pool. Only when that rollback *also* fails is the state unknown, and only then is the connection
thrown away.

## Repository writes join automatically

Because the connection is stored in the current context rather than passed around, a
repository write inside the block uses it:

**Sync**

```python
with tm.transaction():
    wallet = wallet_repository.get_by_id(wallet_id)          # a primary read, by design
    wallet_repository.update(wallet.close())           # joins this transaction
```

**Async**

```python
async with tm.transaction():
    wallet = await wallet_repository.get_by_id(wallet_id)    # a primary read, by design
    await wallet_repository.update(wallet.close())     # joins this transaction
```

Reads are a choice. The inherited reads see committed data on the primary. If a read must see
this block's own uncommitted writes, use `tx.execute` / `tx.rows`, or a repository method built
on `tx_db_else_db`. See [Repositories](../database/repositories.md).


How it works: opening a transaction stores its connection in a `ContextVar`, keyed by the
manager. Every repository write goes through `tx_db_else_db(...)`, which looks there first.
Inside the block it finds the connection and joins the transaction. Outside, it finds nothing,
and each statement commits on its own. Nothing is passed through arguments, and no repository
takes a connection parameter.

Because the key is the manager, a second manager for the same shard (from another registry, or
one built on a member's `jobsConfig`) sees no transaction there. It opens its own, on its own
database.

What follows from this:

- **A repository method written once works in both places.** The same `wallet_repository.update(...)` is
  part of your transaction inside the block and a standalone write outside it.
- **The transaction does not follow work into a thread pool.** Work handed to a pool
  (`ThreadPoolExecutor.submit`) from inside a transaction does not get it, and that is correct:
  another thread needs another connection, and quietly sharing one would corrupt both. See
  [Concurrency](../runtime/concurrency.md).
- **An async transaction is shared with the tasks started inside it.** A task copies the
  caller's context. So tasks started with `gather` or a `TaskGroup` inside
  `async with tm.transaction()` see the transaction, and their statements take turns on its one
  connection, all inside the transaction. Keep such tasks inside the block: one still running
  after it ends finds the connection closed.
- **A sync transaction belongs to the thread that opened it.** Some helpers copy the caller's
  context into a new thread: `asyncio.to_thread`, anyio's `to_thread.run_sync`, Starlette's
  `run_in_threadpool`. The transaction goes along with the context. When that thread reaches for
  the transaction, it is refused with `TransactionThreadError`. Sharing the connection is not
  safe, since one connection serves one thread at a time. And quietly using a connection of its
  own would leave that thread's work in place if the transaction rolled back:

  ```python
  with tm.transaction():
      asyncio.run(asyncio.to_thread(wallet_repository.update, wallet))   # TransactionThreadError
  ```

  Do that work in the thread that opened the transaction, or outside it. `loop.run_in_executor`
  and a pool's `submit` copy no context, so their threads simply find no transaction.
- **A block must end in the context it began in.** A FastAPI sync `yield` dependency, for
  example, opens the block in one copy of the context and closes it in another. The work in
  between then ran outside the transaction, so the end of the block raises
  `TransactionContextError` to say so. Open the transaction where the work runs: see
  [Wiring with FastAPI](../wiring-fastapi.md#never-open-a-transaction-in-a-dependency).

What does *not* join is a connection you ask for yourself: `db()` and `readonly_db()` open
their own connections on purpose — see [Repositories](../database/repositories.md#picking-the-right-one).

## When NOT to use it directly

If the operation changes a model and emits an event, **write an action instead.** An action
writes the outbox row in the same transaction as the domain row, which is the whole point of the
framework. It also gives you a retry on a lost version race, tracing spans, the cross-shard check,
and delivery into the event pipeline.

`tm.transaction()` is the escape hatch. Reach for it when the work genuinely does not fit:

- boot-time or shutdown-time work - default rows, a sanity check;
- admin and ops scripts - backfills, manual fixes, exports;
- multi-step reads that want one consistent snapshot and write nothing;
- batch maintenance where one event per row would be useless outbox volume.

If you find yourself using it for ordinary business work, the operation should probably be an
action.

## Read-only access (no transaction needed)

If you only read, you need no transaction:

**Sync**

```python
with tm.connection() as conn: ...                  # primary, freshest committed state
with tm.connection(readonly=True) as conn: ...     # the replica, tolerates lag
```

**Async**

```python
async with tm.connection() as conn: ...              # primary, freshest committed state
async with tm.connection(readonly=True) as conn: ... # the replica, tolerates lag
```

Inside a `tm.transaction()` block these hand you that transaction's connection instead, so a
read written this way joins rather than opening a second one.

Outside one, no transaction is opened. The connection runs in `AUTOCOMMIT`, so each statement
stands on its own and commits itself. The framework sets this on purpose. By default, SQLAlchemy
quietly begins a transaction on the first `execute`, and rolls it back when the connection is
closed without a `commit`, so a write would simply vanish.

`readonly=True` asks for the replica and falls back to the primary when a shard has none. It is
never the default, because a replica has not necessarily seen what you just wrote.

## Cross-shard

A transaction manager is **per shard**, and there is no two-phase commit. If you need writes
across shards, you cannot have them atomically. That is true of distributed systems in general,
not only of this framework.

**Sync**

```python
with registry.manager(GLOBAL_SHARD).transaction(): ...   # work A
with registry.manager(MEXICO_SHARD).transaction(): ...   # work B
# if B fails, A is already committed.
```

**Async**

```python
async with registry.manager(GLOBAL_SHARD).transaction(): ...   # work A
async with registry.manager(MEXICO_SHARD).transaction(): ...   # work B
# if B fails, A is already committed.
```

This is an escape hatch, with the same risk of a partial commit as `allow_cross_shard=True` on an
action. If a partial commit would need undoing, use a [saga](../concepts/sagas.md).

## Tracing

Each block produces one `ekbatan.transaction` span, with `ekbatan.shard.group` and
`ekbatan.shard.member` attributes. A nested block is a transaction of its own, and gets its own
span.

## See also

- [Actions](../concepts/actions.md) - the recommended path, which uses this internally
- [Repositories](repositories.md) - what joins the open transaction, and what does not
- [Sharding](sharding.md) - one manager per shard
