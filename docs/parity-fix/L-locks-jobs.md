# Parity fix group L: locks and jobs

Items 2, 30, 31, 32, 33, 34, 35 of the importance table in
[`PARITY-FIX-LEDGER.md`](../PARITY-FIX-LEDGER.md). Scan rows in
[`behaviour-parity/E-jobs-locks.md`](../behaviour-parity/E-jobs-locks.md). Commits `EKB-0263`..`EKB-0269`.

| Item | Scan row | Decision | State | Commit | Evidence |
|---|---|---|---|---|---|
| 2 | 31 | PostgreSQL: set `lock_timeout` back to 0 after every bounded wait, got or not; discard the connection if that fails (keeping the lock if it was got, as Java) | done | `EKB-0263` | `test_a_timed_out_wait_leaves_no_lock_timeout_on_the_pooled_connection[postgres]` fails before (`200ms`), passes after; probe `pg.lock_timeout_after_timed_out_try.next_borrower_sees: 0` in both ports |
| 30 | 33 | SQL providers: a failed connection is `LockAcquisitionError` ("could not obtain a connection"), as Java. Redis already wrapped its failures; Java's Redis provider does not (Java bug, recorded) | done | `EKB-0264` | `test_an_unreachable_database_is_a_lock_acquisition_error` (PG, MySQL, MariaDB, marker password absent) fails before, passes after; probe `pg.backend_down.is_lock_acquisition_error: true` in both ports |
| 32 | 17 | MySQL: send the `GET_LOCK` seconds as a `Decimal`, as Connector/J does, with Java's value (0, else ms >= 1 over 1000); fix the docstring and the docs that said MySQL truncates | done | `EKB-0265` | `test_what_a_sub_second_wait_actually_does_on_each_server[mysql]` (now 0.9-1.5 s) and `test_the_get_lock_timeout_is_the_value_java_sends` fail before, pass after; probe `mysql.try_held.wait_500ms.elapsed_bucket_ms: 1000` in both ports; measured: decimal 0.5 -> 1.00 s, float 0.5 -> 0.00 s on MySQL 26.7 |
| 33 | 26 | `close()` on a provider does nothing, as Java's; the holder's `release_all` removed | done | `EKB-0266` | `test_closing_the_provider_leaves_open_leases_held` (SQL, Redis) fails before, passes after; probe `provider_close.lease_held: true` / `other_can_acquire: false` on PG, MySQL, MariaDB and Redis in both ports |
| 31 | 4, 8, 18 | Redis: Redisson's hash layout, its Lua scripts and its unlock message, so Java and Python share a lock both ways | todo | | |
| 34 | 30 | waiting `acquire` cannot be interrupted: a limit | todo | | |
| 35 | 47 | `stop()` cannot interrupt a job past `shutdown_max_wait`: a limit | todo | | |
