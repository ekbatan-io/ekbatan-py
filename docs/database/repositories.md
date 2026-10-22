# Repositories

One repository per persistable type, extending `ModelRepository` or `EntityRepository`. The base
supplies the whole read and write surface; what you add are the questions your business actually
asks.

**Sync**

```python
class WalletRepository(ModelRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
    def __init__(self, registry: DatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(Wallet, WALLETS, registry,
                         sharding=EmbeddedBitsShardingStrategy(), clock=clock)

    def find_all_by_owner(self, owner_id: UUID) -> list[Wallet]:
        return self.find_where(WALLETS.c.owner_id == owner_id)
```

**Async**

```python
class WalletRepository(AsyncModelRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
    def __init__(self, registry: AsyncDatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(Wallet, WALLETS, registry,
                         sharding=EmbeddedBitsShardingStrategy(), clock=clock)

    async def find_all_by_owner(self, owner_id: UUID) -> list[Wallet]:
        return await self.find_where(WALLETS.c.owner_id == owner_id)
```

## The four classes

Four classes, in layers:

| class | what it is |
| --- | --- |
| `Repository[M]` | the promise: what the framework asks of a repository, and all it asks. A `Protocol` - so anything that keeps it can be registered |
| `AbstractRepository[M, R, K]` | the SQL implementation of that promise. Abstract: `to_record` and `from_record` are yours |
| `ModelRepository[M, R, K]` | extend this for a model |
| `EntityRepository[M, R, K]` | extend this for an entity |

`M` is the model, `R` the table's record and `K` the id: `ModelRepository[Wallet, WalletsRecord,
ShardedId[Wallet]]`. An application extends `ModelRepository` or `EntityRepository` and nothing
else. In async mode the same four live in `ekbatan.aio` with an `Async` prefix -
`AsyncModelRepository`, `AsyncEntityRepository` - and every read and write is awaited. The rules
are the same code.

## The record

A row is a **record**: a `TypedDict` with one key per column, each typed as the column reads.
[schemagen](schemagen.md) writes one beside every table it generates - `WalletsRecord` beside
`WALLETS`:

```python
class WalletsRecord(TypedDict):
    id: UUID
    version: int
    state: str
    owner_id: UUID
    currency: Currency      # the column is CHAR(3); its converter gives a Currency
    balance: Decimal
    created_date: Instant
    updated_date: Instant
```

A nullable column is `X | None`; a column the database fills itself (a server default, an
identity, a computed column) is `NotRequired`. To SQLAlchemy a record is a plain `dict`, so it
costs nothing at run time. To mypy it is the table's shape, and that is the point: a mapping
that leaves a column out, misspells one, adds one the table does not have, or gives one the
wrong type - a `str` where the converter's column wants a `Currency` - is a type error, and so
is reading a key the record does not have.

A schema written by hand writes its records by hand, in the same style, next to its tables:

```python
WALLETS = Table(
    "wallets",
    metadata,
    Column("id", uuid_column(), primary_key=True),
    Column("version", BigInteger, nullable=False),
    ...
)


class WalletsRecord(TypedDict):
    id: UUID
    version: int
    ...
```

## Write the mapping

Every repository writes `to_record` and `from_record`, typed by its record. A repository without
them **cannot be created** - the mistake shows at startup, not on the first request:

```
WalletRepository cannot be created: from_record() and to_record() have not been written, and the
mapping between a row and a model is not guessed here.
```

Written out, the two say every column once, and mypy checks each against the record:

```python
def to_record(self, wallet: Wallet) -> WalletsRecord:
    return {
        "id": wallet.id.value,
        "version": wallet.version,
        "state": wallet.state.name,
        "owner_id": wallet.owner_id,
        "currency": wallet.currency,
        "balance": wallet.balance,
        "created_date": wallet.created_date,
        "updated_date": wallet.updated_date or wallet.created_date,
    }

