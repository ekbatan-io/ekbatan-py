# Wiring

Everything a service is made of, assembled in one function: the databases, the repositories,
the actions, the event handlers and the jobs. Nothing scans, nothing is discovered - the list
**is** the wiring, and it is the one place that knows what this service is.

That is the whole of it. There is no container to configure, no annotation to remember and no
startup order to reason about beyond the order the lines are in.

> Everything below is taken from
> [`ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/app.py`](../ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/app.py),
> which runs and is tested. The HTTP half is in [Wiring with FastAPI](wiring-fastapi.md); this
> page is the part that has nothing to do with HTTP.

## What we're wiring

One function that returns one object. Nothing above it knows which database, which handlers or
which jobs; nothing below it does either.

**Sync**

```python
@dataclass(frozen=True, slots=True)
class WalletService:
    registry: DatabaseRegistry
    wallets: WalletRepository
    alerts: AlertRepository
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
    wallets: WalletRepository
    alerts: AlertRepository
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

Frozen and slotted because it is a value, not a service locator - it is built once and handed
around. `start` and `close` (`aclose`, awaited, in async mode) are the only lifecycle there is.

## The domain (unchanged whether you wire it by hand or not)

Nothing here changes because of how you wire it. These are the classes the rest of the page
assembles, and they are the same ones you would write with a container doing the assembling:

**Sync**

```python
@dataclass(frozen=True, kw_only=True)
class Wallet(Model[ShardedId["Wallet"], WalletState, WalletBuilder]):
    owner_id: UUID
    currency: str
    balance: Decimal

    def deposit(self, amount: Decimal) -> "Wallet": ...


class WalletRepository(ModelRepository[Wallet, ShardedId[Wallet]]):
    def __init__(self, registry: DatabaseRegistry) -> None:
        super().__init__(Wallet, WALLETS, registry)


