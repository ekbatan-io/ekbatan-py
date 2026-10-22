# fastapi-wallet-rest-uv-async-mariadb

A wallet service over HTTP on **MariaDB**, with its request path on **native asyncio**,
`ekbatan.aio`: every endpoint an `async def` that awaits an `AsyncActionExecutor` and async
repositories, on aiomysql, and the deposit under an async keyed lock. The event handler is async too:
it runs after the deposit, on dispatch's handler loop, and awaits an executor of its own. The jobs
that poll - db-scheduler, fan-out, dispatch, the daily stipend - stay on threads; the stipend's
work is awaited on the handler loop. The same service as [`fastapi-wallet-rest-uv-mariadb`](../fastapi-wallet-rest-uv-mariadb)
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

Built with **uv**. The same project with [pip and setuptools](../fastapi-wallet-rest-pip-async-mariadb) and [Poetry](../fastapi-wallet-rest-poetry-async-mariadb) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.
<!-- /build -->

## What is async, and what is not

| | here |
| --- | --- |
| the endpoints | `async def`, awaiting the service - nothing blocks the event loop |
| the request path | `AsyncDatabaseRegistry`, `WalletRepository` and `AlertRepository` on the async bases, `AsyncAction`s, an `AsyncActionExecutor` |
| the keyed lock | async: `ekbatan.aio.locks`, taken around `await executor.execute(...)` on the deposit |
| the event handler | async: `MoneyDepositedEventHandler.handle` is `async def` and awaits `RaiseAlertAction` through its own `AsyncActionExecutor`, on dispatch's handler loop - after the deposit has committed |
| the jobs | sync: db-scheduler, and fan-out, dispatch and `PayStipendJob`, on its threads; the stipend's work is a coroutine, run on the handler loop with `loop.run(...)` (`jobs.py`) |
| migrations, `schemagen` | sync, through PyMySQL |

`to_record` and `from_record` stay plain functions - mapping waits on nothing - written in
each repository, as in the sync twin.

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
handler loop - an async pool belongs to the loop it is used on. The stipend job hands its work to
the same loop, so it deposits through the same executor. At shutdown the jobs stop first,
then the handler's registry is closed on that loop, then the loop.

## Two forced types

`pyproject.toml` tells schemagen to turn two plain text columns into small classes of this
app's own:

```toml
# Every CHAR(3) column is a currency code, so the rule picks columns by their type.
[[tool.ekbatan.schemagen.forced-types]]
converter = "fastapi_wallet_rest_async_mariadb.converters.CurrencyConverter"
include-types = "CHAR\\(3\\)"

# One column, picked by its name.
[[tool.ekbatan.schemagen.forced-types]]
converter = "fastapi_wallet_rest_async_mariadb.converters.EmailAddressConverter"
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
class AlertsRecord(dict[str, Any]):
    ...

    def column_recipient(self) -> EmailAddress: ...


class WalletsRecord(dict[str, Any]):
    ...

    def column_currency(self) -> Currency: ...
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

## What differs from the other two databases

The domain is **byte-identical** across all three - model, events, actions, handlers,
repository, HTTP layer. Five things differ, and each is a real difference rather than a setting:

| | this project |
|---|---|
| url scheme | `mariadb+pymysql` (driver: `mariadb` extra) |
| async driver | `aiomysql`, read off the same url (`mariadb-async` extra) |
| keyed lock | `AsyncMariaDBKeyedLockProvider`, on `GET_LOCK` / `RELEASE_LOCK` |
| migration DDL | `CHAR(36)` ids and `JSON` payloads |
| test container | `mariadb:11.4` |

That list is the answer to "why not one project with a different url". Most of it is code, not
configuration, and the migration DDL is a whole file.

**One schema change per migration file on MySQL and MariaDB**, for a reason worth knowing: they
commit every schema change on its own, so a migration that fails after one leaves it behind, to
be put right by hand before it runs again - where PostgreSQL rolls the whole file back. Several
statements in one file are otherwise fine; the migrator splits a file itself.

## What to look at

**`api.py`** - the HTTP layer, and nothing else. Every endpoint is four lines: read the request,
name a unit of work, hand it parameters, shape the reply.

- **The deposit takes a keyed lock**, and the lease spans both `perform()` *and* the commit.
  Optimistic locking alone would be correct - a concurrent deposit loses the version check and
  raises - but correct by *failing*. Measured with the lock removed: eight concurrent deposits
  returned `[200, 409, 409, 200, 409, ...]`. With it, eight 200s and an exact balance.
- **`try_acquire`, not `acquire`.** An HTTP request must not block forever. A lease that cannot
  be had is `None` - *contention*, which the framework keeps distinguishable from failure - and
  here becomes a 503 with `Retry-After`.
- **Error kinds map to status codes once**: not-found 404, validation 400, stale **409** not 500,
  a request that waited out the pool **503**.

**`app.py`** and **`ekbatan.yaml`** - four pools, each for what uses it; see
[One config file, two drivers](#one-config-file-two-drivers).

## Its test

```
make
```

Twenty cases against a real MariaDB in Testcontainers, through FastAPI's `TestClient`. A test that
reaches past HTTP into the service awaits on the loop the app runs on, through `client.portal`.
Two of them say what this example is for: the requests, the lock and the handler really run on
aiomysql and the jobs on PyMySQL, and every pool and setting comes from `ekbatan.yaml`, the UTC session
included.
