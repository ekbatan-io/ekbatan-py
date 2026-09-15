# Repositories

One repository per persistable type, extending `ModelRepository` or `EntityRepository`. The base
supplies the whole read and write surface; what you add are the questions your business actually
asks.

```python
class WalletRepository(ModelRepository[Wallet, WalletId]):
    def __init__(self, registry: DatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(Wallet, WALLETS, registry,
                         sharding=EmbeddedBitsShardingStrategy(), clock=clock)

    def find_all_by_owner(self, owner_id: UUID) -> list[Wallet]:
        return self.find_where(WALLETS.c.owner_id == owner_id)
```

## Mapping is by field name

Note what is **not** there: no `from_row`, no `to_row`. Java writes both by hand for the same
repository, listing every column twice, because jOOQ hands it a generated record and something
has to bridge the two. Here the dataclass already knows its fields and the table already knows
its columns, so they are matched by name.

Override `from_row` or `to_row` only when a name genuinely differs, or a value needs converting
beyond what the column type does. Most repositories never do.

That is the single largest ergonomic difference between the two ports, and it removes an entire
class of bug: a field added to a model and forgotten in `toRecord` is not expressible here.

## What you get for free

**Writes**

```python
repo.add(model)              repo.add_all(models)
repo.update(model)           repo.update_all(models)
```

Plus `add_no_result` / `update_no_result` and their `_all` forms, for when you are writing a
large batch and do not need the rebuilt objects back. On a dialect without `RETURNING` the
framework otherwise has to rebuild the rows itself, and that is not free.

**Reads**

```python
repo.find(key)               # -> M | None
repo.get(key)                # -> M, raises EntityNotFoundError
repo.find_many(keys)         # -> list[M]
repo.all(shard=...)          # -> list[M]
repo.find_one_where(*criteria, shard=...)
repo.find_where(*criteria, shard=...)
```

**Counting and existence**

```python
repo.count(*criteria, shard=...)
repo.exists(key)
repo.exists_where(*criteria, shard=...)
```

Every one of them:

- **applies optimistic locking** on writes - `WHERE version = ?`, and the increment;
- **filters soft-deleted rows** on reads, once, in the framework rather than at each call site;
- **routes to the right shard** - by the id for key-based reads, by scatter-gather across every
  shard for predicate-based ones;
- **reads from the primary** by design. Replica reads are opt-in;
- **resolves the dialect per shard**, so a mixed-dialect deployment is not ruled out.

Criteria are ordinary SQLAlchemy expressions, so the whole of `sqlalchemy` is available inside
them:

```python
repo.find_where(WALLETS.c.state == "OPENED", WALLETS.c.balance < threshold)
```

Several criteria are `AND`ed. For anything else, build the expression yourself and pass it.

### No offset pagination, deliberately

There is no `all(offset, limit)` and no `find_where(..., offset, limit)`, in either port. Offset
pagination does not work across shards - "rows 100 to 120" has no meaning when the rows are on
four databases and each has its own ordering. Use keyset or temporal pagination, written on the
concrete repository where you know the ordering key.

## Dropping down to SQLAlchemy

When the inherited surface is not enough, take a connection. The choice is about **consistency**,
and picking wrongly is the mistake this section exists to prevent.

```python
with self.db(key=..., model=..., shard=...) as conn: ...          # primary
with self.readonly_db(key=..., shard=...) as conn: ...             # the replica
conn = self.tx_db(key=...)                                        # the open transaction, or None
with self.tx_db_else_db(key=...) as conn: ...                     # that, else primary
```

And for every shard at once:

```python
for shard, conn in self.dbs(): ...
for shard, conn in self.readonly_dbs(): ...
```

| you want | use |
| --- | --- |
| the default read behaviour | the inherited reads - primary-consistent by design |
| a custom read that must be current | `db(...)` or `dbs()` |
| a custom read where lag is fine | `readonly_db(...)` or `readonly_dbs()` - these **are** the replica |
| a read that must see this transaction's uncommitted writes | `tx_db_else_db(...)` |
| an insert, update or delete | `tx_db_else_db(...)` - joins the open transaction, falls back to primary |
| "we must already be in a transaction", loudly | `tx_db(...)`, and raise on `None` |
| a scatter-gather | `dbs()` / `readonly_dbs()` |

They are context managers because a connection has a lifetime. `tx_db` is the exception: it
returns the transaction's connection or `None`, and the transaction owns it.

> **`perform()` is not transactional.** The executor opens the transaction *after* `perform()`
> returns. A `tx_db_else_db(...)` write from inside `perform()` finds nothing bound, falls back
> to primary, and **writes immediately, outside the action's commit** - which is exactly the
> dual-write the framework exists to prevent, reintroduced by hand. Inside `perform()`, stage
> with `self.plan.add(...)` and `self.plan.update(...)`.

### Soft deletion is yours again down here

The inherited reads add the soft-delete predicate. Raw SQL through `db()` does not - the filter
is not a table property, it is something the repository applies.

The predicate helpers (`find_where`, `find_one_where`, `exists_where`, `count`) keep both the
soft-delete filter and the shard routing, so reach for those before reaching for a connection.

## Registration

There is no annotation and nothing scans for your repositories. They go in the registry, in the
wiring, where you can see them:

```python
repositories = (
    RepositoryRegistry.builder()
    .with_model_repository(wallets)
    .with_entity_repository(alerts)
    .build()
)
```

Two calls rather than one because the two are different: a model's changes are events and an
entity's are not, and the registry checks that the table can hold what the type carries.

## See also

- [Actions](../concepts/actions.md) - where repository writes actually happen
- [Sharding](sharding.md) - how a key resolves to a database
- [The transaction manager](transaction-manager.md) - transactional work outside an action
- [Models and entities](../concepts/models-and-entities.md) - what is being mapped
