# Agents Guide

## Project Overview

Ekbatan is a **persistence and action framework** for event-driven systems. It is a library, not
an application. Its central idea: in most business software you do not merely want to save data,
you want to save *what happened* alongside it, atomically, so the database tells a complete and
consistent story.

Everything is built around an **Action** - one unit of business work. An action writes nothing
directly. It declares intent: what to create, what to update. Those declarations accumulate in an
**ActionPlan**, a staging area that touches no database. Only when the action's own logic has
finished does the **ActionExecutor** open a single transaction and flush everything at once - the
rows to their tables, the events to the outbox. If any part fails, nothing is written.

That all-or-nothing guarantee is what turns the `events` table into a reliable **outbox**: a
stream of committed business facts a CDC connector can tail, without the dual-write problem.

Two kinds of persistent object, and the difference matters:

- A **Model** is an aggregate whose changes are news. Every mutation produces a `ModelEvent`
  persisted alongside the action that caused it.
- An **Entity** is persisted and version-tracked, but its changes are not events. Lookup tables,
  auxiliary records, anything whose individual history nobody downstream cares about.

Both are **immutable** - frozen dataclasses. A deposit does not mutate a wallet; it returns a new
one with the new balance and an event attached. That is not decoration: it is what makes
two-phase execution safe. During `perform` an action reads and constructs freely, and the plan
collects references to immutable snapshots, so a partial write is not expressible.

**Optimistic locking** is in every persistable object through a mandatory `version`. The
framework never takes row locks. Every update carries `WHERE version = ?`; if another transaction
got there first the update matches zero rows and `StaleRecordError` unwinds the whole thing.

The repository layer sits on **SQLAlchemy Core**. No ORM, no session cache, no lazy loading.
Mapping is by field name, so a repository usually declares nothing about columns at all.

## This is a port

