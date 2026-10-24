# DataSource and AsyncDataSource - ledger

The brief (the user, 2026-10-24): "engine is a very very very bad name, also engine_for() is also
a very very very bad name", then "go with DataSource and AsyncDataSource". `engine_for(config)`
becomes `DataSource.from_config(config)`, `async_engine_for(config)` becomes
`AsyncDataSource.from_config(config)` (in `ekbatan.aio`), and the word "engine" leaves ekbatan's
own API: it stays only for SQLAlchemy's own object, `data_source.sqlalchemy_engine`.

Judgement calls go in `DECISIONS-TO-REVIEW.md` under "DataSource and AsyncDataSource
(EKB-0524-)". Status: `todo` / `done` / `n/a`. A row is `done` only when a run shows it.

## Design

- **One way to build one**, as Java's `ConnectionProvider` (private constructor,
  `hikariConnectionProvider(DataSourceConfig)` the only factory): `DataSource.from_config(config)`.
  Calling the class directly is refused with a `TypeError` that names `from_config`.
- **What it has:** `sqlalchemy_engine` (SQLAlchemy's `Engine`; `AsyncEngine` on the async class),
  `url` (SQLAlchemy's `URL`, which prints its password as `***`), `dialect` (ekbatan's name from
  the config: `postgres`, `mysql`, `mariadb`, `sqlite`), `close()` - `await aclose()` on the
  async class - idempotent, and `with` / `async with`. `repr` shows the url with every credential
  hidden.
- **Only a data source is taken**: `TransactionManager` / `AsyncTransactionManager` (primary and
  replica), the SQL lock providers and their builders (`.data_source(...)`), and
  `job_registry().data_source(...)`. An `Engine`, or the other mode's data source, is refused with
  a `ValidationError` naming the types only. db-scheduler, a separate package, is still handed
  SQLAlchemy's `Engine` - `data_source.sqlalchemy_engine` - by ekbatan.
- **Fork:** a data source records the process that built it; in any other process its engine and
  its `close()` / `aclose()` raise `ForkedProcessError`.
- **Ownership as today:** a transaction manager closes the data sources it was given (Java's
  `close()` does); lock providers and the job registry close nothing.

## Framework

| # | item | status | proof |
| --- | --- | --- | --- |
| F1 | `DataSource` in `ekbatan.persistence`, exported from `ekbatan` | done | unit `test_data_source.py` (27 green); built only by `from_config` (`TypeError` otherwise) |
| F2 | `AsyncDataSource` in `ekbatan.aio.persistence`, exported from `ekbatan.aio`, on the same private core | done | `_DataSourceCore`; the twin's surface pinned in `test_async_engine.py` (`{close, aclose}`); exports twin test |
| F3 | `engine_for` / `async_engine_for` removed, no alias | done | `test_the_names_it_replaces_are_gone` |
| F4 | transaction managers take only data sources; `dialect` defaults to the data source's | done | `test_what_takes_*`, `test_a_transaction_manager_speaks_its_data_sources_dialect`, `..._closes_the_data_sources_it_was_given` |
| F5 | registries build data sources from the layout | done | `test_the_registry_builds_pools_for_the_primary_and_secondary_only` |
| F6 | SQL lock providers and builders (sync, async): `.data_source(...)`, refusals, "data_source is required" | done | `test_what_takes_*`, `test_a_sql_lock_provider_without_a_data_source_is_refused` |
| F7 | `job_registry().data_source(...)`: refusals, "data_source is required"; db-scheduler given `sqlalchemy_engine` | done | `test_what_takes_*`, `test_a_registry_with_no_data_source_is_refused`, job suites |
| F8 | `count_statements` reads the data sources' engines | done | `test_accounting.py` in the SQLite run, both modes |
| F9 | the connection-level `eventlog` mapping (`Db.__init__`, `AsyncDb.__aenter__`): kept or removed by what can still reach it | done | removed - no engine built by hand reaches ekbatan now; `fetching.py` is again its pre-EKB-0520 self; eventlog location tests green |
| F10 | docstrings and comments that meant ekbatan's pool as an "engine" | done | src grep: "engine" left only for SQLAlchemy's own object; the fork message names data sources |

## Tests

| # | item | status | proof |
| --- | --- | --- | --- |
| T1 | `Backend.data_source(name)`; the harness's twin `DataSource` -> `AsyncDataSource` | done | `tests/backends.py`, `tests/modes.py` |
| T2 | every test that used `engine_for`, `async_engine_for`, `.engine(...)`, a hand-built manager, or `.primary` as an engine | done | 40 test files; the hand-built-engine eventlog test removed (unreachable) |
| T3 | new unit tests: built only from a config, nothing printed with a real password, `url`, `dialect`, idempotent close, `with` / `async with`, the twin's surface | done | `tests/unit/test_data_source.py` |
| T4 | new: every taker refuses an `Engine` and the other mode's data source, naming types only | done | 9 takers x 3-4 wrong things; no url or password in any message |
| T5 | new: a data source used or closed in a `fork()` copy is refused, all four databases | done | `test_refusals.py`, two new tests (sync, async), green on SQLite, PostgreSQL, MySQL and MariaDB; the parent's session is untouched |
| T6 | the new tests red on the old code, green on the new | done | on the old code both new modules fail to import; then 10 mutants, one guard each put back, every one RED, restored, green |
| T7 | unit + SQLite + PostgreSQL/MySQL/MariaDB + psycopg async + gevent + lint + mypy | done | unit 2110 collected, green; whole suite on SQLite 3323 (109 skipped, 0 failed); integration on PostgreSQL/MySQL/MariaDB 3717 (47 skipped, 0 failed); PostgreSQL with psycopg's async mode 1351 (15 skipped, 0 failed); whole suite under gevent (488 skipped - the async runs - 0 failed); ruff check/format clean; mypy (src and the uv examples) clean |

## Examples

| # | item | status | proof |
| --- | --- | --- | --- |
| E1 | the 14 uv examples: wiring, fields (`jobs_data_source`, `lock_data_source`), shutdown, tests | done | mypy over src and the uv examples clean; ruff clean; the async examples close the jobs pool once (`events_registry` does) |
| E2 | pip and Poetry copies written by `scripts/sync-examples.py`; `--check` | done | "14 examples x 3 builds, in step" |
| E3 | 14 uv + 14 pip examples (`make -C`), both SQLite services end to end (`make run`); Poetry not installed here | done | 27 of 28 passed in a run three at a time beside the PostgreSQL/MySQL/MariaDB suite; `fastapi-wallet-rest-uv-async-pg` timed out once waiting 15 s for the stipend's first run under that load, then passed alone (21 passed) - its pip twin, the same code, passed in the loaded run. Both SQLite `make run`s exit 0. Poetry not installed: not run |

## Docs

| # | item | status | proof |
| --- | --- | --- | --- |
| D1 | every docs/ page and its website page that builds, passes or closes a pool | done | 16 docs pages and their website twins, plus the two website-only learn pages; check-api 0 wrong, docs-website-sync 34 pairs, 0 below 80% |
| D2 | `DIVERGENCE.md`: the `ConnectionProvider` row and the named-config paragraph | done | and a new chosen section, "A data source wraps SQLAlchemy's engine"; `divergence.py`: every divergence triaged |
| D3 | `DRACULAS.md` #62 and #93, and their area files: the engine part fixed, the rest open | done | A22 and the D lock-provider entry marked "engine part fixed" |
| D4 | `AGENTS.md`: the refusal list and the doors | done | and the module map, and a "A pool is a DataSource" rule |
| D5 | `DECISIONS-TO-REVIEW.md`: the new section | done | "DataSource and AsyncDataSource (EKB-0524-)"; the EKB-0520 row it supersedes is marked |
| D6 | `parity-probes/` scripts (not their recorded `.out` files) | done | 24 sources compile; `round2-N6/locks-jobs/jobs_claims.py` (SQLite) run: its PROBE lines match the recording, plus the SIGTERM warning added in EKB-0498. The rest need the recorded set-up (fixed-port containers, the Java side) and were not run; some were already behind the API before this change (EKB-0520's table parameters) |
| D7 | links, divergence, builders, sync, check-api, docs-website-sync, the website build, the setup guide check | done | links 774 resolve; every divergence triaged; 15 builders, 0 wrong; examples in step; check-api 0 wrong; docs-website-sync 0 below 80%; `npm run build` and `npm run check` exit 0; `check-setup-guide.py --db sqlite --install uv --install pip`: 4 of 4 ok. Not run: its PostgreSQL, MySQL and MariaDB runs (`docker run` on fixed ports 5432/3306, fixed container names it removes with `docker rm -f`, and port 8000 - not safe beside the agent working in parallel) and Poetry (not installed) |
