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
| 4 | Tasks | `AbstractTask`, `OneTimeTask`, `RecurringTask`, `CustomTask`, `Tasks` | **done** - `RecurringTaskWithPersistentSchedule` and `TaskDescriptor` outstanding |
| 5 | Serializer | `Serializer`, `JsonSerializer` | **done** |
| 6 | Repository | `TaskRepository`, `JdbcTaskRepository`, `JdbcCustomization` per dialect, the schema | **done on SQLite; the container run has never executed - see below** |
| 7 | Resolution | `TaskResolver`, `Resolvable` | |
| 8 | Client | `SchedulerClient`, `ScheduledExecution`, `ScheduledExecutionsFilter`, `TaskSummary` | |
| 9 | Execution | `Executor`, `DueExecutionsBatch`, `FetchCandidates`, `LockAndFetchCandidates`, `ExecutePicked`, `PollingStrategyConfig` | |
| 10 | Scheduler | `Scheduler`, `SchedulerBuilder`, `SchedulerState`, `Waiter`, `HeartbeatConfig`, `HeartbeatState`, `CurrentlyExecuting` | |
| 11 | Events | `SchedulerListener`, `SchedulerListeners`, `ExecutionInterceptor`, `ExecutionChain`, `StatsRegistry` | |
| 12 | Test helpers | `TestHelper`, `ManualScheduler` | |

## Deliberate differences, and why

Recorded as they are made, not at the end.

| Java | here | why |
| --- | --- | --- |
| `cron-utils` | `croniter` | the equivalent single-purpose dependency. `CronStyle` **is** ported - croniter reads all five dialects given `second_at_beginning`, which is the only thing they disagree about that matters here. Quartz's numeric day-of-week (1=SUN) is croniter's 0=SUN, so a *numeric* dow in a QUARTZ pattern is off by one; names (`MON`) and `?` are right |
| `ZoneId.systemDefault()` | `system_default_zone()` | Java resolves a named zone; Python's `astimezone()` gives a fixed offset, which breaks across a DST change. Resolved from `TZ`, then `/etc/localtime`, then the offset |
| checked/unchecked exception pairs | one exception each | Python has no checked exceptions, so the pairs collapse |
| `Serializable` / `JavaSerializer` | not ported | Java-native serialization has no counterpart and is a deserialization-gadget risk; the port's default is JSON |

## Verification

Every layer must pass, on every commit:

    .venv/bin/python -m pytest        # unit, no database
    .venv/bin/python -m ruff check .
    .venv/bin/python -m mypy

and, where a layer touches SQL, the compatibility suite against real servers in containers:

    .venv/bin/python -m pytest -m compatibility


## Verification status, honestly

The SQLite suite is green: 120 passed, 2 skipped (lock-and-fetch, which SQLite cannot do and
which the contract skips deliberately).

**The compatibility suite has never run.** Docker Desktop on this machine publishes ports that
do not answer - an nginx container serves fine inside the container network and `curl
localhost:<published>` returns nothing, so Testcontainers cannot reach anything it starts. That
is an environment fault, not a code one, but it means every claim about Postgres, MySQL and
MariaDB in this module is so far untested. The workflow in `.github/workflows/db-scheduler.yml`
is where it first runs for real.

What that leaves unproven, specifically:

- `SELECT ... FOR UPDATE SKIP LOCKED` on Postgres, both the single-statement and generic forms
- the same on MySQL 8
- that `timestamp` round-trips as UTC on MySQL, which has no zoned type
- that `LargeBinary` holds the serialized data on every dialect
- that the concurrent-pick test still yields exactly one winner when the rows are really locked