`ekbatan-py` is a port of [`ekbatan`](https://github.com/ekbatan-io/ekbatan), a Java framework.
The two are expected to interoperate: a Java service and a Python service can read each other's
outbox rows, contend for the same keyed lock, and share one `scheduled_tasks` table. Several
things that look like arbitrary choices are cross-language contracts, and the code says so where
that is true.

Four documents carry that relationship, and **an agent working here should read the relevant one
before changing behaviour**:

| file | what it holds |
| --- | --- |
| `DIVERGENCE.md` | every place this port differs from the Java one, and why. Mostly neutral |
| `docs/SHORTCOMINGS.md` | the biased subset: where this port is *behind* |
| `docs/DECISIONS-TO-REVIEW.md` | decisions taken during the port that deserve arguing about, and two that are still open |
| `docs/JAVA-PARITY-LEDGER.md` | every Java source file and where it went. 246/247 settled |
| `docs/BEYOND-CODE-LEDGER.md` | the same discipline for docs, website and release machinery |

**The method those ledgers embody is the one to use for any exhaustive work here.** Enumerate the
files, read each whole, mark it, and let a script count. It is how the port found that three rows
had been marked done from belief rather than from reading - and one was hiding a real gap.

## Module Structure

```
src/ekbatan/
  domain.py          Model, Entity, ModelEvent, the builders
  ids.py             Id, ShardedId, TypedValue
  sharding.py        ShardIdentifier, the strategies, the bit layout
  repository.py      Repository, ModelRepository, EntityRepository, RepositoryRegistry
  persistence.py     DatabaseRegistry, TransactionManager, Transaction
  fetching.py        Db - the connection every accessor hands back - and FetchResult
  config.py          DataSourceConfig, ShardConfig, ShardingConfig
  schema.py          the framework's own tables, and the column helpers
  errors.py          the exception hierarchy
  clock.py           Clock, SystemClock, VirtualClock
  migrations.py      SQL migrations on yoyo: files, checks, history, one call per layout
  _yoyo.py           the yoyo backends: one connection, the session lock, the replica check
  schemagen/         table definitions generated from a migrated database: config, type table
  codegen.py         typed builders, generated per model
  streaming.py       ActionEvent - the consumer-side view of an outbox row
  accounting.py      statement counting, so the performance claims are checkable
  action/            action.py, plan.py, executor.py, registry.py, persister.py, outbox.py
  aio/               native asyncio: persistence, repository, action, executor, persister,
                     outbox, testing, locks/ - the async twins, on the sync modules' private cores
  events/            handler.py, fanout.py, dispatch.py, jobs.py, notification.py, config.py,
                     loop.py (the handler loop async handlers run on)
  locks/             base.py, sql.py, redis.py, _holder.py, _deadlines.py
  distributedjobs/   DistributedJob, JobRegistry, JobsConfig
db-scheduler/        a separate distribution: the port of kagkarlsson/db-scheduler
ekbatan-py-examples/ fourteen examples x uv/pip/Poetry: 42 standalone projects
```

`db-scheduler/` is **its own distribution** with its own lock file, tests, CI workflow and ruff
config. The root Makefile's `lint` target deliberately does not reach into it.

## Conventions

### Naming - the same as Java's

A reader moving between the two ports should recognise what they are looking at.

| | |
| --- | --- |
| Actions | `OpenWalletAction`, `DepositMoneyAction`, `InitiateTransferAction` |
| Events | `WalletOpenedEvent`, `MoneyDepositedEvent`, `TransferFailedEvent` |
| Handlers | `MoneyDepositedEventHandler`, `TransferInitiatedEventHandler` |
| Jobs | `PayStipendJob`, `WalletReportJob` |
| Repositories | `WalletRepository` |
| Python | `snake_case` functions and fields, `PascalCase` classes |
| SQL | `snake_case` |
| Commits | one `EKB-XXXX` subject line, no body |

### Immutability

Models and entities are `@dataclass(frozen=True, kw_only=True)`. `copy()` returns a builder;
`evolve()` is the framework's own shorthand and domain code should prefer `copy()` so every
change in an application reads the same way.

### Time: the framework never converts it

**Hard rule.** The persistence layer writes and reads exactly the time it is given. No code in
`src/` adds, removes or moves a zone, or shifts a value, on its way to or from the database, or
into or out of a model, row, event, notification or JSON. A value goes to the driver as the
model or the clock held it and comes back as the driver returns it.

- The default clock, `SystemClock().now()`, gives the current **UTC time with no zone attached**
  (`datetime.now(UTC).replace(tzinfo=None)`, in `clock.py`'s `_utc_now()` - the one allowed read
  of the wall clock). `VirtualClock` keeps any instant it is given as given.
- `timestamp_column()` is only a column definition: zone-less, microsecond precision
  (`DATETIME(6)` on MySQL and MariaDB, whose bare `DATETIME` drops the fraction). It converts
  nothing.
- **Never add `astimezone`, `replace(tzinfo=...)`, `fromtimestamp(..., tz=...)` or any other
  zone normalisation** to the library. `tests/unit/test_time_as_is.py` fails on it; its
  allow-list is not to be grown without the user's OK. Changing this rule needs the user's OK.
- Getting time right is the **application's job**. A clock that gives aware values, or a zoned
  column, is the application's choice, and it must keep the two consistent: the framework
  compares what the clock gives with what the database returns (due times, retries, expiry),
  so a naive column and an aware clock raise `TypeError` there.

**UTC everywhere is the strong recommendation** - now plainly the user's job, so code, docs and
examples follow it and say so:

- in Python, time comes from the clock (`clock.now()`, `self.now()` in an action) - UTC, no zone;
  never `datetime.now()` (local time);
- columns are zone-less: `TIMESTAMP` on PostgreSQL, `DATETIME(6)` on MySQL and MariaDB; never
  `TIMESTAMPTZ`, never bare `DATETIME`;
- the database runs in UTC, or each session is put on it through `data_source_properties`
  (`options: -c TimeZone=UTC`, `init_command: SET time_zone = '+00:00'`).

The user's guide is `docs/database/multi-database.md#always-utc`; "What you give is what is
stored" is its section on the rule.

### The ambient transaction lives in a `ContextVar`

Java binds it to a `ScopedValue`. A `ContextVar` is per-thread *and* per-async-task, so the same
mechanism works under a threaded server and an asyncio one. Work handed to a pool from inside a
unit of work does **not** inherit it - which is correct, and is pinned by
`tests/integration/test_concurrency.py`.

### Two modes: sync and async are both first-class

Every reader picks a mode - the sync API or `ekbatan.aio` - and every part of this repository
serves both. The rules:

- **Every new public feature works in both modes**, or the docs say why not (as they do for
  jobs, migrations and `schemagen`, which are sync in both - a job's *work* may still be awaited,
  handed to a `HandlerLoop` with `loop.run(...)` - and for SQLite, which has no keyed lock
  provider in either).
- **Logic lives in the shared private cores, never copied.** The async class adds only the lines
  that await (see the next section).
- **Each test scenario runs in both modes**: a test module sets `MODES = ("sync", "async")` and
  `tests/modes.py` runs it again against `ekbatan.aio`. A scenario that cannot run through the
  harness gets a native coroutine test in `tests/integration/test_async.py`.
- **Every example has an async twin**, with the same tests plus a check that it really runs
  async (its drivers, its loop): `fastapi-wallet-rest-*-async-{pg,mysql,mariadb}`,
  `wallet-service-*-async-sqlite`, `fastapi-wallet-rest-*-async-sharded-pg`,
  `fastapi-wallet-saga-*-async-pg` and `job-worker-*-async-pg` - seven twins of seven, 42
  projects in all. A change to a sync example's behaviour goes into its twin, or the twin's
  README says why not. **A new example gets an async twin** in the same change, built with uv,
  pip and Poetry like the rest.
- **Each code sample on the website and in `docs/` shows both modes where they differ.** On the
  website, wrap it in `<ModeTabs>` (`website/src/components/ModeTabs.astro`, slots `sync` and
  `async`); the choice is shared across pages with the setup guide's picker. In the `docs/` copy,
  put the two blocks one after the other under `**Sync**` and `**Async**` lines. A sample that is
  the same in both modes stays single.
- **The setup guide covers both modes.** `complete-project-setup.mdx` shows the async example's
  real files when the picker says Async (`data-mode`; any picker attribute may list several
  values, space-separated), `tests/unit/test_setup_guide.py` fails when an example has a file the
  page does not show, and `scripts/check-setup-guide.py` builds every database x installer x
  mode - 24 runs - in an empty folder. When the guide and an example disagree, fix the guide or
  the example, not the checker.
- How to choose, for readers, is `docs/sync-or-async.md` (website `learn/sync-or-async`); the
  async-only details are `docs/runtime/async.md`.

### Async lives in `ekbatan.aio`, on the sync code's private cores

There are two APIs: the sync one and `ekbatan.aio`. The rules for keeping them one framework:

- **One copy of every rule.** Statements, routing, the optimistic lock, soft delete, retries,
  the outbox rows and the builders live in private cores (`_RepositoryCore`, `_ShardRegistry`,
  `_ShardRegistryBuilder`, `_ExecutorCore`, `_ExecutorBuilder`, `_ChangePersisterCore`,
  `_SingleTableJson`, `_ActionCore`) that both sides extend. An async class adds only the lines
  that await. A rule changed in a sync class that is not in its core is a rule the async side
  silently lacks - move it into the core.
- **Same public surface.** A public method added to a sync class needs its async twin;
  `tests/unit/test_async_engine.py` compares the two surfaces and fails otherwise. Names carry
  the `Async` prefix; `to_row` / `from_row` stay plain `def` on both.
- **What stays sync, on threads:** event fan-out and dispatch, distributed jobs, migrations,
  `schemagen`. An async service builds a sync registry beside its async one, from the same layout
  file, for those.
- **Keyed locks** have async twins in `ekbatan.aio.locks` (PG, MySQL, MariaDB, Redis on
  `redis.asyncio`), on the sync providers' cores (`_HoldingCore`, `_HolderCore`, `_LeaseCore`,
  the statement and result helpers in `locks/sql.py` and `locks/redis.py`). The caller takes the
  lock around `await executor.execute(...)`; the holder is the task.
