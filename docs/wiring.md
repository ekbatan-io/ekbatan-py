# Wiring

How to build a whole service in one function: the databases, the repositories, the actions, the
event handlers and the jobs. Nothing is found automatically. You list every piece, so this one
function shows everything the service contains.

There is no dependency-injection container to configure and no decorator to remember. Things are
built in the order the lines are written, and that is the only startup order there is.

> The code below is adapted from
> [`ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/app.py`](../ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/app.py),
> which runs and is tested. The HTTP half is in [Wiring with FastAPI](wiring-fastapi.md); this
> page is the part that has nothing to do with HTTP.

**`build()` and `WalletService` are the example's own code**, not part of Ekbatan. `build()` is
one function that creates Ekbatan's objects, and `WalletService` is one class that holds them.
Its `start()` calls `jobs.start()`, and its `close()` stops the jobs and closes the registry and
the data sources.

Some of these objects hold connections or threads: the `DatabaseRegistry`, the lock provider and
its data source, the `JobRegistry` and its data source. Create them in your app's startup code
(the FastAPI lifespan, or the top of a worker's `main()`), and close them in its shutdown code.
Never create them at import time. [Startup and shutdown](startup-and-shutdown.md) lists each object's startup
and shutdown call, without the example's class.

Ordinary code uses the sync API, and `async def` code uses `ekbatan.aio`; the code below shows
both.

## What we're wiring

`build()` returns one object, a `WalletService`. The code that uses the service does not need to
know which database, handlers or jobs are behind it, and neither do the domain classes it is
built from.

**Sync**

```python
@dataclass(frozen=True, slots=True)
class WalletService:
    registry: DatabaseRegistry
    wallet_repository: WalletRepository
    notification_repository: NotificationRepository
    executor: ActionExecutor
    locks: KeyedLockProvider
    jobs: JobRegistry

    def start(self) -> None:
        self.jobs.start()

    def close(self) -> None:
        # Order matters: stop taking work before closing what the work runs on.
        self.jobs.stop()
        self.locks.close()
        self.registry.close()
```

**Async**

```python
@dataclass(frozen=True, slots=True)
class WalletService:
    registry: AsyncDatabaseRegistry          # the requests, on ekbatan.aio
    wallet_repository: WalletRepository
    notification_repository: NotificationRepository
    executor: AsyncActionExecutor
    locks: AsyncKeyedLockProvider
    handler_loop: HandlerLoop                # the loop the async handler runs on
    handler_registry: AsyncDatabaseRegistry  # the handler's own, used only on that loop
    events_registry: DatabaseRegistry        # sync: fan-out, dispatch and the jobs
    jobs: JobRegistry

    def start(self) -> None:
        self.jobs.start()

    async def aclose(self) -> None:
        # Order matters: stop taking work before closing what the work runs on. Stopping the
        # jobs waits for a dispatch in flight, so it runs on a thread, off the event loop.
        await asyncio.to_thread(self.jobs.stop)
        await asyncio.to_thread(self.handler_loop.run, self.handler_registry.aclose())
        self.handler_loop.close()
        self.events_registry.close()
        self.locks.close()
        await self.registry.aclose()
```

It is a frozen dataclass: built once, then passed around and never changed. `start` and `close`
(`aclose`, awaited, in async mode) are its only lifecycle methods.

## The domain (unchanged whether you wire it by hand or not)

These are the classes the rest of the page puts together, shortened. Nothing in them depends on
how they are wired:

**Sync**

```python
@dataclass(frozen=True, kw_only=True)
class Wallet(Model[ShardedId["Wallet"], WalletState, WalletBuilder]):
    owner_id: UUID
    currency: str
    balance: Decimal

    def validate(self) -> None:
        if self.balance < 0:
            raise ValidationError("balance cannot be negative")

    def deposit(self, amount: Decimal) -> "Wallet": ...


class WalletRepository(ModelRepository[Wallet, WalletsRecord, UUID]):
    def __init__(self, registry: DatabaseRegistry) -> None:
        super().__init__(Wallet, WALLETS, registry)


class WalletDepositMoneyAction(Action["WalletDepositMoneyAction.Params", Wallet]):
    def __init__(self, clock: Clock, wallet_repository: WalletRepository) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    def perform(self, principal: str | None, params: Params) -> Wallet:
        return self.plan.update(self.wallet_repository.get_by_id(params.wallet_id.value).deposit(params.amount))
```

**Async**

```python
@dataclass(frozen=True, kw_only=True)
class Wallet(Model[ShardedId["Wallet"], WalletState, WalletBuilder]):
    owner_id: UUID
    currency: str
    balance: Decimal

    def validate(self) -> None:
        if self.balance < 0:
            raise ValidationError("balance cannot be negative")

    def deposit(self, amount: Decimal) -> "Wallet": ...


class WalletRepository(AsyncModelRepository[Wallet, WalletsRecord, UUID]):
    def __init__(self, registry: AsyncDatabaseRegistry) -> None:
        super().__init__(Wallet, WALLETS, registry)


class WalletDepositMoneyAction(AsyncAction["WalletDepositMoneyAction.Params", Wallet]):
    def __init__(self, clock: Clock, wallet_repository: WalletRepository) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    async def perform(self, principal: str | None, params: Params) -> Wallet:
        wallet = await self.wallet_repository.get_by_id(params.wallet_id.value)
        return self.plan.update(wallet.deposit(params.amount))
```

Each class takes what it needs through `__init__`. The model is the same in both modes. In async
mode the repository and the action are the `ekbatan.aio` kinds, and the repository's `to_record`
and `from_record` stay plain `def`s. All the wiring below has to do is **create each object after
the objects it needs, and pass those in.** That is all a dependency-injection container would
have done for you.

## 1. Settings

The Ekbatan settings live in `ekbatan.yaml` - see [Config files](database/config-files.md):

**Sync**

```yaml
ekbatan:
  namespace: example.fastapi_wallet_rest_pg
  sharding:
    defaultShard: {group: 0, member: 0}
    groups:
      - group: 0
        name: global
        members:
          - member: 0
            name: global
            configs:
              primaryConfig:
                url: ${WALLET_DATABASE_URL:postgresql+psycopg://localhost:5432/wallet}
                # The login lives here, never in the url - a url is logged and repeated.
                username: ${WALLET_DATABASE_USERNAME:ekbatan}
                password: ${WALLET_DATABASE_PASSWORD:ekbatan}
                poolSize: 10
                poolTimeout: 5      # past this, a waiting request fails: a 503
              jobsConfig:   # the same url and login
                poolSize: 8
              lockConfig:   # the same url and login
                poolSize: 5
  local-event-handler:
    handling:
      enabled: true
```

**Async**

```yaml
ekbatan:
  namespace: example.fastapi_wallet_rest_async_pg
  sharding:
    defaultShard: {group: 0, member: 0}
    groups:
      - group: 0
        name: global
        members:
          - member: 0
            name: global
            configs:
              # The url names the sync driver; the async side connects with asyncpg.
              primaryConfig:
                url: ${WALLET_DATABASE_URL:postgresql+psycopg://localhost:5432/wallet}
                # The login lives here, never in the url - a url is logged and repeated.
                username: ${WALLET_DATABASE_USERNAME:ekbatan}
                password: ${WALLET_DATABASE_PASSWORD:ekbatan}
                poolSize: 10
                poolTimeout: 5      # past this, a waiting request fails: a 503
              handlersConfig:  # the same url and login - the async handler's own pool
                poolSize: 5
              jobsConfig:      # the same url and login - sync
                poolSize: 8
              lockConfig:      # the same url and login - async
                poolSize: 5
  local-event-handler:
    handling:
      enabled: true
```

The url and the login are `${NAME:default}` placeholders. Each is read from an environment
variable, and the value after the colon is used when the variable is not set. So one file works
everywhere, and the example runs against a local database with no setup. Settings that belong to
this service, not to Ekbatan, go in a small `Settings` class:

```python
@dataclass(frozen=True, slots=True)
class Settings:
    config_file: Path = Path(os.environ.get("WALLET_CONFIG_FILE", CONFIG_FILE))
    job_workers: int = int(os.environ.get("WALLET_JOB_WORKERS", "3"))
    run_migrations: bool = os.environ.get("WALLET_MIGRATE", "1") != "0"
```

In the code below, `config` is an instance of this `Settings`.

`job_workers` is how many threads run jobs. They are real OS threads, so choose the number on purpose. See [Concurrency](runtime/concurrency.md).

## 2. The layout: which databases, and which is the default

```python
# `ekbatan.yaml`'s `${...}` placeholders - the url and the login - are read from the
# process environment as the file is loaded, each by its name.
ekbatan = load_ekbatan_config(config.config_file)
layout = ekbatan.sharding
```

`Settings` holds no copy of the environment. The login is read once, by name, while the file is
loaded, so no `repr` of the settings or the service can show a password. A test points the
service at its own database by setting those variables, with pytest's `monkeypatch.setenv`,
which puts them back afterwards.

The layout is a list of groups of shards. Each shard is a *member* of a group, with named
connection settings. One group with one member is the ordinary case, and costs nothing. To shard,
add members or groups; see [Sharding](database/sharding.md). The same `ShardingConfig` can also be built
in code, with `ShardingConfig.builder()`; the examples use the file.

`"primaryConfig"` is required. Add `"secondaryConfig"` for a read replica; leave it out, and
reads go to the primary too. The registry builds the pools for those two. `"jobsConfig"` and
`"lockConfig"` are for the job scheduler and the keyed locks, and `build()` makes those pools
itself:

**Sync**

```python
member = layout.groups[0].members[0]
jobs_data_source = DataSource.from_config(member.config_for("jobsConfig"))
lock_data_source = DataSource.from_config(member.config_for("lockConfig"))
```

**Async**

```python
member = layout.groups[0].members[0]
# sync: the scheduler
jobs_data_source = DataSource.from_config(member.config_for("jobsConfig"))
# async: the leases
lock_data_source = AsyncDataSource.from_config(member.config_for("lockConfig"))
# async: the handler
handler_data_source = AsyncDataSource.from_config(member.config_for("handlersConfig"))
```

Each is a data source built from that member's settings (the async ones with
`AsyncDataSource.from_config`). They are yours, so close them at shutdown, after the scheduler and
the locks have stopped: `close()`, or `await aclose()` for an async one. A manager closes the data
sources it was given, so on the async side `events_registry.close()` closes the jobs pool.

