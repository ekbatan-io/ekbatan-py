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
Each repository writes its mapping - `to_record` / `from_record`, Java's `toRecord` /
`fromRecord` - typed by the table's **record**, a `TypedDict` schemagen writes beside the table
(`WalletsRecord` beside `WALLETS`), so mypy checks every mapping against the table.

## This is a port

`ekbatan-py` is a port of [`ekbatan`](https://github.com/ekbatan-io/ekbatan), a Java framework.
The two are expected to interoperate: a Java service and a Python service can read each other's
events through a broker (Debezium, Kafka) and contend for the same Redis keyed lock. They never
share one database - not its tables, not its outbox, not `scheduled_tasks` - so the SQL keyed locks
never cross between them. Several things that look like arbitrary choices are cross-language
contracts, and the code says so where that is true.

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
  time.py            Instant, LocalDateTime, OffsetDateTime - the time-kind labels
  migrations.py      SQL migrations on yoyo: files, checks, history, one call per layout
  _yoyo.py           the yoyo backends: one connection, the session lock, the replica check
  schemagen/         table definitions generated from a migrated database: config, type table,
                     and the typed record beside each table
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

### The mapping is typed by the table's record

Every repository is generic in its model, its table's record and its id, in Java's order:
`ModelRepository[Wallet, WalletsRecord, ShardedId[Wallet]]` (and `EntityRepository`, the
`Abstract` base and the `ekbatan.aio` twins alike). `to_record(model) -> WalletsRecord` and
`from_record(record: WalletsRecord) -> Wallet` are the repository's to write. The framework hands
`from_record` the row read by column name - the row's mapping - on every read path, sync and
async, sharded or not; `FetchResult`'s terminals hand a mapper the same. No `dict[str, Any]`
mapping in examples or docs: a generated schema has its records, a hand-written one writes them
beside its tables. (`to_row` / `from_row` until `EKB-0409`; renamed outright, no aliases.)
`to_record` builds its record by keyword, `return WalletsRecord(id=..., version=..., ...)`, not
as a dict literal (`EKB-0447`). A column whose name is not a valid Python identifier or is a
keyword (`order date`, `class`, a name starting `__`) cannot be a keyword argument, so that record
is built as a dict literal; both produce the same plain dict, and mypy checks both.

### Immutability

Models and entities are `@dataclass(frozen=True, kw_only=True)`. `copy()` returns a builder;
`evolve()` is the framework's own shorthand and domain code should prefer `copy()` so every
change in an application reads the same way.

### Secrets never print

No password in any `repr`, log line, error message or result. Redact at the source - the config
classes' `repr`, the config loader's messages - and prove it with a real password: a test runs
against a database whose real password is a marker (`Mk-7f3a-SECRET` in the examples) and
asserts the marker shows nowhere. The config loader reads each environment variable by name as it
fills a placeholder and never holds the whole environment (`tests/unit/test_config_files.py`
checks the frames of a failed load). An example's `Settings` holds no copy of `os.environ`
(decided by the user, 2026-10-22); its tests set the variables with `monkeypatch.setenv`.

### Time: the framework never converts it

**Hard rule.** The persistence layer writes and reads exactly the time it is given. No code in
`src/` adds, removes or moves a zone, or shifts a value, on its way to or from the database, or
into or out of a model, row, event, notification or JSON. A value goes to the driver as the
model or the clock held it and comes back as the driver returns it.

- A clock has **one reading, `clock.instant()`** (`self.clock.instant()` in an action), Java's
  `clock.instant()`. `now()` is gone - renamed outright, no alias; db-scheduler's own clock keeps
  db-scheduler's `now()`. The default clock, `SystemClock().instant()`, gives the current **UTC
  time with no zone attached** (`datetime.now(UTC).replace(tzinfo=None)`, in `clock.py`'s
  `_utc_now()` - the one allowed read of the wall clock). `VirtualClock` keeps any instant it is
  given as given.
- `timestamp_column()` is only a column definition: zone-less, microsecond precision
  (`DATETIME(6)` on MySQL and MariaDB, whose bare `DATETIME` drops the fraction). It converts
  nothing.
- **Never add `astimezone`, `replace(tzinfo=...)`, `fromtimestamp(..., tz=...)` or any other
  zone normalisation** to the library. `tests/unit/test_time_as_is.py` fails on it; its
  allow-list is not to be grown without the user's OK. Changing this rule needs the user's OK.
- The same rule holds in `db-scheduler/`: its repository binds and returns times as given, its
  `SystemClock` gives naive UTC, and its persistence path is guarded by
  `db-scheduler/tests/unit/test_time_kept_as_given.py`. Only its schedules (`Daily`, cron) do
  zone arithmetic, reading a naive time as UTC and answering in the kind they were asked in. A
  `timestamptz` `scheduled_tasks` table needs an aware clock (`customize_scheduler`).
- Getting time right is the **application's job**. A clock that gives aware values, or a zoned
  column, is the application's choice, and it must keep the two consistent: the framework
  compares what the clock gives with what the database returns (due times, retries, expiry),
  so a naive column and an aware clock raise `TypeError` there.

### Time kinds: `ekbatan.time`

Python has one `datetime` for Java's three, so three `typing.NewType` labels say which a value
is - no run-time cost, a value stays a plain `datetime`, and `Instant(x)` is `x`:

| label | holds | Java | column | JSON |
| --- | --- | --- | --- | --- |
| `Instant` | a moment: UTC digits, no zone attached | `Instant` | zone-less `TIMESTAMP` / `DATETIME(6)` | `"...00.120Z"` |
| `LocalDateTime` | a wall date and time, no zone attached | `LocalDateTime` | the same zone-less column | `"...00.12"` |
| `OffsetDateTime` | a moment with its offset, zone attached | `OffsetDateTime` | PostgreSQL `TIMESTAMPTZ` | `"...00.12+03:30"` |

`time` and `date` stay Python's own. No other labels.

- `clock.instant()` is an `Instant`; so are a model's `created_date` / `updated_date` and every
  time the framework writes (outbox, notifications, dispatch and retry, executor, jobs). mypy
  keeps the kinds apart (`tests/unit/test_time_kinds_mypy.py`): an `Instant` where a
  `LocalDateTime` is expected, a plain `datetime` where an `Instant` is, are errors; arithmetic
  keeps the label.
- **Persistence stays as is**: no conversion, no run-time check in columns; `timestamp_column()`
  stays a column definition. `Instant` and `LocalDateTime` columns are the same SQL type.
- **JSON (event payloads and action params) is written by the declared label** (`_json.py`,
  field hints read with `typing.get_type_hints`, cached, through nested dataclasses, lists,
  tuples, dicts, `X | None`, `TypedValue[X]`, `TypedDict`): `Instant` with `Z` exactly as Jackson
  writes an `Instant`, `LocalDateTime` with no zone, `OffsetDateTime` with its offset. A plain
  `datetime` field, a `datetime` nothing declares (`dict[str, Any]`), or a value of the wrong sort
  (aware in an `Instant`, naive in an `OffsetDateTime`) is refused - inside `executor.execute`,
  so the action fails and nothing is saved. `payload()` is a plain dict, so the outbox writes an
  event through `canonical_event(event)`, which matches keys back to the class's fields. Reading
  back: `"...Z"` into an `Instant` gives naive UTC digits (other text is refused - no conversion);
  a round trip is equal.
- **schemagen** types each record's time columns by kind: zone-less PostgreSQL `TIMESTAMP`,
  MySQL/MariaDB `DATETIME` and `TIMESTAMP`, SQLite `DATETIME` -> `Instant` (built-in forced type
  `name = "Instant"`); a column the project forces to `name = "LocalDateTime"` (its rules are
  tried first) -> `LocalDateTime`, same column; PostgreSQL `TIMESTAMPTZ` -> `OffsetDateTime`
  (built-in, its own type and precision kept; PostgreSQL keeps the moment, not the offset).
  Column comments are never read as a hint - the user rejected that.

**UTC everywhere is the strong recommendation** - now plainly the user's job, so code, docs and
examples follow it and say so:

- in Python, time comes from the clock (`clock.instant()`, `self.clock.instant()` in an action) - UTC, no zone;
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
`tests/integration/test_concurrency.py`. But `asyncio.to_thread` copies the context into its
thread, so **a sync transaction records the thread that opened it, and any other thread that
finds it gets `TransactionThreadError`** (`tests/integration/test_transaction_threads.py`). Never
share one sync connection between threads, and never quietly swap in another connection: the
work was meant to be inside the transaction. Async tasks started inside an async transaction do
share it - they take turns on one thread's connection.

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
  the `Async` prefix; `to_record` / `from_record` stay plain `def` on both.
- **What stays sync, on threads:** event fan-out and dispatch, distributed jobs, migrations,
  `schemagen`. An async service builds a sync registry beside its async one, from the same layout
  file, for those.
- **Keyed locks** have async twins in `ekbatan.aio.locks` (PG, MySQL, MariaDB, Redis on
  `redis.asyncio`; all but Redis exported from `ekbatan.aio` too, as the sync ones are from
  `ekbatan` - `test_async_engine.py` compares the two packages' exports), on the sync
  providers' cores (`_HoldingCore`, `_HolderCore`, `_LeaseCore`, the statement and result
  helpers in `locks/sql.py` and `locks/redis.py`). The caller takes the lock around
  `await executor.execute(...)`; the holder is the task.
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
  error with a note naming it (the sync reads add the same note), never a group, never partial
  rows. `find_one_where` / `exists_where` answer as the sync calls do: the first shard in order
  with the row wins, the reads after it are cancelled, and a broken shard after it does not
  fail them (`_first_answer`). `dbs()` / `readonly_dbs()` stay sequential.
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
| jOOQ + container + Flyway → `WALLETS` + `WalletsRecord` | `python -m ekbatan.schemagen` → `WALLETS` (`Table`) + `WalletsRecord` (`TypedDict`), settings in `[tool.ekbatan.schemagen]` |

`schemagen` starts a throwaway container, runs **your** migrations into it and reflects the
result. Its settings are jOOQ's, in `[tool.ekbatan.schemagen]` of `pyproject.toml` (a flag wins):
schemas, includes, excludes, and **forced types** - by column name (`include-expression`) and SQL
type (`include-types`), to a framework helper or a SQLAlchemy type (`name`), to the user's own
**converter** by dotted path (`converter`: any class with `from_db`/`to_db`, jOOQ's
`userType` + `converter`), or to a library's ready-made SQLAlchemy type (`sqlalchemy-type`, a
convenience only). The user's are tried first, then Ekbatan's built-in five (zone-less
timestamps to `Instant`, PostgreSQL `TIMESTAMPTZ` to `OffsetDateTime`, JSON twice, PostgreSQL
`UUID`), which every Java example's build forces too. Four rules hold it together:

