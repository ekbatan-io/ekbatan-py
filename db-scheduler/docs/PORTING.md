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
| SQLite | in-process | 503 passed, 3 skipped |
| PostgreSQL | `postgres:17-alpine` | 504 passed, 2 skipped |
| MySQL | `mysql:8.4` | 504 passed, 2 skipped |
| MariaDB | `mariadb:11.4` | 503 passed, 3 skipped |

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
