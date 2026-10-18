# Ledger: native async (`ekbatan.aio`)

Asked on 2026-10-18: a **trial** of native asyncio support, built as the first slice of a
professional, complete async implementation rather than a throwaway. Branch `async-trial`, in its
own worktree; nothing committed.

The design the user approved:

- everything async lives in `ekbatan.aio`, every name with an `Async` prefix;
- async versions **only** of what waits on the database on the request path - connections,
  transactions, repository reads and writes, the executor and its persister, the outbox write,
  `perform`. Models, events, plans, builders, ids, sharding rules, `schema.py`, schemagen,
  codegen, config and migrations are shared as they are. Event handlers and distributed jobs stay
  sync, on threads;
- **one copy of the logic**: statement building, plan handling, validation, retry policy, shard
  routing, row mapping, the optimistic-lock and soft-delete rules are shared by both. The async
  code differs only where it awaits the database;
- a service is all-sync or all-async; no mixing inside one transaction;
- `to_row` / `from_row` stay plain `def`;
- drivers are optional extras; the trial adds `asyncpg` only. SQLAlchemy's public asyncio API
  (`create_async_engine`, `AsyncConnection`), nothing private.

State: `trial` = built and tested in this trial. `todo` = needed for the professional
implementation. `shared` = nothing async to write, the sync code is used as it is. `n/a` = stays
sync by decision.

## Persistence

| # | Item | State |
|---|---|---|
| P1 | `AsyncDatabaseRegistry`: shards to managers, default shard, `effective_shard`, unknown-shard policy (shared core with `DatabaseRegistry`) | trial |
| P2 | `AsyncDatabaseRegistry.from_config(ShardingConfig)` | trial |
| P3 | `AsyncTransactionManager.transaction()`: begin/commit/rollback by hand, invalidate on failed rollback, span, Java log lines | trial |
| P4 | ambient transaction in the same `ContextVar` as sync, keyed by manager | trial |
| P5 | `connection(readonly=)`: join the ambient transaction, else `AUTOCOMMIT`; replica fallback to primary | trial |
| P6 | `async_engine_for(DataSourceConfig)`: same pool settings, JSON serialiser, `connect_args` as `engine_for` (shared kwargs) | trial |
| P7 | password never printed: async engine/url/registry repr, errors | trial (tested with a marker password) |
| P8 | `AsyncDb`: `rows(stmt)` -> `FetchResult` on an `AsyncConnection` | trial |
| P9 | `AsyncTransaction` (`execute`, `rows`, `shard`, `dialect`) | trial |
| P10 | `AsyncDatabaseRegistryBuilder` | todo |
| P11 | `insert_ignoring_duplicates` async twin (used by fan-out/jobs only) | n/a while handlers stay sync |
| P12 | replica (`secondaryConfig`) engines in `from_config` | trial (built; no replica test) |
| P13 | `close()` as `async def aclose()` / `async with registry` | trial |

## Repository and reads

| # | Item | State |
|---|---|---|
| R1 | `AsyncModelRepository` / `AsyncEntityRepository` / `AbstractAsyncRepository` on a shared core with the sync classes (construction checks, mapping, routing, statements) | trial |
| R2 | `get`, `find` | trial |
| R2b | `all` | trial |
| R3 | `add`, `add_no_result`, `add_all`, `add_all_no_result` | trial |
| R4 | `update`, `update_no_result`, `update_all`, `update_all_no_result` - single and batch (`VALUES` on PG, `UNION ALL` elsewhere), stale check shared | trial |
| R5 | `find_where` (one shard or scatter across all) | trial |
| R6 | `find_one_where`, `find_many`, `exists`, `exists_where`, `count` | todo (each is ~5 lines on the shared statement builders; `_presence` and `_id_among` are already shared) |
| R7 | `db()`, `readonly_db()`, `tx_db()`, `tx_db_else_db()` as async context managers | trial |
| R8 | `dbs()`, `readonly_dbs()` scatter iterators (async generators) | todo |
| R9 | multi-shard reads run concurrently (`asyncio.gather` per shard) rather than one after another | todo - a real gain over sync, decide the semantics of a failing shard |
| R10 | soft delete hidden from every read (`_live`, shared) | trial |
| R11 | `AsyncRepository` protocol (what the executor asks of an async repository) | trial |
| R12 | registry of async repositories (the sync `RepositoryRegistry` is reused - it is a map by model type) | trial |
| R13 | `id_matches`, `column`, `dialect`, `shard_for`, `shard_of`, `shard_of_all` | shared (inherited from the core) |