- **Forcing is what makes the schema right.** A `UUID` reflected from PostgreSQL renders as
  `CHAR(32)` on MySQL - hex with the hyphens stripped, which no Java service can join against.
  Reflected *from* MySQL it is `CHAR(36)`, indistinguishable from any other char column, so it
  is **not** forced by default: the project adds the rule (`CHAR\(36\)` + `.*\.id|.*_id`), as
  each Java MySQL build adds `UuidStringConverter` itself. The MySQL/MariaDB examples carry it
  (`UUID_IDS` in `scripts/sync-examples.py`). Do not bring the name guess back as a default.
  To leave a column or a whole table out of it, `exclude-expression` on the rule (plus a rule
  of its own for that table); the schemagen page shows both, proven on MySQL and MariaDB.
- **Every other type is written faithfully** (`schemagen/_types.py`): a generic SQLAlchemy type
  where one says the same, the dialect's own type through its dialect module where not,
  and nothing reflection merely decorates (MariaDB's `BIGINT(20)` display width). A type
  SQLAlchemy has no class for (PostgreSQL `xml`, MariaDB `INET6`, pgvector's `vector(3)`) is
  `sql_type("<as the database spells it>")`, never `NullType()`. Keys, indexes, checks and
  defaults come too, so `create_all` builds what the migrations built.
- **Every SQLAlchemy name is written through `import sqlalchemy`** - `sqlalchemy.Table`,
  `sqlalchemy.TEXT()`, `sqlalchemy.text(...)`, `postgresql.JSONB()` - never imported bare. A
  table's constant is its name in capitals, so a table named `text` would replace a bare `TEXT`.
  A table that would take the module's own `MIGRATIONS_FINGERPRINT` or `SCHEMA_FORMAT` gets a
  `_`, as a keyword does. `tests/unit/test_schemagen_table_names.py` and its integration twin
  name a table after every name schemagen can write (read off its source) and check nothing
  else changes.
- **A converter keeps the column's type.** It is written `converted(<impl>, Conv())`, where
  `<impl>` is what the column would be without it (the next matching non-converter forced type,
  else the type table). `ekbatan.schema.Converted` passes `None` through, and keys SQLAlchemy's
  statement cache on the converter itself (identity for an unhashable one), never on its class -
  `tests/unit/test_schema_converters.py` shows why. Converter and `sqlalchemy-type` paths are
  imported before any database starts, so a typo fails first.
- **Every type is proven on a real database** - `tests/integration/test_schemagen_types.py`
  generates, imports, `create_all`s and round-trips each one on all four databases. A new type
  rule gets a case there.
- **What reflection misses is read from the catalog** (`schemagen/_reflect.py`): index elements
  as the database writes them (sort order, `COLLATE`, MySQL functional parts, SQLite expression
  indexes), PostgreSQL domains whole, `EXCLUDE` and `DEFERRABLE` keys, partitions, inheritance,
  `UNLOGGED`, array dimensions, every sequence with its options (PostgreSQL, MariaDB), MariaDB's
  `nextval` defaults, MySQL's `SRID`, SQLite's `COLLATE`/`AUTOINCREMENT`/`STRICT`/`WITHOUT
  ROWID`. MySQL/MariaDB table options are written. **Nothing is dropped silently**: what no
  module can declare (routines, triggers, composite types, extensions, invisible columns,
  unenforced checks...) gets one note per kind. `tests/integration/test_schemagen_round_trip.py`
  proves it: migrate A, generate, `create_all` into B, compare the two catalogs line by line on
  all four databases. A new feature gets a case there; the matrix is
  `docs/SCHEMAGEN-COMPLETENESS-LEDGER.md`.
- **Every table gets a typed record**, `<Table>Record` (jOOQ's name: `wallets` ->
  `WalletsRecord`), a `TypedDict` beside the `Table` (`schemagen/_records.py`). Each key is typed
  from the type object the module builds: a converter's column is the converter's user type -
  its `from_db` return annotation, else its `Converter[DbT, UserT]` base; **neither is an error,
  never a silent `Any`**. `Any` only where nothing can say (`sql_type`, a driver-dependent type),
  with a note. Nullable is `| None`; a column the database fills is `NotRequired`. The types are
  proven against what the sync and async drivers really return (`test_schemagen_types`), and
  `tests/unit/test_typed_records_mypy.py` proves mypy catches a missing, misspelt, extra or
  mistyped column. A hand-written schema writes its records by hand.

Generated schemas are **gitignored**, as Java's `build/generated-jooq` is, and each generated
module fingerprints its migrations and checks that on import - because nothing in Python runs
before `python -m yourapp`. It also carries `SCHEMA_FORMAT` (`schemagen/_render.py`), and a
module written by another format refuses to import. **Raise `SCHEMA_FORMAT` whenever the
generated module's shape changes**, or a `schema.py` that the build does not rebuild (its rule
depends only on the migrations) imports quietly without what the code now expects.

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
