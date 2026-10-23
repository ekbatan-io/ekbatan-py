# Golden path and two refusals - ledger

The brief (2026-10-23): one golden path in the docs, two loud refusals in the framework. No
auto-fixing, no server detection, no per-server branches. Judgement calls go in
`DECISIONS-TO-REVIEW.md` under "golden path and two refusals (EKB-0500-)".

Status: `todo` / `done` / `n/a`. A row is `done` only when a run shows it.

## Refusal 1: used in a copied process (after fork)

| # | item | status | proof |
| --- | --- | --- | --- |
| F1 | `ForkedProcessError(EkbatanError)` in `errors.py`, exported from `ekbatan` | done | `errors.py`, `ekbatan.__all__`; unit `test_both_errors_are_exported_and_are_ekbatan_errors` |
| F2 | one helper: remember `os.getpid()` at creation, refuse when it differs | done | `_guards.refuse_if_forked`; unit exact-message test |
| F3 | sync `TransactionManager`: `connection()`, `transaction()`, the unjoined engine door | done | integration `test_a_sync_registry_built_before_fork_is_refused_in_the_child` (6 doors) x4 dbs |
| F4 | `AsyncTransactionManager`: the same three doors | done | integration `test_an_async_registry_built_before_fork_is_refused_in_the_child` (4 doors) x4 dbs |
| F5 | sync lock providers (SQL x3, Redis): `acquire` / `try_acquire` | done | `test_a_sync_lock_provider_...` (pg/mysql/mariadb), `test_a_redis_lock_provider_...` |
| F6 | async lock providers (SQL x3, Redis): `acquire` / `try_acquire` | done | `test_an_async_lock_provider_...` (pg/mysql/mariadb), `test_an_async_redis_lock_provider_...` |
| F7 | `JobRegistry.start()` (db-scheduler's own pool refusal kept unchanged) | done | `test_a_job_registry_built_before_fork_is_refused_in_the_child` (built and manual) |
| F8 | `HandlerLoop.run()` (owns a thread) | done | unit `test_a_handler_loop_started_before_fork_...`; red: "the child hung" |
| F9 | tests: real fork, created-before refused / created-in-child works / parent after fork works, sync + async, four databases | done | built-in-child works and parent untouched asserted in each; red with the check off: 10 of 10 fork tests fail (sqlite+pg run) |

## Refusal 2: the sync API on an event loop's thread

| # | item | status | proof |
| --- | --- | --- | --- |
| B1 | `BlockingCallError(EkbatanError)` in `errors.py`, exported from `ekbatan` | done | as F1 |
| B2 | one helper, like EKB-0250's lock refusal | done | `_guards.refuse_on_an_event_loop`; unit test, fires on the loop only |
| B3 | sync `TransactionManager`: `connection()`, `transaction()`, the unjoined engine door (repository `db()` / `readonly_db()`, events repository, `migrate_registry`) | done | `test_every_sync_door_is_refused_inside_a_coroutine` (6 doors) x4 dbs |
| B4 | sync `ActionExecutor.execute` first line | done | `test_the_sync_executor_is_refused_before_the_action_runs`: perform never ran, nothing written |
| B5 | migrations' door (`migrate`, `info`, `repair`, `baseline` open a backend) | done | `test_a_migration_run_is_refused_inside_a_coroutine` |
| B6 | tests: fires in a coroutine and in an async handler on `HandlerLoop`; silent in a FastAPI `def` endpoint, `asyncio.to_thread`, `run_in_executor`, gevent, a sync handler on a dispatch thread, the test harness's portal | done | silent: to_thread, run_in_executor, plain thread beside a running loop, FastAPI def endpoint, dispatch pool threads, harness thread, gevent greenlets; fires: coroutine, async def endpoint, async handler on HandlerLoop, portal loop. Red: check off -> 12 fail; check firing on worker threads -> 5 silent tests fail |
| B7 | every repo place that called the sync API on a loop, moved and logged | done | table below |

## Places that called the sync API on a loop (B7)

| place | what | moved to | status |
| --- | --- | --- | --- |
| 10 FastAPI example lifespans (rest x8, saga x2; uv, pip/Poetry by sync-examples) | `build()` ran `migrate` on the loop - refused | `await asyncio.to_thread(build, settings)`, `to_thread(service.start)`, sync `close` too | done |
| wallet-service async SQLite `start()` | `jobs.start()` on the loop (not refused) | `to_thread(self.jobs.start)` - consistency | done |
| tests/integration/test_async_parity.py | registries built (migrate) and sync scenarios run via `aw()` on the loop | `to_thread(_registry...)`, `svc.call(fn, ...)` | done |
| tests/integration/test_async.py | `two_shards`, `one_database` x6 migrate on the loop | `to_thread` | done |
| scripts/bench-async.py | sync seed write on the loop | `to_thread`; bench run end to end | done |
| docs: wiring-fastapi (docs+web), wiring-a-service.mdx | lifespan samples showed sync build on the loop | the to_thread form | done |

## Docs

| # | item | status |
| --- | --- | --- |
| D1 | golden path on the front: docs/getting-started.md, docs/README.md, website getting started / wiring | done |
| D2 | Deploying page (renamed Startup and shutdown later), docs + website, linked from front page and nav | done |
| D3 | AGENTS.md rules | done |
| D4 | DRACULAS #38 addressed, #31 note | done |
| D5 | docs/runtime/concurrency.md, docs/jobs/distributed-jobs.md and website copies | done |

## Verify

| check | status | result |
| --- | --- | --- |
| make lint | done | pass |
| unit | done | 2084 passed, 1 skipped |
| integration, four databases, both modes | done | sqlite 1094, postgres 1332, mysql 1322, mariadb 1336 passed; 0 failed. Collected +82 = exactly the new tests |
| make test-psycopg-async | done | 1332 passed |
| make test-gevent | done | 2800 passed, 0 failed (21 wrongly refused before the `_thread_id` fix) |
| 14 uv examples `make` | done | 14/14 pass, both SQLite `make run` pass |
| pip + Poetry, two families | done | rest-pip-pg 21, rest-poetry-pg 21, saga-pip-async-pg 22, saga-poetry-async-pg 22 |
| setup-guide checker, uv x 4 dbs x 2 modes | done | 8/8 OK |
| website build + check | done | 59 pages, 0 broken links, parity 76/76 |
| docs-website sync | done | 34 pairs, 0 behind (startup-and-shutdown.md paired with learn/startup-and-shutdown.mdx) |
