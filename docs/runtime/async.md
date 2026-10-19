# Native asyncio

The same framework with every wait on the database awaited - actions whose perform is a
coroutine, async repositories, and an executor you await - in the package ekbatan.aio. The rules -
one transaction per unit of work, the rows and their events committed together, the optimistic
lock, soft delete, retries, sharding - are the same code as the sync API's, so they mean exactly
the same thing.

## When to use it

Use it when the service is an asyncio app - FastAPI or Starlette with `async def` endpoints -
and many requests wait on the database at once. Measured on one PostgreSQL, the same deposit
both ways, a pool of 40 connections:

| requests in flight | sync API in a 40-thread pool | `ekbatan.aio` on asyncpg |
| ---: | --- | --- |
| 1 | 199 a second, median 4.5 ms | 128 a second, median 7.2 ms |
| 50 | 626 a second, median 75 ms | 977 a second, median 47 ms |
| 1000 | 576 a second, median 1621 ms | 874 a second, median 587 ms |

So: about one and a half times the work under load, and a much shorter wait for most requests.
A lone request is a little slower on asyncpg than on psycopg. The full tables, and how a pool
timeout changes them, are [below](#the-pool-is-the-cap).

Choosing between the two, and what stays the same, is on [Sync or async?](../sync-or-async.md). This page
is the async details: drivers, the pool, tasks, locks, the handler loop.

## Install

One extra per database, beside the sync one:

| extra | async driver | sync driver it sits beside |
| --- | --- | --- |
| `postgres-async` | asyncpg | `postgres` - psycopg |
| `mysql-async` | aiomysql | `mysql` - PyMySQL |
| `mariadb-async` | aiomysql | `mariadb` - PyMySQL |
| `sqlite-async` | aiosqlite | none needed - sqlite3 |

```bash
uv add "ekbatan[postgres,postgres-async,migrations]"
```

Keep the sync extra. Migrations, `schemagen`, distributed jobs and the event fan-out and
dispatch jobs are sync, and connect through the sync driver.

## What is async, and what is not

Every class that waits on the database has an async twin in `ekbatan.aio`, named with the
`Async` prefix; models, events, ids, the registries of actions and repositories, and config are
shared; fan-out, dispatch, jobs, migrations and `schemagen` stay sync, on threads. The full list,
side by side, is on [Sync or async?](../sync-or-async.md#what-differs). `to_row` and `from_row` stay plain
`def`: mapping waits on nothing.

## A service, written async

```python
from ekbatan.aio import AsyncAction, AsyncActionExecutor, AsyncDatabaseRegistry, AsyncModelRepository
from ekbatan.config_files import load_sharding_config


class WalletRepository(AsyncModelRepository[Wallet, ShardedId[Wallet]]):
    def to_row(self, wallet: Wallet) -> dict[str, Any]: ...          # plain def
    def from_row(self, row: Row[Any] | Mapping[str, Any]) -> Wallet: ...

    async def find_all_by_owner(self, owner_id: UUID) -> list[Wallet]:
        return await self.find_where(WALLETS.c.owner_id == owner_id)


class DepositMoneyAction(AsyncAction[DepositParams, Wallet]):
    def __init__(self, wallets: WalletRepository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    async def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        wallet = await self.wallets.get(params.wallet_id)
        return self.plan.update(wallet.deposit(params.amount))


registry = AsyncDatabaseRegistry.from_config(load_sharding_config("ekbatan.yaml"))
wallets = WalletRepository(Wallet, WALLETS, registry, clock=clock)
executor = AsyncActionExecutor(
    namespace="example.wallet",
    registry=registry,
    repositories=[wallets],
    actions=[DepositMoneyAction(wallets, clock)],
    clock=clock,
)

wallet = await executor.execute(DepositMoneyAction, params)
await registry.aclose()                     # or: async with registry: ...
```

Everything is built the way the sync API is built, including the builders:
`AsyncDatabaseRegistry.builder()` and `AsyncActionExecutor.builder()`. An executor refuses the
other kind of action when it is built, so a sync `Action` cannot end up awaited by mistake.

## One config file, two drivers

An async service still has sync parts - the jobs, event fan-out and dispatch among them - so one layout file
has to serve both. The url names the database; each side picks its own driver:

| url in the file | sync registry connects with | async registry connects with |
| --- | --- | --- |
| `postgresql+psycopg://` or `postgresql://` | psycopg | asyncpg |
| `mysql+pymysql://` | PyMySQL | aiomysql |
| `mariadb+pymysql://` | PyMySQL | aiomysql |
| `sqlite:///path.db` | sqlite3 | aiosqlite |
| `postgresql+asyncpg://` | - | asyncpg, as written |
| `postgresql+psycopg_async://` | - | psycopg's own async mode, as written |

A url that names a sync driver, or none, connects through the database's async default. One that
names an async driver is used as written. `async_url(config)` shows which url a config connects
to, and `ASYNC_DEFAULT_DRIVERS` holds the defaults.

psycopg's own async mode needs no extra install - it is psycopg - and takes libpq's settings as
they are written. The whole PostgreSQL suite runs on it as well as on asyncpg.

## Driver settings, and UTC

`data_source_properties` reach the async driver as they are given, with one exception: asyncpg
does not read libpq's settings, so the ones written for libpq are made asyncpg's.

| written for libpq | reaches asyncpg as |
| --- | --- |
| `options: -c TimeZone=UTC -c statement_timeout=5000` | `server_settings: {TimeZone: UTC, statement_timeout: "5000"}` |
| `application_name: svc` | `server_settings: {application_name: svc}` |
| `connect_timeout: 10` | `timeout: 10.0` |
| `sslmode: require` | `ssl: require` |

So the documented way to keep each session on UTC works on every driver, unchanged:

```yaml
dataSourceProperties:
  options: -c TimeZone=UTC                       # PostgreSQL, psycopg and asyncpg
  # init_command: SET time_zone = '+00:00'       # MySQL and MariaDB, PyMySQL and aiomysql
```

Measured against a database whose own default zone is Tehran: the session reports UTC on
asyncpg and on aiomysql, and Tehran without the setting. A layout only async code reads can also
use asyncpg's own spelling, `server_settings: {TimeZone: UTC}`. Something in `options` that is
not a `-c` setting is refused, and so is a setting given twice; the refusal never repeats a value.

**asyncpg is strict about time zones.** It refuses an aware `datetime` for a `TIMESTAMP` (zone-less)
column, where psycopg accepts one. The framework hands every time to the driver as it is, so
this meets only an aware value you give it yourself. The default clock's `now()` has no zone
attached and passes on every driver
([What you give is what is stored](../database/multi-database.md#what-you-give-is-what-is-stored)).

## The pool is the cap

There is no limit of the framework's own on how many units of work run at once. The connection
pool is the limit. An async pool is built from the same `DataSourceConfig` as a sync one -
`pool_size`, `max_overflow`, `pool_timeout`, `pool_recycle`, pre-ping - and:

- past `pool_size + max_overflow`, requests queue for a connection;
- a request that waits longer than `pool_timeout` fails with SQLAlchemy's `TimeoutError`
  ("QueuePool limit of size 10 overflow 0 reached, connection timed out, timeout 5.00"), which
  carries no url and no password. Map it to a 503 with `Retry-After`.

Limit the work in flight at the server too, so a burst is refused at the door rather than
queued inside: `uvicorn --limit-concurrency 50` answers 503 itself past 50 open requests. Keep
that a small multiple of the pool.

Measured with 2000 deposits per row, a pool of 40, PostgreSQL in a local container (one laptop,
so read the shape, not the exact numbers):

**`pool_timeout` 30 s, the default**

| requests in flight | sync API, 40 threads | `ekbatan.aio`, asyncpg | `ekbatan.aio`, psycopg async |
| ---: | --- | --- | --- |
| 1 | 199/s, p50 4.5 ms, p99 9.7 ms | 128/s, p50 7.2 ms, p99 15.8 ms | 196/s, p50 4.6 ms, p99 11.2 ms |
| 50 | 626/s, p50 75 ms, p99 149 ms | 977/s, p50 47 ms, p99 92 ms | 716/s, p50 65 ms, p99 141 ms |
| 200 | 581/s, p50 342 ms, p99 427 ms | 955/s, p50 123 ms, p99 786 ms | 780/s, p50 162 ms, p99 922 ms |
| 1000 | 576/s, p50 1621 ms, p99 1839 ms | 874/s, p50 587 ms, p99 2196 ms | 796/s, p50 773 ms, p99 2382 ms |

Nothing timed out. The slowest one in a hundred waits longer on async at 200 and 1000: every
request starts at once and then queues at the pool, less evenly than in front of 40 threads.

**`pool_timeout` 0.5 s**

| requests in flight | sync API, 40 threads | `ekbatan.aio`, asyncpg |
| ---: | --- | --- |
| 200 | none failed; p50 317 ms, p99 375 ms | 84 of 2000 failed fast; p50 138 ms, p99 609 ms |
| 1000 | none failed; p50 1557 ms, p99 1674 ms | 967 of 2000 failed fast; p50 399 ms, p99 797 ms |

With a short timeout, overload is refused quickly instead of queued, and the requests that are
served stay fast. The sync API never waits at the pool here: its 40 threads queue the work in
front of it. The pre-ping costs 1.5 to 2.5 ms per unit of work on both drivers (two checkouts
each); it is on by default, as on a sync pool. `scripts/bench-async.py` runs all of this.

## Reads across shards run at once

A read with no shard named - `find_where`, `all`, `count`, `exists_where`, `find_one_where`,
and `find_many` across the shards its ids live on - asks every shard **at the same time**, so it
costs about one shard's round trip rather than one per shard. The answer is the sync one:

- rows come back first shard first, as in sync;
- if one shard fails, the whole read fails: the reads still running on the other shards are
  cancelled, and that shard's own error is raised - the same exception the sync read raises,
  with the same note naming the shard, e.g. `ekbatan: reading shard 1:0 (mexico) failed, so the
  whole read failed and the reads still running on the other shards were cancelled`. There are
  no partial results;
- `find_one_where` and `exists_where` take the answers in shard order and stop at the first
  shard that has the row, as the sync calls do: the reads after it are cancelled, and a broken
  shard after it does not fail the call. A shard fails them only when every shard before it
  said "not here" - exactly when the sync call, which asks one shard at a time, would reach it.

`dbs()` and `readonly_dbs()` hand connections to your own code, so they stay as in sync: one
shard at a time, and lazy - `async for shard, conn in wallets.readonly_dbs():`.

## Transactions, tasks, cancellation and timeouts

The open transaction lives in a `ContextVar`, one per task, so two requests on one event loop
never see each other's transaction.

- **Inside `perform`** no transaction is open, and each read takes a connection of its own: reads
  gathered with `asyncio.gather` run at the same time.
- **Inside one transaction** every statement joins its one connection. Statements gathered there
  take turns - SQLAlchemy queues them - and commit together: no error, and no speed-up.
- **A cancelled task** rolls its transaction back, and the connection goes back to the pool clean.
- **`asyncio.timeout(...)`** around `await executor.execute(...)` is a cancellation: whether it
  fires during `perform` or while the transaction is open, nothing is committed.

## Keyed locks, around the unit of work

The caller takes the lock around `await executor.execute(...)`, as on the sync side - the lock
spans `perform` and the commit, and the action knows nothing about it:

```python
from ekbatan.aio.locks import AsyncPostgresKeyedLockProvider

locks = AsyncPostgresKeyedLockProvider.builder().engine(async_engine_for(lock_config)).build()

async with locks.acquire(f"wallet:{wallet_id}", timedelta(seconds=30)):
    wallet = await executor.execute(DepositMoneyAction, params)

lease = await locks.try_acquire(key, max_wait=timedelta(seconds=5), max_hold=timedelta(seconds=30))
if lease is None:
    ...                                   # someone else has it: a 503 with Retry-After
async with lease:
    ...
```

The same backends as the sync providers, holding the same keys - `AsyncPostgresKeyedLockProvider`,
`AsyncMySQLKeyedLockProvider`, `AsyncMariaDBKeyedLockProvider`, and `AsyncRedisKeyedLockProvider`
on redis-py's own `redis.asyncio` in `ekbatan.aio.locks.redis` - so an async service, a sync one
and a Java one contend for one lock. The same rules: `max_hold` bounds every hold, `try_acquire`
answers contention with `None` and a broken backend with `LockAcquisitionError`, a blank key is
refused, a lease may be released twice, the outermost hold's `max_hold` governs.

- **The holder is the task.** A task that takes a key it holds re-enters; any other task waits -
  one it started inside the held region included, `asyncio.gather` too.
- **Waiting does not block the loop.** Other requests run while one waits for a lease.
- **A wait that is cancelled or timed out** gives its connection back closed, so no lock can be
  left behind on a pooled connection; a cancelled holder releases on its way out of `async with`.
- **A SQL lease holds an async connection** for its whole hold: give the locks a pool of their
  own (`lockConfig`), sized for the leases held at once.

The sync providers refuse to be taken on an event loop's thread, and say to use these.

## Event handlers, after the unit of work

A handler never runs inside an action. The action commits its rows and its events; later, fan-out
and dispatch - jobs on their own threads - pick the events up and run the handlers, a batch at a
time. That is the same whatever kind the request path is.

A handler's `handle` may be `async def`, to await an `AsyncActionExecutor`:

```python
class MoneyDepositedEventHandler:
    name = "wallet-deposit-alert"
    event_type = MoneyDepositedEvent

    def __init__(self, executor: AsyncActionExecutor) -> None:
        self.executor = executor

    async def handle(self, envelope: EventEnvelope[MoneyDepositedEvent]) -> None:
        await self.executor.execute(RaiseAlertAction, RaiseAlertParams(...))
```

Dispatch runs an async handler on its **handler loop** - one event loop on a thread of its own,
open for as long as the dispatch is - while the delivery thread waits for it; a batch of async
handlers runs on that loop at the same time. Success, retry, backoff and expiry are decided
exactly as for a sync handler, from what the handler returned or raised. A registry may hold
both kinds.

**What an async handler awaits belongs to the handler loop.** An async pool is bound to the loop
it is used on, so give the handlers a registry and executor of their own (the examples use a
`handlersConfig` pool), and close them on that loop at shutdown, before the loop:

```python
handler_loop = HandlerLoop()
job = (
    EventHandlingJob.builder()
    .registry(events_registry)                  # sync: fan-out and dispatch read the queue
    .handlers(handlers)
    .notifications(EVENT_NOTIFICATIONS)
    .handler_loop(handler_loop)
    .build()
)
# at shutdown, after the jobs have stopped:
handler_loop.run(handler_registry.aclose())
handler_loop.close()
```

Without `.handler_loop(...)` the dispatch makes its own, started only when an async handler first
runs. Fan-out and dispatch themselves read and write the `events` and `event_notifications`
tables through a sync registry, built from the same layout file.

The `fastapi-wallet-rest-uv-async-pg`, `fastapi-wallet-rest-uv-async-mysql`,
`fastapi-wallet-rest-uv-async-mariadb` and `fastapi-wallet-rest-uv-async-sharded-pg` examples are
this shape: async requests under an async keyed lock, an async handler, a pool per use.
`fastapi-wallet-saga-uv-async-pg` runs a whole saga's later steps as async handlers.

## A job whose work is async

A distributed job is sync in every service: the scheduler claims its row and calls `execute` on
one of its threads, and heartbeats it from another. The *work* inside it can still be async.
`execute` hands a coroutine to a `HandlerLoop` - an event loop on a thread of its own, the same
kind dispatch runs async handlers on - with `loop.run(...)`, and waits for it:

```python
class PayStipendJob(DistributedJob):
    def __init__(self, loop: HandlerLoop, executor: AsyncActionExecutor, wallets: WalletRepository):
        self.loop = loop                  # the loop `executor` and `wallets` were built for
        self.executor = executor
        self.wallets = wallets

    def execute(self, ctx: ExecutionContext) -> None:
        self.loop.run(self.pay())         # on the scheduler's thread: run it there, and wait

    async def pay(self) -> None:
        for wallet in await self.wallets.find_open_below(Decimal("100.00")):
            await self.executor.execute(DepositMoneyAction, DepositParams(...))


loop = HandlerLoop(name="worker-loop")
registry = AsyncDatabaseRegistry.from_config(layout)    # used only on `loop`
# ... the async repositories and executor over it, PayStipendJob(loop, executor, wallets)
# at shutdown: jobs.stop(), then loop.run(registry.aclose()), then loop.close()
```

The job's thread waits, so whatever the coroutine raises, `execute` raises: the claim, the
heartbeat, a failure and its retry are the scheduler's, exactly as for a sync job. What the work
awaits is built for that loop and closed on it, after the jobs have stopped. Give dispatch the
same loop (`.handler_loop(loop)`) and an async handler shares the work's registry.

Worth it when the work waits on many things at once and can await them together. A job that is
a few statements in a row gains nothing: write it sync, over a sync executor - an async service
can run that beside itself unchanged. `job-worker-uv-async-pg` is this shape.

## One kind per service

A service is all-sync or all-async on its request path. Two registries over one database are
two pools, and **one transaction cannot span them**. Never call the sync API from inside an
`async def`: it blocks the event loop for every request at once. See
[Sync or async?](../sync-or-async.md).

## Testing an async action

```python
staged = await async_run_action(DepositMoneyAction(wallets, clock), params)
assert staged.updated(Wallet)[0].balance == Decimal("150.00")

checked = await AsyncActionSpec.of(DepositMoneyAction(wallets, clock)).execute(params)
checked.assert_updated(Wallet, lambda w: w.balance == Decimal("150.00"))
```

`execute` and `assert_throws` are awaited; what comes back is the sync `ActionAssert`, so every
check is the sync one. `count_statements(registry)` takes an async registry as well, around
awaited work.

## Passwords

As on the sync side, no password is printed: not by an async engine, url, manager or registry,
not in a connection refused by asyncpg, psycopg's async mode or aiomysql, not by an async keyed
lock, its lease or its refusal - a Redis url's password included - and not with the engine's echo
on. Tested with a marker password on PostgreSQL (asyncpg and psycopg async), MySQL, MariaDB and
Redis.

## gevent

`ekbatan.aio` does not run under gevent's monkey-patching: aiosqlite's worker threads become
greenlets that block the event loop's own thread, and units of work stall at the pool. gevent
and asyncio are two answers to the same question - pick one per service. The sync API under
gevent is covered in [Concurrency](concurrency.md#gevent-cheap-blocking-with-nothing-to-change).

## Reference

Everything is in `ekbatan.aio`, its `locks` package, and two names in `ekbatan.events`:

| name | what it is |
| --- | --- |
| `AsyncDatabaseRegistry` | shards to managers; `from_config`, `builder()`, `aclose()`, `async with` |
| `AsyncTransactionManager` | one shard's pools; `transaction()`, `connection(readonly=)`, `current()` |
| `AsyncDb` | the connection every accessor hands out: SQLAlchemy's `AsyncConnection`, plus `rows()` |
| `AsyncTransaction` | an open transaction: `execute`, `rows`, `shard`, `dialect` |
| `AsyncModelRepository`, `AsyncEntityRepository`, `AbstractAsyncRepository` | the repositories, every read and write a coroutine |
| `AsyncRepository` | what the executor needs of a repository |
| `AsyncAction` | a unit of work with `async def perform` |
| `AsyncActionExecutor` | runs them; `execute` is awaited; `builder()` |
| `AsyncSingleTableJsonEventPersister`, `AsyncEventPersister` | the outbox write, and its protocol |
| `AsyncChangePersister` | writes one shard's slice of a plan |
| `AsyncActionSpec`, `async_run_action` | testing without a database |
| `ekbatan.aio.locks`: `AsyncPostgresKeyedLockProvider`, `AsyncMySQLKeyedLockProvider`, `AsyncMariaDBKeyedLockProvider`, `AsyncKeyedLockProvider`, `AsyncLease`; `ekbatan.aio.locks.redis`: `AsyncRedisKeyedLockProvider` | keyed locks, taken around `await executor.execute(...)` |
| `ekbatan.events`: `AsyncEventHandler`, `HandlerLoop` | an event handler with `async def handle`, and the loop dispatch runs it on - which a job can run its async work on too |
| `async_engine_for`, `async_url`, `ASYNC_DEFAULT_DRIVERS` | the async pool for one config, and which driver it uses |

## See also

- [Concurrency](concurrency.md) - threads, the GIL, and when async helps
- [Wiring with FastAPI](../wiring-fastapi.md) - `def` endpoints, and `async def` ones on `ekbatan.aio`
- [Always UTC](../database/multi-database.md#always-utc) - the three parts of the rule
