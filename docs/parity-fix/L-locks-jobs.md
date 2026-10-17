# Parity fix group L: locks and jobs

Items 2, 30, 31, 32, 33, 34, 35 of the importance table in
[`PARITY-FIX-LEDGER.md`](../PARITY-FIX-LEDGER.md). Scan rows in
[`behaviour-parity/E-jobs-locks.md`](../behaviour-parity/E-jobs-locks.md). Commits `EKB-0263`..`EKB-0269`.

| Item | Scan row | Decision | State | Commit | Evidence |
|---|---|---|---|---|---|
| 2 | 31 | PostgreSQL: set `lock_timeout` back to 0 after every bounded wait, got or not; discard the connection if that fails (keeping the lock if it was got, as Java) | done | `EKB-0263` | `test_a_timed_out_wait_leaves_no_lock_timeout_on_the_pooled_connection[postgres]` fails before (`200ms`), passes after; probe `pg.lock_timeout_after_timed_out_try.next_borrower_sees: 0` in both ports |
| 30 | 33 | SQL providers: a failed connection is `LockAcquisitionError` ("could not obtain a connection"), as Java | todo | | |
| 32 | 17 | MySQL: send the `GET_LOCK` seconds as a `Decimal`, as Connector/J does; fix the docstring | todo | | |
| 33 | 26 | `close()` on a provider does nothing, as Java's | todo | | |
| 31 | 4, 8, 18 | Redis: Redisson's hash layout, its Lua scripts and its unlock message, so Java and Python share a lock both ways | todo | | |
| 34 | 30 | waiting `acquire` cannot be interrupted: a limit | todo | | |
| 35 | 47 | `stop()` cannot interrupt a job past `shutdown_max_wait`: a limit | todo | | |
