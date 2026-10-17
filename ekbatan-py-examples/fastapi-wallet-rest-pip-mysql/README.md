# fastapi-wallet-rest-pip-mysql

A wallet service over HTTP on **MySQL**: FastAPI, migrations from files, three separate
connection pools, and a keyed lock around the endpoint that needs one.

One of three databases for the same service - the Python counterpart to Java's `*-wallet-rest-*`
grid - and each built three ways, with uv, pip and Poetry. See [`../README.md`](../README.md) for
why the matrix is 21 projects here and 36 there.

```
docker run -d -p 3306:3306 -e MYSQL_ROOT_PASSWORD=ekbatan -e MYSQL_DATABASE=wallet mysql:8.4
```

<!-- build: written by scripts/sync-examples.py -->
```
make            # generate what is out of date, then test the built wheel, installed
make run        # run it; the database comes first, above
```

Built with **pip and setuptools**. The same project with [uv](../fastapi-wallet-rest-uv-mysql) and [Poetry](../fastapi-wallet-rest-poetry-mysql) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.

The venv is made with `python3`, which must be 3.13 or later - `make PYTHON=python3.13` if yours is older.
<!-- /build -->

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

**`ekbatan.yaml`** - what the Java examples keep in `application.yml`, in the same keys: the
three pools as `primaryConfig`, `jobsConfig` and `lockConfig`, a driver setting under
`dataSourceProperties`, the namespace, and `local-event-handler.handling.enabled`. The url and
login are `${NAME:default}` placeholders, so the file runs as it is against a local server and
the test fills them from its container.

## Its test

```
make
```

Eleven cases against a real MySQL in Testcontainers, including one that asserts this project is
actually running on MySQL - three projects differing only by url makes a wrong one invisible,
and every other test would still pass.
