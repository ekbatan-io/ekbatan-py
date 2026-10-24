# Event tables fixed, as Java - ledger (Dracula 14)

The brief (the user, 2026-10-24): "it should be exactly similar to java". Java names its two
event tables itself - `eventlog.events` and `eventlog.event_notifications` - and nothing passes
them: not the executor (its default persister), not the fan-out job, not the handling job. The
user chose **option 2** for where they live:

- **PostgreSQL:** `eventlog.events`, `eventlog.event_notifications` - a schema in each database,
  exactly Java's.
- **MySQL, MariaDB:** `events`, `event_notifications` in the application's own database. There a
  schema is a database, and an `eventlog` one would be shared by every database on the server:
  shards and other applications would read each other's events (the fan-out query is
  `delivered = false AND event_type IN (...)`, in Java too).
- **SQLite:** the same two tables, in its one file (no schemas).

Judgement calls go in `DECISIONS-TO-REVIEW.md` under "Event tables fixed (EKB-0520-)".
Status: `todo` / `done` / `n/a`. A row is `done` only when a run shows it.

## Design

- `outbox_table(metadata)` and `notifications_table(metadata)` always describe
  `eventlog.events` and `eventlog.event_notifications`: no `name`, no `schema` - fixed, as Java's.
- On MySQL, MariaDB and SQLite, `eventlog` is mapped onto the connection's own database with
  SQLAlchemy's `schema_translate_map`: on the engines `engine_for` / `async_engine_for` build,
  and on every connection ekbatan opens (so an engine built by hand is covered too).
- No table is passed anywhere: `SingleTableJsonEventPersister(registry)`, the executors lose
  `events=`, `EventFanoutJob` / `EventHandlingJob` builders lose `.events()` /
  `.notifications()`, `EventFanout` / `EventDispatch` and the event repositories lose their table
  parameters. `event_persister(...)` stays, as Java's `eventPersister(...)`.

## Framework

| # | item | status | proof |
| --- | --- | --- | --- |
| F1 | `schema.py`: fixed `eventlog` tables, the translation map | done | unit `test_the_event_tables_are_named_as_java_names_them`, `test_eventlog_is_a_schema_on_postgresql_and_the_database_itself_elsewhere` |
| F2 | translation on engines (`engine_for`, `async_engine_for`) and on ekbatan's connections | done | integration `test_a_registry_on_an_engine_built_by_hand_finds_them_too` (sqlite, mysql, mariadb); a hand-built sync and async SQLite engine read `eventlog.events` with no error |
| F3 | persisters (sync, async) and their repository take no table | done | `test_registries` (default persister over `eventlog.events`), the suite |
| F4 | executors (sync, async) lose `events=` | done | the suite, both modes |
| F5 | event repositories, `EventFanout`, `EventDispatch` take no table | done | `test_fanned_out_notifications_are_beside_the_events` x4, the suite |
| F6 | `EventFanoutJob` / `EventHandlingJob` and builders take no table | done | `test_the_builder_says_what_it_is_missing`: needs only `['handlers']` |
| F7 | `emit_ddl` (PostgreSQL: `CREATE SCHEMA IF NOT EXISTS eventlog`), `migrate_registry` | done | unit `test_emit_ddl_creates_the_schema_on_postgresql_and_no_schema_elsewhere`; `create_all` makes the schema (a `before_create` hook) |
| F8 | schemagen: framework tables read from `eventlog` on PostgreSQL, written as `outbox_table(metadata)` | done | the schemagen suites, x4 |

## Tests

| # | item | status | proof |
| --- | --- | --- | --- |
| T1 | the suite's migrations regenerated (PostgreSQL: `eventlog`) | done | `tests/migrations/postgres/V0001__schema.sql`; mysql and sqlite unchanged |
| T2 | every test that passed a table updated | done | 13 files; the incomplete-table refusal test removed with the parameter it guarded |
| T3 | new: rows land in `eventlog.*` on PostgreSQL and in the app's database elsewhere (raw SQL), x4 | done | `tests/integration/test_eventlog_location.py` green x4; red on the old code for PostgreSQL (and the hand-built-engine test on MySQL) |
| T4 | new: jobs and executor built with no table; a hand-built engine is translated too | done | as F2, F6 |
| T5 | unit + all four databases + psycopg async + gevent green | done | unit; `--dialects=sqlite`; `--dialects=postgres,mysql,mariadb` (3 PostgreSQL failures - index tests asking the inspector for `events` unqualified, and a schemagen test writing an index on it - fixed and re-run x4); `--async-driver=psycopg_async`; `gevent.monkey` |

## Examples

| # | item | status | proof |
| --- | --- | --- | --- |
| E1 | wiring: no `event_persister(...)` line, no `.events()` / `.notifications()` | done | the 14 uv sources edited, `scripts/sync-examples.py` wrote the pip and Poetry copies (`--check`: in step) |
| E2 | PostgreSQL examples' migrations create `eventlog` and the tables in it | done | 10 uv migration files (then synced); hand-written SQL in their tests says `eventlog.events` |
| E3 | every example run | done | the 14 uv and 14 pip examples (`make -C <example>`: schema from a container, the test suite on the installed wheel) and both SQLite services end to end (`make run`); the first run failed 12 PostgreSQL examples whose own tests empty `events` by name - fixed to `eventlog.events`, all 12 green on the re-run. The 14 Poetry examples were not run: Poetry is not installed here; their `src/` is the uv examples', byte for byte (`sync-examples.py --check`) |

## Docs

| # | item | status | proof |
| --- | --- | --- | --- |
| D1 | every page that passes the tables or names where they live, docs and website | done | dialect pages, schema-vs-database, tables, outbox, wiring, event handler, async, migrations, schemagen, beside-java, consuming-events; the PostgreSQL move (`ALTER TABLE ... SET SCHEMA`, run on PostgreSQL); build, links, sync, check-api green |
| D2 | `DRACULAS.md`: 14 fixed, 2 updated | done | and the A, B, C detail files |
