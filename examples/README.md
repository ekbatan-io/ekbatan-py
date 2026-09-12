# Examples

Standalone applications. Each one runs on its own, imports only `ekbatan`, the standard library
and what the `examples` dependency group declares, and is imported by nothing.

That last part is deliberate. The framework's own tests have their own domain, in
`tests/fixtures/`, which exists to exercise every shape worth exercising - including a few no
application would want. If the tests read from here instead, every change made for coverage
would land in the documentation and every change made for clarity would break the suite. So
they do not meet, and `tests/test_structure.py` keeps it that way.

## Start here

| | | |
|---|---|---|
| [`wallet_service/`](wallet_service/) | SQLite, one command, no server | one wallet end to end: a model that emits events, actions that stage them, a handler that reacts, and a scheduled job. Read this one first. |
| [`wallet_api/`](wallet_api/) | FastAPI + PostgreSQL | the deployment shape - REST endpoints, Alembic migrations from files, three separate connection pools, and a keyed lock around the endpoint that needs one |

```
uv run python -m examples.wallet_service     # runs now, needs nothing
```

## The specialized ones

Each shows one thing, rather than repeating the whole application.

| | Shows |
|---|---|
| [`wallet_sharded/`](wallet_sharded/) | two databases and routing packed into the id - no lookup table, no tenant column, and a cross-shard reach refused rather than half-committed |
| [`wallet_saga/`](wallet_saga/) | a transfer as three single-aggregate steps chained by events, with forward-only compensation and a replay that credits nothing twice |
| [`job_worker/`](job_worker/) | a worker process with no HTTP at all: two jobs, once per slot across a cluster, and shutdown done properly |

## Why there are five and not thirty-six

Java's `ekbatan-examples` holds 41 projects, 36 of which are one grid: three DI frameworks x two
build tools x three databases, each JVM and native. That matrix is a *Java packaging* concern -
there, changing the database means new driver dependencies and a fresh jOOQ codegen, and
changing the build tool means a different project layout.

Here the database is a url and the build tool is a line in `pyproject.toml`. So the grid
collapses to one REST example, and what is left maps one-for-one onto Java's *specialized*
examples - the ones that demonstrate framework behaviour rather than packaging. Native-image has
no counterpart and is not represented.

## Naming

The same conventions as the Java side, so a reader moving between the two recognises what they
are looking at: `...Action` for a unit of work, `...Event` for something that happened,
`...EventHandler` for what reacts to one, `...Job` for scheduled work, `...Repository` for reads.

## Running them

```
uv sync --group examples
uv run pytest examples          # all five; wallet_api and wallet_sharded need Docker
```

An example nobody runs is an example that rots - and one did, silently, for several commits. So
each carries its own test asserting what its README claims, and CI runs them all.
