# Area E: distributed jobs and keyed locks

Part of the [behaviour parity ledger](../BEHAVIOUR-PARITY-LEDGER.md). Run on 2026-10-17.

**Scope.** Java `ekbatan-distributed-jobs`, `ekbatan-keyed-lock-redis` and the PostgreSQL, MySQL
and MariaDB lock providers in `ekbatan-core` (`io.ekbatan.core.concurrent`), against Python
`distributedjobs/`, `locks/` and the job names in `events/jobs.py`. Every row was run in both
ports against the same containers: PostgreSQL (`postgres:latest`), MySQL (`mysql:latest`, 26.7),
MariaDB (`mariadb:latest`, 12.2) and Redis (`redis:7-alpine`), started by hand on fixed ports so
that a Java process and a Python process could also contend on one lock. The lock rows run on all
four backends unless they say otherwise; the job rows run on PostgreSQL. The db-scheduler port
itself, cron (Dracula bug 10) and SIGTERM handling (Dracula 32) were not re-scanned.

Probes and outputs: [`parity-probes/E-jobs-locks/`](../../parity-probes/E-jobs-locks/).
`locks`, `jobs`, `wait` and `crossport` each have a Python script, a Java test
(`ParityE*.java`, run from `ekbatan-integration-tests/keyed-lock-provider/redis` with the extra
dependencies in `java-build.gradle.kts.diff`) and a `.java.out` / `.python.out` pair.

## Table

