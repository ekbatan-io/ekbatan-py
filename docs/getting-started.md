# Getting started

What it takes to have a service writing domain rows and their events in one transaction: one
dependency, one connection url, one migration, one generated schema module, and a wiring
function you can read top to bottom.

The step with any depth to it is code generation - see [schemagen](database/schemagen.md).

## Requirements

Python **3.13** or newer. The framework uses PEP 695 type parameters and `Self`, and the suite
runs on 3.13 and 3.14.

A database: PostgreSQL, MySQL or MariaDB. SQLite works and the suite uses it, but it is not a
supported production dialect - it has no `SKIP LOCKED`, so the job scheduler and the keyed lock
degrade to behaviour that is fine for one process and wrong for a cluster.

## Install

```
uv add "ekbatan[postgres]"
```

The quotes matter: `zsh`, the macOS default shell, reads bare brackets as a glob and stops with
*"no matches found"*.

Everything optional is an extra, and nothing you do not name is installed:

| extra | brings | when |
| --- | --- | --- |
| `postgres` | psycopg 3 | your dialect |
| `mysql`, `mariadb` | PyMySQL | your dialect |
| `postgres-async` | asyncpg | the async API, `ekbatan.aio`, on PostgreSQL - beside `postgres` |
| `mysql-async`, `mariadb-async` | aiomysql | `ekbatan.aio` on MySQL or MariaDB - beside the sync extra |
| `sqlite-async` | aiosqlite | `ekbatan.aio` on SQLite |
| `migrations` | yoyo | you want `ekbatan.migrations.migrate` to run your SQL migrations across every shard |
| `redis` | redis-py | you want the Redis-backed keyed lock rather than the SQL one |
| `codegen` | testcontainers, psycopg | **development only** - see below |

`SQLAlchemy` and `ekbatan-py-db-scheduler` come unconditionally. The second is this repository's
port of `kagkarlsson/db-scheduler`, published separately because there is no upstream for it on
PyPI, and pinned to the version `ekbatan` was cut with.

`codegen` belongs in the dev group, not the runtime dependencies. It starts a database container
to reflect your schema, which is a thing your build does and your deployment must never do:

```
uv add --dev "ekbatan[codegen]"
```

That is the whole of the setup. There is no plugin to register, no build step to hook into and
no flag to remember: Python knows a function's parameter names and a dataclass's fields at
runtime, so the framework reads them from your classes as they are.

## The one thing that is generated

The table definitions your repositories query are generated from your migrations:

```toml
# pyproject.toml
[tool.ekbatan.schemagen]
migrations = "migrations"
out = "myapp/schema.py"
image = "postgres:17-alpine"
```

```
uv run python -m ekbatan.schemagen
```

It starts a throwaway container, runs **your** migrations into it, reflects the result and
writes a module of SQLAlchemy `Table` definitions. Forced types, includes and excludes go in the
same table - see [schemagen](database/schemagen.md). The output is **gitignored**, because it is
derived from a database you have to start, and it is regenerated whenever the migrations change.

**Gitignored is not left out of the package - check that it is not.** Some build tools leave a
gitignored file out of the wheel: poetry-core and hatchling drop `schema.py` unless told to keep
it, while `uv_build` keeps it. The wheel then imports a module it does not contain, and fails at
startup. *Complete project setup* has the one line each tool needs, and a command that lists what
a wheel holds.

There is a second, smaller generator for typed builders:

```
uv run python -m ekbatan.codegen myapp.model --out myapp/builders.py
```

