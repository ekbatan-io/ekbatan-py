# Porting plan

The order is bottom-up, so every layer is tested against something already proven rather than
against a mock of something not written yet. Each layer is TDD: the Java tests are read first,
turned into Python tests, watched to fail, and only then implemented.

`docs/java-api-inventory.md` and `docs/java-test-inventory.md` are machine-extracted from
kagkarlsson/db-scheduler@e2c911b and are the checklist. 159 main classes, 246 test methods.

## Layers

| # | Layer | Java classes | State |
| --- | --- | --- | --- |
| 1 | Clock | `Clock`, `SystemClock`, `SettableClock` | **done** |
| 1 | Schedules | `Schedule`, `FixedDelay`, `Daily`, `CronSchedule`, `DisabledSchedule`, `Schedules`, the four parsers | **done** |
| 2 | Task model | `TaskInstance`, `TaskInstanceId`, `Execution`, `ExecutionComplete`, `Priority`, `SchedulableInstance` | **done** |
| 3 | Handlers | `CompletionHandler`, `FailureHandler`, `DeadExecutionHandler`, `MaxRetriesExceededListener` | **done** |
| 4 | Tasks | `AbstractTask`, `OneTimeTask`, `RecurringTask`, `CustomTask`, `Tasks` | **done** |
| 5 | Serializer | `Serializer`, `JsonSerializer` | **done** |
| 6 | Repository | `TaskRepository`, `JdbcTaskRepository`, `JdbcCustomization` per dialect, the schema | **done** |
| 7 | Resolution | `TaskResolver`, `Resolvable` | **done** |
| 8 | Client | `SchedulerClient`, `ScheduledExecution`, `ScheduledExecutionsFilter`, `TaskSummary` | **done** |
| 9 | Execution | `Executor`, `DueExecutionsBatch`, `FetchCandidates`, `LockAndFetchCandidates`, `ExecutePicked`, `PollingStrategyConfig` | **done** |
| 10 | Scheduler | `Scheduler`, `SchedulerBuilder`, `SchedulerState`, `Waiter`, `HeartbeatConfig`, `HeartbeatState`, `CurrentlyExecuting` | **done** |
| 11 | Events | `SchedulerListener`, `SchedulerListeners`, `ExecutionInterceptor`, `ExecutionChain`, `StatsRegistry` | **done** |
| 12 | Test helpers | `TestHelper`, `ManualScheduler` | **done** |

## Deliberate differences, and why

Recorded as they are made, not at the end.

