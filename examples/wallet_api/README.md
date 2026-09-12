# wallet_api

A wallet service over HTTP, shaped like something you could deploy: FastAPI, PostgreSQL,
migrations from files, three separate connection pools, and a keyed lock around the endpoint
that needs one.

This is the Python counterpart to Java's `*-wallet-rest-*` grid. Java needs thirty-six of those
because DI framework, build tool and dialect each change the *project*; here the database is a
URL and the build tool is a line in `pyproject.toml`, so one project covers the same ground.

```
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=ekbatan -e POSTGRES_USER=ekbatan \
  -e POSTGRES_DB=wallet postgres:17-alpine

uv run python -m examples.wallet_api
```

Then:

```
curl -XPOST localhost:8000/wallets -H 'content-type: application/json' \
  -d '{"owner_id":"11111111-1111-1111-1111-111111111111","currency":"EUR","opening_balance":"100.00"}'

curl -XPOST localhost:8000/wallets/<id>/deposits -H 'content-type: application/json' \
  -d '{"amount":"25.50","recipient":"ada@example.com"}'

curl localhost:8000/wallets/<id>/alerts     # written by a handler nobody called
```

## What to look at, in order

**`api.py`** — the HTTP layer, and nothing else. Every endpoint is four lines: read the request,
name a unit of work, hand it parameters, shape the reply. No transaction, no flush, no publish.

Three things in here are worth copying:

- **The deposit takes a keyed lock**, and the lease spans both `perform()` *and* the executor's
  commit. Optimistic locking alone would be correct — a concurrent deposit loses the version
  check and raises — but correct by *failing*. Measured, with the lock removed: eight concurrent
  deposits returned `[200, 409, 409, 200, 409, …]`. With it, eight 200s and an exact balance.
- **`try_acquire`, not `acquire`.** An HTTP request must not block forever. A lease that cannot
  be had is `None`, which is *contention* rather than failure — the framework keeps those
  distinguishable on purpose — and here it becomes a 503 with a `Retry-After`.
- **The error kinds map to status codes once**, in one place: not-found → 404, validation → 400,
  stale → **409**, not 500. The caller of a 409 did nothing wrong and the right response is to
  retry, which is exactly what the framework's error hierarchy exists to let you say.

**`app.py`** — the wiring, and **three separate pools**. Requests get one, the scheduler gets
its own (a job body holding the last connection would starve the heartbeat, and an execution
whose heartbeat stops is declared dead and run again elsewhere), and held lock leases get a
third (a lease *is* a held connection, so it must not sit on a request's). Java's examples carry
the same three under a `lockConfig` slot.

While this example was being written, db-scheduler warned at startup that the job pool was too
small for the worker count. That warning was right, and the default here changed because of it.

**`migrations/`** — Alembic, run at startup from files, not `create_all`. The revision was
generated with `emit_ddl` and pasted, deliberately: a revision has to mean the same thing
forever, and one that read today's `schema.py` would quietly change meaning every time a column
was added.

**`model.py` → `actions.py` → `handlers.py`** — the same domain as `wallet_service`, so you can
read either and recognise the other.

## What is different from `wallet_service`

`wallet_service` is the ten-second demo: SQLite, one command, no server, everything driven from
a script so it reads top to bottom. This is the deployment shape. Same domain, and the
difference between them is entirely in `app.py` and what surrounds it.

| | `wallet_service` | `wallet_api` |
|---|---|---|
| database | SQLite, temporary | PostgreSQL |
| schema | `create_all` | Alembic revisions |
| pools | one | three |
| jobs | run by hand, manual clock | real scheduler, real threads |
| locks | — | `PostgresKeyedLockProvider` |
| driven by | a script | HTTP |

## Its test

```
uv run pytest examples/wallet_api
```

Testcontainers, a real Postgres, and the endpoints driven over HTTP — the same shape as the
Testcontainers test in every Java grid example. It asserts what this README claims, so prose
that goes stale here turns something red.
