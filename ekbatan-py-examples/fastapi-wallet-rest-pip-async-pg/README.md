# fastapi-wallet-rest-pip-async-pg

A wallet service over HTTP on **PostgreSQL**, with its request path on **native asyncio**,
`ekbatan.aio`: every endpoint an `async def` that awaits an `AsyncActionExecutor` and async
repositories, on asyncpg, and the deposit under an async keyed lock. The event handler is async too:
it runs after the deposit, on dispatch's handler loop, and awaits an executor of its own. The jobs
that poll - db-scheduler, fan-out, dispatch, the daily stipend - stay on threads; the stipend's
work is awaited on the handler loop. The same service as [`fastapi-wallet-rest-uv-pg`](../fastapi-wallet-rest-uv-pg)
otherwise - the same model, events, converters and migrations - and the same again on
MySQL in [`fastapi-wallet-rest-uv-async-mysql`](../fastapi-wallet-rest-uv-async-mysql) and MariaDB in
[`fastapi-wallet-rest-uv-async-mariadb`](../fastapi-wallet-rest-uv-async-mariadb).

## How the schema gets there

This is the example that shows the **generated** schema, and it is the closest thing this port
has to Java's build:

```
make            # generate if a migration changed, then test
make run        # generate if a migration changed, then serve
make schema     # just the generation
```

`schema.py` is **generated and gitignored**, exactly as Java's `build/generated-jooq` is. A fresh
clone has no `schema.py`, runs `make`, and gets one - the same reason a fresh clone of the Java
project runs `./gradlew build`.

Under the hood, `python -m ekbatan.schemagen` does what the `dev.monosoul.jooq-docker` plugin
does there: start a throwaway container, run **your** migrations into it, reflect the result.
Make supplies the missing piece - a dependency on the migration files, so it regenerates when
they change and skips the container entirely when they have not. Measured: **12 milliseconds**
when nothing changed.

Its settings are in `pyproject.toml`, where Java keeps jOOQ's in the build script:

```toml
[tool.ekbatan.schemagen]
migrations = "src/fastapi_wallet_rest_async_pg/migrations"
out = "src/fastapi_wallet_rest_async_pg/schema.py"
image = "postgres:17-alpine"
```

Forced types go there too - this example has two, [below](#two-forced-types) - and includes and
excludes; see [schemagen](../../docs/database/schemagen.md).

Two things it does that a plain reflection would not:

**It forces the types.** A column reflected from PostgreSQL as `TIMESTAMP` compiles on MySQL to
`TIMESTAMP` - which has a 2038 ceiling and shifts with the session timezone - and a `UUID`
becomes `CHAR(32)`, the hex with its hyphens stripped, which no Java service can join against.
Both silent. Reflected types are mapped back onto the framework's helpers, which is precisely
Java's `forcedTypes` block.

**It refuses to reflect the framework's own tables.** `events`, `event_notifications` and
`scheduled_tasks` are emitted as calls to `ekbatan.schema`, because their shape is a contract a
CDC connector and other ports bind to. Java does the same with `includeFlywayTable(false)`.

And one thing Java gets for free that this had to add: the generated module records a fingerprint
of the migrations it came from and **checks it on import**. Gradle cannot compile without
regenerating; nothing in Python runs before `python -m app`, so a stale schema would otherwise
just quietly disagree with the database.

```
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=ekbatan -e POSTGRES_USER=ekbatan -e POSTGRES_DB=wallet postgres:17-alpine
```

<!-- build: written by scripts/sync-examples.py -->
```
make            # generate what is out of date, then test the built wheel, installed
make run        # run it; the database comes first, above
```

Built with **pip and setuptools**. The same project with [uv](../fastapi-wallet-rest-uv-async-pg) and [Poetry](../fastapi-wallet-rest-poetry-async-pg) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.

The venv is made with `python3`, which must be 3.13 or later - `make PYTHON=python3.13` if yours is older.
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

`to_record` and `from_record` stay plain functions - mapping waits on nothing - written in
each repository, as in the sync twin.

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

## Two forced types

`pyproject.toml` tells schemagen to turn two plain text columns into small classes of this
app's own:

```toml
# Every CHAR(3) column is a currency code, so the rule picks columns by their type.
[[tool.ekbatan.schemagen.forced-types]]
converter = "fastapi_wallet_rest_async_pg.converters.CurrencyConverter"
include-types = "CHAR\\(3\\)"

# One column, picked by its name.
[[tool.ekbatan.schemagen.forced-types]]
converter = "fastapi_wallet_rest_async_pg.converters.EmailAddressConverter"
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

## What differs from the other two databases

The domain is **byte-identical** across all three - model, events, actions, handlers,
repository, HTTP layer. Five things differ, and each is a real difference rather than a setting:

| | this project |
|---|---|
| url scheme | `postgresql+psycopg` (driver: `postgres` extra) |
| async driver | `asyncpg`, read off the same url (`postgres-async` extra) |
| keyed lock | `AsyncPostgresKeyedLockProvider`, on `pg_advisory_lock` |
| migration DDL | native `UUID` ids and `JSONB` payloads |
| test container | `postgres:17-alpine` |

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

Twenty cases against a real PostgreSQL in Testcontainers, through FastAPI's `TestClient`. A test that
reaches past HTTP into the service awaits on the loop the app runs on, through `client.portal`.
Two of them say what this example is for: the requests, the lock and the handler really run on
asyncpg and the jobs on psycopg, and every pool and setting comes from `ekbatan.yaml`, the UTC session
included.
