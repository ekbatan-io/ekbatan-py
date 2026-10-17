# Parity fix, group T: transactions, actions, config, repository

Part of [the parity-fix ledger](../PARITY-FIX-LEDGER.md). Items are the numbers of the importance
table of 2026-10-17. Rule: match Java. Commits `EKB-0300`..`EKB-0309`.

Probe container for this group: PostgreSQL on port 57501 (role `probe`, the scan's marker
password), databases `testdb` and `replicadb`; the Java probes ran in a throwaway Java worktree.

| Item | What | Decision | State | Commit | Evidence |
|---|---|---|---|---|---|
| 3 | Two managers for one shard share one transaction | key the open transaction by manager, as Java's one `ScopedValue` per manager | done | `EKB-0300` | `test_two_managers_for_one_shard_do_not_share_a_transaction` fails on the old code; probe `tx.two_managers_same_shard.*` now `empty` / `replicadb`, as Java |
| 4 | A transaction inside a transaction joins the outer one (Dracula 44) | open a second transaction on a second connection, as Java; copy Java's "do not nest" warning | done | `EKB-0301` | four `test_a_nested_transaction_*` tests fail on the old code; probe `tx.nested.*` now as Java (second connection, sees nothing, inner kept after outer rollback, inner failure rolled back, blocked on the outer's lock). Framework callers checked: the executor, fan-out and dispatch each open one transaction and nest none; repositories and the outbox still join through `connection()` / `tx_db_else_db`, as Java's `txDbElseDb` |
| 5 | An action executed inside the caller's transaction joins it | follows from 4: the executor's transaction is its own | done | `EKB-0301` | `test_an_action_run_inside_an_open_transaction_commits_on_its_own` fails on the old code; B probe `s43.*` (see 22/23 row for the rerun) |
| 22 | An action built with another clock is refused (Dracula 12) | accept it, as Java | done | `EKB-0302` | `test_an_action_holding_a_different_clock_is_accepted_as_in_java` and `test_an_action_with_another_clock_reaches_the_executor_as_in_java` fail on the old code; B probe `s32.action_clock_differs_from_executor_clock: none`, as Java |
| 23 | No event persister: refused (Dracula 14) | build the default one, as Java (builder and constructor, over `outbox_table(MetaData())`) | done | `EKB-0302` (one commit with 22: both are the executor's construction) | `test_an_executor_without_an_event_persister_gets_the_default_one` fails on the old code; B probe `s37.no_event_persister: none`, as Java; `s43.*` 1 / 1 as Java (item 5) |
| 27 | YAML policy `useDefaultShard` refused | normalise as Java: keep only letters, any case; empty or `None` is the default - in the file loader, `ShardingConfig`, its builder and the registry (rows 12, 13 and 17 follow) | done | `EKB-0303` | `test_unknown_shard_policy_is_read_as_java_reads_it` and `test_the_policy_in_a_file_is_read_as_java_reads_it` fail on the old code (15 cases); pure probe `policy.*` now all as Java (`useDefaultShard`, `padded_fail`, `empty_string`, `null_value`, `config_builder_null`) |
| 28 | Pool of 3 holds 1 connection after one query (Dracula 10) | fill to `pool_size` in the background from the moment `engine_for` builds the pool, and refill after a connection is thrown away, as Hikari's `minimumIdle` default (= the maximum); a filler thread per pool, stopped by `dispose()` | done | `EKB-0305` | `test_a_pool_fills_to_its_size_without_being_asked`, `test_a_pool_refills_after_a_connection_is_thrown_away` fail on the old code; probe `pool.open_connections_after_one_query: 3`, as Java |
| 29 | Wrong password fails at once; Java retries for 30 s | a caller whose connection cannot be opened waits up to `pool_timeout` (30 s, Hikari's `connectionTimeout`) while the filler retries with Hikari's backoff, then gets the driver's error; each attempt logged at DEBUG without the password | done | `EKB-0305` (one commit with 28: the same pool) | `test_a_wrong_password_is_retried_until_the_pool_timeout_and_never_logged` fails on the old code; probe `red.wrong_password.seconds_to_fail: about 30`, as Java; `red.all_captured_logs.marker: no` with every logger at DEBUG |
| 36 | `add_all(None)` and friends do nothing | refuse with Java's messages: "Models collection cannot be null", "Model cannot be null", "Model in collection cannot be null" ("Entities"/"Entity" on an entity repository), as `ValidationError` like the port's other argument checks; also the single-row calls (A row 10) | done | `EKB-0304` | `test_none_is_refused_with_javas_message_and_nothing_is_written` (10 cases) and `test_an_entity_repository_names_entities` fail on the old code; A probe `*.null*.msg` lines now Java's word for word on PostgreSQL, MySQL and MariaDB |

## Measured before deciding

Hikari 7.0.2, as Ekbatan's `ConnectionProvider.hikariConnectionProvider` builds it, on PostgreSQL
16 (a temporary JUnit test in the Java worktree, server log with `log_connections=on`):

| what | measured |
|---|---|
| settings | `connectionTimeout` 30000, `minimumIdle` 3 = `maximumPoolSize` 3, `idleTimeout` 600000 (never applies when the two are equal), `maxLifetime` 1800000, `keepaliveTime` 120000, `validationTimeout` 5000, `initializationFailTimeout` -1 |
| open connections after the pool is built, no query | 0 at 0 ms, 2 at 50 ms, 3 at 300 ms, 3 at 2 s |
| wrong password, pool of 1: caller | fails after 30004 ms: `RuntimeException("Failed to acquire connection")` <- `SQLTransientConnectionException` "HikariPool-2 - Connection is not available, request timed out after 30003ms (total=0, active=0, idle=0, waiting=0)" <- the driver's "password authentication failed" |
| wrong password: login attempts on the server | at +0, 10, 20, 40, 80, 160, 320, 640, 1280, 2560 ms apart, then every 5 s - and they go on after the caller has failed, until the pool is closed |
| wrong password: a second caller on the same pool | waits the full 30 s again |

Python after the fix, same database: 1 / 2 / 3 / 3 open connections at 0 / 50 / 300 / 2000 ms;
with `pool_timeout=3`, a wrong password fails after 3.02 s with 9 attempts logged at DEBUG, the
marker password in none of them.

## Full suite

| when | result |
|---|---|
| before any change, `--dialects=all` | 2404 passed, 67 skipped |
| after `EKB-0301`, `--dialects=all` | 2424 passed, 67 skipped |
| `EKB-0305` first try, `--dialects=all` | 12 failed: `test_locks.py` counts `pool.checkedout()`, which counted a connection the filler was still opening. Fixed: those are reported apart |
| with `EKB-0305`, `--dialects=all` | 2496 passed, 71 skipped (the 4 new pool tests skip on SQLite) |
| `make test-gevent`, and the pool, persistence, executor and concurrency tests under gevent on PostgreSQL | 1293 passed, 47 skipped; 37 passed |
| `make examples` (all 21) with `EKB-0305` | exit 0 |
