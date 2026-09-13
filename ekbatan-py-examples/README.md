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

These exist once each, on PostgreSQL, exactly as their Java counterparts do - a second copy per
database would demonstrate nothing the matrix above does not already. The first two are the same
REST service with something added, which is why they keep the `fastapi_wallet_` prefix; Java
names its equivalents `...-wallet-rest-...-sharded-pg` and `...-wallet-saga-...-pg` for the same
reason.

| | Shows |
|---|---|
| [`fastapi_wallet_rest_sharded_pg/`](fastapi_wallet_rest_sharded_pg/) | the REST service over **two** databases: routing packed into the id, and a cross-shard transfer answered **422** rather than half-committed |
| [`fastapi_wallet_saga_pg/`](fastapi_wallet_saga_pg/) | a transfer as three single-aggregate steps chained by events, with forward-only compensation - and `POST /transfers` answering **202**, because only step one has committed |
| [`job_worker_pg/`](job_worker_pg/) | a worker process with **no HTTP at all**: two jobs, once per slot across a cluster, and shutdown done properly. No web framework, so no prefix |

## Start here instead

| | |
|---|---|
| [`wallet_service_sqlite/`](wallet_service_sqlite/) | one wallet end to end on SQLite, in one command, with no server and nothing to install. No Java counterpart - there, the smallest example still needs Docker. |

```
cd ekbatan-py-examples && uv run python -m wallet_service_sqlite     # runs now, needs nothing
```

## The schema is generated, not written

Every project here except `wallet_service_sqlite` generates its `schema.py` from the database
its **own migrations** produce - a throwaway container, the migrations run into it, the result
reflected. That is Java's build step, and the reason is the same: the schema is the database's,
not a hand-transcription that can drift from it.

Each example carries its **own** Makefile, as each Java example carries its own wrapper - so you
can work in one without the rest:

```
cd ekbatan-py-examples/fastapi_wallet_saga_pg
make            # generate if a migration changed, then test
make run        # ...then serve
```

The root Makefile delegates to them rather than repeating the rules:

```
make schema     # every example that generates
make            # generate what changed, then lint and test
```

The generated modules are **gitignored**, as Java's `build/generated-jooq` is. `make` builds a
missing one, so a fresh clone works the same way `./gradlew build` does.

Two things the generator does that a plain reflection would not, both of which were bugs before
they were features:

- **It forces the types.** A `UUID` reflected from PostgreSQL becomes `CHAR(32)` on MySQL - hex
  with the hyphens stripped, which no Java service can join against. Reflected from MySQL it is
  `CHAR(36)`, indistinguishable from any other char column, so it is forced by name instead -
  which is what Java's `withIncludeExpression` does.
- **It normalises dialect quirks.** MariaDB reflects a bigint as `BIGINT(display_width=20)`;
  emitted verbatim that module does not even import.

`wallet_service_sqlite` keeps a hand-written schema on purpose: its whole job is running with
nothing installed, and generating needs Docker.

## Naming

`[stack]_[domain]_[style][_variant]_[db]`, which is Java's
`[stack]-[domain]-[style]-[build][-variant]-[db]` minus the build tool - there is one here.
Every directory ends in the database it runs on, and each one's test asserts it really is
running on that database, because three projects differing only by url makes a wrong one
invisible.

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
uv run pytest ekbatan-py-examples          # everything except wallet_service_sqlite needs Docker
```

An example nobody runs is an example that rots - and one did, silently, for several commits. So
each carries its own test asserting what its README claims, and CI runs them all.