## Actions and executor

| # | Item | State |
|---|---|---|
| A1 | `AsyncAction` with `async def perform`; clock, name, `plan`, `now()` shared with `Action` | trial |
| A2 | `AsyncActionExecutor.execute(Type, params, principal=, config=)` | trial |
| A3 | retry on `StaleRecordError` with the same `ExecutionConfiguration`/`RetryConfig`, `asyncio.sleep` for the delay | trial |
| A4 | fresh plan per attempt, plan in the same `ContextVar` | trial |
| A5 | group by shard, cross-shard refusal, partial-commit logging (shared core) | trial |
| A6 | `AsyncChangePersister`: rows then outbox on one transaction (write plan shared) | trial |
| A7 | `AsyncSingleTableJsonEventPersister` + `AsyncEventPersister` protocol; event rows built by the shared function | trial |
| A8 | sentinel row for a unit of work with no events | trial |
| A9 | `AsyncActionExecutor.builder()` | todo |
| A10 | `ActionRegistry` holds async actions (shared, typed on a common base) | trial |
| A11 | `AsyncActionSpec` (testing helper twin of `ekbatan.testing.ActionSpec`) | todo |
| A12 | cancellation: a cancelled task mid-transaction rolls back and the connection is not reused dirty | trial (rollback path is `BaseException`; one test) |
| A13 | timeouts: `asyncio.timeout` around `execute` behaves like cancellation | todo (test) |

## Outbox, events, jobs

| # | Item | State |
|---|---|---|
| E1 | outbox write inside the action transaction | trial |
| E2 | `EventEntityRepository` async reads (`count`, `find_all`, `find_by_action_id`) | todo (tests only use SQL) |
| E3 | event fan-out (`events` -> `event_notifications`), dispatch, handlers | n/a - stay sync on threads, read the same tables |
| E4 | distributed jobs, db-scheduler | n/a - sync |
| E5 | keyed locks (SQL, Redis) inside an async action | todo - decide: async lock providers, or run sync providers in a thread |
| E6 | a handler that runs an *async* executor (sync handler -> `asyncio.run`?) | todo - decide |

## Cross-cutting

| # | Item | State |
|---|---|---|
| X1 | tracing spans (same names and attributes: `ekbatan.action.execute`, `.perform`, `.persist`, `ekbatan.transaction`, `ekbatan.repository`, `ekbatan.event.persist`) | trial (same `span()` helper and names; not yet checked with an SDK installed, as the sync `tracing` job does) |
| X2 | logging (same Java logger names and messages - the shared helpers emit them) | trial (not asserted by a test) |
| X3 | logging MDC equivalent (action name/principal on every line) | todo - the sync side has none either; if added, a ContextVar works for both |
| X4 | `ekbatan.accounting` statement counting | todo - check it hooks the async engine's `sync_engine` events |
| X5 | metrics | todo (sync has spans only) |
| X6 | sharding: routing by `EmbeddedBitsShardingStrategy` on async | trial |
| X7 | `build_async(...)` wiring (the counterpart to `ekbatan.build`) from config files | todo |
| X8 | `mypy --strict` over `ekbatan.aio` | trial (clean) |
| X9 | `AGENTS.md` module map: add `aio/` | todo |
| X10 | `make test-gevent`: check `ekbatan.aio` is not imported under gevent, or document that the two do not mix | todo |

## Drivers

| # | Item | State |
|---|---|---|
| D1 | PostgreSQL via asyncpg (`ekbatan[asyncpg]`) | trial |
| D2 | PostgreSQL via psycopg async (`postgresql+psycopg_async`) - no new dependency | todo - try; may be the default |
| D3 | MySQL / MariaDB via aiomysql or asyncmy | todo - new extra, user approval |
| D4 | SQLite via aiosqlite | todo - new extra, user approval |
| D5 | `data_source_properties` per async driver (asyncpg takes `server_settings`, not libpq `options`) and the refused-key ledger `tests/driver_settings.tsv` for each | todo |
| D6 | the UTC rule on asyncpg (`server_settings={"timezone": "UTC"}`) | todo - document |

