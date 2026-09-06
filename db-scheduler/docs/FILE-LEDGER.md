# File ledger

Every Java source file in db-scheduler@e2c911b, and where it went.

The unit of work is **the file**, not a feature. A feature-driven port only finds what the
porter thought to look for - which is how the first version of this port read one of eight DDL
files and shipped a schema MySQL refuses to create. Reading each file whole, and porting it
whole, makes completeness something you can count rather than something you hope for.

**159 source files.** Status is one of:

- **done** - read in full, ported, tested
- **partial** - some of it ported; what is missing is named
- **todo** - not started
- **n/a** - deliberately not ported; the reason is given

| Java file | lines | Python | status |
| --- | --- | --- | --- |
| **`jdbc`** | | | |
| BatchPreparedStatementSetter | 6 | - | n/a - as above |
| ConnectionSupplier | 8 | - | todo |
| DataSourceConnectionSupplier | 24 | - | todo |
| DoInTransaction | 5 | - | todo |
| ExternallyManagedConnection | 20 | - | todo |
| IntegrityConstraintViolation | 7 | - | todo |
| JdbcRunner | 244 | - | n/a - SQLAlchemy is the JDBC layer |
| Mappers | 42 | - | n/a - as above |
| PreparedStatementExecutor | 20 | - | n/a - as above |
| PreparedStatementSetter | 11 | - | n/a - SQLAlchemy binds parameters |
| ResultSetMapper | 6 | - | n/a - as above |
| RowMapper | 6 | - | n/a - as above |
| SQLRuntimeException | 16 | - | n/a - SQLAlchemy raises its own |
| ThreadLocalTransactionContextProvider | 16 | - | todo |
| TransactionContext | 11 | - | todo |
| TransactionContextProvider | 6 | - | todo |
| TransactionManager | 87 | - | todo |
| **`scheduler`** | | | |
| ClientEvent | 37 | - | todo |
| Clock | 5 | `clock.py` | done |
| CurrentlyExecuting | 33 | - | todo |
| DueExecutionsBatch | 51 | - | todo |
| ExceptionUtils | 12 | - | todo |
| ExecutePicked | 148 | - | todo |
| ExecutionTimeAndId | 37 | - | todo |
| Executor | 83 | - | todo |
| ExecutorUtils | 48 | - | todo |
| FetchCandidates | 134 | - | todo |
| HeartbeatConfig | 13 | - | todo |
| HeartbeatState | 59 | - | todo |
| ImmediateCheckForDueExecutions | 34 | - | todo |
| LockAndFetchCandidates | 96 | - | todo |
| PollStrategy | 2 | - | todo |
| PollingStrategyConfig | 42 | - | todo |
| Resolvable | 19 | - | todo |
| RunAndLogErrors | 23 | - | todo |
| RunUntilShutdown | 45 | - | todo |
| ScheduledExecution | 71 | - | todo |
| ScheduledExecutionsFilter | 55 | - | todo |
| Scheduler | 404 | - | todo |
| SchedulerBuilder | 306 | - | todo |
| SchedulerClient | 365 | - | todo |
| SchedulerName | 41 | - | todo |
| SchedulerState | 39 | - | todo |
| StreamUtils | 22 | - | todo |
| StringUtils | 13 | - | todo |
| SystemClock | 8 | `clock.py` | done |
| TaskRepository | 66 | `jdbc/repository.py` | partial |
| TaskResolver | 79 | - | todo |
| TaskSummary | 12 | - | todo |
| Waiter | 77 | - | todo |
| **`scheduler/concurrent`** | | | |
| LoggingRunnable | 15 | - | todo |
| **`scheduler/event`** | | | |
| AbstractSchedulerListener | 22 | - | todo |
| ExecutionChain | 27 | - | todo |
| ExecutionInterceptor | 8 | - | todo |
| SchedulerListener | 49 | - | todo |
| SchedulerListeners | 95 | - | todo |
| **`scheduler/exceptions`** | | | |
| DataClassMismatchException | 14 | - | todo |
| DbSchedulerException | 11 | - | todo |
| ExecutionException | 22 | - | todo |
| FailedToScheduleBatchException | 11 | - | todo |
| MissingRawDataException | 10 | - | todo |
| SerializationException | 8 | - | todo |
| TaskInstanceCurrentlyExecutingException | 11 | - | todo |
| TaskInstanceException | 24 | - | todo |
| TaskInstanceNotFoundException | 8 | - | todo |
| **`scheduler/jdbc`** | | | |
| AndCondition | 7 | - | todo |
| AutodetectJdbcCustomization | 131 | `jdbc/customization.py` | done |
| DefaultJdbcCustomization | 108 | `jdbc/customization.py` | done |
| ExecutionUpdate | 172 | - | todo |
| JdbcConfig | 3 | - | todo |
| JdbcCustomization | 24 | `jdbc/customization.py` | done |
| JdbcTaskRepository | 972 | `jdbc/repository.py` | partial - see PORTING.md |
| JdbcTaskRepositoryContext | 27 | - | todo |
| MariaDBJdbcCustomization | 27 | `jdbc/customization.py` | done |
| MssqlJdbcCustomization | 48 | - | n/a - MSSQL is not a dialect this port claims |
| MySQL8JdbcCustomization | 27 | `jdbc/customization.py` | done |
| MySQLBaseJdbcCustomization | 39 | - | todo |
| MySQLJdbcCustomization | 10 | - | todo |
| OracleJdbcCustomization | 27 | - | n/a - Oracle is not a dialect this port claims |
| PostgreSqlJdbcCustomization | 77 | `jdbc/customization.py` | done |
| Queries | 33 | - | todo |
| QueryBuilder | 58 | - | todo |
| SqliteJdbcCustomization | 15 | `jdbc/customization.py` | done |
| **`scheduler/logging`** | | | |
| ConfigurableLogger | 57 | - | todo |
| LogLevel | 9 | - | todo |
| **`scheduler/serializer`** | | | |
| GsonSerializer | 52 | - | n/a - a Gson-specific binding; JSON is the port's default |
| Jackson3Serializer | 55 | - | n/a - as above |
| JacksonSerializer | 57 | - | n/a - as above |
| JavaSerializer | 29 | - | n/a - pickle is the equivalent and executes what it is handed |
| Serializer | 6 | `serializer/__init__.py` | done |
| SerializerWithFallbackDeserializers | 27 | - | n/a - only needed to migrate off JavaSerializer |
| **`scheduler/serializer/gson`** | | | |
| DurationAdapter | 16 | - | todo |
| InstantAdapter | 18 | - | todo |
| LocalTimeAdapter | 20 | - | todo |
| RuntimeTypeAdapterFactory | 138 | - | todo |
| ZoneIdAdapter | 16 | - | todo |
| **`scheduler/serializer/jackson`** | | | |
| InstantDeserializer | 19 | - | todo |
| InstantSerializer | 22 | - | todo |
| ScheduleMixin | 19 | - | todo |
| **`scheduler/serializer/jackson3`** | | | |
| InstantDeserializer | 12 | - | todo |
| InstantSerializer | 15 | - | todo |
| **`scheduler/stats`** | | | |
| MicrometerStatsRegistry | 98 | - | todo |
| StatsRegistry | 40 | - | todo |
| StatsRegistryAdapter | 49 | - | todo |
| **`scheduler/task`** | | | |
| AbstractTask | 64 | `task/helper.py` | done |
| CompletionHandler | 93 | `task/handlers.py` | done |
| DeadExecutionHandler | 27 | `task/handlers.py` | done |
| Execution | 97 | `task/__init__.py` | done |
| ExecutionComplete | 65 | `task/__init__.py` | done |
| ExecutionContext | 32 | `task/__init__.py` | done |
| ExecutionFailed | 22 | - | todo |
| ExecutionHandler | 4 | `task/handlers.py` | done |
| ExecutionOperations | 65 | - | todo |
| FailureHandler | 187 | `task/handlers.py` | done |
| HasTaskName | 17 | `task/__init__.py` | done |
| MaxRetriesExceededListener | 17 | `task/handlers.py` | done |
| NewValue | 3 | - | todo |
| NextExecutionTime | 10 | `task/__init__.py` | done - a `Callable` alias; a one-method interface has no class to be |
| OnStartup | 6 | `task/__init__.py` | done |
| Priority | 6 | `task/__init__.py` | done |
| RescheduleUpdate | 50 | - | todo |
| SchedulableInstance | 46 | `task/__init__.py` | done |
| SchedulableTaskInstance | 22 | `task/__init__.py` | done |
| ScheduledTaskInstance | 31 | - | todo |
| StateReturningExecutionHandler | 4 | `task/__init__.py` | done |
| Task | 23 | `task/helper.py` | done |
| TaskDescriptor | 33 | `task/__init__.py` | done |
| TaskInstance | 97 | `task/__init__.py` | done |
| TaskInstanceId | 37 | `task/__init__.py` | done |
| TaskWithDataDescriptor | 21 | - | todo |
| TaskWithoutDataDescriptor | 19 | - | todo |
| VoidExecutionHandler | 4 | `task/__init__.py` | done |
| **`scheduler/task/helper`** | | | |
| CustomTask | 65 | `task/helper.py` | done |
| OneTimeTask | 54 | `task/helper.py` | done |
| PlainScheduleAndData | 46 | `task/helper.py` | done |
| RecurringTask | 102 | `task/helper.py` | done |
| RecurringTaskWithPersistentSchedule | 54 | `task/helper.py` | done |
| ScheduleAndData | 10 | `task/helper.py` | done |
| ScheduleOnStartup | 7 | `task/helper.py` | done |
| ScheduleOnceOnStartup | 34 | `task/helper.py` | done |
| ScheduleRecurringOnStartup | 121 | `task/helper.py` | done |
| Tasks | 311 | `task/helper.py` | done |
| **`scheduler/task/schedule`** | | | |
| CompositeParser | 27 | `task/schedule/__init__.py` | done |
| CronSchedule | 159 | `task/schedule/__init__.py` | done |
| CronStyle | 9 | `task/schedule/__init__.py` | done |
| Daily | 69 | `task/schedule/__init__.py` | done |
| DailyParser | 32 | `task/schedule/__init__.py` | done |
| DisabledSchedule | 25 | `task/schedule/__init__.py` | done |
| DisabledScheduleParser | 16 | `task/schedule/__init__.py` | done |
| FixedDelay | 68 | `task/schedule/__init__.py` | done |
| FixedDelayParser | 16 | `task/schedule/__init__.py` | done |
| Parser | 7 | `task/schedule/__init__.py` | done |
| PersistentCronSchedule | 36 | `task/schedule/__init__.py` | done |
| RegexBasedParser | 34 | - | todo |
| Schedule | 14 | `task/schedule/__init__.py` | done |
| Schedules | 37 | `task/schedule/__init__.py` | done |
| **`scheduler/testhelper`** | | | |
| ManualScheduler | 80 | - | todo |
| SettableClock | 22 | `clock.py` | done |
| TestHelper | 181 | - | todo |