## 3. Migrations, before anything opens a pool

```python
if config.run_migrations:
    migrate(layout, MIGRATIONS)
```

`migrate` migrates every shard, one at a time, and stops at the first failure. It runs plain SQL
files through yoyo, used as a **library**: there is no yoyo config file and no command to run. A
config file that has to be found is one that goes missing in the one environment you did not
test.

It refuses to run if an applied migration has since been edited, renamed or deleted, or if a new
one is older than one already applied. It is safe to start several copies of the service at
once: each database is locked while it is migrated, so one process migrates and the rest wait. See [Migrations](database/migrations.md).

Order matters: run this before anything opens a pool against a schema that may not exist yet.
Migrations are sync in both modes, so an async service runs them here too, before its first
await.

## 4. The registry

**Sync**

```python
registry = DatabaseRegistry.from_config(layout)
```

**Async**

```python
registry = AsyncDatabaseRegistry.from_config(layout)

# What polls - the scheduler, fan-out, dispatch - stays sync, on the jobs pool.
events_registry = (
    DatabaseRegistry.builder()
    .with_default_database(TransactionManager(layout.default_shard, jobs_data_source))
    .build()
)
```

The registry holds one `TransactionManager` per shard, looked up by `ShardIdentifier`.
Everything that touches a database goes through it, sharded or not. In async mode it is an
`AsyncDatabaseRegistry` of `AsyncTransactionManager`s, built from the same layout. The async side
reads the url only to know which database to reach, and connects through its async driver.

