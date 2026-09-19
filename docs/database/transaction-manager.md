# The transaction manager

The executor is the right entry point for **business operations** - anything that changes a
domain object and emits events. Sometimes you need transactional access outside that: an admin
script backfilling a column, a startup hook reconciling two tables, a multi-step read that does
not fit the action shape, batch maintenance with no event worth emitting.

For those, take a transaction manager directly. It is the same primitive the executor uses
internally: one transaction, one connection, commit-or-roll-back, with the connection bound to
the calling context so anything you call from inside joins it.

## Getting one

You rarely construct one. Reach through the registry:

```python
tm = registry.default                      # the default shard
tm = registry.manager(MEXICO)              # a specific one
```

Each one owns four things, for exactly one shard:

- **One pool pair** — the primary `Engine`, and the replica `Engine` if that shard has one. An
  `Engine` is pool, dialect and lifecycle in one, which is why there is no separate connection
  provider here.
- **A dialect**, read off `engine.dialect.name`. It is what picks the keyed-lock provider, the
  `RETURNING` support and the column renderings, so it is the manager's business rather than a
  per-call argument.
- **A `ShardIdentifier`** — `ShardIdentifier(0, 0)` when you are not sharding. It labels the
  OpenTelemetry span and is what `DatabaseRegistry` routes by.
- **A `ContextVar`** holding the in-flight transaction's connection for the duration of a
  `tm.transaction()` block. A `ContextVar` rather than a thread local, so the binding follows an
  `await` and a `ThreadPoolExecutor` does not inherit one by accident.

## `with tm.transaction()`

```python
with tm.transaction() as tx:
    orphans = tx.execute(
        select(WALLETS.c.id).where(WALLETS.c.currency.is_(None)).with_for_update()
    ).scalars().all()

    if orphans:
        tx.execute(update(WALLETS).where(WALLETS.c.id.in_(orphans)).values(currency="EUR"))
        tx.execute(insert(AUDIT_LOG).values(at=clock.now(), note="currency backfill",
                                            affected=len(orphans)))
```

Both writes commit together or neither does - the same atomicity an action gives you, without any
of the action machinery: no plan, no outbox row, no retry on a lost version race.

`tx.rows(statement)` is forwarded too, for a statement whose rows become objects - the same
`fetch(mapper)` family a repository uses, described in [Repositories](repositories.md#turning-rows-into-objects).
`tx.connection` is the `Db` underneath, for everything else SQLAlchemy offers.

**One `with` block where Java has four overloads.** Java needs `inTransaction` and
`inTransactionChecked`, each in a value-returning and a void form, because a lambda that throws a
checked exception needs its own functional interface. A `with` block returns whatever you want
and propagates whatever it likes.

## It joins; it does not nest

This is the sharpest divergence in this whole section, and it is in this port's favour.

**Here, opening a transaction on a shard that already has one open joins the existing one.**
Nothing new is borrowed, nothing new is committed, and the inner block's work lands in the outer
block's commit. That is what makes "everything in this unit of work commits together" true even
when a repository three calls down opens its own block.

**In Java it nests** - a second `inTransaction` on the same manager rebinds the `ScopedValue`,
shadows the outer transaction, and quietly runs on a *second* pooled connection with its own
commit boundary. Java's own documentation says so and says "do not nest". The failure mode is
worse than a wrong answer: the inner connection can block on locks the outer one holds, on the
same thread, which is a deadlock against yourself.

So the Java rule "structure your code so all the work is in one outer block" is good advice
there. Here it is not required.

## What happens around your block

1. Borrow a connection from the **primary** pool.
2. Bind it into the ambient context for this shard.
3. `BEGIN`, then run your block.
4. Normal return: `COMMIT`, release the connection.
5. Exception: `ROLLBACK`, release, re-raise.

The transaction is begun, committed and rolled back by hand rather than through SQLAlchemy's own
`with conn.begin()`. That context manager rolls back for you, but it has nowhere to report that
**the rollback itself** failed - and SQLAlchemy then returns the connection to the pool and hands
it to the next caller, still carrying whatever the rollback did not undo. Java refuses to do that:
a failed rollback marks the transaction dirty and the connection is evicted rather than released.
So does this.

The reasoning behind the eviction is worth knowing because it is counter-intuitive: restoring
autocommit on a connection whose transaction may still be open **is itself a commit**. psycopg
issues an explicit `COMMIT`; MySQL and MariaDB send `SET autocommit=1`, which they document as an
implicit commit. Restoring it after a failed rollback would commit the very transaction that just
failed to roll back. Physically closing the connection makes the server discard what was pending.

## Repository calls join automatically

Because the connection is bound into the ambient context rather than passed around, a repository
write inside the block uses it:

```python
with tm.transaction():
    wallet = wallets.get(wallet_id)          # a primary read, by design
    wallets.update(wallet.close())           # joins this transaction
```

Reads are a choice. Inherited reads see committed state on the primary; if a read must see this
block's own uncommitted writes, use `tx.execute` / `tx.rows` or a repository method built on
`tx_db_else_db`. See [Repositories](repositories.md).

## When not to reach for it

If the operation changes a model and emits an event, **write an action instead.** The action path
gets you the outbox row written atomically with the domain row - which is the entire point of the
framework - plus retry on a lost version race, the span hierarchy, cross-shard validation, and
fan-out into the event pipeline.

`tm.transaction()` is the escape hatch. Reach for it when the work genuinely does not fit:

- boot-time or shutdown-time work - default rows, a sanity check;
- admin and ops scripts - backfills, manual fixes, exports;
- multi-step reads that want one consistent snapshot and write nothing;
- batch maintenance where one event per row would be useless outbox volume.

If you find yourself reaching for it for ordinary business work, that is a signal the operation
wants to be an action.

## Reading without a transaction

If you only read, you need no transaction:

```python
with tm.connection() as conn: ...                  # primary, freshest committed state
with tm.connection(readonly=True) as conn: ...     # the replica, tolerates lag
```

Inside a `tm.transaction()` block these hand you that transaction's connection instead, so a
read written this way joins rather than opening a second one.

Outside one, no transaction is opened and nothing is bound — the connection runs in
`AUTOCOMMIT`, so each statement stands on its own and commits itself. That is the behaviour Java
gets for free from JDBC, where `db()` is a context over a DataSource with auto-commit left on.
SQLAlchemy will not do it by default: an `execute` outside a transaction quietly begins one, and
a connection closed without `commit` rolls it back, so a write would simply vanish.

`readonly=True` asks for the replica and falls back to the primary when a shard has none. It is
never the default, because a replica has not necessarily seen what you just wrote.

## Cross-shard

A transaction manager is **per shard**, and there is no two-phase commit. If you need writes
across shards you cannot have them atomically - that is a property of distributed systems, not of
this framework.

```python
with registry.manager(GLOBAL).transaction(): ...   # work A
with registry.manager(MEXICO).transaction(): ...   # work B
# if B fails, A is already committed.
```

An escape hatch, with the same partial-commit risk as `allow_cross_shard=True` on an action. If a
partial commit would need compensating, use a [saga](../concepts/sagas.md).

## Tracing

The block produces an `ekbatan.transaction` span with `ekbatan.shard.group` and
`ekbatan.shard.member` attributes. **Joining produces no span** - joining is not a transaction
boundary, and a span there would report a nested duration for something that neither began nor
committed.

## See also

- [Actions](../concepts/actions.md) - the recommended path, which uses this internally
- [Repositories](repositories.md) - what joins the open transaction, and what does not
- [Sharding](sharding.md) - one manager per shard