- **Event handlers run after the action**, on dispatch's threads, never inside it. A handler may
  be `async def`: dispatch runs it on its `HandlerLoop` (one loop on its own thread) and records
  the outcome exactly as for a sync handler. What an async handler awaits belongs to that loop.
- **Drivers.** A url naming a sync driver, or none, connects through the database's async
  default (`async_url`, `ASYNC_DEFAULT_DRIVERS`: asyncpg, aiomysql, aiosqlite); an async driver
  in the url is kept. For asyncpg, libpq's `options` / `application_name` / `connect_timeout` /
  `sslmode` are translated, nothing else. A new driver means rows in `tests/driver_settings.tsv`.
- **The pool is the cap.** No semaphore of the framework's own; the async pool takes the same
  `DataSourceConfig` settings as a sync one.
- **Reads across shards run at once** (`TaskGroup`): all or nothing, the failing shard's own
  error with a note naming it, never a group, never partial rows. `dbs()` / `readonly_dbs()`
  stay sequential.
- **Tests run both ways.** A test module sets `MODES = ("sync", "async")` and `tests/modes.py`
  runs each test a second time against `ekbatan.aio` through a blocking portal, every call a
  thread makes in one task of its own; a test's own actions get async twins with `twin`,
  `stager_twin` or `rebased`. A module with `HANDLER_KINDS = ("sync", "async")` runs its event
  tests with sync handlers and again with async ones (`pipeline`, `fixtures.handlers.of_kind`). What the harness cannot say -
  tasks, cancellation, timeouts, the pool cap, concurrent shard reads - is in
  `tests/integration/test_async.py`, written as coroutines. Any change to the sync suite must
  leave its sync counts unchanged.