| # | Behaviour | Java | Python | Verdict | Probe |
|---|---|---|---|---|---|
| 1 | Key to PostgreSQL advisory-lock number (`wallet:abc`, `café:1`) | `-9133792321015084474`, `8236439431089013590` | same numbers | same | locks `pg.name.*` |
| 2 | Key to MySQL / MariaDB `GET_LOCK` name (EKB-0244 re-checked) | `813e4046e5b48a46`, `724db6062814c356` | same names | same | locks `mysql/mariadb.name.*` |
| 3 | Key to Redis key | `ekbatan-lock:wallet:abc` | same | same | locks `redis.name.*` |
| 4 | What is stored at the Redis key | a hash (Redisson) | a hash (was: a string; fixed in EKB-0267) | same | locks `redis.name.*` |
| 5 | Redis key TTL is `maxHold` | 30 s | 30 s | same | locks `redis.ttl_s` |
| 6 | Java holds, Python tries (PG, MySQL, MariaDB, Redis) | - | Python is kept out, gets it after Java releases | same | crossport `java_holds.*` |
| 7 | Python holds, Java tries (PG, MySQL, MariaDB) | Java is kept out, gets it after release | - | same | crossport `python_holds.*` |
| 8 | Python holds, Java tries (Redis) | Java is kept out, gets it after release, and Python's release wakes a waiting Java (was: `RedisException: WRONGTYPE`; fixed in EKB-0267) | - | same | crossport `python_holds.redis.*` |
| 9 | Same thread takes the key again | at once, no backend call | same | same | locks `reenter.fast` |
| 10 | Another thread while held | kept out | kept out | same | locks `reenter.other_thread_can_acquire` |
| 11 | `tryAcquire(0)` re-entry | returns a lease | same | same | locks `try_reenter_zero_wait` |
| 12 | Inner lease closed three times, outer still open | lock stays held | lock stays held (was: released; fixed in EKB-0262) | same | locks `inner_close_x3.*` |
| 13 | Inner lease's `isHeld` after its own close | false (was: true; fixed in EKB-0262) | same | locks `inner_close_once.inner_held` |
| 14 | Outer closed first, inner open | outer `isHeld` false, lock kept | outer `held` false, lock kept (was: true; fixed in EKB-0262) | same | locks `outer_first.*` |
| 15 | One lease closed twice | no error, released once | same | same | locks `close_twice` |
| 16 | `tryAcquire` on a held key, 0 / 0.5 / 1 / 1.5 s (PG, MariaDB) | empty after about the wait | same | same | locks `try_held.*` |
| 17 | MySQL `tryAcquire` with a 500 ms wait | waits 1 s | waits 1 s (was: 0 s; fixed in EKB-0265) | same | locks `mysql.try_held.wait_500ms`, wait `mysql.*` |
| 18 | Redis `tryAcquire` wait length (1 s, 5 s) | 1 s, 5 s | 1 s, 5 s (was: 1.25 s, 5.75 s; fixed in EKB-0267) | same | locks `redis.try_held.*`, wait `redis.*` |
| 19 | `tryAcquire` gets it when the holder lets go in time | yes, under 1.5 s | same | same | locks `try_released_in_time` |
| 20 | `acquire` waits while held, then gets it | yes | same | same | locks `blocking_acquire` |
| 21 | `maxHold` passes: lock freed, lease not held, close is harmless | yes | same | same | locks `expiry.*` |
| 22 | Same thread after its hold expired | takes a fresh lock | same | same | locks `reenter_after_expiry` |
| 23 | Inner `maxHold` is ignored (Redis TTL too) | outer's limit holds | same | same | locks `inner_max_hold_ignored` |
| 24 | Outer `maxHold` ends the inner lease too | yes | same | same | locks `outer_max_hold_governs` |
| 25 | Lease closed by a different thread | releases | same | same | locks `close_from_other_thread` |
| 26 | Provider closed while a lease is open | lease kept, lock held | lease kept, lock held (was: released; fixed in EKB-0266) | same | locks `provider_close.*` |
| 27 | Blank or empty key, zero or negative `maxHold`, negative `maxWait` | refused (`IllegalArgumentException`) | refused (`ValidationError`, a `ValueError`) | same | locks `validate.*` |
| 28 | SQL session killed under a held lock | others get in, lease still says held, close is quiet, provider keeps working | same | same | locks `conn_drop.*` |
| 29 | Redis key deleted under the holder | others get in; old holder's close leaves the new owner's key | same | same | locks `redis.conn_drop.*` |
| 30 | Interrupting a thread waiting in `acquire` | `InterruptedException` | no way to do it (a limit, documented in EKB-0268) | differs | locks `interrupt_waiting_acquire` |
| 31 | PG: `lock_timeout` on the pooled connection after a timed-out bounded wait | `0` | `0` (was: `200ms` stays on it; fixed in EKB-0263) | same | locks `pg.lock_timeout_after_timed_out_try` |
| 32 | PG: `lock_timeout` after a bounded wait that got the lock | `0` | `0` | same | locks `pg.lock_timeout_after_acquired_try` |
| 33 | Database unreachable on `tryAcquire` | `LockAcquisitionException` | `LockAcquisitionError` (was: raw `OperationalError`; fixed in EKB-0264) | same | locks `pg.backend_down.*` |
| 34 | Taking a lock on an asyncio event loop's thread | (nothing like it) | refused, `ValidationError` (EKB-0250) | differs | locks `pg.on_asyncio_loop` |
| 35 | Registry build checks: no jobs, no database, null job, blank or duplicate name, null schedule, zero or negative poll, zero heartbeat, negative shutdown wait | refused; zero shutdown wait accepted | same | same | jobs `validate.*` |
| 36 | `JobsConfig` checks and defaults | zero intervals and negative wait refused, 1 µs accepted, all unset by default | same | same | jobs `config.*` |
| 37 | The row a recurring job writes | `scheduled_tasks`, instance `recurring`, no data, version 3 after one run, next run +60 min | same | same | jobs `j2.row`, `j2.next_run_in_minutes` |
| 38 | `priority` column of that row | NULL | 90 | differs | jobs `j2.row` |
| 39 | A `FixedDelay` job runs once at start, then waits | yes | same | same | jobs `j2.ran_at_start` |
| 40 | Restart: still one row, no early second run | yes | same | same | jobs `j2.restart.*` |
| 41 | Disabled schedule | start succeeds, no row | same | same | jobs `j3.*` |
| 42 | Job runs repeatedly on its schedule | yes | same | same | jobs `j4` |
| 43 | Job that throws | failures counted, `last_failure` set, keeps running | same | same | jobs `j5.*` |
| 44 | Start with `scheduled_tasks` missing | refused, names the job (`IllegalStateException`) | refused, names the job (`ValidationError`) | same | jobs `j6.*` |
| 45 | A row for a task nobody registered | left alone, never picked | same | same | jobs `j7.ghost_row` |
| 46 | `stop()` with a running job, wait 5 s | waits for it (1.5 s) | same | same | jobs `j8a.*` |
| 47 | `stop()` with a job longer than `shutdownMaxWait` (1 s) | returns at 1 s, job interrupted | returns at 1 s, job runs on to the end | differs | jobs `j8b.*` |
| 48 | Three jobs due at once | run in parallel (3) | same | same | jobs `j9` |
| 49 | `heartbeatInterval(1s)` moves `last_heartbeat` | yes | same | same | jobs `j10.*` |
| 50 | Defaults: 10 s polling (first run at 10 s), 10 s heartbeat | yes | same | same | jobs `j11.*` |
| 51 | Default `shutdownMaxWait` | 30 min (db-scheduler's) | 30 min | read: needs a 30-minute run | - |
| 52 | Event job names and poll delay | `ekbatan-event-fanout`, `ekbatan-event-handling`, 1 s | same | read: plain constants; running the jobs needs the event pipeline, area D | - |
| 53 | A Java and a Python registry sharing one `scheduled_tasks` | one row, both ran it, never at the same time | same | same | crossport `shared_job.*` |

## Differences

> **Fixed in EKB-0262:** each lease now remembers it was closed, as Java's `HeldLease` does. Rerun on all four backends: every `inner_close_*` and `outer_first.*` line matches Java.

**12-14. A Python lease has no "closed" mark of its own** (surprise: high). In Java each lease
remembers it was closed, so closing an inner lease again does nothing and its `isHeld` turns
false. In Python closing a lease always counts down the shared hold, so closing an inner lease
twice frees the database lock while the outer code still thinks it holds it, and a second thread
gets in. `held` also answers for the whole hold, not the lease.
Java `KeyedReentrantHolder.java:255-262`. Python `locks/base.py:66-73`, `locks/_holder.py:71`.
Python can match: give `Lease` its own closed flag, checked in `release()` and `held`. A few lines.

> **Fixed in EKB-0263:** the reset runs after every bounded wait. Rerun: `pg.lock_timeout_after_timed_out_try.next_borrower_sees: 0`, as Java.

**31. PostgreSQL: a timed-out wait leaves `lock_timeout` on the pooled connection** (surprise:
high). Java sets `lock_timeout` back to 0 whether or not it got the lock. Python resets it only
when it got the lock, so after a `try_acquire` that timed out, the next user of that pooled
connection inherits a 200 ms `lock_timeout`, and ordinary statements on that engine can start
failing with lock timeouts.
Java `PostgresKeyedLockProvider.java:182-185`. Python `locks/sql.py:175-183`.
Python can match: reset in a `finally`. Cheap.

> **Fixed in EKB-0264:** the connection is drawn inside the `try`. Rerun: `pg.backend_down.is_lock_acquisition_error: true`, as Java.

**33. An unreachable database is not a `LockAcquisitionError`** (surprise: medium). Java wraps a
failed connection as `LockAcquisitionException` ("could not obtain a connection"). Python opens
the connection before its `try`, so SQLAlchemy's `OperationalError` escapes, past any
`except LockAcquisitionError`.
Java `PostgresKeyedLockProvider.java:119-123` (same in MySQL / MariaDB). Python `locks/sql.py:108`.
Python can match: open the connection inside the `try`. Cheap.

> **Fixed in EKB-0267:** Redisson's layout and scripts, and a waiter woken by its unlock message. Rerun: `redis.name.*` say `hash`; crossport `python_holds.redis.*` and the new `wake.*` lines are true in both directions.

**4, 8. Redis: Python stores a string, Redisson a hash** (surprise: medium). Same key name, so
exclusion holds both ways. But when Python holds the key, Java's `tryAcquire` and `acquire` throw
`RedisException: WRONGTYPE` instead of returning empty or waiting. Java holding and Python trying
works as it should. This confirms the "unverified" Dracula in D.
Java `RedisKeyedLockProvider.java:72,90-92`. Python `locks/redis.py:140`.
Python could match by writing Redisson's hash layout with Redisson's Lua scripts. Moderate
cost, and it ties the port to Redisson's internal format.

> **Fixed in EKB-0265:** the seconds go as a `Decimal`. Rerun: `mysql.try_held.wait_500ms.elapsed_bucket_ms: 1000`, as Java.

**17. MySQL: a 500 ms wait lasts 1 s in Java and 0 s in Python** (surprise: medium). Both send
0.5. Connector/J sends it as a decimal, which MySQL rounds to 1 s; PyMySQL sends a float
(`0.5e0`), which MySQL truncates to 0. Checked in the `mysql` client: `GET_LOCK('zz', 0.5)` waits
1.08 s and `GET_LOCK('zz', 5e-1)` 0.07 s. 250 ms is 0 s and 1.5 s is 2 s in both. The Python
docstring's claim that Java gets 0 s too is wrong. MariaDB keeps fractions in both.
Java `MySQLKeyedLockProvider.java:164,179`. Python `locks/sql.py:235`.
Python can match: send the seconds as a `Decimal`. Cheap.

> **Fixed in EKB-0267** (with 4 and 8): the wait runs against a deadline. Rerun: `redis.wait_5000ms.elapsed_bucket_ms: 5000`, as Java.

**18. Redis: Python waits longer than asked** (surprise: low). Python polls every 50 ms and takes
50 ms off the remaining time per round, without counting the time each Redis call takes, so a
5 s `try_acquire` gives up after about 5.75 s. Redisson's wait is on time.
Java `RedisKeyedLockProvider.java:92`. Python `locks/redis.py:122-130`.
Python can match: wait against a monotonic deadline. Cheap.

> **Fixed in EKB-0266:** `close()` does nothing. Rerun on all four backends: `provider_close.lease_held: true`, `other_can_acquire: false`, as Java.

**26. `close()` on a provider frees open locks** (surprise: medium). Known: Dracula 37. Java's
`close()` does nothing. Python's releases every lock the provider holds, on every thread.
Java `KeyedLockProvider.java:125`. Python `locks/sql.py:96-97`, `locks/redis.py:132-133`,
`locks/_holder.py:181`. Python can match: make it a no-op. Cheap.

> **Kept as a limit in EKB-0268:** Python has no thread interrupt. Documented on the keyed-locks page, with `try_acquire` and a bounded wait as the way to stay stoppable.

**30. A waiting `acquire` cannot be interrupted** (surprise: medium). Known: Dracula D "A waiting
`acquire` cannot be interrupted". Java throws `InterruptedException` within one 5 s segment.
Python has no thread interrupt. Java `PostgresKeyedLockProvider.java:88`. Python
`locks/sql.py` `acquire` loop. Python could only add a cancel flag, which is new API.

**47. `stop()` does not interrupt a job that runs past `shutdownMaxWait`** (surprise: medium).
Known: Dracula 35. Both return after the wait. Java then interrupts the job; Python leaves it
running to the end, and it records its success after the scheduler has stopped.
Java db-scheduler `Executor` / `ExecutorUtils.shutdownNow`. Python `db-scheduler` `executor.py`.
Python cannot interrupt a thread; no match.

**38. Python writes `priority`** (surprise: low). Known: Dracula D "The port always writes
`priority`". Java leaves it NULL. Harmless, and a shared table works (row 53).
Python `db-scheduler/src/db_scheduler/jdbc/repository.py`. Python could match; leaving it is
safer.

**34. A lock on an asyncio loop is refused** (surprise: low, by design). Known: Dracula bug 3,
fixed by EKB-0250. Java has nothing like an event loop thread. No action.

## Totals

53 rows: 40 same, 11 differs, 2 read (rows 12-14 fixed in EKB-0262; at the scan, 37 same and 14 differ). Of the 14 differing rows, 5 are already-known Draculas
(26, 30, 34, 38, 47) and 2 confirm a Dracula that had never been run (4, 8: Redis key layout).
The other 7 are new: the lease close mark (12-14), the MySQL 500 ms wait (17), the Redis wait
overshoot (18), the PostgreSQL `lock_timeout` leak (31) and the unwrapped connection error (33).
