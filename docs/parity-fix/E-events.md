# Parity fix, group E: events

Items 11, 12, 17, 18, 19, 20 of the importance table. Rule: match Java (see
[the ledger](../PARITY-FIX-LEDGER.md)). Scan rows: [D-events](../behaviour-parity/D-events.md).
Commits `EKB-0270`..`EKB-0276`.

| Item | Scan row | What | Decision | State | Commit | Evidence |
|---|---|---|---|---|---|---|
| 17 | 22, 23 | event repository reads join the open transaction | as Java: `find_undelivered` and `find_due` read outside it; the outbox repository's reads join, as Java's do, so it stays | done | `EKB-0270` | `test_find_undelivered_does_not_join_an_open_transaction`, `test_find_due_does_not_join_an_open_transaction` fail on the old code (all 4 dbs); probe `t.find_due_sees_own_uncommitted: 0`, `t.find_undelivered_sees_own_uncommitted: 0`, `t.undelivered_after_mark_in_same_tx: 1` now match Java |
| 11 | 25 | replica read at the opposite step | as Java: fan-out reads the replica, handling reads the primary | todo | | |
| 19 | 5, 19, 20 | new notification due at fan-out time | as Java: `next_retry_at` = the event's `event_date` | todo | | |
| 20 | 18 | failing row expires one retry late | as Java: expire as soon as the next retry would land past the deadline | todo | | |
| 18 | 21 | one failed outcome write rolls back the batch | as Java: each outcome group its own auto-committed statement, in Java's order | todo | | |
| 12 | 29 | hand-built handling job never runs (Dracula 11) | as Java: always `FixedDelay(poll delay)`, default 1 s; the job no longer reads `handling.enabled` - the wiring does, as Java's DI does | todo | | |