The async block also builds `events_registry`, a small sync registry on the jobs pool. The parts
that poll the database (the job scheduler, fan-out and dispatch) are sync in both modes, so they
use it.

## 5. Repositories

```python
wallet_repository = WalletRepository(registry, clock=clock)
notification_repository = NotificationRepository(registry, clock=clock)

repositories = (
    RepositoryRegistry.builder()
    .with_model_repository(wallet_repository)
    .with_entity_repository(notification_repository)
    .build()
)
```

`clock` is the service's one clock: `SystemClock()`, unless a test passes a fixed one to
`build()`.

The same lines in both modes; only the repository classes differ. Make one repository for each
model or entity type. `with_model_repository` and `with_entity_repository` are separate calls
because models and entities differ: a model emits events and an entity does not. The registry also
checks that the table can hold what the type carries.

## 6. The executor

**Sync**

```python
executor = ActionExecutor(
    namespace=ekbatan.namespace,
    registry=registry,
    repositories=repositories,
    actions=(
        ActionRegistry.builder()
        .with_action(WalletCreateAction(clock))
        .with_action(WalletDepositMoneyAction(clock, wallet_repository))
        .with_action(WalletCloseAction(clock, wallet_repository))
        .with_action(CreateNotificationAction(clock))
        .build()
    ),
    clock=clock,
)
```

