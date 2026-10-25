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
`fromRecord` - typed by the table's **record**, a `dict` subclass schemagen writes beside the
table (`WalletsRecord` beside `WALLETS`), read as `record.column_wallet_id()` (preferred) or
`record["wallet_id"]`, so mypy checks every mapping against the table.

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
  persistence.py     DataSource, DatabaseRegistry, TransactionManager, Transaction
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

A reader moving between the two ports should recognise what they are looking at. **Copy Java's
names exactly** - there is no word-order rule to apply, because Java itself mixes them: the
wallet's actions put the model first (`WalletCreateAction`), the saga's steps the verb
(`InitiateTransferAction`).

| | |
| --- | --- |
| Actions | `WalletCreateAction`, `WalletDepositMoneyAction`, `WalletCloseAction`, `CreateNotificationAction`, `InitiateTransferAction` |
| Action parameters | the action's nested `Params`, as Java's `WalletCreateAction.Params`: `class WalletCreateAction(Action["WalletCreateAction.Params", Wallet])` |
| Events | `WalletCreatedEvent`, `WalletMoneyDepositedEvent`, `WalletClosedEvent`, `TransferFailedEvent` |
| Handlers | `WalletMoneyDepositedEventHandler`, `TransferInitiatedEventHandler` |
| Jobs | `WalletStipendJob`, `WalletReportJob` |
| Models and entities | `Wallet`, `Notification`, `TransferStep` |
| Repositories | `WalletRepository`, `NotificationRepository` |
| Repository variables | Java's field name in snake_case: `wallet_repository`, `notification_repository` - never `wallets`. Other wiring objects likewise: `database_registry`, `lock_provider`, `job_registry` |
| Python | `snake_case` functions and fields, `PascalCase` classes |
| SQL | `snake_case`; table names as Java's (`wallets`, `notifications`) |
| Commits | one `EKB-XXXX` subject line, no body |

Only what the language asks for may differ: snake_case, builders instead of DI annotations,
`def` / `async def`, pytest, yoyo, schemagen. The examples follow the matching Java example
piece for piece; see `docs/JAVA-MODEL-LEDGER.md`.

### The mapping is typed by the table's record

Every repository is generic in its model, its table's record and its id, in Java's order:
`ModelRepository[Wallet, WalletsRecord, UUID]` (and `EntityRepository`, the `Abstract` base and
the `ekbatan.aio` twins alike). The third is the type the id column holds, as Java's `DB_ID`: a
`UUID`, so a read by id takes `wallet.id.value`, as Java's takes `wallet.id.getValue()`, and a key
of another type is refused. A schemagen forced type that converts the id column makes its user
type - a typed id - the key, as a jOOQ forced type changes `DB_ID`. `to_record(model) -> WalletsRecord` and
`from_record(record: WalletsRecord) -> Wallet` are the repository's to write. No `dict[str, Any]`
mapping in examples or docs: a generated schema has its records, a hand-written one writes them
beside its tables in the same style. (`to_row` / `from_row` until `EKB-0409`; renamed outright,
no aliases.)