def from_record(self, record: WalletsRecord) -> Wallet:
    return Wallet(
        id=ShardedId[Wallet](record["id"]),
        version=record["version"],
        state=WalletState[record["state"]],
        owner_id=record["owner_id"],
        currency=record["currency"],
        balance=record["balance"],
        created_date=record["created_date"],
        updated_date=record["updated_date"],
    )
```

`from_record` is handed the row read by column name - a result row's mapping, or, where an insert
or update has no `RETURNING`, the record `to_record` wrote. Every read the framework does hands
it the same: `find`, `get`, `find_where`, `all`, a read across shards, and a query of your own
ended with `.fetch(self.from_record)`.

Where the names line up - which is most of the time, because a dataclass already knows its own
fields - that can be two lines instead:

```python
def to_record(self, wallet: Wallet) -> WalletsRecord:
    return self.columns_by_field_name(wallet)

def from_record(self, record: WalletsRecord) -> Wallet:
    return self.model_by_field_name(record)
```

`columns_by_field_name` turns a typed id into its value and an enum into its name; every other
value - a `datetime` included - goes to the column exactly as the model holds it, and
`model_by_field_name` hands a time back exactly as the driver read it. No zone is added, removed
or moved ([What you give is what is stored](multi-database.md#what-you-give-is-what-is-stored)).
The two are built from the dataclass's fields at run time, so mypy takes their record on trust;
the mapping written out key by key is the one it checks.

`to_record` and `from_record` are plain `def` in both modes - mapping waits on nothing.

**By name was the default until `EKB-0217`, and being the default is what made it wrong.**
Rename a column without renaming the field and the mapping silently stopped matching - no
error, no warning, a repository that had quietly stopped round-tripping. The two lines are the
difference between a mapping somebody chose and one nobody can find.

What you still do not write is the *bridge*: you do not list every column twice.
`columns_by_field_name` is written once in the framework rather than once per repository, because
a dataclass knows its fields. Choosing it is yours; implementing it is not.

## Inherited CRUD surface

**Writes**

**Sync**

```python
repo.add(model)          # -> M, stamped
repo.add_all(models)     # -> list[M]
repo.update(model)       # -> M, version already moved
repo.update_all(models)  # -> list[M]
```

**Async**

```python
await repo.add(model)          # -> M, stamped
await repo.add_all(models)     # -> list[M]
await repo.update(model)       # -> M, version already moved
await repo.update_all(models)  # -> list[M]
```

Plus `add_no_result` / `update_no_result` and their `_all` forms, for when you are writing a
large batch and do not need the rebuilt objects back. On a dialect without `RETURNING` the
framework otherwise has to rebuild the rows itself, and that is not free.

**Reads**

**Sync**

```python
repo.find(key)               # -> M | None
repo.get(key)                # -> M, raises EntityNotFoundError
repo.find_many(keys)         # -> list[M]
repo.all(shard=...)          # -> list[M]
repo.find_one_where(*criteria, shard=...)
repo.find_where(*criteria, shard=...)
```

**Async**

```python
await repo.find(key)               # -> M | None
await repo.get(key)                # -> M, raises EntityNotFoundError
await repo.find_many(keys)         # -> list[M]
await repo.all(shard=...)          # -> list[M]
await repo.find_one_where(*criteria, shard=...)
await repo.find_where(*criteria, shard=...)
```

**Counting and existence**

**Sync**

```python
repo.count(*criteria, shard=...)
repo.exists(key)
repo.exists_where(*criteria, shard=...)
```

**Async**

```python
await repo.count(*criteria, shard=...)
await repo.exists(key)
await repo.exists_where(*criteria, shard=...)
```

Every one of them:

- **applies optimistic locking** on writes - `WHERE version = ?`, and the increment;
- **filters soft-deleted rows** on reads, once, in the framework rather than at each call site;
- **routes to the right shard** - by the id for key-based reads, by scatter-gather across every
  shard for predicate-based ones (in async mode the shards are asked at the same time);
- **reads from the primary** by design. Replica reads are opt-in;
- **resolves the dialect per shard**, so a mixed-dialect deployment is not ruled out.

Criteria are ordinary SQLAlchemy expressions, so the whole of `sqlalchemy` is available inside
them:

**Sync**

```python
repo.find_where(WALLETS.c.state == "OPENED", WALLETS.c.balance < threshold)
```

**Async**

```python
await repo.find_where(WALLETS.c.state == "OPENED", WALLETS.c.balance < threshold)
```

Several criteria are `AND`ed. For anything else, build the expression yourself and pass it.

### No offset pagination, deliberately

There is no `all(offset, limit)` and no `find_where(..., offset, limit)`. Offset
pagination does not work across shards - "rows 100 to 120" has no meaning when the rows are on
four databases and each has its own ordering. Use keyset or temporal pagination, written on the
concrete repository where you know the ordering key.

## Custom queries

When the inherited surface is not enough, take a connection. The choice is about **consistency**,
and picking wrongly is the mistake this section exists to prevent.

**Sync**

```python
with self.db(key=..., model=..., shard=...) as conn: ...          # primary
with self.readonly_db(key=..., shard=...) as conn: ...             # the replica
conn = self.tx_db(key=...)                                        # the open transaction, or None
with self.tx_db_else_db(key=...) as conn: ...                     # that, else primary
```

**Async**

```python
async with self.db(key=..., model=..., shard=...) as conn: ...    # primary
async with self.readonly_db(key=..., shard=...) as conn: ...       # the replica
conn = self.tx_db(key=...)                                        # the open transaction, or None
async with self.tx_db_else_db(key=...) as conn: ...               # that, else primary
```

And for every shard at once:

**Sync**

```python
for shard, conn in self.dbs(): ...
for shard, conn in self.readonly_dbs(): ...
```

**Async**

```python
async for shard, conn in self.dbs(): ...
async for shard, conn in self.readonly_dbs(): ...
```

### Picking the right one

| you want | use |
| --- | --- |
| the default read behaviour | the inherited reads - the primary, outside any open transaction |
| a custom read that must be current | `db(...)` or `dbs()` |
| a custom read where lag is fine | `readonly_db(...)` or `readonly_dbs()` - these **are** the replica |
| a read that must see this transaction's uncommitted writes | `tx_db_else_db(...)` |
| an insert, update or delete | `tx_db_else_db(...)` - joins the open transaction, falls back to primary |
| "we must already be in a transaction", loudly | `tx_db(...)`, and raise on `None` |
| a scatter-gather | `dbs()` / `readonly_dbs()` |

They are context managers because a connection has a lifetime - `async with` in async mode, and
`async for` over the shards. `tx_db` is the exception: it returns the transaction's connection or
`None`, and the transaction owns it.

> **`Action.perform()` itself is not transactional.** The executor opens the transaction *after*
> `perform()` returns, and then persists the staged plan. So a custom `tx_db_else_db(...)` write
> called from inside `perform()` does **not** join the action's commit — there is no transaction
> bound yet, so it falls back to the primary and writes immediately, outside the atomicity the
> action promises. Inside `perform()`, stage changes with `plan.add(...)` / `plan.update(...)`.
>
> The same call is right inside a handler, a job, or anything running under
> `tm.transaction()` — it is only `perform()` where the transaction has not started yet.

### Examples

What comes back from any of them is a `Db` - a `sqlalchemy.Connection` with one method added
(in async mode an `AsyncDb`, SQLAlchemy's `AsyncConnection`, where `rows()` is awaited).
Everything SQLAlchemy documents is still there and still means what its documentation says;
`rows()` is the addition:

**Sync**

```python
def find_all_by_owner(self, owner_id: UUID) -> list[Wallet]:
    statement = select(WALLETS).where(
        WALLETS.c.owner_id == owner_id,
        WALLETS.c.state != WalletState.DELETED.name,
    )
    with self.readonly_db() as conn:
        return conn.rows(statement).fetch(self.from_record)
