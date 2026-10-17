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
| 28 | Pool of 3 holds 1 connection after one query (Dracula 10) | fill to the maximum in the background, as Hikari's `minimumIdle` default | todo | | |
| 29 | Wrong password fails at once; Java retries for 30 s | retry as Hikari for `pool_timeout` (Hikari's `connectionTimeout`, 30 s) | todo | | |
| 36 | `add_all(None)` and friends do nothing | refuse with Java's message "Models collection cannot be null" (and "Model cannot be null") | todo | | |

## Measured before deciding