- **gevent and `ekbatan.aio` do not mix**; the async runs skip under `make test-gevent`.

### Nothing in `src/` may import a web framework

`tests/test_structure.py` enforces it, along with the rule that an example imports only the
framework, the standard library and what its own `pyproject.toml` declares. The pip and Poetry
projects are written from the uv ones by `scripts/sync-examples.py`; edit only the uv ones.

## Testing

```bash
make test        # unit, then the whole suite on SQLite
make test-all    # PostgreSQL, MySQL and MariaDB too - needs Docker
make test-psycopg-async   # the PostgreSQL suite, its async runs on psycopg's async mode
make examples    # every example, each as its built wheel installed
uv run python scripts/check-setup-guide.py   # the setup guide, followed as written: 24 runs
```

- `tests/unit/` is **database-free by rule**, not by luck, and a test enforces it.
- `tests/integration/` is parametrised over dialects with `--dialects={sqlite,postgres,mysql,mariadb,all}`,
  and a module with `MODES` over the sync and async APIs as well (`[sync-postgres]`,
  `[async-postgres]`).
- `tests/fixtures/` is the framework's own domain. It is deliberately **not** shared with the
  examples, and `tests/test_structure.py` keeps them apart: coverage changes would otherwise land
  in the documentation, and clarity changes would break the suite.

### Traps this suite has already fallen into

Each of these was a real bug in the tests, found by running:

- **MariaDB announcing itself as MySQL.** `MySqlContainer` hands back a `mysql+pymysql` url even
  when running MariaDB. SQLAlchemy picks DDL, version reporting and `SKIP LOCKED` support off the
  scheme, and this framework picks its lock provider off `engine.dialect.name`. The scheme must
  be rewritten or "the MariaDB suite" tests MySQL.
- **A frozen clock makes outbox rows tie.** `event_date` is the only ordering key and there is no
  ordinal column, so under `VirtualClock` every row of a run ties and order falls back to a random
  uuid. Assert on `action_name` or `action_id`, or use a running clock.
- **`str(engine.url)` masks the password**, so a "separate connection" built from it connects to
  nothing.
- **A module-scoped container shares state between tests.** Truncate in the fixture.
- **asyncpg refuses an aware `datetime` for a zone-less column**, where psycopg accepts one. Use
  naive UTC values in tests (`datetime(2026, 1, 1, 10, 0)`, `VirtualClock(...)` with one), as
  the default clock gives.
- **A patched class attribute must be restored by deleting it** when the class only inherited
  it: `AsyncDb.execute = real` leaves a copy on `AsyncDb` that hides every later patch of
  `AsyncConnection.execute`. `monkeypatch` gets this right.
- **SQLAlchemy's pool `checkout` event fires after the pre-ping**, so counting checkouts does not
  show two fast reads overlapping. Slow the statement down and count what runs at once.

### Prove a test can fail

Reinstate the bug and watch it go red before believing it. Most of the defects this port has
found were found that way, and several "passing" tests turned out to assert nothing.

## Build and tooling

- **uv** for dependencies and running - `uv sync`, `uv run`. There is no task runner, which is
  why there is a Makefile.
- **hatchling** builds the wheel; `uv build` drives it.
- **ruff** for lint and format, **mypy `--strict`** over `src` *and* the examples' uv projects -
  an example is documentation, and documentation that would not type-check is wrong.