**Async**

```python
executor = AsyncActionExecutor(
    namespace=ekbatan.namespace,
    registry=registry,
    repositories=repositories,
    actions=(
        ActionRegistry.builder()
        .with_action(WalletCreateAction(clock))
        .with_action(WalletDepositMoneyAction(clock, wallet_repository))
        .with_action(WalletCloseAction(clock, wallet_repository))
        .build()
    ),
    clock=clock,
)
```

The executor is what application code calls. Each action is created once and shared by every
call (state for one call lives on the plan, not on the action), so register one instance of each,
built with whatever it reads from.

`namespace` is written on every outbox row, and is how a consumer tells one service's events from
another's. Pick it once: changing it later splits your own stream of events in two.

## 7. Event handlers, if the service handles its own events

**Sync**

```python
handlers = EventHandlerRegistry([WalletMoneyDepositedEventHandler(executor)])
events_config = ekbatan.local_event_handler  # `local-event-handler` in the file
```

**Async**

```python
# The async handler awaits an executor of its own, on a registry of its own: an async pool
# belongs to the loop it is used on, and the handler runs on dispatch's handler loop. The
# stipend job's work runs on the same loop, so it uses them too.
handler_loop = HandlerLoop()
handler_registry = (
    AsyncDatabaseRegistry.builder()
    .with_default_database(AsyncTransactionManager(layout.default_shard, handler_data_source))
    .build()
)
handler_wallet_repository = WalletRepository(handler_registry, clock=clock)
handler_executor = AsyncActionExecutor(
    namespace=ekbatan.namespace,
    registry=handler_registry,
    repositories=[handler_wallet_repository, NotificationRepository(handler_registry, clock=clock)],
    actions=[WalletDepositMoneyAction(clock, handler_wallet_repository), CreateNotificationAction(clock)],
    clock=clock,
)
handlers = EventHandlerRegistry([WalletMoneyDepositedEventHandler(handler_executor)])
events_config = ekbatan.local_event_handler  # `local-event-handler` in the file
```

This block is optional. Without it, the executor still writes every event, with
`delivered=false`. That is the right state when something outside the service, such as Debezium,
reads the outbox. The handlers here run *in the same process*, and you can use both.

The handler is given the `executor`, because a handler does its work by running an action of its
own, with its own transaction and its own outbox row. It is not given the transaction that
produced the event: that transaction has committed and is gone by the time the handler runs.

In async mode the handler also gets its own registry and executor, on the `handlersConfig` pool.
An async connection pool belongs to the event loop it is used on, and handlers run on a loop of
their own, the `HandlerLoop`.

## 8. Locks and jobs

**Sync**

```python
locks = PostgresKeyedLockProvider(lock_data_source)

jobs = (
    job_registry()
    .data_source(jobs_data_source)
    .with_jobs([
        EventFanoutJob(registry, handlers, config=events_config, clock=clock),
        EventHandlingJob(registry, handlers,
                         config=events_config, clock=clock,
                         # Registered either way; scheduled only when the switch is on.
                         enabled=events_config.handling.enabled),
        WalletStipendJob(executor, wallet_repository),
    ])
    .with_config(ekbatan.jobs)  # `jobs` in the file, if any
    .workers(config.job_workers)
    .build()
)
```

