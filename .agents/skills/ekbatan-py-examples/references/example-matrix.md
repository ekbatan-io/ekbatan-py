# Example matrix

Seven examples, each built three ways - with uv, pip and Poetry - so 21 projects:
`<stack>-<domain>-<style>-<build>[-variant]-<db>`, Java's own naming. Only the REST example is
replicated per database, the same shape the Java port uses.

| example (`{build}` = uv, pip, poetry) | dialect | shows | generates a schema |
| --- | --- | --- | :---: |
| `fastapi-wallet-rest-{build}-pg` | PostgreSQL | the reference REST service | yes |
| `fastapi-wallet-rest-{build}-mysql` | MySQL | the same service, one dialect over | yes |
| `fastapi-wallet-rest-{build}-mariadb` | MariaDB | the same service again | yes |
| `fastapi-wallet-rest-{build}-sharded-pg` | PostgreSQL x2 | routing by the shard bits in an id | yes |
| `fastapi-wallet-saga-{build}-pg` | PostgreSQL | choreography, compensation, and a 202 | yes |
| `job-worker-{build}-pg` | PostgreSQL | cluster-exclusive jobs, no HTTP at all | yes |
| `wallet-service-{build}-sqlite` | SQLite | the smallest one. No container, no web stack | no |

The uv project is the source; `scripts/sync-examples.py` writes the other two from it.

## What each carries

Every project is standalone - `cd` into it and `make` works, the way each Java example carries
its own Gradle wrapper.

```
pyproject.toml    the build: uv_build, setuptools or poetry-core - and where ekbatan comes from
Makefile          env, schema, test (the wheel, installed), run, clean
.gitignore        the environment, the wheel, the generated schema.py
README.md         what this example shows, and what to look at first
src/<package>/    the package - the same name, and the same files, in all three builds:
  app.py            the wiring, all of it in one place
  api.py            the HTTP layer, and nothing else
  model.py          the domain
  actions.py        the units of work
  repository.py     the queries this application actually asks
  handlers.py       the in-process event handlers
  jobs.py           the distributed jobs
  ekbatan.yaml      the settings, read at startup
  migrations/       V<version>__<desc>.sql files - migrated at startup, and the input to schemagen
  schema.py         GENERATED, gitignored. Never commit or edit
```

The SQLite examples are the exception: a hand-written `schema.py`, committed, because there is
no container to reflect from.

## The axes that do not exist here

Java's matrix is 41 projects because it multiplies stack x build tool x dialect x runtime.
Two of those axes collapse:

- **stack** - there is no Spring/Quarkus/Micronaut split. FastAPI, or wire it yourself.
- **runtime** - no native image. CPython has no counterpart.

The **build tool** axis is kept: it decides what a wheel holds (Poetry drops a gitignored
`schema.py`, setuptools drops the migrations and `ekbatan.yaml`), which only a built, installed
wheel shows. The dialect axis is mirrored exactly, plus the four special cases.
