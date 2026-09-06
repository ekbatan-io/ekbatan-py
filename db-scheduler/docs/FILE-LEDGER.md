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
| ConnectionSupplier | 8 | - | n/a - internal to db-scheduler's `jdbc` package, not reachable from its builder. SQLAlchemy's Engine is the whole seam, as the DataSource is there |
| DataSourceConnectionSupplier | 24 | - | n/a - `engine.connect()` |
| DoInTransaction | 5 | - | n/a - `with engine.begin()` is this |
| ExternallyManagedConnection | 20 | - | n/a - its only caller is `JdbcRunner.inTransaction`, meaning "reuse this open connection, do not commit or close it". `with engine.begin() as conn` already is that. It is *not* a seam for joining a caller's transaction - db-scheduler's own javadoc disclaims that - so porting it would not buy transactional scheduling |
| IntegrityConstraintViolation | 7 | - | n/a - SQLAlchemy raises IntegrityError |
| JdbcRunner | 244 | - | n/a - SQLAlchemy is the JDBC layer |
| Mappers | 42 | - | n/a - as above |
| PreparedStatementExecutor | 20 | - | n/a - as above |
| PreparedStatementSetter | 11 | - | n/a - SQLAlchemy binds parameters |
| ResultSetMapper | 6 | - | n/a - as above |
| RowMapper | 6 | - | n/a - as above |
| SQLRuntimeException | 16 | - | n/a - SQLAlchemy raises its own |
| ThreadLocalTransactionContextProvider | 16 | - | n/a - as above |
| TransactionContext | 11 | - | n/a - as above |
| TransactionContextProvider | 6 | - | n/a - as above |
| TransactionManager | 87 | - | n/a - SQLAlchemy owns transactions |
| **`scheduler`** | | | |
| ClientEvent | 37 | `client.py` | done - unused in db-scheduler too, but public |
| Clock | 5 | `clock.py` | done |
| CurrentlyExecuting | 33 | `state.py` | done |
| DueExecutionsBatch | 51 | `executor.py` | done |
| ExceptionUtils | 12 | `util.py` | done |
| ExecutePicked | 148 | `executor.py` | done |
| ExecutionTimeAndId | 37 | `scheduled_execution.py` | done |
| Executor | 83 | `executor.py` | done |
| ExecutorUtils | 48 | - | n/a - ThreadPoolExecutor.shutdown does this; the thread-name prefix is a factory argument |
| FetchCandidates | 134 | `poll.py` | done |
| HeartbeatConfig | 13 | `state.py` | done |
| HeartbeatState | 59 | `state.py` | done |
| ImmediateCheckForDueExecutions | 34 | `scheduler.py` | done |
| LockAndFetchCandidates | 96 | `poll.py` | done |
| PollStrategy | 2 | `executor.py` | done |
| PollingStrategyConfig | 42 | `executor.py` | done |
| Resolvable | 19 | `resolver.py` | done |
| RunAndLogErrors | 23 | `scheduler.py` | done |
| RunUntilShutdown | 45 | `scheduler.py` | done |
| ScheduledExecution | 71 | `scheduled_execution.py` | done |
| ScheduledExecutionsFilter | 55 | `scheduled_execution.py` | done |
| Scheduler | 404 | `scheduler.py` | done |
| SchedulerBuilder | 306 | `builder.py` | done |
| SchedulerClient | 365 | `client.py` | done |
| SchedulerName | 41 | `scheduler_name.py` | done |
| SchedulerState | 39 | `state.py` | done |
| StreamUtils | 22 | `util.py` | done |
| StringUtils | 13 | `util.py` | done |
| SystemClock | 8 | `clock.py` | done |
| TaskRepository | 66 | `jdbc/repository.py` | done |
| TaskResolver | 79 | `resolver.py` | done |
| TaskSummary | 12 | `scheduled_execution.py` | done |
| Waiter | 77 | `executor.py` | done |
| **`scheduler/concurrent`** | | | |
| LoggingRunnable | 15 | - | n/a - Python threads log their own uncaught exceptions |
| **`scheduler/event`** | | | |
| AbstractSchedulerListener | 22 | `event/__init__.py` | done |
| ExecutionChain | 27 | `event/__init__.py` | done |
| ExecutionInterceptor | 8 | `event/__init__.py` | done |
| SchedulerListener | 49 | `event/__init__.py` | done |
| SchedulerListeners | 95 | `event/__init__.py` | done |
| **`scheduler/exceptions`** | | | |
| DataClassMismatchException | 14 | `exceptions.py` | done |
| DbSchedulerException | 11 | `exceptions.py` | done |
| ExecutionException | 22 | `exceptions.py` | done |
| FailedToScheduleBatchException | 11 | `exceptions.py` | done |
| MissingRawDataException | 10 | `exceptions.py` | done |
| SerializationException | 8 | `exceptions.py` | done |
| TaskInstanceCurrentlyExecutingException | 11 | `exceptions.py` | done |
| TaskInstanceException | 24 | `exceptions.py` | done |
| TaskInstanceNotFoundException | 8 | `exceptions.py` | done |
| **`scheduler/jdbc`** | | | |
| AndCondition | 7 | `jdbc/repository.py` | done - SQLAlchemy expressions compose without a condition interface |
| AutodetectJdbcCustomization | 131 | `jdbc/customization.py` | done |
| DefaultJdbcCustomization | 108 | `jdbc/customization.py` | done |
| ExecutionUpdate | 172 | `jdbc/execution_update.py` | done |
| JdbcConfig | 3 | `jdbc/repository.py` | done - SQLAlchemy's Table and Engine carry what this record held |
| JdbcCustomization | 24 | `jdbc/customization.py` | done |
| JdbcTaskRepository | 972 | `jdbc/repository.py` | done |
| JdbcTaskRepositoryContext | 27 | `jdbc/repository.py` | done - the repository passes itself; no context object needed |
| MariaDBJdbcCustomization | 27 | `jdbc/customization.py` | done |
| MssqlJdbcCustomization | 48 | `jdbc/customization.py` | done - untested; no container for it here |
| MySQL8JdbcCustomization | 27 | `jdbc/customization.py` | done |
| MySQLBaseJdbcCustomization | 39 | `jdbc/customization.py` | done |
| MySQLJdbcCustomization | 10 | `jdbc/customization.py` | done |
| OracleJdbcCustomization | 27 | `jdbc/customization.py` | done - untested; no container for it here |
| PostgreSqlJdbcCustomization | 77 | `jdbc/customization.py` | done |
| Queries | 33 | - | n/a - SQLAlchemy builds the SQL; the dialect differences it encodes are properties on the customization |
| QueryBuilder | 58 | `jdbc/repository.py` | done - `select()` is the query builder |
| SqliteJdbcCustomization | 15 | `jdbc/customization.py` | done |
| **`scheduler/logging`** | | | |
| ConfigurableLogger | 57 | `logging.py` | done |
| LogLevel | 9 | `logging.py` | done |
| **`scheduler/serializer`** | | | |
| GsonSerializer | 52 | - | n/a - a Gson-specific binding; JSON is the port's default |
| Jackson3Serializer | 55 | - | n/a - as above |
| JacksonSerializer | 57 | - | n/a - as above |
| JavaSerializer | 29 | - | n/a - pickle is the equivalent and executes what it is handed |
| Serializer | 6 | `serializer/__init__.py` | done |
| SerializerWithFallbackDeserializers | 27 | - | n/a - only needed to migrate off JavaSerializer |
| **`scheduler/serializer/gson`** | | | |
| DurationAdapter | 16 | `serializer/__init__.py` | done - milliseconds, as Gson writes them |
| InstantAdapter | 18 | `serializer/__init__.py` | done - ISO-8601 |
| LocalTimeAdapter | 20 | `serializer/__init__.py` | done - ISO, not Java's localized short form, which is not a wire format |
| RuntimeTypeAdapterFactory | 138 | `serializer/__init__.py` | done - as above |
| ZoneIdAdapter | 16 | `serializer/__init__.py` | done - the zone id string |
| **`scheduler/serializer/jackson`** | | | |
| InstantDeserializer | 19 | `serializer/__init__.py` | done - ISO-8601, in the JSON codec |
| InstantSerializer | 22 | `serializer/__init__.py` | done - as above |
| ScheduleMixin | 19 | `serializer/__init__.py` | done - the tagged shape, written by hand |
| **`scheduler/serializer/jackson3`** | | | |
| InstantDeserializer | 12 | `serializer/__init__.py` | done - ISO-8601, in the JSON codec |
| InstantSerializer | 15 | `serializer/__init__.py` | done - as above |
| **`scheduler/stats`** | | | |
| MicrometerStatsRegistry | 98 | - | n/a - Micrometer is Java; the Python equivalent is the user's choice of client |
| StatsRegistry | 40 | `stats/__init__.py` | done |
| StatsRegistryAdapter | 49 | `stats/adapter.py` | done |
| **`scheduler/task`** | | | |
| AbstractTask | 64 | `task/helper.py` | done |
| CompletionHandler | 93 | `task/handlers.py` | done |
| DeadExecutionHandler | 27 | `task/handlers.py` | done |
| Execution | 97 | `task/__init__.py` | done |
| ExecutionComplete | 65 | `task/__init__.py` | done |
| ExecutionContext | 32 | `task/__init__.py` | done |
| ExecutionFailed | 22 | `task/__init__.py` | done - unused in db-scheduler too, but public |
| ExecutionHandler | 4 | `task/handlers.py` | done |
| ExecutionOperations | 65 | `task/operations.py` | done |
| FailureHandler | 187 | `task/handlers.py` | done |
| HasTaskName | 17 | `task/__init__.py` | done |
| MaxRetriesExceededListener | 17 | `task/handlers.py` | done |
| NewValue | 3 | `task/__init__.py` | done |
| NextExecutionTime | 10 | `task/__init__.py` | done - a `Callable` alias; a one-method interface has no class to be |
| OnStartup | 6 | `task/__init__.py` | done |
| Priority | 6 | `task/__init__.py` | done |
| RescheduleUpdate | 50 | `task/__init__.py` | done |
| SchedulableInstance | 46 | `task/__init__.py` | done |
| SchedulableTaskInstance | 22 | `task/__init__.py` | done |
| ScheduledTaskInstance | 31 | `task/__init__.py` | done |
| StateReturningExecutionHandler | 4 | `task/__init__.py` | done |
| Task | 23 | `task/helper.py` | done |
| TaskDescriptor | 33 | `task/__init__.py` | done |
| TaskInstance | 97 | `task/__init__.py` | done |
| TaskInstanceId | 37 | `task/__init__.py` | done |
| TaskWithDataDescriptor | 21 | `task/__init__.py` | done - deprecated, superseded by TaskDescriptor |
| TaskWithoutDataDescriptor | 19 | `task/__init__.py` | done - as above |
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
| RegexBasedParser | 34 | `task/schedule/__init__.py` | done |
| Schedule | 14 | `task/schedule/__init__.py` | done |
| Schedules | 37 | `task/schedule/__init__.py` | done |
| **`scheduler/testhelper`** | | | |
| ManualScheduler | 80 | `testhelper/__init__.py` | done |
| SettableClock | 22 | `clock.py` | done |
| TestHelper | 181 | `testhelper/__init__.py` | done |
