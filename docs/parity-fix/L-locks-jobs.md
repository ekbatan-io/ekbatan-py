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
| 31 | 4, 8, 18 | Redis: Redisson 3.39.0's hash layout, its two Lua scripts verbatim, its unlock latch and its `0` message on `redisson_lock__channel:{key}`; waiters subscribe, wait against a deadline | done | `EKB-0267` | new tests `test_the_lock_is_a_redisson_hash`, `test_a_release_publishes_redissons_unlock_message`, `test_a_redisson_holder_keeps_a_python_waiter_out_until_it_unlocks`, `test_a_backend_failure_on_acquire_is_reported_as_one` fail before, pass after; probes: `redis.name.*: hash`, `redis.wait_5000ms: 5000`, crossport both ways true incl. `redis.wake.java_woken_under_3s` and `python_woken_under_3s` (5-min TTL, 15 s wait: only the message can wake) |
| 34 | 30 | waiting `acquire` cannot be interrupted: a limit. No cancel flag (new API) and no async exception (a hack); the docs say so and point to `try_acquire` with a bounded wait | limit | `EKB-0268` | probe `interrupt_waiting_acquire: no-api` stays, as recorded; docs: keyed-locks page (and website), Dracula 36, DECISIONS |
| 35 | 47 | `stop()` cannot interrupt a job past `shutdown_max_wait`: a limit. Return after one wait (when Java returns for an interrupted job), no second wait; log "Cannot interrupt executor service threads in Python" where Java logs its interrupt; jobs page corrected (it said the wait is doubled) | limit | `EKB-0269` | `test_and_names_what_did_not_finish` (db-scheduler) checks the new line, fails before, passes after; probe `j8b` unchanged: both return at 1 s |

## Verification, on the last commit

- `uv run pytest --dialects=all`: 2417 passed, 71 skipped.
- `make test-gevent`: 1270 passed, 45 skipped; the lock suites (SQL on PostgreSQL, Redis) also
  pass under gevent.
- `make examples`: all 21 pass, and the SQLite walk-through runs.
- `make lint`: ruff and mypy clean; the docs link check flags only the other groups' ledgers,
  which exist in their own worktrees.
- Website built (54 pages); the new anchor `#mysql-rounds-the-wait-to-whole-seconds` resolves.
- Probes rerun on group L's own containers (ports 571xx): `locks`, `wait`, `jobs` and `crossport`
  (Java and Python against one Redis, both directions).

## Item 31, pinned to real Redisson (`EKB-0311`)

Asked by the user on 2026-10-18 ("does it have enough tests?"): the suite only compared this port
with its own copy of Redisson's scripts, so a wrong copy would have passed. Two tests now compare
it with a recording of real Redisson 3.39.0, made once under Redis `MONITOR` while Ekbatan's Java
provider ran `tests/fixtures/redisson/RedissonRecordingTest.java`:

- `test_the_scripts_are_redissons_own` - the Lua this port sends is the Lua Redisson sends.
- `test_redis_sees_from_this_port_exactly_what_it_saw_from_java` - the same scenarios, every
  command Redis runs (the scripts' own `hincrby`, `pexpire`, `del`, `PUBLISH`, the latch) equal,
  values included.

Neither needs Java. Planted faults - one space added to the lock script, unlock message `1`,
latch 13 000 ms - each fail them; restored, all 41 Redis tests pass.
