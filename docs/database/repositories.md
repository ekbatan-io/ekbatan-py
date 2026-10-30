# Repositories

A repository reads and writes one model or entity type. You write one per type, by extending
`ModelRepository` or `EntityRepository`. The base class already has the common reads and writes;
you add only the queries your business needs.

**Sync**

```python
class WalletRepository(ModelRepository[Wallet, WalletsRecord, UUID]):
    def __init__(self, registry: DatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(Wallet, WALLETS, registry,
                         sharding=EmbeddedBitsShardingStrategy(), clock=clock)

    def find_all_by_owner(self, owner_id: UUID) -> list[Wallet]:
        return self.find_all_where(WALLETS.c.owner_id == owner_id)
```

**Async**

```python
class WalletRepository(AsyncModelRepository[Wallet, WalletsRecord, UUID]):
    def __init__(self, registry: AsyncDatabaseRegistry, *, clock: Clock | None = None) -> None:
        super().__init__(Wallet, WALLETS, registry,
                         sharding=EmbeddedBitsShardingStrategy(), clock=clock)

    async def find_all_by_owner(self, owner_id: UUID) -> list[Wallet]:
        return await self.find_all_where(WALLETS.c.owner_id == owner_id)
```

The constructor names the model, its table and how its rows are spread over shards.
`find_all_by_owner` is the one query this business added. A complete repository also writes
`to_record` and `from_record`, shown in [Write the mapping](#write-the-mapping).

## The four classes

Four classes, in layers:

| class | what it is |
| --- | --- |
| `Repository[M]` | what the framework needs from a repository, and nothing more. It is a `Protocol`, so any class with these methods can be registered |
| `AbstractRepository[M, R, K]` | the SQL implementation of `Repository`. Abstract: `to_record` and `from_record` are yours |
| `ModelRepository[M, R, K]` | extend this for a model |
| `EntityRepository[M, R, K]` | extend this for an entity |

An application extends `ModelRepository` or `EntityRepository`, and nothing else. In async mode
the same four live in `ekbatan.aio` with an `Async` prefix (`AsyncModelRepository`,
`AsyncEntityRepository`), and every read and write is awaited. The rules are the same.

The three type parameters are `M`, the model; `R`, the table's record ([The record](#the-record),
below); and `K`, the type the id column holds.

For a plain uuid column, `K` is `UUID`: `ModelRepository[Wallet, WalletsRecord, UUID]`. The reads
by id then take `wallet.id.value`. Passing the typed id itself is refused, with a message saying
to pass its `.value`.