That one is *committed*. It is the only thing Python cannot infer for itself - the runtime
builder resolves setters by name, so a type checker sees `__getattr__` and `.balance("oops")`
slips through. See [Typed builders](../README.md#typed-builders).

## A Makefile, because uv has no task runner

`uv` manages dependencies and runs commands. What it does not have is a **dependency graph**: something that knows `schema.py` is derived from
`migrations/` and rebuilds it when they change. Without that, either every run starts a
container or you regenerate by hand and forget.

Make is the answer, and it is four lines:

```make
myapp/schema.py: $(wildcard migrations/*.sql) pyproject.toml
	uv run python -m ekbatan.schemagen

lint: myapp/schema.py
	uv run mypy
```

`lint` depends on the generated module because mypy reads it - a fresh clone would otherwise
report `cannot find implementation for module myapp.schema`, which is true and unhelpful.

Every example in [`ekbatan-py-examples/`](../ekbatan-py-examples) carries one of these. The repository root delegates to them with `$(MAKE) -C`.
The complete one for a new project - the builders too, and a run and a test target - is on the
website's *Complete project setup* page.

## Your first action, end to end

The framework comes in two modes: the sync API, and `ekbatan.aio`, where every database call is
awaited. Only the code that waits on the database differs, so where a sample differs it is shown
both ways, under **Sync** and **Async**. [Sync or async?](sync-or-async.md) helps you choose.

### 1. A model

```python
@dataclass(frozen=True, kw_only=True)
class Wallet(Model[ShardedId["Wallet"], WalletState, WalletBuilder]):
    owner_id: UUID
    currency: str
    balance: Decimal

    def deposit(self, amount: Decimal) -> Wallet:
        if amount <= 0:
            raise ValidationError(f"deposit amount must be positive, got {amount}")
        new_balance = self.balance + amount
        return (
            self.copy()
            .with_event(MoneyDepositedEvent(model_id=str(self.id), amount=amount, new_balance=new_balance))
            .balance(new_balance)
            .build()
        )
```

Frozen, so a deposit returns a new wallet rather than mutating one. `with_event` is the entire
outbox API from a domain author's point of view.

**Time is UTC, and stored as given.** A model's dates come from the framework's clock
(`self.now()` in an action) - UTC with no zone attached - and are stored in zone-less columns
exactly as they are: the framework never converts a time
([What you give is what is stored](database/multi-database.md#what-you-give-is-what-is-stored)). UTC is the strong
recommendation everywhere - in your code and on the database - see
[Always UTC](database/multi-database.md#always-utc).

### 2. A repository

**Sync**

```python
class WalletRepository(ModelRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
    def __init__(self, registry: DatabaseRegistry) -> None:
        super().__init__(Wallet, WALLETS, registry)

    def to_record(self, wallet: Wallet) -> WalletsRecord:
        return self.columns_by_field_name(wallet)

    def from_record(self, record: WalletsRecord) -> Wallet:
        return self.model_by_field_name(record)
```

**Async**

```python
class WalletRepository(AsyncModelRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
    def __init__(self, registry: AsyncDatabaseRegistry) -> None:
        super().__init__(Wallet, WALLETS, registry)

    def to_record(self, wallet: Wallet) -> WalletsRecord:       # plain def: mapping waits on nothing
        return self.columns_by_field_name(wallet)

    def from_record(self, record: WalletsRecord) -> Wallet:
        return self.model_by_field_name(record)
```

That is the whole repository - `get`, `find_where`, `add`, `update`, counting and existence are
inherited. `WalletsRecord` is the table's record, which schemagen writes into `schema.py` beside
`WALLETS`: a `TypedDict` with a key per column, typed as the column reads, so mypy checks the
mapping against the table. The mapping between a record and the model is yours to write, and
when the names line up it is the two methods above: every field to the column of the same name. A repository without
them is refused when it is created, so a renamed column can never quietly stop matching. Where a
name or a shape differs, write the mapping out instead.

What you add here are the questions your application actually asks:

**Sync**

```python
    def find_all_by_owner(self, owner_id: UUID) -> list[Wallet]:
        return self.find_where(WALLETS.c.owner_id == owner_id)
```

**Async**

```python
    async def find_all_by_owner(self, owner_id: UUID) -> list[Wallet]:
        return await self.find_where(WALLETS.c.owner_id == owner_id)
```

### 3. An action

**Sync**

```python
class DepositMoneyAction(Action[DepositParams, Wallet]):
    def __init__(self, wallets: WalletRepository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        wallet = self.wallets.get(params.wallet_id)
        return self.plan.update(wallet.deposit(params.amount))
```

**Async**

```python
class DepositMoneyAction(AsyncAction[DepositParams, Wallet]):
    def __init__(self, wallets: WalletRepository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    async def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        wallet = await self.wallets.get(params.wallet_id)
        return self.plan.update(wallet.deposit(params.amount))
```

`self.plan.update` writes nothing. It *stages*. The executor opens one transaction afterwards
and flushes the wallet row and its event together.

### 4. Wire it

See [Wiring](wiring.md) for the whole of it. The short version:

**Sync**

```python
registry = DatabaseRegistry.from_config(layout)
wallets = WalletRepository(registry)

executor = ActionExecutor(
    namespace="com.example.wallets",
    registry=registry,
    repositories=RepositoryRegistry.builder().with_model_repository(wallets).build(),
    actions=ActionRegistry.builder().with_action(DepositMoneyAction(wallets, clock)).build(),
    events=EVENTS,
)
```

**Async**

```python
registry = AsyncDatabaseRegistry.from_config(layout)
wallets = WalletRepository(registry)

executor = AsyncActionExecutor(
    namespace="com.example.wallets",
    registry=registry,
    repositories=RepositoryRegistry.builder().with_model_repository(wallets).build(),
    actions=ActionRegistry.builder().with_action(DepositMoneyAction(wallets, clock)).build(),
    events=EVENTS,
)
```

### 5. Run it

**Sync**

```python
wallet = executor.execute(
    DepositMoneyAction,
    DepositParams(wallet_id=wallet_id, amount=Decimal("25.50")),
)
```

**Async**

```python
wallet = await executor.execute(
    DepositMoneyAction,
    DepositParams(wallet_id=wallet_id, amount=Decimal("25.50")),
)
```

One transaction. The wallet row, its version bump and the outbox row, or none of them.

## Testing before there is a database

An action's logic needs no database at all. `ActionSpec` (`AsyncActionSpec` in async mode, from
`ekbatan.aio.testing`) runs the action and checks what it staged and emitted:

**Sync**

```python
(ActionSpec.of(DepositMoneyAction(wallets, clock))
    .with_principal("alice")
    .execute(DepositParams(wallet_id=..., amount=Decimal("50.00")))
    .assert_updated(Wallet, lambda w: w.balance == Decimal("150.00"))
    .assert_emitted(MoneyDepositedEvent, lambda e: e.amount == Decimal("50.00"))
    .assert_no_additions())
```

**Async**

```python
checked = await (
    AsyncActionSpec.of(DepositMoneyAction(wallets, clock))
    .with_principal("alice")
    .execute(DepositParams(wallet_id=..., amount=Decimal("50.00")))
)
(checked
    .assert_updated(Wallet, lambda w: w.balance == Decimal("150.00"))
    .assert_emitted(MoneyDepositedEvent, lambda e: e.amount == Decimal("50.00"))
    .assert_no_additions())
```

A verifier can be a `lambda` returning its verdict or a function that asserts inside itself; a
falsy verdict fails, so neither can pass by accident. When plain `assert` reads better,
`run_action` (`async_run_action`) hands back the same information as data:

**Sync**

```python
staged = run_action(DepositMoneyAction(wallets, clock), DepositParams(wallet_id=..., amount=Decimal("50.00")))

assert staged.result.balance == Decimal("150.00")
assert staged.emitted(MoneyDepositedEvent)[0].amount == Decimal("50.00")
```

**Async**

```python
staged = await async_run_action(DepositMoneyAction(wallets, clock), DepositParams(wallet_id=..., amount=Decimal("50.00")))

assert staged.result.balance == Decimal("150.00")
assert staged.emitted(MoneyDepositedEvent)[0].amount == Decimal("50.00")
```

Both bind the plan through the same mechanism the executor uses, so `self.plan`,
`self.clock` and the `principal` argument behave as they do in production. Nothing is written - which is
also the limit of it: that state and events land *together* is a claim about a transaction, and
only a test against a real database checks it.

## Async mode

The async samples above need the async driver beside the sync one -
`"ekbatan[postgres,postgres-async,migrations]"`, `mysql-async`, `mariadb-async` or
`sqlite-async`. Models, events, migrations and the generated schema are the same in both modes;
the keyed lock has an async twin in `ekbatan.aio.locks`, an event handler may be `async def`, and
the jobs stay on threads. How to choose is [Sync or async?](sync-or-async.md); the reference is
[Native asyncio](runtime/async.md).

## Where to go next

- [Wiring](wiring.md) - the complete bootstrap, every block of it
- [schemagen](database/schemagen.md) - the code generation step in full
- [Wiring with FastAPI](wiring-fastapi.md) - a lifespan, a dependency, and the three pools
- [Concurrency](runtime/concurrency.md) - threads, the GIL, and the one way to use this wrongly
- [Sync or async?](sync-or-async.md) - how to choose, and what is the same either way
- [`ekbatan-py-examples/`](../ekbatan-py-examples) - eleven runnable projects
