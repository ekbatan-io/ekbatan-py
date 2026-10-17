# Parity fix, group T: transactions, actions, config, repository

Part of [the parity-fix ledger](../PARITY-FIX-LEDGER.md). Items are the numbers of the importance
table of 2026-10-17. Rule: match Java. Commits `EKB-0300`..`EKB-0309`.

Probe container for this group: PostgreSQL on port 57501 (role `probe`, the scan's marker
password), databases `testdb` and `replicadb`; the Java probes ran in a throwaway Java worktree.

| Item | What | Decision | State | Commit | Evidence |
|---|---|---|---|---|---|
| 3 | Two managers for one shard share one transaction | key the open transaction by manager, as Java's one `ScopedValue` per manager | done | `EKB-0300` | `test_two_managers_for_one_shard_do_not_share_a_transaction` fails on the old code; probe `tx.two_managers_same_shard.*` now `empty` / `replicadb`, as Java |
| 4 | A transaction inside a transaction joins the outer one (Dracula 44) | open a second transaction on a second connection, as Java; copy Java's "do not nest" warning | todo | | |
| 5 | An action executed inside the caller's transaction joins it | follows from 4: the executor's transaction is its own | todo | | |
| 22 | An action built with another clock is refused (Dracula 12) | accept it, as Java | todo | | |
| 23 | No event persister: refused (Dracula 14) | build the default one, as Java | todo | | |
| 27 | YAML policy `useDefaultShard` refused | normalise as Java: keep only letters, any case | todo | | |
| 28 | Pool of 3 holds 1 connection after one query (Dracula 10) | fill to the maximum in the background, as Hikari's `minimumIdle` default | todo | | |
| 29 | Wrong password fails at once; Java retries for 30 s | retry as Hikari for `pool_timeout` (Hikari's `connectionTimeout`, 30 s) | todo | | |
| 36 | `add_all(None)` and friends do nothing | refuse with Java's message "Models collection cannot be null" (and "Model cannot be null") | todo | | |

## Measured before deciding

