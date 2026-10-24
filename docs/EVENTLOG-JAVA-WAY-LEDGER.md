# The event tables in an `eventlog` database on MySQL and MariaDB, as Java - ledger

The brief (the user, 2026-10-24): "switch to java way, without any extra question". This reverses
option 2 of [EVENTLOG-LEDGER.md](EVENTLOG-LEDGER.md) for MySQL and MariaDB only:

- **PostgreSQL:** unchanged - `eventlog` is a schema in each database.
- **MySQL, MariaDB:** `eventlog` is a database of its own on the server, holding `events` and
  `event_notifications` - Java's `V0000__create_eventlog_database.sql`
  (`CREATE DATABASE IF NOT EXISTS eventlog;`), then the tables written as `eventlog.events` and
  `eventlog.event_notifications`. The connecting user needs rights on that database too.
- **SQLite:** unchanged - the two tables are in its one file.

The trade-off the user accepted: there is one `eventlog` per MySQL or MariaDB server, shared by
every database on it - two shards or two applications on one server write into the same two
tables.

Judgement calls go in `DECISIONS-TO-REVIEW.md` under "The eventlog database on MySQL and MariaDB
(EKB-0526-)". Status: `todo` / `done` / `n/a`. A row is `done` only when a run shows it.

## Java, read first

| What | Where | What it does |
| --- | --- | --- |
| the migration | `ekbatan-examples/*-mysql`, `*-mariadb`: `V0000__create_eventlog_database.sql`; `ekbatan-integration-tests/local-event-handler/{mysql,mariadb}`: `V0000__create_eventlog_schema.sql` | `CREATE DATABASE IF NOT EXISTS eventlog;`, then `V0001` creates `eventlog.events`, `eventlog.event_notifications` and their indexes |
| the grants | `mysql_init.sql` / `mariadb_init.sql`, mounted into the container | `GRANT ALL PRIVILEGES ON *.* TO 'wallet'@'%'; FLUSH PRIVILEGES;` - the examples' and tests' named user is not root |
| the code | `EventEntityRepository`, `EventNotificationRepository` | `DSL.name("eventlog", "events")` on every dialect; no render mapping, nothing per shard |
| the tests | `MysqlLocalEventHandlerIntegrationTest` | one container per test class, one shard, `TRUNCATE TABLE eventlog.events` between tests |
| sharding | the examples' `compose.yaml` | each shard its own server (`mysql-global` on 3306, `mysql-mexico` on 3307), so each has its own `eventlog` |
| the docs | `multi-database.md` "Schema vs database", `mysql.md`, `mariadb.md`, `tables/events.md`, `complete-project-setup.mdx` step 5a | say why `eventlog` is a database there, show V0000 and the grant |

## Framework

| # | item | status | proof |
| --- | --- | --- | --- |
| F1 | `eventlog_translation`: a map only on SQLite | done | unit `test_eventlog_is_mapped_onto_the_database_only_on_sqlite` - red on the old code, green |
| F2 | `create_all` makes the `eventlog` database on MySQL/MariaDB (`before_create`) | done | the schemagen round trips rebuild a module with `create_all` on MySQL and MariaDB (`test_a_framework_table_s_own_indexes_are_kept...`), green |
| F3 | `emit_ddl`: `CREATE DATABASE IF NOT EXISTS eventlog;` and the tables in it, on MySQL/MariaDB | done | unit `test_emit_ddl_creates_eventlog_first_where_it_is_a_schema_or_a_database` - red on the old code, green; the suite's `mysql-eventlog/` is generated from it |
| F4 | `migrate_registry`: the same | done | `test_registries` on MySQL and MariaDB, green |
| F5 | schemagen reads the event tables from `eventlog` on MySQL/MariaDB too | done | the schemagen suites on MySQL and MariaDB; the four MySQL/MariaDB examples' regenerated `schema.py` carry `EVENTS = outbox_table(metadata)` with its indexes |
| F6 | docstrings and comments that said "eventlog only on PostgreSQL" | done | `schema.py` notes, `persistence._engine_kwargs`, `fetching`, `accounting`, `migrations`, schemagen `_reflect` |

## Tests