```

**Async**

```python
async def find_all_by_owner(self, owner_id: UUID) -> list[Wallet]:
    statement = select(WALLETS).where(
        WALLETS.c.owner_id == owner_id,
        WALLETS.c.state != WalletState.DELETED.name,
    )
    async with self.readonly_db() as conn:
        return (await conn.rows(statement)).fetch(self.from_record)
```

`conn.rows(statement)` hands back a `FetchResult`, and each of its terminals takes the mapper -
so the mapping is the last thing you read. The mapper is handed each row **read by column
name** - `row["currency"]` - which is what `from_record` takes. Its own parameter says which
record it expects; the statement is not checked against it, so select the table's columns when
you hand it `from_record`.

| | |
| --- | --- |
| `.fetch(mapper)` | every row, as a `list` |
| `.fetch_first(mapper)` | the first, or `None` |
| `.fetch_one(mapper)` | exactly one; raises on none **and** on two |
| `.fetch_one_or_none(mapper)` | at most one, else `None`; raises on two |
| `.each(mapper)` | every row, lazily - what a scatter-gather comprehension wants |
| `.yield_per(n)` | read `n` rows at a time; chains ahead of a terminal |
| `.partitions(mapper, size=None)` | batches of mapped rows, lazily |
| `.unique(strategy=None)` | drop duplicate rows, before mapping |

The difference between `fetch_first` and `fetch_one_or_none` is what a *second* row means:
ignored by the first, an error to the second. Pick the one that matches your invariant.

Each name says what it promises: `fetch_one` means exactly one, and a terminal that allows none
or takes the first of several says so in its name.

The mapper is any callable, not only `from_record`, so a projection is the same call with a
different argument:

**Sync**

```python
with self.readonly_db() as conn:
    return conn.rows(select(WALLETS.c.currency).distinct()).fetch(lambda row: str(row["currency"]))
