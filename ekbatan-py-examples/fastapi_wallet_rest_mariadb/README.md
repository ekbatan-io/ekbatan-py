# fastapi_wallet_rest_mariadb

A wallet service over HTTP on **MariaDB**: FastAPI, migrations from files, three separate
connection pools, and a keyed lock around the endpoint that needs one.

One of three projects that differ only by database - the Python counterpart to Java's
`*-wallet-rest-*` grid. See [`../README.md`](../README.md) for why the matrix is three here and
thirty-six there.

```
docker run -d -p 3306:3306 -e MARIADB_ROOT_PASSWORD=ekbatan -e MARIADB_DATABASE=wallet mariadb:11.4
uv sync --extra mariadb --group examples
cd ekbatan-py-examples && uv run python -m fastapi_wallet_rest_mariadb
```

## What differs from the other two

The domain is **byte-identical** across all three - model, events, actions, handlers,
repository, HTTP layer. Four things differ, and each is a real difference rather than a setting:

| | this project |
|---|---|
| url scheme | `mariadb+pymysql` (driver: `mariadb` extra) |
| keyed lock | `MariaDBKeyedLockProvider`, on `GET_LOCK` / `RELEASE_LOCK` |
| migration DDL | `CHAR(36)` ids and `JSON` payloads |
| test container | `mariadb:11.4` |

That list is the answer to "why not one project with a different url". Three of the four are
code, not configuration, and the fourth is a whole file.

**One statement per `op.execute`** in the migration, for a reason worth knowing: PyMySQL
executes a single statement per call and rejects a script, while psycopg accepts one. A
migration written as one block works on PostgreSQL and fails on MySQL and MariaDB with a syntax
error pointing at the second `CREATE TABLE`.

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
- **Error kinds map to status codes once**: not-found 404, validation 400, stale **409** not 500.

**`app.py`** - three pools. Requests get one, the scheduler its own (a job body holding the last
connection starves the heartbeat), and held lock leases a third (a lease *is* a held
connection).

## Its test

```
uv run pytest ekbatan-py-examples/fastapi_wallet_rest_mariadb
```

Nine cases against a real MariaDB in Testcontainers, including one that asserts this project is
actually running on MariaDB - three projects differing only by url makes a wrong one invisible,
and every other test would still pass.
