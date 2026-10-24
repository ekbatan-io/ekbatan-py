# Example matrix

Fourteen examples, each built three ways - with uv, pip and Poetry - so 42 projects:
`<stack>-<domain>-<style>-<build>[-variant]-<db>`, Java's own naming. Only the REST example is
replicated per database, the same shape the Java port uses. Each follows the matching Java
example piece for piece - model, actions, handler, jobs, endpoints, test cases - under Java's
names; the REST examples and the job worker run two shards (global, Mexico) on two servers, as
Java's do, the saga one database.

| example (`{build}` = uv, pip, poetry) | dialect | shows | generates a schema |
| --- | --- | --- | :---: |
| `fastapi-wallet-rest-{build}-pg` | PostgreSQL x2 | the reference REST service, two shards | yes |
| `fastapi-wallet-rest-{build}-mysql` | MySQL x2 | the same service, one dialect over | yes |
| `fastapi-wallet-rest-{build}-mariadb` | MariaDB x2 | the same service again | yes |
| `fastapi-wallet-rest-{build}-sharded-pg` | PostgreSQL x2 | the same code as `-pg`, as Java's sharded example is; its test follows a Mexican wallet | yes |
| `fastapi-wallet-saga-{build}-pg` | PostgreSQL | choreography, compensation, and a 202 | yes |
| `job-worker-{build}-pg` | PostgreSQL x2 | cluster-exclusive jobs, no HTTP at all | yes |
| `wallet-service-{build}-sqlite` | SQLite x2 | the REST pieces run by a script. No container, no web stack | no |
| `fastapi-wallet-rest-{build}-async-pg` | PostgreSQL x2 | the REST service on `ekbatan.aio` (asyncpg) | yes |
| `fastapi-wallet-rest-{build}-async-mysql` | MySQL x2 | the same, on aiomysql | yes |
| `fastapi-wallet-rest-{build}-async-mariadb` | MariaDB x2 | the same, on aiomysql | yes |
| `wallet-service-{build}-async-sqlite` | SQLite x2 | the SQLite one on `ekbatan.aio` (aiosqlite) | no |
| `fastapi-wallet-rest-{build}-async-sharded-pg` | PostgreSQL x2 | the sharded one on `ekbatan.aio` | yes |
| `fastapi-wallet-saga-{build}-async-pg` | PostgreSQL | the saga, its later steps run by `async def` handlers on the handler loop | yes |
| `job-worker-{build}-async-pg` | PostgreSQL x2 | the job worker, its jobs' work run on a worker loop (`HandlerLoop`), awaited | yes |

Only the job worker has jobs of its own (`WalletStipendJob`, `WalletReportJob`), as in Java; the
REST, saga and SQLite examples run just the fan-out and handling jobs.

The uv project is the source; `scripts/sync-examples.py` writes the other two from it.

## What each carries

Every project is standalone - `cd` into it and `make` works, the way each Java example carries
its own Gradle wrapper.

```
pyproject.toml    the build: uv_build, setuptools or poetry-core - and where ekbatan comes from;
                  [tool.ekbatan.schemagen]: migrations, out, image, and its forced types
Makefile          env, schema (bare `python -m ekbatan.schemagen`), test (the wheel, installed), run, clean
.gitignore        the environment, the wheel, the generated schema.py
README.md         what this example shows, and what to look at first
src/<package>/    the package - the same name, and the same files, in all three builds:
  app.py            the wiring, all of it in one place
  api.py            the HTTP layer, and nothing else
  model.py          the domain, with the small value classes the forced types read into
  converters.py     the converters the forced types name; imports model.py, never schema.py
  actions.py        the units of work
  repository.py     the queries this application actually asks
  handlers.py       the in-process event handlers
  jobs.py           the distributed jobs (the job worker only)
  ekbatan.yaml      the settings, read at startup
  migrations/       V<version>__<desc>.sql files - migrated at startup, and the input to schemagen
  schema.py         GENERATED, gitignored. Never commit or edit
```

The SQLite examples are the exception: a hand-written `schema.py`, committed. They have no
migrations - `create_all` builds the tables from that file - so there is nothing for schemagen to
read; their `schema.py` wraps the currency column in `CurrencyConverter` by hand.

## Forced types

Every generating example reads `CHAR(3)` columns as the app's own `Currency`, picked by type;
the saga also reads its step column as an enum, picked by name:

| example | by type (`include-types`) | by name (`include-expression`) |
| --- | --- | --- |
| REST (pg, mysql, mariadb, sharded-pg, and their async twins), job worker (both) | `CHAR\(3\)` -> `Currency` | - |
| saga (both) | `CHAR\(3\)` -> `Currency` | `transfer_steps\.step` -> `TransferStepName` |

The rules live in `scripts/sync-examples.py` (`Forced`, `WALLET_FORCED`, `SAGA_FORCED`), which
writes every `pyproject.toml` - an edit to a project's own file is overwritten. Action parameters
and events keep the plain text (`"currency": "EUR"`), because they are outbox JSON; the
`state` columns are never converted, because the framework reads them as text.

## The axes that do not exist here

Java's matrix is 41 projects because it multiplies stack x build tool x dialect x runtime.
Two of those axes collapse:

- **stack** - there is no Spring/Quarkus/Micronaut split. FastAPI, or wire it yourself.
- **runtime** - no native image. CPython has no counterpart.

The **build tool** axis is kept: it decides what a wheel holds (Poetry drops a gitignored
`schema.py`, setuptools drops the migrations and `ekbatan.yaml`), which only a built, installed
wheel shows. The dialect axis is mirrored exactly, plus the four special cases.