- **Make** supplies the one thing uv has no answer for: a dependency graph. It is how generated
  schemas are rebuilt when their migrations change and skipped when they have not.

```bash
make            # lint and the fast tests
make lint       # ruff check, ruff format --check, mypy
make format     # rewrite what ruff can
make schema     # regenerate every generated schema
make build      # wheel and sdist
```

## Code generation

Two generators, mirroring two different Java mechanisms:

| Java | here |
| --- | --- |
| annotation processor → `WalletBuilder` | `python -m ekbatan.codegen <module> --out builders.py` |
| jOOQ + container + Flyway → record classes | `python -m ekbatan.schemagen`, settings in `[tool.ekbatan.schemagen]` |

`schemagen` starts a throwaway container, runs **your** migrations into it and reflects the
result. Its settings are jOOQ's, in `[tool.ekbatan.schemagen]` of `pyproject.toml` (a flag wins):
schemas, includes, excludes, and **forced types** - by column name (`include-expression`) and SQL
type (`include-types`), to a framework helper or a SQLAlchemy type (`name`), to the user's own
**converter** by dotted path (`converter`: any class with `from_db`/`to_db`, jOOQ's
`userType` + `converter`), or to a library's ready-made SQLAlchemy type (`sqlalchemy-type`, a
convenience only). The user's are tried first, then Ekbatan's built-in four (timestamps, JSON,
PostgreSQL `UUID`), which every Java example's build forces too. Four rules hold it together:

- **Forcing is what makes the schema right.** A `UUID` reflected from PostgreSQL renders as
  `CHAR(32)` on MySQL - hex with the hyphens stripped, which no Java service can join against.
  Reflected *from* MySQL it is `CHAR(36)`, indistinguishable from any other char column, so it
  is **not** forced by default: the project adds the rule (`CHAR\(36\)` + `.*\.id|.*_id`), as
  each Java MySQL build adds `UuidStringConverter` itself. The MySQL/MariaDB examples carry it
  (`UUID_IDS` in `scripts/sync-examples.py`). Do not bring the name guess back as a default.
  To leave a column or a whole table out of it, `exclude-expression` on the rule (plus a rule
  of its own for that table); the schemagen page shows both, proven on MySQL and MariaDB.
- **Every other type is written faithfully** (`schemagen/_types.py`): a generic SQLAlchemy type
  where one says the same, the dialect's own type imported from its dialect module where not,
  and nothing reflection merely decorates (MariaDB's `BIGINT(20)` display width). A type
  SQLAlchemy has no class for (PostgreSQL `xml`, MariaDB `INET6`, pgvector's `vector(3)`) is
  `sql_type("<as the database spells it>")`, never `NullType()`. Keys, indexes, checks and
  defaults come too, so `create_all` builds what the migrations built.
- **A converter keeps the column's type.** It is written `converted(<impl>, Conv())`, where
  `<impl>` is what the column would be without it (the next matching non-converter forced type,
  else the type table). `ekbatan.schema.Converted` passes `None` through, and keys SQLAlchemy's
  statement cache on the converter itself (identity for an unhashable one), never on its class -
  `tests/unit/test_schema_converters.py` shows why. Converter and `sqlalchemy-type` paths are
  imported before any database starts, so a typo fails first.
- **Every type is proven on a real database** - `tests/integration/test_schemagen_types.py`
  generates, imports, `create_all`s and round-trips each one on all four databases. A new type
  rule gets a case there.

Generated schemas are **gitignored**, as Java's `build/generated-jooq` is, and each generated
module fingerprints its migrations and checks that on import - because nothing in Python runs
before `python -m yourapp`.

## Adding a feature

1. **Read the Java first, whole.** Not its tests, not its name - the file. Every defect this port
   found in its own code was found that way.
2. Check `DIVERGENCE.md` before "fixing" something that looks wrong; it may be deliberate.
3. Write the test so it fails for the right reason, then make it pass.
4. Run `make test-all` if the change touches SQL. Dialects disagree, and SQLite agrees with
   everyone.
5. Record any new divergence in `DIVERGENCE.md`, and anything that is a step *backwards* from
   Java in `docs/SHORTCOMINGS.md`.

## Working in the Java repository

`../ekbatan` is the reference implementation. **Do not commit there** - write the files and say
what is ready. Defects found in the Java while porting go in an openspec change, not in a code
change.
