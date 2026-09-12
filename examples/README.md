# Examples

Standalone applications. Each one runs on its own, imports only `ekbatan`, the standard library
and what the `examples` dependency group declares, and is imported by nothing.

That last part is deliberate. The framework's own tests have their own domain, in
`tests/fixtures/`, which exists to exercise every shape worth exercising - including a few no
application would want. If the tests read from here instead, every change made for coverage
would land in the documentation and every change made for clarity would break the suite. So
they do not meet, and `tests/test_structure.py` keeps it that way.

Every directory carries its database in its name, as Java's do.

## The matrix: one REST service per database

The same application three times, differing only by database - the counterpart to Java's
`*-wallet-rest-*` grid.

| | Database |
|---|---|
| [`fastapi_wallet_rest_pg/`](fastapi_wallet_rest_pg/) | PostgreSQL |
| [`fastapi_wallet_rest_mysql/`](fastapi_wallet_rest_mysql/) | MySQL |
| [`fastapi_wallet_rest_mariadb/`](fastapi_wallet_rest_mariadb/) | MariaDB |

REST endpoints, Alembic migrations from files, three separate connection pools, and a keyed lock
around the deposit endpoint. The domain is byte-identical across all three; **four things
differ** and each is a real difference rather than a setting:

- the **url scheme** and driver extra
- the **keyed lock provider** - `pg_advisory_lock` on PostgreSQL, `GET_LOCK` on the other two
- the **migration DDL** - native `UUID`/`JSONB` against `CHAR(36)`/`JSON`
- the **test container**

And one that only shows up when you run it: PyMySQL executes a single statement per call and
rejects a script, where psycopg accepts one. A migration written as one block passes on
PostgreSQL and fails on the other two.

## Outside the matrix

These exist once each, on PostgreSQL, exactly as their Java counterparts do. A second copy per
database would demonstrate nothing the matrix above does not already.

| | Shows |
|---|---|
| [`wallet_saga_pg/`](wallet_saga_pg/) | a transfer as three single-aggregate steps chained by events, with forward-only compensation and a replay that credits nothing twice |
| [`wallet_sharded_pg/`](wallet_sharded_pg/) | two databases and routing packed into the id - no lookup table, no tenant column, and a cross-shard reach refused rather than half-committed |
| [`job_worker_pg/`](job_worker_pg/) | a worker process with no HTTP at all: two jobs, once per slot across a cluster, and shutdown done properly |

## Start here instead

| | |
|---|---|
| [`wallet_service_sqlite/`](wallet_service_sqlite/) | one wallet end to end on SQLite, in one command, with no server and nothing to install. No Java counterpart - there, the smallest example still needs Docker. |

```
uv run python -m examples.wallet_service_sqlite     # runs now, needs nothing
```

## Why three REST projects and not thirty-six

Java's grid is three DI frameworks x two build tools x three databases, each JVM and native.
Two of those axes are Java packaging concerns: changing the build tool means a different project
layout, and changing the database means new driver dependencies and a fresh jOOQ codegen.

Here the build tool is a line in `pyproject.toml` and there is one web framework worth
targeting, so those axes collapse. The **database** axis does not, for the four reasons above -
which is why it is the one that survives.

## Naming

The same conventions as the Java side, so a reader moving between the two recognises what they
are looking at: `...Action` for a unit of work, `...Event` for something that happened,
`...EventHandler` for what reacts to one, `...Job` for scheduled work, `...Repository` for reads.

## Running them

```
uv sync --all-extras --group examples
uv run pytest examples          # everything except wallet_service_sqlite needs Docker
```

An example nobody runs is an example that rots - and one did, silently, for several commits. So
each carries its own test asserting what its README claims, and CI runs them all.
