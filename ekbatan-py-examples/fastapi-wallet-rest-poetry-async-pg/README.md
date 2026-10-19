# fastapi-wallet-rest-poetry-async-pg

A wallet service over HTTP on **PostgreSQL**, with its request path on **native asyncio**,
`ekbatan.aio`: every endpoint an `async def` that awaits an `AsyncActionExecutor` and async
repositories, on asyncpg, and the deposit under an async keyed lock. The event handler is async too:
it runs after the deposit, on dispatch's handler loop, and awaits an executor of its own. The jobs
that poll - db-scheduler, fan-out, dispatch, the daily stipend - stay on threads; the stipend's
work is awaited on the handler loop. The same service as [`fastapi-wallet-rest-uv-pg`](../fastapi-wallet-rest-uv-pg)
otherwise - the same model, events, converters and migrations - and the same again on
MySQL in [`fastapi-wallet-rest-uv-async-mysql`](../fastapi-wallet-rest-uv-async-mysql) and MariaDB in
[`fastapi-wallet-rest-uv-async-mariadb`](../fastapi-wallet-rest-uv-async-mariadb).

```
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=ekbatan -e POSTGRES_USER=ekbatan -e POSTGRES_DB=wallet postgres:17-alpine
```

<!-- build: written by scripts/sync-examples.py -->
```
make            # generate what is out of date, then test the built wheel, installed
make run        # run it; the database comes first, above
```

Built with **Poetry**. The same project with [uv](../fastapi-wallet-rest-uv-async-pg) and [pip and setuptools](../fastapi-wallet-rest-pip-async-pg) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.

Needs `poetry` 2 on the path.
<!-- /build -->

## What is async, and what is not

| | here |
| --- | --- |
| the endpoints | `async def`, awaiting the service - nothing blocks the event loop |
| the request path | `AsyncDatabaseRegistry`, `WalletRepository` and `AlertRepository` on the async bases, `AsyncAction`s, an `AsyncActionExecutor` |
| the keyed lock | async: `ekbatan.aio.locks`, taken around `await executor.execute(...)` on the deposit |
| the event handler | async: `MoneyDepositedEventHandler.handle` is `async def` and awaits `RaiseAlertAction` through its own `AsyncActionExecutor`, on dispatch's handler loop - after the deposit has committed |
| the jobs | sync: db-scheduler, and fan-out, dispatch and `PayStipendJob`, on its threads; the stipend's work is a coroutine, run on the handler loop with `loop.run(...)` (`jobs.py`) |
| migrations, `schemagen` | sync, through psycopg |

`to_row` and `from_row` stay plain functions - mapping waits on nothing - written in each
repository, as in the sync twin.

## One config file, two drivers

`ekbatan.yaml` names the sync driver, `postgresql+psycopg://`. The sync pools connect with psycopg; the async
registry reads the same url as "this PostgreSQL" and connects through its async default, asyncpg. Four
pools, each sized for what uses it:

| pool | used by | driver |
| --- | --- | --- |
| `primaryConfig` | the requests | asyncpg |
| `lockConfig` | the deposit's keyed lock - one connection per held lease | asyncpg |
| `handlersConfig` | the event handler, on dispatch's handler loop | asyncpg |
| `jobsConfig` | db-scheduler, and fan-out and dispatch | psycopg |

Each session is put on UTC by `options: -c TimeZone=UTC`, the documented setting, which both drivers read - asyncpg as its own `server_settings`.

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
handler loop - an async pool belongs to the loop it is used on. The stipend job hands its work to
the same loop, so it deposits through the same executor. At shutdown the jobs stop first,
then the handler's registry is closed on that loop, then the loop.

## Its test

```
make
```

Fifteen cases against a real PostgreSQL in Testcontainers, through FastAPI's `TestClient`. A test that
reaches past HTTP into the service awaits on the loop the app runs on, through `client.portal`.
Two of them say what this example is for: the requests, the lock and the handler really run on
asyncpg and the jobs on psycopg, and every pool and setting comes from `ekbatan.yaml`, the UTC session
included.