An edge case: if a schemagen [forced type](schemagen.md#forced-types) converts the id column to a typed id, that
typed id is the key instead, and a raw `UUID` is refused. The repository reads that type from the
converter's `from_db` return type while the program runs. So a return type naming a class imported
only under `TYPE_CHECKING` cannot be read, and the repository is refused when it is made.

## The record

A row is a **record**: a `dict` with one key per column, each typed as the column reads.
[schemagen](schemagen.md) writes a record class beside every table it generates - `WalletsRecord`
beside `WALLETS`. A column can be read two ways, and mypy checks both:

```python
record.column_owner_id()    # by method - the preferred way
record["owner_id"]          # the dict way - also fine, the same value, checked the same
```

Shortened, the class looks like this:

```python
class WalletsRecord(dict[str, Any]):
    # one method per column, typed by what the column holds
    def column_id(self) -> UUID: ...
    def column_version(self) -> int: ...
    def column_state(self) -> str: ...
    def column_owner_id(self) -> UUID: ...
    def column_currency(self) -> Currency: ...      # CHAR(3), through its converter
    def column_balance(self) -> Decimal: ...
    def column_created_date(self) -> Instant: ...
    def column_updated_date(self) -> Instant: ...
```

Each `column_x()` returns `record["x"]`. The generated class also has a constructor that takes
every column by keyword (that is what `to_record` calls), and it tells mypy the type of each
`record["x"]`. Your generated `schema.py` shows the class in full.

At run time a record is a plain `dict` and nothing more. It has `__slots__ = ()`, so there is no
per-record `__dict__`, and `record["x"]` is `dict`'s own lookup.

To mypy, though, a record has the table's exact shape, and that is the point. mypy reports a type
error when you:

- build one with a column left out, misspelt, added, or of the wrong type (a `str` where the
  column's converter wants a `Currency`);
- read a key the table does not have (`record["balanse"]`), or a method it does not have
  (`record.column_balanse()`);
- use a value as the wrong type.

A nullable column is `X | None`.

**Every column has a method, always `column_` and its name.** The prefix means no column name
can clash with a `dict` method (`keys`, `items`, `get`), be a keyword or start with a digit. A
character a Python name cannot hold becomes `_`: `order date` is `column_order_date()`, `2fa`
is `column_2fa()`. The key stays the column's real name: `record["order date"]`.

**The constructor keyword is the column's name, where that is a valid Python name**, and
otherwise as close as Python allows. The `dict` key is still the column's real name:

| column | keyword | method |
| --- | --- | --- |
| `wallet_id` | `wallet_id=` | `column_wallet_id()` |
| `class` | `class_=` (a keyword gets `_` after it) | `column_class()` |
| `order date` | `order_date=` (a character no name holds becomes `_`) | `column_order_date()` |
| `2fa` | `_2fa=` (a leading digit gets `_` before it) | `column_2fa()` |
| `__meta` | `_meta=` (a class would mangle two leading `_`) | `column___meta()` |
| `self` | `self_=` (`__init__` needs `self`) | `column_self()` |

An edge case: if two columns would get the same keyword or method (`order date` and
`order_date`), the one whose name needed no change keeps it, and the other gets `_2` (then `_3`,
and so on). The result is the same on every run, and schemagen says so when it writes the module.

**A column the database fills itself** - a server default, an identity, an auto-increment, a
computed column - is a keyword that defaults to `UNSET` (`from ekbatan.schema import UNSET`).
Left at that, the key is not in the record at all. So the INSERT leaves the column out and the
database fills it, and an update leaves the row's value as it is. On a record read from the
database the key is always there, so `record.column_created()` and `record["created"]` are typed
as the column reads, not as "maybe missing".

**A mypy limit.** mypy refuses `{**record}`, `dict(record)` and `other.update(record)`, because
it cannot pick an overload for a plain `str` key. Write `dict(record.items())`, or type the value
as `Mapping[str, Any]` first. At run time all three work.

If you write your schema by hand instead of generating it, write its record classes by hand
too, in the same style, next to the tables:

```python
WALLETS = Table(
    "wallets",
    metadata,
    Column("id", uuid_column(), primary_key=True),
    Column("version", BigInteger, nullable=False),
    ...
)


class WalletsRecord(dict[str, Any]):
    __slots__ = ()

    def __init__(self, *, id: UUID, version: int) -> None:  # every column, by keyword
        super().__init__(id=id, version=version)

    if TYPE_CHECKING:
        @overload  # type: ignore[override]
        def __getitem__(self, key: Literal["id"]) -> UUID: ...
        ...
        def __getitem__(self, key: str) -> Any: ...

    def column_id(self) -> UUID:
        return self["id"]
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
    return WalletsRecord(
        id=wallet.id.value,
        version=wallet.version,
        state=wallet.state.name,
        owner_id=wallet.owner_id,
        currency=wallet.currency,
        balance=wallet.balance,
        created_date=wallet.created_date,
        updated_date=wallet.updated_date or wallet.created_date,
    )

def from_record(self, record: WalletsRecord) -> Wallet:
    return Wallet(
        id=ShardedId[Wallet](record.column_id()),
        version=record.column_version(),
        state=WalletState[record.column_state()],
        owner_id=record.column_owner_id(),
        currency=record.column_currency(),
        balance=record.column_balance(),
        created_date=record.column_created_date(),
        updated_date=record.column_updated_date(),
    )
```

`to_record` builds the record by keyword: `WalletsRecord(id=..., ...)`. A column whose name is
not a valid Python name uses the keyword from the table above, such as `class_=` or
`order_date=`. The record it returns is the very `dict` the INSERT is given; no copy is made.

`from_record` is handed the table's record, a `WalletsRecord`. Usually it is built from the row
the database returned. Where an insert or update has no `RETURNING` (MySQL), it is the record
`to_record` wrote. Every read the framework does goes through `from_record`: `find_by_id`,
`get_by_id`, `find_all_by_ids`, `find_all_where`, `find_one_where`, `find_all`, the rows `add` and
`update` read back, a read across shards, and a query of your own that ends with
`.fetch(self.from_record)`.

Read each column by its method, as above; that is the preferred way. `record["owner_id"]` works
too: the record is a `dict`, the value is the same, and mypy checks the key just as it checks the
method.

Building the record from a row is cheap, about what reading the row's own mapping costs. The
keyword `__init__` is skipped, since the row's column names are already the keys.

**What a write sends is the record, key for key.** `add` and `update` write every column in the
record `to_record` returns, under the record's own keys, and nothing else. That holds for one row
or a batch, on every database:

- A field named `nick` that `to_record` puts under `nickname` is written to `nickname`, by an
  update just as by an insert.
- A column the record leaves out (one the database fills) is not written. On an update it keeps
  whatever the row held.
- One statement writes one set of columns, so every record in one `add_all` or `update_all` must
  have the same keys. A batch whose records differ is refused before anything is sent.

Where the field names and the column names match, which is most of the time, the mapping can be
two lines instead, because a dataclass already knows its own fields:

```python
def to_record(self, wallet: Wallet) -> WalletsRecord:
    return self.columns_by_field_name(wallet)

def from_record(self, record: WalletsRecord) -> Wallet:
    return self.model_by_field_name(record)
```

`columns_by_field_name` turns a typed id into its value and an enum into its name. Every other
value, a `datetime` included, goes to the column exactly as the model holds it. And
`model_by_field_name` hands a time back exactly as the driver read it: no zone is added, removed
or moved ([What you give is what is stored](multi-database.md#what-you-give-is-what-is-stored)).

These two work from the dataclass's fields at run time, so mypy cannot check them against the
record. Only the mapping written out column by column is checked.

`to_record` and `from_record` are plain `def` in async mode too: mapping does not wait on the
database.

**Why mapping by name is not automatic.** Rename a column without renaming the field, and a
mapping by name silently stops matching: no error, no warning, just a repository that no longer
reads back what it wrote. Writing the two lines makes it a choice you can see in your code. You
still do not list every column: `columns_by_field_name` and `model_by_field_name` are written
once, in the framework. You only choose them.

## Inherited reads and writes

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

There are also `add_no_result` / `update_no_result` and their `_all` forms, for a large batch
whose rebuilt objects you do not need back. On a database without `RETURNING`, the framework
otherwise has to rebuild the rows itself, and that costs time.

**Reads**

**Sync**

```python
repo.find_by_id(key)          # -> M | None
repo.get_by_id(key)           # -> M, raises EntityNotFoundError
repo.find_all_by_ids(keys)    # -> list[M]
repo.find_all(shard=...)      # -> list[M]
repo.find_one_where(condition, *more, shard=...)
repo.find_all_where(condition, *more, shard=...)
```

**Async**

```python
await repo.find_by_id(key)          # -> M | None
await repo.get_by_id(key)           # -> M, raises EntityNotFoundError
await repo.find_all_by_ids(keys)    # -> list[M]
await repo.find_all(shard=...)      # -> list[M]
await repo.find_one_where(condition, *more, shard=...)
await repo.find_all_where(condition, *more, shard=...)
```

**Counting and existence**

**Sync**

```python
repo.count(shard=...)
repo.count_where(condition, *more, shard=...)
repo.exists_by_id(key)
repo.exists_where(condition, *more, shard=...)
```

**Async**

```python
await repo.count(shard=...)
await repo.count_where(condition, *more, shard=...)
await repo.exists_by_id(key)
await repo.exists_where(condition, *more, shard=...)
```

Every one of them:

- **applies optimistic locking** on writes - `WHERE version = ?`, and the increment;
- **leaves out soft-deleted rows** on reads, in the framework, so no call has to filter them;
- **routes to the right shard** - by the id, for a read by id; for a read by condition, by asking
  every shard and combining the answers, a *scatter-gather* (in async mode the shards are asked at
  the same time);
- **reads from the primary** by design. Replica reads are opt-in;
- **resolves the dialect per shard**, so different shards may run different kinds of database.

Conditions are ordinary SQLAlchemy expressions, so everything SQLAlchemy offers can go in
them:

**Sync**

```python
repo.find_all_where(WALLETS.c.state == "OPENED", WALLETS.c.balance < threshold)
```

**Async**

```python
await repo.find_all_where(WALLETS.c.state == "OPENED", WALLETS.c.balance < threshold)
```

Several conditions are joined with `AND`. For anything else (an `OR`, say), build the expression
yourself and pass it. A `*_where` read needs at least one condition, and is refused without one.
The reads with no condition are `find_all()` and `count()`.

### No offset pagination, deliberately

There is no `find_all(offset, limit)` and no `find_all_where(..., offset, limit)`. Offset
pagination does not work across shards: "rows 100 to 120" has no meaning when the rows are on
four databases, each with its own ordering. Page by key instead ("the next 20 after the last id
I saw") or by time, in a method on your own repository, where you know the ordering key.

## Custom queries

When the inherited reads and writes are not enough, take a connection and write the query
yourself. Which connection you take decides **what your query sees**: the primary or a replica,
inside the open transaction or outside it. Picking the wrong one is the mistake this section helps
you avoid.

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

They are context managers because a connection has a lifetime: `with` (`async with` in async
mode), and `for` (`async for`) over the shards. `tx_db` is the exception: it returns the
transaction's connection or `None`, and the transaction owns it.

> **`Action.perform()` itself is not transactional.** The executor opens the transaction *after*
> `perform()` returns, and then persists the staged plan. So a custom `tx_db_else_db(...)` write
> called from inside `perform()` does **not** join the action's commit. There is no transaction
> yet, so it falls back to the primary and writes immediately, outside the action's commit: the
> very [dual write](../concepts/the-dual-write-trap.md) the framework exists to prevent. Inside `perform()`, stage
> changes with `self.plan.add(...)` / `self.plan.update(...)`.
>
> The same call is right inside a handler, a job, or anything running under
> `tm.transaction()` — it is only `perform()` where the transaction has not started yet.

### Examples

Each of the connection methods above gives you a `Db`: a `sqlalchemy.Connection` with one method
added, `rows()`. (In async mode it is an `AsyncDb`, SQLAlchemy's `AsyncConnection`, and `rows()`
is awaited.)
Everything SQLAlchemy documents is still there, and means what its documentation says:

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

`conn.rows(statement)` returns a `FetchResult`. Each of its finishing methods (`fetch` and the
others in the table below) takes a *mapper*: a function that turns one row into what you want
back, such as `self.from_record`. So the mapping is the last thing on the line. The mapper is
handed each row **read by column name**:

- A mapper whose first parameter is typed as a record class gets that class, built from the row.
  That covers `from_record(self, record: WalletsRecord)` and any function of your own typed the
  same way. So `record.column_currency()` works (the preferred way), and so does
  `record["currency"]`. A repository's own method gets the repository's record even when its
  annotation cannot be read.
- Anything else, such as a lambda, gets the row's mapping: `row["currency"]`.

The statement is not checked against the record, so select the table's columns when you hand
it `from_record`.

| | |
| --- | --- |
| `.fetch(mapper)` | every row, as a `list` |
| `.fetch_any(mapper)` | the first, or `None`; any rows after it are ignored |
| `.fetch_one(mapper)` | at most one, else `None`; raises on two |
| `.fetch_single(mapper)` | exactly one; raises on none **and** on two |
| `.each(mapper)` | every row, lazily - what a scatter-gather comprehension wants |
| `.yield_per(n)` | read `n` rows at a time; call it before one of the finishing methods |
| `.partitions(mapper, size=None)` | batches of mapped rows, lazily |
| `.unique(strategy=None)` | drop duplicate rows, before mapping |

The difference between `fetch_any` and `fetch_one` is what a *second* row means: `fetch_any`
ignores it, and `fetch_one` raises. Pick the one that matches what your query should return.
`fetch_single` is for a query that must return exactly one row: none is an error too.

The mapper can be any function, not only `from_record`. So reading just some columns is the
same call with a different mapper:

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

**A statement with no rows to map uses plain `execute`.** A count, a row count, an `EXISTS`,
DDL: none of these returns rows to map, so `rows()` does not fit:

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

A scatter-gather across every shard is one comprehension over the shards:

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

### Soft delete in custom queries

The inherited reads leave out soft-deleted rows. A statement you build yourself does not: the
filter is added by the repository's own reads, not by the table. Write it out:

```python
select(WALLETS).where(
    WALLETS.c.owner_id == owner_id,
    WALLETS.c.state != WalletState.DELETED.name,
)
```

The `*_where` reads (`find_all_where`, `find_one_where`, `exists_where`, `count_where`) keep
both the soft-delete filter and the shard routing, so try those before taking a connection.

## Discovery via the registry

There is no decorator, and nothing scans your code for repositories. You list them in a
registry, in your wiring code, where you can see them:

```python
repositories = (
    RepositoryRegistry.builder()
    .with_model_repository(wallet_repository)
    .with_entity_repository(notification_repository)
    .build()
)
```

There are two calls because models and entities differ: a model emits events and an entity does
not. The registry also checks that the table can hold what the type carries.

## See also

- [Actions](../concepts/actions.md) - where repository writes actually happen
- [Sharding](sharding.md) - how a key resolves to a database
- [The transaction manager](transaction-manager.md) - transactional work outside an action
- [Models and entities](../concepts/models-and-entities.md) - what is being mapped
