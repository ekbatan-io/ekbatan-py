# fastapi-wallet-rest-uv-async-pg

A wallet service over HTTP on **PostgreSQL**, with its request path on **native asyncio**,
`ekbatan.aio`: every endpoint an `async def` that awaits an `AsyncActionExecutor` and async
repositories, on asyncpg. The event handler and the jobs stay sync, on threads, as they do in any
Ekbatan service. The same service as [`fastapi-wallet-rest-uv-pg`](../fastapi-wallet-rest-uv-pg)
otherwise - the same model, events, converters and migrations - and the same again on
MariaDB in [`fastapi-wallet-rest-uv-async-mariadb`](../fastapi-wallet-rest-uv-async-mariadb).

```
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=ekbatan -e POSTGRES_USER=ekbatan -e POSTGRES_DB=wallet postgres:17-alpine
```

<!-- build: written by scripts/sync-examples.py -->
```
make            # generate what is out of date, then test the built wheel, installed
make run        # run it; the database comes first, above
```

Built with **uv**. The same project with [pip and setuptools](../fastapi-wallet-rest-pip-async-pg) and [Poetry](../fastapi-wallet-rest-poetry-async-pg) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.
<!-- /build -->

## What is async, and what is not

| | here |
| --- | --- |
| the endpoints | `async def`, awaiting the service - nothing blocks the event loop |
| the request path | `AsyncDatabaseRegistry`, `WalletRepository` and `AlertRepository` on the async bases, `AsyncAction`s, an `AsyncActionExecutor` |
| the event handler | sync: `MoneyDepositedEventHandler` runs `RaiseAlertAction` through a sync `ActionExecutor`, on dispatch's threads |
| the jobs | sync: fan-out and dispatch, on db-scheduler's threads |
| migrations, `schemagen` | sync, through psycopg |

`to_row` and `from_row` are plain functions either way, so each mapping is written once
(`WalletRows`, `AlertRows`) and both kinds of repository take it.

## One config file, two drivers

`ekbatan.yaml` names the sync driver, `postgresql+psycopg://`. The sync pools connect with psycopg; the async
registry reads the same url as "this PostgreSQL" and connects through its async default, asyncpg. Three
pools, each sized for what uses it:

| pool | used by | driver |
| --- | --- | --- |
| `primaryConfig` | the requests | asyncpg |
| `handlersConfig` | the event handler | psycopg |
| `jobsConfig` | db-scheduler | psycopg |

Each session is put on UTC by `options: -c TimeZone=UTC`, the documented setting, which both drivers read - asyncpg as its own `server_settings`.

## The caps

The request pool is the cap on requests waiting on the database: ten connections, each request
waiting at most `poolTimeout` (5 s) for one, then a **503** with `Retry-After`. Before that,
`__main__.py` runs uvicorn with `limit_concurrency` (50 by default, `WALLET_LIMIT_CONCURRENCY`):
past 50 open requests uvicorn answers 503 itself.

## No keyed lock

A keyed lock held across an async unit of work is not provided, so the deposit takes none. The
optimistic lock keeps it correct: a deposit that loses the version check is run again on a fresh
read, once by default, and one that loses twice is a **409** for the caller to retry. The test
fires eight deposits at one wallet at once and checks that exactly the ones answered 200 are in
the balance.

## Its test

```
make
```

Fifteen cases against a real PostgreSQL in Testcontainers, through FastAPI's `TestClient`. A test that
reaches past HTTP into the service awaits on the loop the app runs on, through `client.portal`.
Two of them say what this example is for: the requests really run on asyncpg and the handler on
psycopg, and every pool and setting comes from `ekbatan.yaml`, the UTC session included.
