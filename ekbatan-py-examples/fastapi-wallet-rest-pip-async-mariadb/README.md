# fastapi-wallet-rest-pip-async-mariadb

A wallet service over HTTP on **MariaDB**, with its request path on **native asyncio**,
`ekbatan.aio`: every endpoint an `async def` that awaits an `AsyncActionExecutor` and async
repositories, on aiomysql. The event handler and the jobs stay sync, on threads, as they do in any
Ekbatan service. The same service as [`fastapi-wallet-rest-uv-mariadb`](../fastapi-wallet-rest-uv-mariadb)
otherwise - the same model, events, converters and migrations - and the same again on
PostgreSQL in [`fastapi-wallet-rest-uv-async-pg`](../fastapi-wallet-rest-uv-async-pg).

```
docker run -d -p 3306:3306 -e MARIADB_ROOT_PASSWORD=ekbatan -e MARIADB_DATABASE=wallet mariadb:11.4
```

<!-- build: written by scripts/sync-examples.py -->
```
make            # generate what is out of date, then test the built wheel, installed
make run        # run it; the database comes first, above
```

Built with **pip and setuptools**. The same project with [uv](../fastapi-wallet-rest-uv-async-mariadb) and [Poetry](../fastapi-wallet-rest-poetry-async-mariadb) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.

The venv is made with `python3`, which must be 3.13 or later - `make PYTHON=python3.13` if yours is older.
<!-- /build -->

## What is async, and what is not

| | here |
| --- | --- |
| the endpoints | `async def`, awaiting the service - nothing blocks the event loop |
| the request path | `AsyncDatabaseRegistry`, `WalletRepository` and `AlertRepository` on the async bases, `AsyncAction`s, an `AsyncActionExecutor` |
| the event handler | sync: `MoneyDepositedEventHandler` runs `RaiseAlertAction` through a sync `ActionExecutor`, on dispatch's threads |
| the jobs | sync: fan-out and dispatch, on db-scheduler's threads |
| migrations, `schemagen` | sync, through PyMySQL |

`to_row` and `from_row` are plain functions either way, so each mapping is written once
(`WalletRows`, `AlertRows`) and both kinds of repository take it.

## One config file, two drivers

`ekbatan.yaml` names the sync driver, `mariadb+pymysql://`. The sync pools connect with PyMySQL; the async
registry reads the same url as "this MariaDB" and connects through its async default, aiomysql. Three
pools, each sized for what uses it:

| pool | used by | driver |
| --- | --- | --- |
| `primaryConfig` | the requests | aiomysql |
| `handlersConfig` | the event handler | PyMySQL |
| `jobsConfig` | db-scheduler | PyMySQL |

Each session is put on UTC by `init_command: SET time_zone = '+00:00'`, the documented setting, which both drivers read.

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

Fifteen cases against a real MariaDB in Testcontainers, through FastAPI's `TestClient`. A test that
reaches past HTTP into the service awaits on the loop the app runs on, through `client.portal`.
Two of them say what this example is for: the requests really run on aiomysql and the handler on
PyMySQL, and every pool and setting comes from `ekbatan.yaml`, the UTC session included.