A record (since `EKB-0485`, the user's design) is a subclass of `dict[str, Any]` with
`__slots__ = ()`, a keyword-only `__init__`, `__getitem__` overloads per column under
`TYPE_CHECKING` (mypy only - one `type: ignore[override]` on the first), and a
`column_<name>()` method per column. `record.column_x()` is the preferred way to read a
column, and what every example, doc and `from_record` shows; `record["x"]` is also fine (the
record is a `dict`) and equally type-checked - say "preferred", never "avoid". Rules:

- **The record is the dict.** `to_record` builds it by keyword, `WalletsRecord(id=..., ...)`
  (`EKB-0447`); the INSERT is given that very object, and an UPDATE reads its values from it -
  `EKB-0480`'s rules hold: a write sends exactly the record's keys, a batch whose records differ
  is refused.
- **Reading builds one record per row**, the record class the repository is generic in, read
  off its bases once (`_record_type_of`; `dict` where it names none): `dict.__new__` +
  `dict.update(zip(keys, row))`, skipping `__init__` (`fetching.records` / `record_of`). Every
  read path, sync and async, sharded or not, goes through `_read_all` / `_read_back`; a MySQL
  read-back hands `from_record` the record `to_record` wrote (`_as_record`). A
  `FetchResult` mapper gets the record class its first parameter names (or a repository
  method's own record); any other mapper - a lambda - the row's mapping.
- **Names.** Every column's method is `column_` + its name with non-`[A-Za-z0-9_]` made `_`.
  Its keyword is its name, else sanitized: `class_`, `order_date`, `_2fa`, `_meta` (for
  `__meta`), `self_`/`super_`; collisions get `_2`, `_3` on the one that changed, with a note.
- **A column the database fills** is a keyword defaulting to `ekbatan.schema.UNSET` (type
  `Unset`); left so, the key is absent and the INSERT omits it. Read back it is typed as the
  column reads.

### Immutability

Models and entities are `@dataclass(frozen=True, kw_only=True)`. `copy()` returns a builder;
`evolve()` is the framework's own shorthand and domain code should prefer `copy()` so every
change in an application reads the same way.

### Every model and entity defines `validate()`

**Rule** (since `EKB-0497`, the user's decision): every concrete `Model` and `Entity` - in
examples, docs, tests and fixtures alike - defines `validate(self) -> None`, its one place for
rules beyond its types (`pass  # no rules beyond its types` when it has none). `None` checks
never go in it: the built-in checks read them off the types (`balance: Decimal` refuses
`None`, `note: str | None` accepts it), along with `version >= 1`. Raise `ValidationError`.

- `validate` is abstract on `Persistable` (an `ABC`), so mypy and pyright flag a model without
  it; `Persistable.__init_subclass__` refuses the class at definition ("X must define
  validate(); ..."). A base class that lists `ABC` in its own bases, or still has other abstract
  methods, is let through.
- The built-in checks (`_check_built_ins`), then `validate()`, run in
  `Persistable.__post_init__`, so on every construction path: constructor, `build()`,
  `copy()...build()`, `evolve`, `without_events`, `next_version`, `from_record`.
- A model's own `__post_init__` is wrapped at definition (`_guard_post_init`): built-ins, then
  its code, then `validate()`, whether or not it calls `super().__post_init__()`; nothing runs
  twice. Nobody has to call super.

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

### The golden path, and two refusals

**Rule** (since `EKB-0500`, the user's decision): one golden path in the docs, and two loud
refusals in the framework - no auto-fixing, no server detection, no per-server branches, nothing
done behind the user's back.

- **The golden path**, said the same way on the front of the docs and the website: create
  ekbatan's objects in the app's startup code and close them in its shutdown code, never at
  import time - FastAPI: in the lifespan; a plain worker: at the top and bottom of `main()`.
  ekbatan is a library of objects, not a service: no doc says "start ekbatan", and the
  examples' `WalletService` / `Worker` and `build()` are the examples' own code, said so where
  they are shown. `docs/startup-and-shutdown.md` has the table of every object that holds connections or
  threads and its real startup and shutdown calls - keep it true to the code. Normal code: the sync API; `async def` code: `ekbatan.aio`. Nothing
  server-specific on the front; the per-server recipes live on one page, `docs/startup-and-shutdown.md`
  (website `learn/startup-and-shutdown`), which marks what is tested and what is only expected.
- **Refusal 1, `ForkedProcessError`**: every object that owns connections or threads records
  `os.getpid()` when built - `DataSource` and `AsyncDataSource`, `TransactionManager` and
  `AsyncTransactionManager` (so every registry), the sync and async lock providers (SQL and
  Redis), `JobRegistry`, `HandlerLoop` - and its doors (a data source's `sqlalchemy_engine` and
  `close()` / `aclose()`; `transaction()`, `connection()`, the unjoined `_engine()` a
  repository's `db()` and `readonly_db()` use; `acquire` / `try_acquire`; `start()`; `run()`)
  raise in any other process. Never reset, reconnect or dispose in the child. db-scheduler's own
  pool refusal (`register_at_fork`) stays as it is.
- **A pool is a `DataSource`** (since `EKB-0524`, the user's naming): built only with
  `DataSource.from_config(config)` / `AsyncDataSource.from_config(config)`, as Java's
  `ConnectionProvider` only with `hikariConnectionProvider(cfg)`. The transaction managers, the
  SQL lock providers (`.data_source(...)`) and `job_registry().data_source(...)` take nothing
  else - an SQLAlchemy `Engine` is refused. "Engine" means SQLAlchemy's own object only:
  `data_source.sqlalchemy_engine`. db-scheduler is still handed that engine by ekbatan.
- **Refusal 2, `BlockingCallError`**: the sync API on a thread with a running asyncio loop -
  the sync `TransactionManager`'s doors, `ActionExecutor.execute` (before `perform`), and a
  migration run (`_each` in `migrations.py`). Silent on a worker thread (`asyncio.to_thread`,
  `run_in_executor`, a FastAPI `def` endpoint, dispatch's threads), under gevent, and on the test
  harness's calling thread. Never moved to a thread for the caller.
- Both live in `src/ekbatan/_guards.py`. A new object that owns connections or threads, or a new
  sync way in to a database, calls them. `tests/integration/test_refusals.py` proves each with
  real `os.fork()` children on all four databases, and each allowed case silent.
- **Sync work in an `async def` lifespan goes through `asyncio.to_thread`** - building the
  service (it runs the migrations), starting and stopping it - in every example, doc and test.
  A test scenario written as a coroutine calls the sync API the same way.

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
- **Event handlers run after the action**, on dispatch's threads, never inside it. A handler
  extends `EventHandler` - Java's four methods, `name()`, `aliases()` (empty by default),
  `event_type()` and `handle()` - or `AsyncEventHandler`, whose `handle()` is `async def`: dispatch
  runs that one on its `HandlerLoop` (one loop on its own thread) and records the outcome exactly
  as for a sync handler. What an async handler awaits belongs to that loop. The registry refuses
  a handler that extends neither, or whose `handle()` is the other kind.
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
| jOOQ + container + Flyway → `WALLETS` + `WalletsRecord` | `python -m ekbatan.schemagen` → `WALLETS` (`Table`) + `WalletsRecord` (a `dict` subclass with `column_` methods), settings in `[tool.ekbatan.schemagen]` |

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
  ROWID`, foreign key actions (`PRAGMA foreign_key_list`) and generated columns, the sequence a
  PostgreSQL default draws on (`pg_depend`, in its real schema), MySQL/MariaDB view columns'
  NOT NULL and collation. MySQL/MariaDB table options are written - from `SHOW CREATE TABLE` too
  where reflection does not read them - and partitioning is applied after the table
  (`after_create` DDL), as are PostgreSQL storage parameters, replica identity and column
  storage. **Nothing is dropped silently**: what no module can declare (routines, triggers,
  composite types, extensions, invisible columns, unenforced checks, row level security,
  policies, rules, collations...) gets one note per kind.
  `tests/integration/test_schemagen_round_trip.py` proves it: migrate A, generate, `create_all`
  into B, compare the two catalogs line by line, and each view's columns against
  `view_metadata.create_all` in C. The docs table says "yes" only where a case there covers it
  on that database. A new feature gets a case there; the matrix is
  `docs/SCHEMAGEN-COMPLETENESS-LEDGER.md`.
- **Every table gets a typed record**, `<Table>Record` (jOOQ's name: `wallets` ->
  `WalletsRecord`), a `dict` subclass beside the `Table` (`schemagen/_records.py`; the shape is
  above, under "The mapping is typed by the table's record"). Each key is typed
  from the type object the module builds: a converter's column is the converter's user type -
  its `from_db` return annotation, else its `Converter[DbT, UserT]` base (an annotation of `Any`
  falls through to the base); **neither is an error, never a silent `Any`**. `Any` only where nothing can say (`sql_type`, a driver-dependent type),
  with a note. Nullable is `| None`; a column the database fills is a keyword defaulting to
  `UNSET`. The types are proven against what the sync and async drivers really return
  (`test_schemagen_types`; `tests/records.py` reads a record's keys and types back by running
  its `__init__`), and `tests/unit/test_typed_records_mypy.py` proves mypy catches a missing,
  misspelt, extra or mistyped column, an unknown key or `column_` method, and a value used as
  the wrong type. A hand-written schema writes its records by hand.

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
