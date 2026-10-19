# fastapi-wallet-rest-pip-async-mariadb

A wallet service over HTTP on **MariaDB**, with its request path on **native asyncio**,
`ekbatan.aio`: every endpoint an `async def` that awaits an `AsyncActionExecutor` and async
repositories, on aiomysql, and the deposit under an async keyed lock. The event handler is async too:
it runs after the deposit, on dispatch's handler loop, and awaits an executor of its own. The jobs
that poll - db-scheduler, fan-out, dispatch - stay on threads. The same service as [`fastapi-wallet-rest-uv-mariadb`](../fastapi-wallet-rest-uv-mariadb)
otherwise - the same model, events, converters and migrations - and the same again on
PostgreSQL in [`fastapi-wallet-rest-uv-async-pg`](../fastapi-wallet-rest-uv-async-pg) and MySQL in
[`fastapi-wallet-rest-uv-async-mysql`](../fastapi-wallet-rest-uv-async-mysql).

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
| the keyed lock | async: `ekbatan.aio.locks`, taken around `await executor.execute(...)` on the deposit |
| the event handler | async: `MoneyDepositedEventHandler.handle` is `async def` and awaits `RaiseAlertAction` through its own `AsyncActionExecutor`, on dispatch's handler loop - after the deposit has committed |
| the jobs | sync: db-scheduler, and fan-out and dispatch, on its threads |
| migrations, `schemagen` | sync, through PyMySQL |

`to_row` and `from_row` are plain functions, so each mapping is written once (`WalletRows`,
`AlertRows`) in a small class the repository takes it from.

## One config file, two drivers

`ekbatan.yaml` names the sync driver, `mariadb+pymysql://`. The sync pools connect with PyMySQL; the async
registry reads the same url as "this MariaDB" and connects through its async default, aiomysql. Four
pools, each sized for what uses it:

| pool | used by | driver |
| --- | --- | --- |
| `primaryConfig` | the requests | aiomysql |
| `lockConfig` | the deposit's keyed lock - one connection per held lease | aiomysql |
| `handlersConfig` | the event handler, on dispatch's handler loop | aiomysql |
| `jobsConfig` | db-scheduler, and fan-out and dispatch | PyMySQL |

Each session is put on UTC by `init_command: SET time_zone = '+00:00'`, the documented setting, which both drivers read.

## The caps

The request pool is the cap on requests waiting on the database: ten connections, each request
waiting at most `poolTimeout` (5 s) for one, then a **503** with `Retry-After`. Before that,
`__main__.py` runs uvicorn with `limit_concurrency` (50 by default, `WALLET_LIMIT_CONCURRENCY`):
past 50 open requests uvicorn answers 503 itself.

## The keyed lock, and the handler

The deposit takes the wallet's key with `try_acquire` around `await executor.execute(...)`: a
second deposit on the same wallet waits its turn on the event loop - the holder is the request's
task - and then succeeds; one that cannot get the key within five seconds is a **503** with
`Retry-After`. The test fires eight deposits at one wallet at once: all eight are 200, and the
balance is exact.

The handler's registry and executor are its own, built on `handlersConfig` and used only on the
handler loop - an async pool belongs to the loop it is used on. At shutdown the jobs stop first,
then the handler's registry is closed on that loop, then the loop.

## Its test

```
make
```

Fifteen cases against a real MariaDB in Testcontainers, through FastAPI's `TestClient`. A test that
reaches past HTTP into the service awaits on the loop the app runs on, through `client.portal`.
Two of them say what this example is for: the requests, the lock and the handler really run on
aiomysql and the jobs on PyMySQL, and every pool and setting comes from `ekbatan.yaml`, the UTC session
included.
