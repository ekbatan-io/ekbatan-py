# Examples

Standalone applications. Each one is a project of its own - its own `pyproject.toml`, its own
`Makefile`, its own environment - imports only `ekbatan`, the standard library and what its
`pyproject.toml` declares, and is imported by nothing. `cd` into one and `make` builds it and
tests it as its installed wheel, as `cd` into a Java example and `./gradlew build` does.

The framework's own tests have their own domain, in `tests/fixtures/`, which exists to exercise
every shape worth exercising - including a few no application would want. If the tests read from
here instead, every change made for coverage would land in the documentation and every change made
for clarity would break the suite. So they do not meet, and `tests/test_structure.py` keeps it
that way.

## Eleven examples, each built three ways

Java carries each of its examples per build tool - `*-gradle-*` and `*-maven-*`. Python projects
are built three common ways, so each example here is carried three times, with only its build
files telling the copies apart:

| build | in the name | `pyproject.toml` builds with | environment |
|---|---|---|---|
| uv | `-uv-` | `uv_build` | uv's |
| pip | `-pip-` | setuptools | a venv the Makefile makes |
| Poetry | `-poetry-` | poetry-core | Poetry's |

The build tool is not a detail: it decides what goes into the wheel. Poetry leaves a gitignored
`schema.py` out unless told, and setuptools leaves out the migrations and `ekbatan.yaml` - each
project carries the line its tool needs, and each tests its wheel installed, so a file left out
fails here rather than on a server.

**The uv project is the source.** Change its `src/` or README, then run
`uv run python scripts/sync-examples.py`: it writes the pip and Poetry twins - the same `src/`,
byte for byte - and every project's build files. `make lint` runs it with `--check`.

## The REST service, one per database

| | uv | pip | Poetry |
|---|---|---|---|
| PostgreSQL | [`fastapi-wallet-rest-uv-pg`](fastapi-wallet-rest-uv-pg/) | [`fastapi-wallet-rest-pip-pg`](fastapi-wallet-rest-pip-pg/) | [`fastapi-wallet-rest-poetry-pg`](fastapi-wallet-rest-poetry-pg/) |
| MySQL | [`fastapi-wallet-rest-uv-mysql`](fastapi-wallet-rest-uv-mysql/) | [`fastapi-wallet-rest-pip-mysql`](fastapi-wallet-rest-pip-mysql/) | [`fastapi-wallet-rest-poetry-mysql`](fastapi-wallet-rest-poetry-mysql/) |
| MariaDB | [`fastapi-wallet-rest-uv-mariadb`](fastapi-wallet-rest-uv-mariadb/) | [`fastapi-wallet-rest-pip-mariadb`](fastapi-wallet-rest-pip-mariadb/) | [`fastapi-wallet-rest-poetry-mariadb`](fastapi-wallet-rest-poetry-mariadb/) |

The counterpart to Java's `*-wallet-rest-*` grid: REST endpoints, SQL migrations from files,
three separate connection pools, and a keyed lock around the deposit endpoint. The domain is
byte-identical across the databases; **four things differ** and each is a real difference rather
than a setting:

- the **url scheme** and driver extra
- the **keyed lock provider** - `pg_advisory_lock` on PostgreSQL, `GET_LOCK` on the other two
- the **migration DDL** - native `UUID`/`JSONB` against `CHAR(36)`/`JSON`
- the **test container**

And one that only shows up when a migration fails half-way: MySQL and MariaDB commit each schema
change on its own, so a migration that fails after one leaves it behind, where PostgreSQL rolls
the whole file back.

## The REST service on native asyncio

| | uv | pip | Poetry |
|---|---|---|---|
| PostgreSQL | [`fastapi-wallet-rest-uv-async-pg`](fastapi-wallet-rest-uv-async-pg/) | [`fastapi-wallet-rest-pip-async-pg`](fastapi-wallet-rest-pip-async-pg/) | [`fastapi-wallet-rest-poetry-async-pg`](fastapi-wallet-rest-poetry-async-pg/) |
| MySQL | [`fastapi-wallet-rest-uv-async-mysql`](fastapi-wallet-rest-uv-async-mysql/) | [`fastapi-wallet-rest-pip-async-mysql`](fastapi-wallet-rest-pip-async-mysql/) | [`fastapi-wallet-rest-poetry-async-mysql`](fastapi-wallet-rest-poetry-async-mysql/) |
| MariaDB | [`fastapi-wallet-rest-uv-async-mariadb`](fastapi-wallet-rest-uv-async-mariadb/) | [`fastapi-wallet-rest-pip-async-mariadb`](fastapi-wallet-rest-pip-async-mariadb/) | [`fastapi-wallet-rest-poetry-async-mariadb`](fastapi-wallet-rest-poetry-async-mariadb/) |

The same service with its request path on `ekbatan.aio`: `async def` endpoints that await an
`AsyncActionExecutor` and async repositories, on asyncpg or aiomysql, and the deposit under an
async keyed lock. The event handler is `async def`: it runs after the deposit, on dispatch's
handler loop, awaiting an executor of its own. The jobs that poll stay on threads, through a sync
registry built from the same `ekbatan.yaml` - whose urls name the sync driver; the async side
connects through the async default for the same database. Each session runs in UTC by the documented setting, which asyncpg receives as its
own `server_settings`. Java has no counterpart: its virtual threads make a blocking API cheap.

