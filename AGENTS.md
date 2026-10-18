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
  schemagen.py       table definitions generated from a migrated database
  codegen.py         typed builders, generated per model
  streaming.py       ActionEvent - the consumer-side view of an outbox row
  accounting.py      statement counting, so the performance claims are checkable
  action/            action.py, plan.py, executor.py, registry.py, persister.py, outbox.py
  events/            handler.py, fanout.py, dispatch.py, jobs.py, notification.py, config.py
  locks/             base.py, sql.py, redis.py, inprocess.py, _holder.py
  distributedjobs/   DistributedJob, JobRegistry, JobsConfig
db-scheduler/        a separate distribution: the port of kagkarlsson/db-scheduler
ekbatan-py-examples/ seven examples x uv/pip/Poetry: 21 standalone projects
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

### Timestamps: zone-less UTC columns, aware UTC values

The columns carry no zone and hold the UTC wall-clock time, as Java's Ekbatan stores an
`Instant`. Every timestamp the framework hands back is an aware `datetime` in UTC, as Java's is
an `Instant` (`to_instant`); `to_storage` drops the zone only on the way into a column.
`timestamp_column()` does both and also pins microsecond precision, because MySQL's bare
`DATETIME` silently truncates to whole seconds.

**UTC everywhere is the framework's strong recommendation** - not enforced, but assumed
throughout, so code, docs and examples must follow it and say so:

- in Python, time comes from the clock (`clock.now()`, `self.now()` in an action), aware UTC;
  never `datetime.now()` or a naive `datetime`;
- columns are zone-less: `TIMESTAMP` on PostgreSQL, `DATETIME(6)` on MySQL and MariaDB; never
  `TIMESTAMPTZ`, never bare `DATETIME`;
- the database runs in UTC, or each session is put on it through `data_source_properties`
  (`options: -c TimeZone=UTC`, `init_command: SET time_zone = '+00:00'`).

The user's guide is `docs/database/multi-database.md#always-utc`.

### The ambient transaction lives in a `ContextVar`

Java binds it to a `ScopedValue`. A `ContextVar` is per-thread *and* per-async-task, so the same
mechanism works under a threaded server and an asyncio one. Work handed to a pool from inside a
unit of work does **not** inherit it - which is correct, and is pinned by
`tests/integration/test_concurrency.py`.

### Nothing in `src/` may import a web framework

`tests/test_structure.py` enforces it, along with the rule that an example imports only the
framework, the standard library and what its own `pyproject.toml` declares. The pip and Poetry
projects are written from the uv ones by `scripts/sync-examples.py`; edit only the uv ones.

## Testing

```bash
make test        # unit, then the whole suite on SQLite
make test-all    # PostgreSQL, MySQL and MariaDB too - needs Docker
make examples    # every example, each as its built wheel installed
```

- `tests/unit/` is **database-free by rule**, not by luck, and a test enforces it.
- `tests/integration/` is parametrised over dialects with `--dialects={sqlite,postgres,mysql,mariadb,all}`.
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
| jOOQ + container + Flyway → record classes | `python -m ekbatan.schemagen --migrations <dir> --out schema.py` |

`schemagen` starts a throwaway container, runs **your** migrations into it and reflects the
result. Two things it does that a plain reflection must not skip:

- **It forces the types.** A `UUID` reflected from PostgreSQL renders as `CHAR(32)` on MySQL -
  hex with the hyphens stripped, which no Java service can join against. Reflected *from* MySQL
  it is `CHAR(36)`, indistinguishable from any other char column, so it is forced by name.
- **It normalises dialect quirks.** MariaDB reflects a bigint as `BIGINT(display_width=20)`;
  emitted verbatim the module does not import.

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
