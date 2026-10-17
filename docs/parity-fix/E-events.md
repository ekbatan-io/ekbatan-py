# Parity fix, group E: events

Items 11, 12, 17, 18, 19, 20 of the importance table. Rule: match Java (see
[the ledger](../PARITY-FIX-LEDGER.md)). Scan rows: [D-events](../behaviour-parity/D-events.md).
Commits `EKB-0270`..`EKB-0276`.

| Item | Scan row | What | Decision | State | Commit | Evidence |
|---|---|---|---|---|---|---|
| 17 | 22, 23 | event repository reads join the open transaction | as Java: `find_undelivered` and `find_due` read outside it; the outbox repository's reads join, as Java's do, so it stays | done | `EKB-0270` | `test_find_undelivered_does_not_join_an_open_transaction`, `test_find_due_does_not_join_an_open_transaction` fail on the old code (all 4 dbs); probe `t.find_due_sees_own_uncommitted: 0`, `t.find_undelivered_sees_own_uncommitted: 0`, `t.undelivered_after_mark_in_same_tx: 1` now match Java |
| 11 | 25 | replica read at the opposite step | as Java: fan-out reads the replica, handling reads the primary | done | `EKB-0271` | `test_fan_out_reads_the_replica_and_handling_reads_the_primary` fails on the old code (all 4 dbs); probe `r.find_undelivered: 0`, `r.find_due: 1`, `r.fanout.any: false`, `r.handling.calls: 1` now match Java |
| 19 | 5, 19, 20 | new notification due at fan-out time | as Java: `next_retry_at` = the event's `event_date` | done | `EKB-0272` | `test_a_new_notification_is_due_at_its_event_s_date_not_at_fan_out_time` fails on the old code (all 4 dbs); probe `f.s0.*`, `f.s1.*`, `f.after.*`, `f.late.e3_row`, `c.future_event.*`, `c.order.*` now match Java |
| 20 | 18 | failing row expires one retry late | as Java: expire as soon as the next retry would land past the deadline | done | `EKB-0273` | `test_a_failing_row_expires_as_soon_as_its_next_retry_would_land_past_the_deadline` fails on the old code (all 4 dbs); probe `x.step1`-`x.step5` (no `x.step6` any more) and `x.near_deadline.first_failure` now match Java. `x.boundary.*` still differ: that is scan row 16 (exactly at the edge), not in items 1-44 |
| 18 | 21 | one failed outcome write rolls back the batch | as Java: each outcome group its own auto-committed statement, in Java's order | done | `EKB-0274` | `test_a_failed_outcome_write_keeps_the_outcomes_already_written` fails on the old code (all 4 dbs); probe `t.outcome_write_failed.ok_row: SUCCEEDED`, `ok_calls_after_two_rounds: 1` now match Java |
| 12 | 29 | hand-built handling job never runs (Dracula 11) | as Java: always `FixedDelay(poll delay)`, default 1 s; the job no longer reads `handling.enabled` - the wiring does, as Java's DI does | todo | | |