## Outside the database matrix

These exist once per build tool, on PostgreSQL, as their Java counterparts exist once per
database - a second copy per database would demonstrate nothing the REST service does not.

| | Shows |
|---|---|
| `fastapi-wallet-rest-{uv,pip,poetry}-sharded-pg` | the REST service over **two** databases: routing packed into the id, and a cross-shard transfer answered **422** rather than half-committed |
| `fastapi-wallet-saga-{uv,pip,poetry}-pg` | a transfer as three single-aggregate steps chained by events, with forward-only compensation - and `POST /transfers` answering **202**, because only step one has committed |
| `job-worker-{uv,pip,poetry}-pg` | a worker process with **no HTTP at all**: two jobs, once per slot across a cluster, and shutdown done properly |

## Start here instead

`wallet-service-{uv,pip,poetry}-sqlite`: one wallet end to end on SQLite, with no server and
nothing to install. No Java counterpart - there, the smallest example still needs Docker.

```
make -C ekbatan-py-examples/wallet-service-uv-sqlite run     # runs now, needs nothing
```

And its async twin, `wallet-service-{uv,pip,poetry}-async-sqlite`: the same wallet with every unit
of work awaited on `ekbatan.aio`, through aiosqlite. Its README lists what changes and what
stays the same.

```
make -C ekbatan-py-examples/wallet-service-uv-async-sqlite run
```

## The schema is generated, not written

Every project here except the SQLite ones generates its `schema.py` from the database its **own
migrations** produce - a throwaway container, the migrations run into it, the result reflected.
That is Java's build step, and the reason is the same: the schema is the database's, not a
hand-transcription that can drift from it. `make` regenerates it when a migration changed and
not otherwise.

The generated modules are **gitignored**, as Java's `build/generated-jooq` is - and named in the
Poetry projects' `pyproject.toml`, which would otherwise leave them out of the wheel.

Two things the generator does that a plain reflection would not, both of which were bugs before
they were features:

- **It forces the types.** A `UUID` reflected from PostgreSQL becomes `CHAR(32)` on MySQL - hex
  with the hyphens stripped, which no Java service can join against. Reflected from MySQL it is
  `CHAR(36)`, indistinguishable from any other char column, so it is forced by name instead -
  which is what Java's `withIncludeExpression` does.
- **It normalises dialect quirks.** MariaDB reflects a bigint as `BIGINT(display_width=20)`;
  emitted verbatim that module does not even import.

Each generating example also has **two forced types of its own**, so a reader sees a converter
end to end: plain text in the table, a small class of the app's own in Python. One rule picks
columns by their type, the other one column by its name:

| example | picked by type | picked by name |
|---|---|---|
| REST (all four), job worker | every `CHAR(3)` column becomes a `Currency` | `alerts.recipient` becomes an `EmailAddress` |
| saga | every `CHAR(3)` column becomes a `Currency` | `transfer_steps.step` becomes the `StepName` enum |

Each example's README shows the rules and the `schema.py` lines they produce. The rules are the
same in all three builds of an example, so `scripts/sync-examples.py` writes them into every
`pyproject.toml` (`WALLET_FORCED`, `SAGA_FORCED`): change them there, not in a project.

The SQLite examples keep a hand-written schema on purpose: they have no migrations. Their tables
are built from that one file with `create_all`, so the whole example reads in one place and runs
with nothing installed. schemagen can read SQLite without Docker (`image = "sqlite"`), but here
there is nothing for it to read.

## Naming

Java's own: `[stack]-[domain]-[style]-[build][-variant]-[db]` - `fastapi-wallet-rest-uv-pg` as
`spring-boot-wallet-rest-gradle-pg`, `fastapi-wallet-rest-poetry-sharded-pg` as
`spring-boot-wallet-rest-gradle-sharded-pg`. Every directory ends in the database it runs on, and
each one's test asserts it really is running on that database. The package inside keeps its
Python name - `src/fastapi_wallet_rest_pg/` - in all three builds, so the code is the same file.

In the code, the same conventions as the Java side: `...Action` for a unit of work, `...Event`
for something that happened, `...EventHandler` for what reacts to one, `...Job` for scheduled
work, `...Repository` for reads.

## Why 33 and not Java's 36

Java's grid is three DI frameworks x two build tools x three databases, each JVM and native.
Here there is one web framework worth targeting and no native image, so those axes collapse. The
**database** axis does not, for the four reasons above, and neither does the **build** axis: it
decides what a wheel holds, which is the one thing an example cannot show from the source tree.

## Running them

```
make examples                    # all 33, each in its own environment, each as its wheel
make -C ekbatan-py-examples/fastapi-wallet-rest-pip-mysql      # or one
```

Everything except the SQLite examples needs Docker. The pip projects need a Python 3.13 or later
(`make PYTHON=python3.13`; the root Makefile finds one through uv), and the Poetry ones need
`poetry` 2 on the path.

`ekbatan` is not on PyPI yet, so each project takes it - and its scheduler - from this repository:
`[tool.uv.sources]`, a Poetry path dependency, or an editable install in the pip Makefile. One
marked block each, to delete once it is published.

An example nobody runs is an example that rots - and one did, silently, for several commits. So
each carries its own test asserting what its README claims, and CI runs them all.