## Tests

| # | Item | State |
|---|---|---|
| T1 | one scenario suite run against both sync and async, on PostgreSQL | trial |
| T2 | atomic commit: row and event both or neither, rollback proven | trial |
| T3 | optimistic lock conflict and retry | trial |
| T4 | soft delete hidden from reads | trial |
| T5 | sharded routing | trial |
| T6 | ContextVar isolation across concurrent tasks | trial |
| T7 | marker password never printed (async url, engine, registry) | trial |
| T8 | the whole integration suite on async, every dialect it supports | todo |
| T9 | unit tests for the shared core (database-free) | todo |

## Docs, website, examples

| # | Item | State |
|---|---|---|
| W1 | `docs/runtime/async.md` (and the website page beside it, written together) | todo |
| W2 | the concurrency page: when async helps and when threads are fine (use the numbers below) | todo |
| W3 | an async FastAPI example (`fastapi-wallet-rest-async-uv-pg`), uv/pip/Poetry | todo |
| W4 | setup guide: the `asyncpg` extra | todo |
| W5 | `DIVERGENCE.md`: Java has no async API (it uses virtual threads) | todo |
| W6 | `scripts/check-api.py` will check the new names once documented | todo |

## Trial results

### What was built

`src/ekbatan/aio/`: `persistence.py` (`AsyncDb`, `AsyncTransaction`, `AsyncTransactionManager`,
`AsyncDatabaseRegistry`, `async_engine_for`), `repository.py` (`AsyncRepository`,
`AbstractAsyncRepository`, `AsyncModelRepository`, `AsyncEntityRepository`), `action.py`
(`AsyncAction`), `outbox.py`, `persister.py`, `executor.py` (`AsyncActionExecutor`). About 900
lines, most of them docstrings; the logic in them is the awaits.

The user-facing shape is the approved one:

```python
class WalletRepository(AsyncModelRepository[Wallet, ShardedId[Wallet]]):
    def to_row(self, w): ...                      # plain def
    def from_row(self, row): ...                  # plain def
    async def find_all_by_owner(self, owner_id):
        return await self.find_where(WALLETS.c.owner_id == owner_id)

class DepositMoneyAction(AsyncAction[DepositParams, Wallet]):
    async def perform(self, principal, params):
        wallet = await self.wallets.get(params.wallet_id)
        return self.plan.update(wallet.deposit(params.amount))

wallet = await executor.execute(DepositMoneyAction, params)
```

### Shared core: what moved out of the sync code

No sync behaviour changed; each sync class keeps its name, signature and behaviour and now
extends a private base the async class extends too.

| sync file | shared now | async differs only in |
|---|---|---|
| `persistence.py` | `_ShardRegistry` (validation, `manager`, `effective_shard`, `from_config`, default, repr); the `ContextVar` and `_join`; the transaction span and its four log lines; `_engine_kwargs` | opening, begin/commit/rollback, `dispose` |
| `repository.py` | `_RepositoryCore`: construction checks, mapping, routing, `_live`, scatter shards, every statement (`_by_id`, `_where`, `_insert_rows`, `_insert_returning`, `_stamped`, `_update_statement` incl. the PG `VALUES` / `UNION ALL` choice and its cache), the optimistic-lock check (`_checked`) and the read-back (`_updated`); `_must_be_model` / `_must_be_entity` | `await conn.execute(...)` |
| `action/action.py` | `_ActionCore`: clock, name, `plan`, `now()` | `async def perform` |
| `action/registry.py` | holds both kinds; `get` typed `type[A] -> A` | - |
| `action/executor.py` | `_ExecutorCore`: setup and refusals, `_running`, the execute span and log, `_retry_delay` (policy match, retry log, span event, failure log), `_succeeded`, `_shards_to_commit` (grouping, cross-shard refusal, sentinel shard, cross-shard warning), `_partially_committed` | awaiting `perform`, `asyncio.sleep`, `async with transaction()` |
| `action/persister.py` | `_ChangePersisterCore._writes`: which repository, which rows, which events, in which order | awaiting each write |
| `action/outbox.py` | `_SingleTableJson`: the rows (incl. the sentinel), the JSON-object refusals, the event-name collision check, the span | awaiting the insert |

