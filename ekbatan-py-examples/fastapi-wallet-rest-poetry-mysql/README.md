# fastapi-wallet-rest-poetry-mysql

A wallet service over HTTP on **MySQL**: FastAPI, migrations from files, three separate
connection pools, and a keyed lock around the endpoint that needs one.

One of three databases for the same service - the Python counterpart to Java's `*-wallet-rest-*`
grid - and each built three ways, with uv, pip and Poetry. See [`../README.md`](../README.md) for
why the matrix is 42 projects here and 36 there.

```
docker run -d -p 3306:3306 -e MYSQL_ROOT_PASSWORD=ekbatan -e MYSQL_DATABASE=wallet mysql:8.4
```

<!-- build: written by scripts/sync-examples.py -->
```
make            # generate what is out of date, then test the built wheel, installed
make run        # run it; the database comes first, above
```

Built with **Poetry**. The same project with [uv](../fastapi-wallet-rest-uv-mysql) and [pip and setuptools](../fastapi-wallet-rest-pip-mysql) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.

Needs `poetry` 2 on the path.
<!-- /build -->

## Two forced types

`pyproject.toml` tells schemagen to turn two plain text columns into small classes of this
app's own:

```toml
# Every CHAR(3) column is a currency code, so the rule picks columns by their type.
[[tool.ekbatan.schemagen.forced-types]]
converter = "fastapi_wallet_rest_mysql.converters.CurrencyConverter"
include-types = "CHAR\\(3\\)"

# One column, picked by its name.
[[tool.ekbatan.schemagen.forced-types]]
converter = "fastapi_wallet_rest_mysql.converters.EmailAddressConverter"
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
repository, HTTP layer. Four things differ, and each is a real difference rather than a setting:

| | this project |
|---|---|
| url scheme | `mysql+pymysql` (driver: `mysql` extra) |
| keyed lock | `MySQLKeyedLockProvider`, on `GET_LOCK` / `RELEASE_LOCK` |
| migration DDL | `CHAR(36)` ids and `JSON` payloads |
| test container | `mysql:8.4` |

That list is the answer to "why not one project with a different url". Three of the four are
code, not configuration, and the fourth is a whole file.

**One schema change per migration file on MySQL and MariaDB**, for a reason worth knowing: they
commit every schema change on its own, so a migration that fails after one leaves it behind, to
be put right by hand before it runs again - where PostgreSQL rolls the whole file back. Several
statements in one file are otherwise fine; the migrator splits a file itself.

## The caps

The request pool is the cap on requests waiting on the database: ten connections, each request
waiting at most `poolTimeout` (5 s) for one, then a **503** with `Retry-After`. Before that,
`__main__.py` runs uvicorn with `limit_concurrency` (50 by default, `WALLET_LIMIT_CONCURRENCY`):
past 50 open requests uvicorn answers 503 itself.

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

**`app.py`** - three pools. Requests get one, the scheduler its own (a job body holding the last
connection starves the heartbeat), and held lock leases a third (a lease *is* a held
connection).

**`ekbatan.yaml`** - what the Java examples keep in `application.yml`, in the same keys: the
three pools as `primaryConfig`, `jobsConfig` and `lockConfig`, driver settings under
`dataSourceProperties` - `time_zone = '+00:00'` in `init_command` on every pool puts each session on UTC, whatever
the server's default (the test proves it against a server set to Tehran) - the namespace, and `local-event-handler.handling.enabled`. The url and
login are `${NAME:default}` placeholders, so the file runs as it is against a local server and
the test sets them in the environment from its container.

## Its test

```
make
```

Twenty cases against a real MySQL in Testcontainers, including one that asserts this project is
actually running on MySQL - three projects differing only by url makes a wrong one invisible,
and every other test would still pass.