**Async**

```python
locks = AsyncPostgresKeyedLockProvider(lock_data_source)

jobs = (
    job_registry()
    .data_source(jobs_data_source)
    .with_jobs([
        EventFanoutJob(events_registry, handlers, config=events_config, clock=clock),
        EventHandlingJob(events_registry, handlers,
                         config=events_config, clock=clock,
                         # Registered either way; scheduled only when the switch is on.
                         enabled=events_config.handling.enabled,
                         handler_loop=handler_loop),
        WalletStipendJob(handler_loop, handler_executor, handler_wallet_repository),
    ])
    .with_config(ekbatan.jobs)  # `jobs` in the file, if any
    .workers(config.job_workers)
    .build()
)
```

Fan-out and dispatch are themselves jobs, by design. Like any other job, the event pipeline is
scheduled work that runs on one node of the cluster at a time. So it gets the same leader
election, the same heartbeat, and the same recovery when a node dies mid-run. Register them, and
`jobs.start()` runs the pipeline. Leave them out, and events pile up unread.

Jobs are sync in both modes: `DistributedJob.execute` is a plain `def`, run on a worker thread.
An async service registers them on a sync registry, as above. A job's *work* can still be
awaited. In the async block, `WalletStipendJob.execute` hands a coroutine to the handler loop
with `handler_loop.run(...)` and waits for it, so the work can await the handler's executor and
repositories. If the work fails, db-scheduler still handles the retry.

### Three pools, and why

This is the part worth copying into a real deployment: three connection pools, each a named
config on the member (`primaryConfig`, `jobsConfig`, `lockConfig`).

| pool | sized for | why it is separate |
| --- | --- | --- |
| **requests** | concurrent requests | the ordinary one |
| **jobs** | `job_workers + 3` | one connection per worker, and one each for db-scheduler's three loops: finding due jobs, the heartbeat, and spotting dead ones. Share the request pool and a traffic burst can starve that heartbeat - and an execution whose heartbeat stops is **declared dead and run again somewhere else** |
| **locks** | maximum leases held at once | a keyed lock holds its connection for the life of the lease. That *is* the lock. A lease taken during a request would otherwise pin a request connection for its duration |

The example sizes the jobs pool at 8 for its 3 workers, because a job holding the last
connection starves the heartbeat. db-scheduler warns at startup when the pool is too small for the
number of workers.

The request pool also limits how many requests can use the database at once, in both modes. Past
`poolSize`, requests queue for at most `poolTimeout`, then fail; mapped once, that failure becomes
a 503 ([Wiring with FastAPI](wiring-fastapi.md#cap-the-load-in-both-modes)). An async service has a fourth pool,
`handlersConfig`: the async handler's own, used only on the loop the handler runs on.

## What changes if you skip a feature

Each block above is removable on its own:

- **No replica?** Leave `"secondaryConfig"` out of the member's `configs`. Reads go to the
  primary too.
- **No in-process handlers?** Drop block 7 and the two event jobs in block 8. Events still land
  in the outbox for an external consumer.
- **No background jobs at all?** Drop block 8. Drop block 7 with it - without the jobs there is
  no dispatch.
- **No keyed locks?** Drop the provider. Optimistic locking is still on, in every write.
- **No sharding?** That is what is shown. One entry, no overhead.
- **Sharding?** Add members or groups to the layout and give repositories an
  `EmbeddedBitsShardingStrategy`. See [Sharding](database/sharding.md).

## No decorators, and nothing is discovered

There is no decorator to mark an action, and nothing scans your package for one. The framework
knows exactly what you put in a registry, and nothing else. That is a trade-off:

- An action you forget to register fails at `execute`, with a clear error, at run time rather
  than at startup.
- In return, you can read this one file and know what the program contains, which you cannot do
  when a scan decides.

A registry refuses two different instances for the same type, instead of silently keeping one of
them. That is the one mistake a plain list would let through.

## See also

- [Wiring with FastAPI](wiring-fastapi.md) - the lifespan, the dependency, and where to put
  `build()`
- [Concurrency](runtime/concurrency.md) - what `job_workers` is actually sizing
- [Getting started](getting-started.md)