| # | item | status | proof |
| --- | --- | --- | --- |
| T1 | the suite's migrations regenerated | done | `tests/migrations/mysql/` holds the logical database's tables, `tests/migrations/mysql-eventlog/` the server's `eventlog` (`V0000`, `V0001`); PostgreSQL and SQLite unchanged; `test_suite_migrations` checks every file |
| T2 | backends: the `eventlog` database once per MySQL/MariaDB server; emptied between tests | done | `MysqlFamilyBackend.ensure_eventlog` / `drop_eventlog`; the first run without it: 30 failures ("Table 'event_notifications' already exists", events seen across databases) |
| T3 | tests where two databases share a server and read events: a server per shard, as Java's examples | done | `Backend.apart`: the sharded fixture, the parity tests' shards, the lagging replica; before it, two events on the global shard instead of one |
| T4 | `test_eventlog_location`: rows in `eventlog.*` on MySQL/MariaDB; red on the old code | done | 4 failures on the old code (MySQL, MariaDB), green after |
| T5 | unit + all four databases + psycopg async + gevent + lint green | done | unit; `--dialects=sqlite`; `tests/integration` on postgres, mysql, mariadb (each its own run); `--async-driver=psycopg_async`; `gevent.monkey`; `make lint` (ruff, mypy over src and the examples, links, divergence, builders, sync, check-api, docs-website-sync); the site builds and `npm run check` passes |

## Examples

| # | item | status | proof |
| --- | --- | --- | --- |
| E1 | the MySQL/MariaDB examples: `V0000__create_eventlog_database.sql`, `V0001` in `eventlog.*`, their tests' SQL | done | the four uv examples (their tests list `V0000`; their outbox reads go through `EVENTS`, so no hand-written SQL changed) |
| E2 | compose and grants (`scripts/sync-examples.py`), copies synced | done | no grant needed: compose, the tests and the suite connect as root (measured: `GRANT ALL PRIVILEGES ON eventlog.* TO ...` is what a login of its own needs); `sync-examples.py --check`: in step |
| E3 | `schema.py` regenerated through the Makefile | done | `make -C <example> schema` x4, each from a container with the migrations applied |
| E4 | the MySQL/MariaDB uv and pip examples run | done | `make -C <example>` for the eight uv and pip MySQL/MariaDB examples, each its installed wheel: 21 passed each. Two needed a second run: uv-mysql, because a `make lint` run alongside swapped its venv's wheel for an editable install mid-test; pip-async-mysql, whose 15 s wait for the startup job ran out while three suites loaded the machine - both green run alone. Poetry is not installed here, so the Poetry copies did not run; `sync-examples.py --check` shows their `src/` is the uv examples' |

## Docs

| # | item | status | proof |
| --- | --- | --- | --- |
| D1 | every page that said "the app's own database on MySQL/MariaDB", docs and website | done | tables, events, event_notifications, MySQL, MariaDB, PostgreSQL, multi-database, migrations, schemagen, outbox, consuming-events, beside-java; the PostgreSQL-era table names left on the learn pages (outbox DDL, getting started, first action, sharding) fixed on the way; the docs' `RENAME TABLE` move and the `eventlog.*` grant run on `mysql:8.4` and `mariadb:11.4`; links, check-api, docs-website-sync, the site build and `npm run check` green |
| D2 | the setup guide: a step for V0000 | done | "The `eventlog` database" under step 5, the tree, step 2's login, step 16's commands; `test_setup_guide` green; the site builds |
| D3 | `DRACULAS.md` #2 and its area files | done | #2 matched on PostgreSQL, MySQL and MariaDB; A5, B, C |
| D4 | `EVENTLOG-LEDGER.md` note, DECISIONS-TO-REVIEW | done | the old section's MySQL/MariaDB rows marked; a new section with the calls |

## Landing

| # | item | status | proof |
| --- | --- | --- | --- |
| L1 | rebased onto main after the DataSource commits (EKB-0525), re-run, fast-forwarded | done | rebased onto `02937bc` (conflicts: `fetching.py` and the hand-built-engine test - both removed by EKB-0524, kept removed; `tests/backends.py` docstring; the tables page paragraph and DECISIONS-TO-REVIEW, both merged). After it: ruff, mypy, unit, `--dialects=sqlite`, postgres, mysql, mariadb, links, divergence, builders, sync, check-api, docs-website-sync, the site build, `npm run check`, and the 28 uv and pip examples (`make -C <example>`, each its installed wheel) - all green; main fast-forwarded |

## After landing

| # | item | status | proof |
| --- | --- | --- | --- |
| A1 | `scripts/check-setup-guide.py` for PostgreSQL, MySQL and MariaDB, uv and pip | done | run on main at `0c18991`, after landing: 12 of 12 ok - each database with uv and pip, sync and async; the ports were free first and nothing was left running |
| A2 | psycopg async and gevent, re-run on main after landing (they ran before the rebase only) | done | on `0c18991`: `make test-psycopg-async` 1336 passed, 15 skipped; `make test-gevent` 2841 passed, 488 skipped (the async runs, which gevent does not mix with); both exit 0 |
