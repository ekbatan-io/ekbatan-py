# fastapi-wallet-rest-pip-async-sharded-pg

The sharded REST wallet service, over **two databases**, with its request path on **native
asyncio**, `ekbatan.aio`: every endpoint an `async def` that awaits an `AsyncActionExecutor` and
async repositories, on asyncpg, and the deposit under an async keyed lock. The event handler is
async too. The same service as [`fastapi-wallet-rest-uv-sharded-pg`](../fastapi-wallet-rest-uv-sharded-pg)
otherwise - the same model, events, converters and migrations, the same routing, the same tests.

Two databases on one server are enough to run it:

```
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=ekbatan -e POSTGRES_USER=ekbatan -e POSTGRES_DB=postgres postgres:17-alpine
psql ... -c 'CREATE DATABASE shard_global' -c 'CREATE DATABASE shard_mexico'
```

<!-- build: written by scripts/sync-examples.py -->
```
make            # generate what is out of date, then test the built wheel, installed
make run        # run it; the database comes first, above
```

Built with **pip and setuptools**. The same project with [uv](../fastapi-wallet-rest-uv-async-sharded-pg) and [Poetry](../fastapi-wallet-rest-poetry-async-sharded-pg) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.

The venv is made with `python3`, which must be 3.13 or later - `make PYTHON=python3.13` if yours is older.
<!-- /build -->

## The claim, through HTTP

The only line in the whole application that knows there is more than one database is the
country code on `POST /wallets`:

```
POST /wallets            {"country": "MX", ...}   -> id carries shard 1:0
GET  /wallets/{id}                                -> routed by the id, no hint
POST /wallets/{id}/deposits                       -> names no database; under a keyed lock
POST /wallets/{id}/transfers  (same shard)        -> 200
POST /wallets/{id}/transfers  (across shards)     -> 422, and says to use a saga
GET  /shards                                      -> one query per database, all at once
GET  /wallets/{id}/alerts                         -> what the deposit handler wrote
```

`DepositMoneyAction` mentions no shard at all:

```python
async def perform(self, principal: str | None, params: DepositParams) -> Wallet:
    wallet = await self.wallets.get(params.wallet_id)  # routed by the id
    return self.plan.update(wallet.deposit(params.amount))
```

## What is async, and what is not

| | here |
| --- | --- |
| the endpoints | `async def`, awaiting the service - nothing blocks the event loop |
| the request path | `AsyncDatabaseRegistry` over both shards, `WalletRepository` and `AlertRepository` on the async bases, `AsyncAction`s, an `AsyncActionExecutor` |
| reads across shards | **at the same time**: `find_all_by_owner` (`find_where`) asks both databases at once, as `ekbatan.aio` does for every read that names no shard; `total_per_shard` asks each replica in a `TaskGroup` |
| the keyed lock | async: `ekbatan.aio.locks`, taken around `await executor.execute(...)` on the deposit. One database holds every key - a lock is about a wallet's key, not its rows |
| the event handler | async: `MoneyDepositedEventHandler.handle` is `async def` and awaits `RaiseAlertAction` through its own `AsyncActionExecutor`, on dispatch's handler loop - after the deposit has committed |
| the jobs | sync: one db-scheduler, and fan-out and dispatch over every shard, on its threads |
| migrations, `schemagen` | sync, through psycopg, every shard one at a time |

## The pools

`ekbatan.yaml` names the sync driver, `postgresql+psycopg://`. The sync pools connect with
psycopg; the async registries read the same urls and connect through asyncpg. Every pool puts
its sessions on UTC with `options: -c TimeZone=UTC`, which both drivers read.

| pool | member | used by | driver |
| --- | --- | --- | --- |
| `primaryConfig` | each shard | the requests | asyncpg |
| `lockConfig` | global | the deposit's keyed lock | asyncpg |
| `handlersConfig` | global | the event handler, on the handler loop; alerts all live on the default shard | asyncpg |
| `jobsConfig` | each shard | db-scheduler (global), and fan-out and dispatch on every shard | psycopg |

## Three things the tests pin

**Isolation is checked from the outside.** A Mexican wallet is on the Mexican database and not
on the other one, asserted by asking each database directly.

**A query without an id asks every shard** - at the same time, here, but still one query per
database. A question you ask often should be answerable from one shard.

**Reaching a second shard is refused, not half-committed.** The framework raises
`CrossShardError` and names the two that disagreed; the API answers 422 and points at the saga,
[`fastapi-wallet-saga-uv-async-pg`](../fastapi-wallet-saga-uv-async-pg).

## Its test

```
make
```

Twenty-two cases against two databases on one PostgreSQL in Testcontainers - the sync twin's
twenty, and two more. A test that drives the service directly awaits on a loop of its own (a
`HandlerLoop`); one through HTTP awaits on the app's loop, through `client.portal`. The two
extra say what this example is for: eight deposits at once on a Mexican wallet all land under
the keyed lock, and the requests, the lock and the handler really run on asyncpg while the
jobs run on psycopg.

## Two databases, one server

The tests create `shard_global` and `shard_mexico` on one Postgres container. The framework
opens a pool per url and cannot tell that one process is behind both, so the routing is
genuinely exercised.
