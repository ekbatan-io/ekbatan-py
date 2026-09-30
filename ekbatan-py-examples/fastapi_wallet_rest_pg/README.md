# fastapi_wallet_rest_pg

A wallet service over HTTP on **PostgreSQL**: FastAPI, migrations from files, three separate
connection pools, and a keyed lock around the endpoint that needs one.

One of three projects that differ only by database - the Python counterpart to Java's
`*-wallet-rest-*` grid. See [`../README.md`](../README.md) for why the matrix is three here and
thirty-six there.

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
uv sync --extra postgres --group examples
cd ekbatan-py-examples && uv run python -m fastapi_wallet_rest_pg
```

## What differs from the other two

The domain is **byte-identical** across all three - model, events, actions, handlers,
repository, HTTP layer. Four things differ, and each is a real difference rather than a setting:

| | this project |
|---|---|
| url scheme | `postgresql+psycopg` (driver: `postgres` extra) |
| keyed lock | `PostgresKeyedLockProvider`, on `pg_advisory_lock` |
| migration DDL | native `UUID` ids and `JSONB` payloads |
| test container | `postgres:17-alpine` |

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

## Its test

```
uv run pytest ekbatan-py-examples/fastapi_wallet_rest_pg
```

Nine cases against a real PostgreSQL in Testcontainers, including one that asserts this project is
actually running on PostgreSQL - three projects differing only by url makes a wrong one invisible,
and every other test would still pass.