Proof sync is unchanged (all runs 2026-10-18, this worktree):

| suite | before the trial (clean `HEAD`) | after |
|---|---|---|
| `pytest tests/unit tests/test_structure.py` | 1026 passed | 1026 passed |
| `pytest tests/integration` (SQLite) | 554 passed, 51 skipped | 562 passed, 67 skipped = the same 554 + 8 sync parity scenarios; the new skips are async-on-SQLite and one two-writer test SQLite cannot run |
| `pytest tests/integration --dialects=postgres` | 702 passed, 11 skipped | 723 passed, 14 skipped = the same 702 + 21 parity tests (sync and async); 3 new skips are async-only tests in sync mode |
| `pytest tests/integration --dialects=mysql` | not run before | 715 passed, 26 skipped (run because the refactor moved MySQL's no-`RETURNING` update path) |
| `ruff check` / `ruff format --check` (src, tests, the bench script) | - | clean |
| `mypy src` (strict) | - | clean, 63 files. (`mypy` over the examples needs the `examples` group installed; not installed in this worktree, unchanged by the trial) |

### Tests: `tests/integration/test_async_parity.py`

Each scenario is one coroutine run in both modes; what was committed is read by a separate
sync connection that belongs to neither run.

| scenario | sync | async | proven able to fail |
|---|---|---|---|
| deposit commits row and event together | pass | pass | - |
| failing `perform` commits neither | pass | pass | - |
| outbox write fails *after* the row is on the transaction (the persister sees the new balance there) -> row rolled back, no event | pass | pass | yes: committing inside the async persister before the outbox write turns it red |
| lost version race is retried, commits once, 2 attempts | pass | pass | yes: an async executor that never retries turns it red |
| lost race with `no_retry()` raises `StaleRecordError`, nothing written | pass | pass | - |
| soft-deleted row hidden from `find`, `get`, `find_where`, `all` | pass | pass | yes: dropping the `DELETED` filter in the shared core turns both red |
| sharded: row and outbox on the id's shard only | pass | pass | - |
| each executor refuses the other kind of action | pass | pass | - |
| two transactions open at once never see each other; a repository call joins its own; nothing ambient afterwards (threads / tasks) | pass (skip on SQLite) | pass | yes: an async manager sharing one connection across tasks turns it red |
| 50 concurrent deposits on one loop each commit exactly once | - | pass | - |
| a cancelled task rolls back; the connection goes back clean | - | pass | - |
| marker password never in config/engine/url/manager/registry repr, nor in the refused-login error and its traceback | (own test exists) | pass | - |

### Measurement: `scripts/bench-async.py`

Same deposit (read the wallet, then update + outbox row in one transaction), `N` workers at
once each on its own wallet, 2000 deposits in total per level, pool of 40 connections for every
engine, PostgreSQL 17 in a local container, Python 3.14.7, one laptop (Apple silicon). A
heartbeat task asks to wake every 10 ms; "worst lag" is how late its worst wake-up was. Two
runs; the second shown, the first within about 10%.

**Today: sync executor in a 40-thread pool (psycopg), `asyncio.to_thread`**

| N | ops/s | p50 ms | p99 ms | heartbeat worst lag ms |
|---:|---:|---:|---:|---:|
| 1 | 241 | 3.5 | 7.2 | 4.9 |
| 50 | 692 | 69.7 | 146.0 | 20.2 |
| 200 | 719 | 275.5 | 313.9 | 18.5 |
| 1000 | 713 | 1375.9 | 1438.1 | 21.1 |

**`AsyncActionExecutor` on asyncpg**

| N | ops/s | p50 ms | p99 ms | heartbeat worst lag ms |
|---:|---:|---:|---:|---:|
| 1 | 167 | 5.2 | 9.7 | 9.1 |
| 50 | 1059 | 43.8 | 78.4 | 5.7 |
| 200 | 1049 | 114.1 | 695.0 | 5.9 |
| 1000 | 1062 | 468.2 | 1787.6 | 19.1 |

**`AsyncActionExecutor` on psycopg's async mode (no extra driver)**

| N | ops/s | p50 ms | p99 ms | heartbeat worst lag ms |
|---:|---:|---:|---:|---:|
| 1 | 238 | 3.7 | 7.2 | 1.2 |
| 50 | 902 | 51.5 | 134.0 | 49.1 |
| 200 | 917 | 136.5 | 749.8 | 10.2 |
| 1000 | 938 | 704.8 | 2005.0 | 26.8 |

What the numbers say, plainly:

- **Under load, native async does about 1.5x the work** (asyncpg ~1050/s against ~710/s) and
  halves the median wait. The thread pool tops out at 40 requests in flight; async keeps all 40
  connections busy without 40 threads fighting over the GIL.
- **One request at a time, asyncpg is slower** (5.2 ms against 3.5 ms). psycopg's async mode is
  not (3.7 ms). Not investigated; likely the per-connection setup asyncpg does through
  SQLAlchemy's adapter. Worth a look before recommending asyncpg.
- **The slowest 1% wait longer on async at 200 and 1000** (p99 0.7 s and 1.8 s against 0.3 s
  and 1.4 s): the thread pool queues requests evenly in front of 40 threads, while async lets
  every request start and then queues them at the connection pool, less fairly. A limit on
  requests in flight would likely fix it; not tried.
- **The event loop stayed responsive in every run, both ways** (worst heartbeat lateness 5-50 ms,
  no run lost its heartbeat). Today's thread-pool approach does not block the loop either -
  the gain from async is throughput and memory per request, not loop health.

Verdict: async is faster under concurrency (about 1.5x throughput, half the median latency),
slower for a lone request on asyncpg, and worse at the far tail without a cap on requests in
flight.

### What the trial proved

- The approved shape works end to end on PostgreSQL: an async repository with plain
  `to_row`/`from_row`, `async def perform`, `await executor.execute(...)`, rows and outbox
  committed atomically, the same retry on `StaleRecordError`, soft delete, sharding.
- One copy of the logic is achievable without contortion: the async modules contain only
  awaits and docstrings, and the sync suites are unchanged on SQLite, PostgreSQL and MySQL.
- The existing `ContextVar` design already isolates tasks; nothing new was needed.
- Cancellation rolls back cleanly and returns the connection.

### What remains for the professional implementation (rough size)

Every `todo` row above. Grouped:

- **repository completeness** (R6, R8, R9, E2): the remaining reads, scatter iterators,
  concurrent scatter - small, a day; concurrent scatter needs a decision first.
- **wiring and builders** (P10, A9, A11, X7): `build_async`, builders, `AsyncActionSpec` - a
  day or two.
- **drivers** (D2-D6): psycopg async is nearly free and may be the better default; MySQL/MariaDB
  and SQLite need new extras the user must approve, plus the driver-settings ledger and the
  timezone recipe per driver - a few days.
- **whole-suite parity** (T8, T9): run the integration suite's behaviour on async for every
  supported dialect, not only the trial's scenarios - the largest single item, about a week,
  probably by turning more of the suite into mode-parametrised scenarios like the trial's.
- **locks and handlers inside async** (E5, E6): decisions, then a few days.
- **accounting, metrics, gevent** (X4, X5, X10): small, each needs a check by running.
- **docs, website, example, setup guide, DIVERGENCE** (W1-W6, X9): a few days, written together
  per the website-docs rule.

Overall: roughly 3-4 weeks of focused work to "complete and professional", most of it tests,
drivers and docs rather than core code.

### Risks

- **asyncpg's strictness**: it types parameters itself; everything in the trial worked (UUIDs,
  `Numeric`, JSONB, naive timestamps, the `VALUES` batch update with casts), but the full
  suite has not run on it. psycopg async is the low-risk fallback.
- **greenlet**: SQLAlchemy's asyncio runs on it. Under gevent (which also uses greenlet) the two
  have not been tried together.
- **two hierarchies to keep in step**: the shared bases make drift hard but not impossible - a
  new public method added to `AbstractRepository` has to be added to the async class too. A
  structure test that compares the two public surfaces would catch it (todo).
- **tail latency under overload** (above) needs an answer in the docs or the executor before
  anyone is told async is "faster".
- **mixing**: nothing stops an application from building both a sync and an async registry over
  the same database; it works (separate pools) but one transaction cannot span them. The docs
  must say so.