```

**Async**

```python
async with self.readonly_db() as conn:
    found = await conn.rows(select(WALLETS.c.currency).distinct())
    return found.fetch(lambda row: str(row["currency"]))
```

**A query with no rows to map still uses `execute`.** A count, a rowcount, an `EXISTS`, DDL -
`rows()` would be claiming a shape the answer does not have:

**Sync**

```python
with self.tx_db_else_db(key=wallet_id) as conn:
    return conn.execute(delete(WALLETS).where(self.id_matches(wallet_id))).rowcount == 1
```

**Async**

```python
async with self.tx_db_else_db(key=wallet_id) as conn:
    erased = await conn.execute(delete(WALLETS).where(self.id_matches(wallet_id)))
    return erased.rowcount == 1
```

Scatter-gather is a flattening comprehension over the shards:

**Sync**

```python
return [
    wallet
    for _, conn in self.readonly_dbs()
    for wallet in conn.rows(statement).each(self.from_record)
]
```

**Async**

```python
return [
    wallet
    async for _, conn in self.readonly_dbs()
    for wallet in (await conn.rows(statement)).each(self.from_record)
]
```

> **`perform()` is not transactional.** The executor opens the transaction *after* `perform()`
> returns. A `tx_db_else_db(...)` write from inside `perform()` finds nothing bound, falls back
> to primary, and **writes immediately, outside the action's commit** - which is exactly the
> dual-write the framework exists to prevent, reintroduced by hand. Inside `perform()`, stage
> with `self.plan.add(...)` and `self.plan.update(...)`.

### Soft delete in custom queries

The inherited reads add the soft-delete predicate. A statement you build yourself does not - the
filter is not a table property, it is something the repository applies on the way to a query it
built. Write it out:

```python
select(WALLETS).where(
    WALLETS.c.owner_id == owner_id,
    WALLETS.c.state != WalletState.DELETED.name,
)
```

The predicate helpers (`find_where`, `find_one_where`, `exists_where`, `count`) keep both the
soft-delete filter and the shard routing, so reach for those before reaching for a connection.

## Discovery via the registry

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
