# Async draculas

Everything that would surprise someone who knows the sync way of using ekbatan-py when they
read the async way (or the other way round), where **async itself does not need the
difference**. Found on 2026-10-19 by diffing every sync/async example pair (uv builds), the
public API of `ekbatan` against `ekbatan.aio`, and every Sync | Async code sample on the website
and in `docs/`. Acted on from 2026-10-20 (the user approved every row): each row says
what was done and in which commit; the work is logged in `docs/ASYNC-DRACULAS-FIX-LEDGER.md`.

- **Why:** *leftover* = was needed once, no longer is; *choice* = someone decided it, async did
  not need it; *forced* = async (or its driver) leaves no other way.
- **Match?** yes = make the two sides the same; no = keep the difference.
- **Surprise:** H / M / L.
- **Checked:** *run* = shown by running code; *read* = read on both sides.

`ex/` is `ekbatan-py-examples/`. Example paths name the uv build; the pip and Poetry builds are
generated from it and carry the same difference. Where a pair is named once (say
`rest-uv-pg`), the MySQL and MariaDB pairs are the same unless the row says otherwise.

## Leftovers and unforced choices

| # | What differs | Sync side | Async side | Why | Match? | Fix (not applied) | Surprise | Checked |
|---|---|---|---|---|---|---|---|---|
| 1 | **The row mapping sits in a separate `*Rows` mixin** in every async repository (`class WalletRepository(WalletRows, AsyncModelRepository[...])`); sync writes `to_row`/`from_row` inside the repository. Docs teach the mixin as "the async way". Nothing reuses the `*Rows` classes. | `ex/wallet-service-uv-sqlite/src/wallet_service_sqlite/repository.py:36`; same in every sync example | `ex/wallet-service-uv-async-sqlite/src/wallet_service_async_sqlite/repository.py:33,68,93,175`; every async example's `repository.py`; a test pins it: `test_wallet_service_async_sqlite.py:315` (sync twin's test, `:324`, checks `vars(WalletRepository)`). Docs: `docs/going-async.md:41`, `docs/database/repositories.md:69`, `docs/database/sharding.md:220`, `docs/wiring.md:117,134`; website `learn/async.mdx:46`, `learn/sharding.mdx:61`, `learn/complete-project-setup.mdx:1607`, `reference/repository.mdx:27,77`, `reference/sharding.mdx:254`, `reference/wiring.mdx:127,147` | leftover: `EKB-0360` shared the mapping with a sync handler repository; `EKB-0366` removed that repository, the split stayed and was copied | yes | Put `to_row`/`from_row` back inside each async repository, delete the `*Rows` classes, make the async test check `vars(WalletRepository)` as the sync one does, and drop the "small class" sentence from the docs | H | run: a copy of `wallet-service-uv-async-sqlite` with the mapping inlined - the only failure was the test that imports `WalletRows`; with that test written as the sync twin's, 18 passed and `python -m` ran to the end |
| 2 | **The stipend job is missing** from the async REST twins (pg, mysql, mariadb) without a word, and from the async SQLite twin with a reason ("written exactly as the sync `jobs.py`, over a sync executor") that the async job worker twin does not follow (it runs the job's work on a loop, awaited) | `ex/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/jobs.py:27`, wired at `app.py:198`; `ex/wallet-service-uv-sqlite/src/wallet_service_sqlite/app.py:59-64,180` (and `start()` waits for its first run); test `test_the_stipend_job_pays_every_open_wallet` | `ex/fastapi-wallet-rest-uv-async-pg/src/fastapi_wallet_rest_async_pg/app.py:248` (no job); `ex/wallet-service-uv-async-sqlite/README.md:48-51`; vs `ex/job-worker-uv-async-pg/src/job_worker_async_pg/jobs.py:82`; docs `docs/wiring.md:432,470`, `reference/wiring.mdx:471,513` | choice, logged in `DECISIONS-TO-REVIEW.md` (rows at 768, 823) before async job work existed; now a leftover | yes | Add the stipend to the async twins the way `job-worker-uv-async-pg` does (`loop.run(self.pay())`), or say in each async README why it is gone | M | read |
| 3 | **Only async examples cap load**: pool `TimeoutError` mapped to 503 + `Retry-After`, `poolTimeout: 5`, uvicorn `limit_concurrency`. A sync request that waits out the pool gets a 500 after the default 30 s | `ex/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/api.py:144-152` (no 503), `__main__.py:28`, `ekbatan.yaml:33`; same in the sync saga and sharded twins | `ex/fastapi-wallet-rest-uv-async-pg/src/fastapi_wallet_rest_async_pg/api.py:26,140`, `__main__.py:38`, `ekbatan.yaml:24`; docs present `poolTimeout` only in the async tab (`docs/wiring.md:193`, `reference/wiring.mdx:208`) | choice (logged, row at 862) | yes | Add the same 503 handler, `poolTimeout` and `limit_concurrency` to the sync REST, saga and sharded examples | M | run: sync and async pools both raise `sqlalchemy.exc.TimeoutError` past `pool_timeout`, so the handler fits both |
| 4 | **The sharded deposit takes a keyed lock only in async** | `ex/fastapi-wallet-rest-uv-sharded-pg/src/fastapi_wallet_rest_sharded_pg/api.py:164` (no lock, no `lockConfig`) | `ex/fastapi-wallet-rest-uv-async-sharded-pg/src/fastapi_wallet_rest_async_sharded_pg/api.py:185,192`, `ekbatan.yaml:42`, plus a concurrency test | choice (logged, row at 854: "as in the other async REST examples") | yes | Add the lock (and its test) to the sync sharded example, or remove it from the async one | M | read |
| 5 | **`find_one_where` and `exists_where` give a different answer when a shard is down.** Sync stops at the first shard that answers; async asks every shard at once, so a broken shard the sync read never reaches fails the whole read. The async page says "the answer is the sync one" | `src/ekbatan/repository.py:933,969` | `src/ekbatan/aio/repository.py:258,287` (via `_on_each`, `:168`); `docs/runtime/async.md:194`, `reference/runtime/async.mdx:193` | choice (asking all shards at once is a speed-up, not a need) | partly | Keep the speed-up but say plainly that a down shard now fails `find_one_where`/`exists_where`; or, in async, stop waiting once the earliest shard in order has the answer | M | run: two SQLite shards, the second with no table: sync `find_one_where` returned the row and `exists_where` returned `True`; async raised `OperationalError` for both |
| 6 | **A failed read across shards names the shard only in async** (a note on the exception). Sync raises the same error with no note | `src/ekbatan/repository.py:987` | `src/ekbatan/aio/repository.py:207` | choice | yes | Add the same note in the sync scatter reads | L | run: same script - async errors carry `ekbatan: reading shard 1:0 (1:0) failed...`, sync errors carry nothing |
| 7 | **MySQL/MariaDB: `innodb_lock_wait_timeout=10` is gone in async** (sync sets it on the request pool, Java's `sessionVariables`, and a test reads it back) | `ex/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/ekbatan.yaml:27`, test `:300`; MariaDB the same (`ekbatan.yaml:27`) | `ex/fastapi-wallet-rest-uv-async-mysql/src/fastapi_wallet_rest_async_mysql/ekbatan.yaml:30` (`SET time_zone` only); MariaDB `:30` | leftover (aiomysql takes `init_command` as PyMySQL does) | yes | Put `SET SESSION innodb_lock_wait_timeout=10, time_zone = '+00:00'` back on the async request pool and its assertion back in the test | M | read |
| 8 | **The async tests no longer prove UTC against a server that is not on UTC.** Sync moves the server default to Tehran, checks the control, then checks every pool; async only checks the pools on a UTC server, so it would pass without the setting | `ex/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/test_fastapi_wallet_rest_pg.py:300` (and the mysql, mariadb twins) | `ex/fastapi-wallet-rest-uv-async-pg/src/fastapi_wallet_rest_async_pg/test_fastapi_wallet_rest_async_pg.py:264` | leftover | yes | Port `test_every_pool_runs_in_utc_even_on_a_server_that_does_not` to the three async twins | M | read |
| 9 | **Other REST tests dropped in async**: the bad-deposit 422 test; the recipient test no longer checks the column or the API; the currency test no longer checks the API | `ex/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/test_fastapi_wallet_rest_pg.py:172,214` | `ex/fastapi-wallet-rest-uv-async-pg/src/fastapi_wallet_rest_async_pg/test_fastapi_wallet_rest_async_pg.py:175` | leftover | yes | Port them; none needs anything async can't do | L | read (test names diffed per pair) |
| 10 | **Different repository helpers**: sync REST and job-worker repositories carry `find_on_shard`, `exists_on_shard`, `erase` (and more) that nothing calls - copied from the SQLite example; the async twins dropped them | `ex/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/repository.py:99-169`; `ex/job-worker-uv-pg/src/job_worker_pg/repository.py` | `ex/fastapi-wallet-rest-uv-async-pg/src/fastapi_wallet_rest_async_pg/repository.py:93-122`; `ex/job-worker-uv-async-pg/.../repository.py` | leftover (sync side) | yes | Give both twins the same helpers - keep only what each example uses | L | read |
| 11 | **`RaiseAlertAction` moved file** in the async REST twins (into `handlers.py`); it stays in `actions.py` in sync and in every other async twin | `ex/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/actions.py:89` | `ex/fastapi-wallet-rest-uv-async-pg/src/fastapi_wallet_rest_async_pg/handlers.py:33`; but `ex/fastapi-wallet-rest-uv-async-sharded-pg/.../actions.py:114`, `ex/wallet-service-uv-async-sqlite/.../actions.py:86` | choice | yes | Move it back to `actions.py` | L | read |
| 12 | **The lock provider is picked from a dict by dialect** in async (`LOCKS[registry.dialect]`, one `app.py` for all three databases; its comment names PostgreSQL and MariaDB but not MySQL). Sync names its provider | `ex/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/app.py:173` | `ex/fastapi-wallet-rest-uv-async-pg/src/fastapi_wallet_rest_async_pg/app.py:84-86` | choice | yes | Name `AsyncPostgresKeyedLockProvider` (etc.) directly, as sync does | L | read |
| 13 | **Async READMEs drop content that applies to both**: how `schema.py` is generated, the two forced types, what differs between the databases, "what to look at", one schema change per migration on MySQL. The Makefiles are the same, so async generates `schema.py` the same way | `ex/fastapi-wallet-rest-uv-pg/README.md:10,73,130`; `ex/wallet-service-uv-sqlite/README.md` ("What to look at") | `ex/fastapi-wallet-rest-uv-async-pg/README.md:25` onward | choice | yes | Keep those sections in the async READMEs, or one line pointing to the sync README for them | L | read |
| 14 | **Sync PostgreSQL tests import the deprecated `testcontainers.postgres`**; async uses `testcontainers.community.postgres` | `ex/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/test_fastapi_wallet_rest_pg.py:26` (12 sync test files) | `ex/fastapi-wallet-rest-uv-async-pg/.../test_fastapi_wallet_rest_async_pg.py:25` (12 async test files) | leftover (sync side) | yes | Use `testcontainers.community.postgres` in the sync tests | L | run: importing `testcontainers.postgres` raises a `DeprecationWarning` |
| 15 | ~~**The `tm.transaction()` sample writes the timestamp differently**: async passes `to_storage(clock.now())`, sync passes the aware `clock.now()` into a zone-less column. asyncpg refuses the aware value, so async had to; but the sync sample is the wrong one by the UTC rule~~ **already resolved** (`EKB-0384`-`EKB-0388`, before this list was acted on): `to_storage` is gone; both tabs pass `clock.now()`, naive UTC, as is, and one paragraph explains it for both | `reference/transaction-manager.mdx:55`, `docs/database/transaction-manager.md:52` | `reference/transaction-manager.mdx:71,79`, `docs/database/transaction-manager.md:67,72` | forced on async by the driver; the sync sample is a leftover | yes | Use `to_storage(clock.now())` in the sync tab too, and drop the "the async tab passes..." paragraph | M | run (PostgreSQL 17): psycopg on a Tehran session stored `15:30` for `12:00 UTC`; with `to_storage`, `12:00`; asyncpg refused the aware value |
| 16 | ~~**The config-file sample imports from different places**: `from ekbatan.persistence import DatabaseRegistry` (sync) vs `from ekbatan.aio import AsyncDatabaseRegistry` (async)~~ **fixed** (`EKB-0390`): the sync tab imports `from ekbatan import DatabaseRegistry`, as the async tab imports from `ekbatan.aio`; website and `docs/` | `reference/config-files.mdx:20`, `docs/database/config-files.md:12` | the async tab of the same sample | choice | yes | `from ekbatan import DatabaseRegistry` in the sync tab | L | read |
| 17 | **Keyed locks are exported from `ekbatan` but their async twins are not exported from `ekbatan.aio`** (only from `ekbatan.aio.locks`) | `src/ekbatan/__init__.py:192,206` (`KeyedLockProvider`, `Lease`, `PostgresKeyedLockProvider`, ...) | `src/ekbatan/aio/__init__.py:37` | choice | yes | Export the async lock classes from `ekbatan.aio`, or stop exporting the sync ones from `ekbatan` | L | read |
| 18 | ~~**The sync job worker says it calls `stop()`; it calls `close()`.** The async README says `close()`~~ **fixed** (`EKB-0391`): the sync README and `app.py` docstring say `close()`, which `__main__` calls | `ex/job-worker-uv-pg/README.md:57`, `src/job_worker_pg/app.py:18`; code `__main__.py:85` | `ex/job-worker-uv-async-pg/README.md` | leftover (sync side) | yes | Say `close()` in the sync README and docstring | L | read |
| 19 | **One url names the sync driver and async connects with another.** `ekbatan.yaml` holds `postgresql+psycopg://`; the async registry connects to it with asyncpg (`mysql+pymysql://` with aiomysql, `sqlite://` with aiosqlite) | `src/ekbatan/persistence.py` `engine_for` | `src/ekbatan/aio/persistence.py` `async_url`, `async_engine_for` | choice, documented: one config file serves the sync jobs and the async requests | no | Keep; it is said in every async `ekbatan.yaml` and README | M | read |

## Forced by async - no action

These differ only because async needs them. Listed so the next reader does not have to check
them again.

- `await`, `async def`, `async with`, `async for`; `Async*` classes; `aclose()` instead of
  `close()` on registries and transaction managers; the `*-async` install extras.
- The async event handler gets its own registry, executor and pool (`handlersConfig`) on a
  `HandlerLoop`, closed on that loop: an async pool belongs to the loop it is used on.
- The jobs (db-scheduler, fan-out, dispatch) stay sync, so the async examples build a sync
  `events_registry` on `jobsConfig` (one per shard in the sharded twin) and stop the jobs with
  `asyncio.to_thread`.
- Tests reach the app's async objects through `client.portal` or a `HandlerLoop`.
- libpq `options` turned into asyncpg `server_settings`; asyncpg refusing aware datetimes.
- `asyncio.gather` instead of a `ThreadPoolExecutor` in the "reads at the same time inside
  `perform`" samples; `(await conn.rows(stmt)).fetch(...)` (the statement runs on `rows`, in
  both modes).
- Sample comments and line wrapping that differ only because of the longer async names.

## Checked and found the same

- **The executor**: the same scenario (open, deposit, deposit to a missing wallet, an action
  that raises after staging, a refused amount) on `ActionExecutor` and `AsyncActionExecutor`
  over SQLite gave the same results, the same exceptions and the same 20 log lines - logger,
  level and text. (run)
- **Signatures**: every method of every sync/async class pair (repositories, action, executor
  and builder, registry and builder, transaction manager, transaction, action spec, lease, lock
  providers and builders, `engine_for`, `run_action`) has the same parameters, order and
  defaults; only the types are the async ones and the waiting methods are coroutines. (run)
- **`dbs()` / `readonly_dbs()`**: one shard at a time and lazy in both. (run)
- **Pool timeout**: the same `sqlalchemy.exc.TimeoutError` in both. (run)
- **Every Sync | Async sample** (80 on the website, 56 in `docs/`): once the forced
  differences are taken out, what is left is rows 1, 2, 3, 15 and 16 above, and wording.

## Side notes

- `tests/unit/test_async_engine.py:196` checks only that the public **names** match, for twelve
  class pairs. It does not check parameters, `Action`/`AsyncAction`, the model and entity
  repositories, `Transaction`/`AsyncTransaction`, `Db`/`AsyncDb`, the keyed lock base class,
  the persisters, `run_action`/`async_run_action`, or what each package exports (row 17 would
  have been caught). The signature run above found no gap today.
- `ex/fastapi-wallet-rest-uv-pg/README.md:161` (and mysql, mariadb) says "Fifteen cases"; the
  sync test has sixteen. Not an async difference.
