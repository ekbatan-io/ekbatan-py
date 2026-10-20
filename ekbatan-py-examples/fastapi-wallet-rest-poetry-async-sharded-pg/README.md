# fastapi-wallet-rest-poetry-async-sharded-pg

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

Built with **Poetry**. The same project with [uv](../fastapi-wallet-rest-uv-async-sharded-pg) and [pip and setuptools](../fastapi-wallet-rest-pip-async-sharded-pg) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.

Needs `poetry` 2 on the path.
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

## The whole idea, in one line

The shard is packed into the identifier. `WalletId.generate(MEXICO)` mints an id that carries
its coordinate, and every read and write afterwards follows it — no tenant column, no lookup
table, no argument saying where to look.

Which means `DepositMoneyAction` mentions no shard at all:

```python
async def perform(self, principal: str | None, params: DepositParams) -> Wallet:
    wallet = await self.wallets.get(params.wallet_id)  # routed by the id
    return self.plan.update(wallet.deposit(params.amount))
```

That is the property worth protecting. Business logic that named a database would have to be
edited every time the layout changed.

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

## Two forced types

`pyproject.toml` tells schemagen to turn two plain text columns into small classes of this
app's own:

```toml
# Every CHAR(3) column is a currency code, so the rule picks columns by their type.
[[tool.ekbatan.schemagen.forced-types]]
converter = "fastapi_wallet_rest_async_sharded_pg.converters.CurrencyConverter"
include-types = "CHAR\\(3\\)"

# One column, picked by its name.
[[tool.ekbatan.schemagen.forced-types]]
converter = "fastapi_wallet_rest_async_sharded_pg.converters.EmailAddressConverter"
include-expression = "alerts\\.recipient"
```

and schemagen writes these two lines into `schema.py`:

```python
Column("currency", converted(CHAR(length=3), CurrencyConverter()), nullable=False)
Column("recipient", converted(VARCHAR(length=255), EmailAddressConverter()), nullable=False)
```

and, in the records beside the tables, the converters' own types - so a repository's
`to_record` that hands the table a plain string where a `Currency` belongs is a mypy error:

```python
class AlertsRecord(TypedDict):
    ...
    recipient: EmailAddress


class WalletsRecord(TypedDict):
    ...
    currency: Currency
```

- **Picked by type.** Every `CHAR(3)` column is a currency code, whatever it is called. A
  wallet's `currency` is a `Currency`, which takes only three capital letters: `"eur"` is
  refused.
- **Picked by name.** `alerts.recipient` is an `EmailAddress`. A bad address is refused when
  the deposit is made (a 400), rather than later in the handler that writes the alert, where it
  would fail on every retry.

The two classes are in `model.py` and the converters in `converters.py`; neither knows
SQLAlchemy. The repository hands the table a `Currency` and gets one back, and the column holds
`EUR`. Action parameters and events keep the plain text, `"currency": "EUR"`, because they are
written to the outbox as JSON for other services to read. The test checks each value in Python,
in the table, and through the API.

## What to look at

| File | What it shows |
|---|---|
| `actions.py` | `OpenWalletAction` — the **one** place a shard is chosen, from a country code |
| `model.py` | `WalletId(ShardedId)`, and nothing else about sharding |
| `schema.py` | no shard column anywhere; every database holds the same tables |
| `repository.py` | one async repository over every shard, and what a query without an id costs - asked of every shard at once |
| `ekbatan.yaml` | the only file that knows how many databases there are, in the keys of Java's `application.yml`; every pool on UTC (`options: -c TimeZone=UTC`) |
| `app.py` | the wiring, reading the layout from `ekbatan.yaml` |
| `api.py` | `async def` endpoints; the deposit under an async keyed lock - one database, the global member, holds every key, whichever shard the wallet is on; a request that waits out the pool is a 503 |

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

Twenty-three cases against two databases on one PostgreSQL in Testcontainers - the sync twin's
twenty-two, and one more. A test that drives the service directly awaits on a loop of its own (a
`HandlerLoop`); one through HTTP awaits on the app's loop, through `client.portal`. The extra one
says what this example is for: the requests, the lock and the handler really run on asyncpg
while the jobs run on psycopg.

## Two databases, one server

The tests create `shard_global` and `shard_mexico` on one Postgres container. The framework
opens a pool per url and cannot tell that one process is behind both, so the routing is
genuinely exercised.

## Ids that name a shard you have not configured

`default_shard` is where those land, rather than being refused. That is what lets one
environment read another's ids — a staging deployment with one database can read a production
id minted for shard 3 instead of throwing at it.
