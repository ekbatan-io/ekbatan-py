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
| 2 | Task model | `TaskInstance`, `TaskInstanceId`, `Execution`, `ExecutionComplete`, `Priority`, `SchedulableInstance`, `ScheduledTaskInstance`, `NextExecutionTime` | in progress |
| 3 | Handlers | `CompletionHandler`, `FailureHandler`, `DeadExecutionHandler`, `MaxRetriesExceededListener` | |
| 4 | Tasks | `AbstractTask`, `OneTimeTask`, `RecurringTask`, `CustomTask`, `RecurringTaskWithPersistentSchedule`, `Tasks`, `TaskDescriptor` | |
| 5 | Serializer | `Serializer`, `JavaSerializer` equivalent, `GsonSerializer` equivalent | |
| 6 | Repository | `TaskRepository`, `JdbcTaskRepository`, `JdbcCustomization` per dialect, `QueryBuilder`, `ExecutionUpdate` | |
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
| `cron-utils`, `CronStyle` | `croniter` | the equivalent single-purpose dependency. `CronStyle` (UNIX / QUARTZ / SPRING53) has no croniter counterpart; only the pattern dialect differs, and the port takes croniter's |
| checked/unchecked exception pairs | one exception each | Python has no checked exceptions, so the pairs collapse |
| `Serializable` / `JavaSerializer` | not ported | Java-native serialization has no counterpart and is a deserialization-gadget risk; the port's default is JSON |

## Verification

Every layer must pass, on every commit:

    .venv/bin/python -m pytest        # unit, no database
    .venv/bin/python -m ruff check .
    .venv/bin/python -m mypy

and, where a layer touches SQL, the compatibility suite against real servers in containers:

    .venv/bin/python -m pytest -m compatibility