| Java | here | why |
| --- | --- | --- |
| `cron-utils` | `croniter` | the equivalent single-purpose dependency. `CronStyle` **is** ported - croniter reads all five dialects given `second_at_beginning`, which is the only thing they disagree about that matters here. Quartz's numeric day-of-week (1=SUN) is croniter's 0=SUN, so a *numeric* dow in a QUARTZ pattern is off by one; names (`MON`) and `?` are right |
| `ZoneId.systemDefault()` | `system_default_zone()` | Java resolves a named zone; Python's `astimezone()` gives a fixed offset, which breaks across a DST change. Resolved from `TZ`, then `/etc/localtime`, then the offset |
| checked/unchecked exception pairs | one exception each | Python has no checked exceptions, so the pairs collapse |
| `ReviveDeadExecution` reads `Instant.now()` | reads `completion.time_done` | same instant on a real clock, and the *right* one on a substituted one. Java's version revives to real time even under its own `ManualScheduler`, so the revived row lands outside the test's timeline |
| `Executors.newFixedThreadPool` | `DaemonThreadPool` | Python joins every non-daemon thread at interpreter exit and has no `Thread.interrupt()`, so the standard pool lets one stuck handler hang the process for ever. Daemon workers abandon it instead - which is the failure the dead-execution handler already exists for |
| `Thread.interrupt()` to end a wait | a sticky flag re-read before waiting | Java's interrupt flag stays on the thread, so a shutdown signalled while the loop was working cannot be missed. Python's `Condition.notify()` is lost if nobody is waiting, so the loop re-reads the shutdown flag - itself sticky - before it sleeps at all |
| `lock.wait(millis)` after a spurious wakeup | waits the *remaining* time | Java re-waits the whole interval, which can silently double a poll period |
| `ExecutorService.shutdownNow()` | cancel queued work only | nothing in Python can interrupt a running thread. `stop()` waits `shutdown_max_wait`, then names what did not finish and leaves it |
| `priority` written only when priority ordering is on | always written | Java leaves the column NULL when priority is disabled, and `ORDER BY priority DESC` then sorts NULLs *first* on Postgres (ahead of HIGH) and *last* on MySQL. Writing a real value means enabling priority later on an existing table behaves the same everywhere |
| no validation of the intervals | `polling_interval` and `heartbeat_interval` must be > 0 | at zero the loops spin: measured ~10,000 polls/second and ~900,000 heartbeat rounds/second. A value that can only hammer the database is refused |
| `DataSource` | SQLAlchemy `Engine` | both are "ask this for a connection"; neither library pools anything itself. `ConnectionSupplier` / `DataSourceConnectionSupplier` / `ExternallyManagedConnection` are internal to db-scheduler's `jdbc` package and not reachable from its builder, so the `Engine` is the whole public seam - as the `DataSource` is there |
| liveness by wall-clock timestamp | the same | inherited, and worth stating: an instance whose clock is ahead of another by more than `heartbeat_interval * missed_heartbeats_limit` will declare that instance's live executions dead. Pinned in both directions by `TestClockSkewBetweenInstances` |
| `Serializable` / `JavaSerializer` | not ported | Java-native serialization has no counterpart and is a deserialization-gadget risk; the port's default is JSON |

## Verification

Every layer must pass, on every commit:

    .venv/bin/python -m pytest        # unit, no database
    .venv/bin/python -m ruff check .
    .venv/bin/python -m mypy

and, where a layer touches SQL, the compatibility suite against real servers in containers:

    .venv/bin/python -m pytest -m compatibility


## Verification status

All four dialects, whole suite, every commit:

| dialect | image | result |
| --- | --- | --- |
| SQLite | in-process | 653 passed, 6 skipped |
| PostgreSQL | `postgres:17-alpine` | 655 passed, 4 skipped |
| MySQL LTS | `mysql:8.4` | 655 passed, 4 skipped |
| MySQL current | `mysql:26.7` | 655 passed, 4 skipped |
| MariaDB | `mariadb:11.4` | 653 passed, 6 skipped |

Every integration test runs twice per dialect, once for each timestamp shape:
`persist_timestamp_in_utc=True` (zone-less columns holding UTC) and `False` (zoned columns,
which is db-scheduler's own default and therefore the shape of a table a Java instance created).
Reading a table in the wrong shape is an hour's silent drift, not an error.

MySQL runs twice on purpose. 8.4 is the LTS most deployments are on; 26.7 is the current release
- MySQL left semantic versioning after 9.4, so the major number jumped to a calendar year. The
two take different branches of the version check in `AutodetectJdbcCustomization`, and only the
26.x lane would catch a check written as `major == 8`.

The skips are the lock-and-fetch contract on the dialects that cannot do
`SELECT ... FOR UPDATE SKIP LOCKED`, and one test that asserts the *refusal* on those - so the
counts differ by exactly the dialects' real capabilities.

What the container runs prove that SQLite alone cannot:

- `SELECT ... FOR UPDATE SKIP LOCKED` in both forms - PostgreSQL's single statement, MySQL 8's
  transaction-scoped one
- that timestamps round-trip as UTC on MySQL and MariaDB, which have no zoned type
- that the schema is one MySQL will actually create - `varchar(100)` keys, `datetime(6)`
- that two schedulers over one table run each of 30 executions exactly once, under real
  concurrency rather than a simulation of it
- that a row picked by an instance that then vanished is revived by another

## Layers

Complete. `docs/FILE-LEDGER.md` has the file-by-file record; `python scripts/ledger.py` counts
it.