class DepositMoneyAction(Action[DepositParams, Wallet]):
    def __init__(self, wallets: WalletRepository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        return self.plan.update(self.wallets.get(params.wallet_id).deposit(params.amount))
```

**Async**

```python
@dataclass(frozen=True, kw_only=True)
class Wallet(Model[ShardedId["Wallet"], WalletState, WalletBuilder]):
    owner_id: UUID
    currency: str
    balance: Decimal

    def deposit(self, amount: Decimal) -> "Wallet": ...


class WalletRepository(WalletRows, AsyncModelRepository[Wallet, ShardedId[Wallet]]):
    def __init__(self, registry: AsyncDatabaseRegistry) -> None:
        super().__init__(Wallet, WALLETS, registry)


class DepositMoneyAction(AsyncAction[DepositParams, Wallet]):
    def __init__(self, wallets: WalletRepository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    async def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        wallet = await self.wallets.get(params.wallet_id)
        return self.plan.update(wallet.deposit(params.amount))
```

Four kinds of thing, and each takes what it needs through `__init__`. The model is the same in
both modes; in async mode the repository and the action are the `ekbatan.aio` kinds, and the
mapping (`WalletRows`, plain `def`s) is written once in a small class the repository takes it
from. That is the only contract
the wiring below has to satisfy: **construct them in dependency order and hand each one its
collaborators.** There is nothing else a container would have been doing for you.

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

The url and login are placeholders with defaults, so one file runs everywhere and running the
module works against a local server with no setup. What is this service's own, rather than
Ekbatan's, stays in a small `Settings`:

```python
@dataclass(frozen=True, slots=True)
class Settings:
    config_file: Path = Path(os.environ.get("WALLET_CONFIG_FILE", CONFIG_FILE))
    #: Where the file's `${...}` placeholders are looked up.
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)
    job_workers: int = int(os.environ.get("WALLET_JOB_WORKERS", "3"))
    run_migrations: bool = os.environ.get("WALLET_MIGRATE", "1") != "0"
```

`job_workers` is how many threads run job bodies. They are real OS threads, so the number is a
decision, not a default to forget. See [Concurrency](runtime/concurrency.md).

## 2. The layout: which databases, and which is the default

```python
ekbatan = load_ekbatan_config(config.config_file, environ=config.environ)
layout = ekbatan.sharding
```

Groups of shards, each shard a member with named connection settings. The same
`ShardingConfig` can be built in code instead, with `ShardingConfig.builder()`; the file is what
the examples use. One group with one member
is the ordinary case and costs nothing. Add members or groups to shard; see
[Sharding](database/sharding.md).

`"primaryConfig"` is required. Add `"secondaryConfig"` for a read replica, or leave it out and both
go to the primary. The registry builds those two; `"jobsConfig"` and `"lockConfig"` are for the
scheduler and the locks, and this file builds them:

**Sync**

```python
member = layout.groups[0].members[0]
jobs_engine = engine_for(member.config_for("jobsConfig"))
lock_engine = engine_for(member.config_for("lockConfig"))
```

**Async**

```python
member = layout.groups[0].members[0]
jobs_engine = engine_for(member.config_for("jobsConfig"))            # sync: the scheduler
lock_engine = async_engine_for(member.config_for("lockConfig"))      # async: the leases
handler_engine = async_engine_for(member.config_for("handlersConfig"))  # async: the handler
```

Each is an ordinary SQLAlchemy engine built from that member's settings - async ones with
`async_engine_for`. They are yours, so dispose them at shutdown, after the scheduler and the
locks have stopped.

## 3. Migrations, before anything opens a pool

```python
if config.run_migrations:
    migrate(layout, MIGRATIONS)
```

Every shard, one at a time, fail-fast. `migrate` runs plain SQL files through yoyo as a
**library** - there is no config file and no binary, because a config file that has to be found is
a config file that is missing in the one environment you did not test.

It refuses to run if an applied migration has since been edited, renamed or deleted, or if a new
one is older than one already applied. Several replicas starting at once is
safe: each database is locked while it is migrated, one process migrates and the rest wait. See [Migrations](database/migrations.md).

Ordering matters: run this before anything opens a pool against a schema that may not exist yet.
Migrations are sync in both modes; an async service runs them here too, before its first await.

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
    .with_default_database(TransactionManager(layout.default_shard, jobs_engine))
    .build()
)
```

One `TransactionManager` per shard, indexed by `ShardIdentifier`. Everything that touches a
database goes through it, sharded or not. In async mode it is an `AsyncDatabaseRegistry` of
`AsyncTransactionManager`s, from the same layout - the async side reads the url as naming the
database and connects through its async driver.

## 5. Repositories

```python
wallets = WalletRepository(registry, clock=running)
alerts = AlertRepository(registry, clock=running)

repositories = (
    RepositoryRegistry.builder()
    .with_model_repository(wallets)
    .with_entity_repository(alerts)
    .build()
)
```

The same lines in both modes; only the repository classes differ. One instance per persistable
type. `with_model_repository` and `with_entity_repository` are
separate calls because the two are different things - a model's changes are events and an
entity's are not - and the registry checks that the table can hold what the type carries.

## 6. The executor

**Sync**

```python
executor = ActionExecutor(
    namespace=ekbatan.namespace,
    registry=registry,
    repositories=repositories,
    actions=(
        ActionRegistry.builder()
        .with_action(OpenWalletAction(clock))
        .with_action(DepositMoneyAction(wallets, clock))
        .with_action(CloseWalletAction(wallets, clock))
        .with_action(RaiseAlertAction(clock))
        .build()
    ),
    events=EVENTS,
    clock=running,
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
        .with_action(OpenWalletAction(clock))
        .with_action(DepositMoneyAction(wallets, clock))
        .with_action(CloseWalletAction(wallets, clock))
        .build()
    ),
    events=EVENTS,
    clock=running,
)
```

The thing application code calls. Actions are singletons - per-call state lives on the plan, not
on the action - so one instance each, constructed with whatever they read from.

`namespace` is stamped on every outbox row and is how a consumer tells one service's events from
another's. Pick it once; changing it later splits your own stream.

`events` is the outbox table. Passing it explicitly rather than reaching for a global is what
lets a test point two services at two tables in one process - which is exactly what the saga
example does.

## 7. Event handlers, if you want the listen-to-yourself path

**Sync**

```python
handlers = EventHandlerRegistry([MoneyDepositedEventHandler(executor)])
events_config = ekbatan.local_event_handler  # `local-event-handler` in the file
```

**Async**

```python
# The async handler awaits an executor of its own, on a registry of its own: an async pool
# belongs to the loop it is used on, and the handler runs on dispatch's handler loop.
handler_loop = HandlerLoop()
handler_registry = (
    AsyncDatabaseRegistry.builder()
    .with_default_database(AsyncTransactionManager(layout.default_shard, handler_engine))
    .build()
)
handler_executor = AsyncActionExecutor(
    namespace=ekbatan.namespace,
    registry=handler_registry,
    repositories=[AlertRepository(handler_registry, clock=running)],
    actions=[RaiseAlertAction(clock)],
    events=EVENTS,
    clock=running,
)
handlers = EventHandlerRegistry([MoneyDepositedEventHandler(handler_executor)])
events_config = ekbatan.local_event_handler  # `local-event-handler` in the file
```

Optional. Without it the executor still writes every event with `delivered=false`, which is
exactly the right state for a deployment that consumes the outbox externally through Debezium.
The handlers here are the *in-process* path, and the two can both be on.

Note the handler is constructed with the `executor`: a handler reacting to an event does its own
work as its own unit of work, with its own transaction and its own outbox row. It does not get
handed the transaction that produced the event, and it must not - that transaction is committed
and gone by the time a handler runs.

## 8. Locks and jobs

**Sync**

```python
locks = PostgresKeyedLockProvider(lock_engine)

jobs = (
    job_registry()
    .engine(jobs_engine)
    .with_jobs([
        EventFanoutJob(registry, handlers, events=EVENTS,
                       notifications=EVENT_NOTIFICATIONS, config=events_config, clock=running),
        EventHandlingJob(registry, handlers,
                         notifications=EVENT_NOTIFICATIONS, config=events_config, clock=running,
                         # Registered either way; scheduled only when the switch is on.
                         enabled=events_config.handling.enabled),
        PayStipendJob(executor, wallets),
    ])
    .with_config(ekbatan.jobs)  # `jobs` in the file, if any
    .workers(config.job_workers)
    .build()
)
```

**Async**

```python
locks = AsyncPostgresKeyedLockProvider(lock_engine)

jobs = (
    job_registry()
    .engine(jobs_engine)
    .with_jobs([
        EventFanoutJob(events_registry, handlers, events=EVENTS,
                       notifications=EVENT_NOTIFICATIONS, config=events_config, clock=running),
        EventHandlingJob(events_registry, handlers,
                         notifications=EVENT_NOTIFICATIONS, config=events_config, clock=running,
                         # Registered either way; scheduled only when the switch is on.
                         enabled=events_config.handling.enabled,
                         handler_loop=handler_loop),
    ])
    .with_config(ekbatan.jobs)  # `jobs` in the file, if any
    .workers(config.job_workers)
    .build()
)
```

Fan-out and dispatch are themselves jobs. That is not an implementation detail leaking - it is
the design: the event pipeline is cluster-exclusive scheduled work like any other, so it gets
the same leader election, the same heartbeat and the same dead-execution recovery. Register them
and `jobs.start()` runs the pipeline. Leave them out and events accumulate unread.

Jobs are sync in both modes: `DistributedJob.execute` is a plain `def`, run on a worker thread.
An async service registers them on a sync registry, as above. A job of its own, like the sync
block's `PayStipendJob`, is written the same way in an async service, over a sync executor of its
own; the async example leaves it out.

### Three pools, and why

This is the part worth copying into a real deployment: three pools, each a named config on the
member (`primaryConfig`, `jobsConfig`, `lockConfig`).

| pool | sized for | why it is separate |
| --- | --- | --- |
| **requests** | concurrent requests | the ordinary one |
| **jobs** | at least twice `job_workers` | db-scheduler polls continuously and writes a heartbeat from a housekeeping thread. Share the request pool and a traffic burst can starve that heartbeat - and an execution whose heartbeat stops is **declared dead and run again somewhere else** |
| **locks** | maximum leases held at once | a keyed lock holds its connection for the life of the lease. That *is* the lock. A lease taken during a request would otherwise pin a request connection for its duration |

The jobs pool default is 8, not 3, for a reason worth repeating: a job body holding the last
connection starves the heartbeat. db-scheduler warns at startup when the pool is too small for
the worker count - and did, while the example was being written.

An async service has a fourth, `handlersConfig`: the async handler's own pool, used only on the
loop the handler runs on. Its request pool is also the cap on requests waiting for the database:
past `poolSize` they queue, for at most `poolTimeout`, then fail.

## What changes if you skip a feature

Each block above is removable on its own:

- **No replica?** Leave `"secondaryConfig"` out of the member's `configs`. Both go to the primary.
- **No in-process handlers?** Drop block 7 and the two event jobs in block 8. Events still land
  in the outbox for an external consumer.
- **No background jobs at all?** Drop block 8. Drop block 7 with it - without the jobs there is
  no dispatch.
- **No keyed locks?** Drop the provider. Optimistic locking is still on, in every write.
- **No sharding?** That is what is shown. One entry, no overhead.
- **Sharding?** Add members or groups to the layout and give repositories an
  `EmbeddedBitsShardingStrategy`. See [Sharding](database/sharding.md).

## No annotations, and nothing is discovered

There is no decorator to mark an action and nothing scanning your package for one. The
framework knows about exactly what you put in a registry. That is a real trade:

- An action you forget to register fails at `execute` with a clear error, at runtime, not at
  startup.
- Against that: you can read this file and know what the program contains, which you cannot do
  when a scan decides.

The registries are builders rather than plain lists so a duplicate registration is refused
rather than silently overwriting - which is the one class of mistake a list would let through.

## See also

- [Wiring with FastAPI](wiring-fastapi.md) - the lifespan, the dependency, and where to put
  `build()`
- [Concurrency](runtime/concurrency.md) - what `job_workers` is actually sizing
- [Getting started](getting-started.md)
