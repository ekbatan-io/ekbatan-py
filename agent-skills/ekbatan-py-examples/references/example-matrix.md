# Example matrix

Seven projects. Only the REST example is replicated per database - the same shape the Java port
uses, where the wallet REST project exists per dialect and nothing else does.

| project | dialect | shows | generates a schema |
| --- | --- | --- | :---: |
| `fastapi_wallet_rest_pg` | PostgreSQL | the reference REST service | yes |
| `fastapi_wallet_rest_mysql` | MySQL | the same service, one dialect over | yes |
| `fastapi_wallet_rest_mariadb` | MariaDB | the same service again | yes |
| `fastapi_wallet_rest_sharded_pg` | PostgreSQL x2 | routing by the shard bits in an id | yes |
| `fastapi_wallet_saga_pg` | PostgreSQL | choreography, compensation, and a 202 | yes |
| `job_worker_pg` | PostgreSQL | cluster-exclusive jobs, no HTTP at all | yes |
| `wallet_service_sqlite` | SQLite | the smallest one. No container, no web stack | no |

## What each carries

Every project is standalone - `cd` into it and `make` works, the way each Java example carries
its own Gradle wrapper.

```
Makefile          schema, run, test, clean. The up-to-date check lives here
.gitignore        the generated schema.py
README.md         what this example shows, and what to look at first
app.py            the wiring, all of it in one place
api.py            the HTTP layer, and nothing else
model.py          the domain
actions.py        the units of work
repository.py     the queries this application actually asks
handlers.py       the in-process event handlers
jobs.py           the distributed jobs
migrations/       Alembic revisions - the input to schemagen
schema.py         GENERATED, gitignored. Never commit or edit
```

`wallet_service_sqlite` is the exception: a hand-written `schema.py`, committed, because there is
no container to reflect from.

## The axes that do not exist here

Java's matrix is 41 projects because it multiplies stack x build tool x dialect x runtime.
Three of those axes collapse:

- **stack** - there is no Spring/Quarkus/Micronaut split. FastAPI, or wire it yourself.
- **build tool** - uv, and a Makefile. There is no Maven equivalent to mirror.
- **runtime** - no native image. CPython has no counterpart.

What remains is the dialect axis, which is mirrored exactly, plus the four special cases.
