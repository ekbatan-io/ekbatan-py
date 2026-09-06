# db-scheduler: extracted public API

Machine-extracted from the Java source at kagkarlsson/db-scheduler@e2c911b.


## `jdbc`


### interface BatchPreparedStatementSetter


### interface ConnectionSupplier


### class DataSourceConnectionSupplier

- `DataSourceConnectionSupplier(DataSource dataSource, boolean commitWhenAutocommitDisabled)`
- `Connection getConnection() throws SQLException`
- `boolean commitWhenAutocommitDisabled()`
- `boolean isExternallyManagedConnection()`

### interface DoInTransaction


### class ExternallyManagedConnection

- `ExternallyManagedConnection(Connection externallyManagedConnection)`
- `Connection getConnection()`
- `boolean commitWhenAutocommitDisabled()`
- `boolean isExternallyManagedConnection()`

### class IntegrityConstraintViolation

- `IntegrityConstraintViolation(SQLException ex)`

### class JdbcRunner

- `JdbcRunner(DataSource dataSource)`
- `JdbcRunner(DataSource dataSource, boolean commitWhenAutocommitDisabled)`
- `JdbcRunner( DataSource dataSource, boolean commitWhenAutocommitDisabled, TransactionContextProvider transactionContextProvider)`
- `JdbcRunner( ConnectionSupplier connectionSupplier, TransactionContextProvider transactionContextProvider)`
- `<T> T inTransaction(Function<JdbcRunner, T> doInTransaction)`
- `int execute(String query, PreparedStatementSetter setParameters)`
- `<T> List<T> query( String query, PreparedStatementSetter setParameters, RowMapper<T> rowMapper)`
- `<T> T query( String query, PreparedStatementSetter setParameters, ResultSetMapper<T> resultSetMapper)`
- `<T> T execute( String query, PreparedStatementSetter setParameters, AfterExecution<T, Boolean> afterExecution)`
- `<U> int[] executeBatch( String query, List<U> batchValues, BatchPreparedStatementSetter<U> setParameters)`
- `Integer doAfterExecution(PreparedStatement executedPreparedStatement, U executeResult) throws SQLException`

### class Mappers

- `static final SingleResultMapper<Integer> SINGLE_INT = new SingleResultMapper<>(rs -> rs.getInt(1))`
- `static final SingleResultMapper<Long> SINGLE_LONG = new SingleResultMapper<>(rs -> rs.getLong(1))`
- `static final SingleResultMapper<String> SINGLE_STRING = new SingleResultMapper<>(rs -> rs.getString(1))`
- `static final ResultSetMapper<Boolean> NON_EMPTY_RESULTSET = new NonEmptyResultMapper()`
- `static class SingleResultMapper<T> implements ResultSetMapper<T>`
- `SingleResultMapper(RowMapper<T> rowMapper)`
- `T map(ResultSet rs) throws SQLException`
- `static class SingleResultExpected extends SQLRuntimeException`
- `SingleResultExpected(String message)`
- `Boolean map(ResultSet resultSet) throws SQLException`

### interface PreparedStatementExecutor


### interface PreparedStatementSetter


### interface ResultSetMapper


### interface RowMapper


### class SQLRuntimeException

- `SQLRuntimeException()`
- `SQLRuntimeException(String message)`
- `SQLRuntimeException(Throwable ex)`
- `SQLRuntimeException(String message, SQLException cause)`

### class ThreadLocalTransactionContextProvider

- `TransactionContext getCurrent()`
- `void setCurrent(TransactionContext transactionContext)`
- `void removeCurrent()`

### class TransactionContext

- `TransactionContext(Connection c)`
- `Connection getConnection()`

### interface TransactionContextProvider


### class TransactionManager

- `TransactionManager( ConnectionSupplier dataSource, TransactionContextProvider transactionContextProvider)`
- `<T> T inTransaction(DoInTransaction<T> doInTransaction)`

## `scheduler`


### class ClientEvent

- `ClientEvent(ClientEventContext ctx)`
- `ClientEventContext getContext()`
- `static class ClientEventContext`
- `ClientEventContext( EventType eventType, TaskInstanceId taskInstanceId, Instant executionTime)`
- `EventType getEventType()`
- `TaskInstanceId getTaskInstanceId()`
- `Instant getExecutionTime()`

### interface Clock


### class CurrentlyExecuting

- `CurrentlyExecuting(Execution execution, Clock clock, HeartbeatConfig heartbeatConfig)`
- `Duration getDuration()`
- `TaskInstance getTaskInstance()`
- `Execution getExecution()`
- `HeartbeatState getHeartbeatState()`
- `void heartbeat(boolean successful, Instant now)`

### class DueExecutionsBatch

- `DueExecutionsBatch( int generationNumber, int executionsAdded, boolean possiblyMoreExecutionsInDb, Predicate<Integer> whenToTriggerCheckForNewBatch)`
- `void markBatchAsStale()`
- `void oneExecutionDone(Runnable triggerCheckForNewBatch)`
- `boolean isOlderGenerationThan(int compareTo)`
- `int getGenerationNumber()`

### class ExceptionUtils


### class ExecutePicked

- `ExecutePicked( Executor executor, TaskRepository taskRepository, SchedulerClient schedulerClient, SchedulerListeners schedulerListeners, List<ExecutionInterceptor> executionInterceptors, TaskResolver taskResolver, SchedulerState schedulerState, ConfigurableLogger failureLogger, Clock clock, HeartbeatConfig heartbeatConfig, Execution pickedExecution)`
- `void run()`

### record ExecutionTimeAndId

- `ExecutionTimeAndId(Instant executionTime, String taskInstanceId)`
- `static ExecutionTimeAndId from(ScheduledExecution<?> execution)`
- `String toEncodedString()`
- `static ExecutionTimeAndId fromEncodedString(String encoded)`

### class Executor

- `Executor(ExecutorService executorService, Clock clock)`
- `void addToQueue(Runnable r, Runnable afterDone)`
- `List<CurrentlyExecuting> getCurrentlyExecuting()`
- `void stop(Duration shutdownMaxWait)`
- `int getNumberInQueueOrProcessing()`
- `UUID addCurrentlyProcessing(CurrentlyExecuting currentlyExecuting)`
- `void removeCurrentlyProcessing(UUID executionId)`

### class ExecutorUtils

- `static boolean shutdownAndAwaitTermination( ExecutorService executorService, Duration waitBeforeInterrupt, Duration waitAfterInterrupt)`
- `static boolean awaitTermination(ExecutorService executor, Duration timeout)`
- `static ThreadFactory defaultThreadFactoryWithPrefix(String prefix)`
- `PrefixingDefaultThreadFactory(String prefix)`
- `Thread newThread(Runnable r)`

### class FetchCandidates

- `FetchCandidates( Executor executor, TaskRepository taskRepository, SchedulerClient schedulerClient, int threadpoolSize, SchedulerListeners schedulerListeners, List<ExecutionInterceptor> executionInterceptors, SchedulerState schedulerState, ConfigurableLogger failureLogger, TaskResolver taskResolver, Clock clock, PollingStrategyConfig pollingStrategyConfig, Runnable triggerCheckForNewExecutions, HeartbeatConfig heartbeatConfig)`
- `void run()`
- `PickDue(Execution candidate, DueExecutionsBatch dueExecutionsBatch)`
- `Optional<Execution> call()`

### class HeartbeatConfig

- `final Duration heartbeatInterval`
- `final int missedHeartbeatsLimit`
- `final Duration maxAgeBeforeConsideredDead`
- `HeartbeatConfig( Duration heartbeatInterval, int missedHeartbeatsLimit, Duration maxAgeBeforeConsideredDead)`

### class HeartbeatState

- `HeartbeatState(Clock clock, Instant startTime, HeartbeatConfig heartbeatConfig)`
- `boolean hasStaleHeartbeat()`
- `double getFractionDead()`
- `int getFailedHeartbeats()`
- `void heartbeat(boolean successful, Instant lastHeartbeatAttempt)`
- `String describe()`

### class ImmediateCheckForDueExecutions

- `ImmediateCheckForDueExecutions(Scheduler scheduler, Clock clock)`
- `void onExecutionScheduled( TaskInstanceId taskInstanceId, Instant scheduledToExecutionTime)`

### class LockAndFetchCandidates

- `LockAndFetchCandidates( Executor executor, TaskRepository taskRepository, SchedulerClient schedulerClient, int threadpoolSize, SchedulerListeners schedulerListeners, List<ExecutionInterceptor> executionInterceptors, SchedulerState schedulerState, ConfigurableLogger failureLogger, TaskResolver taskResolver, Clock clock, PollingStrategyConfig pollingStrategyConfig, Runnable triggerCheckForNewExecutions, HeartbeatConfig maxAgeBeforeConsideredDead)`
- `void run()`

### interface PollStrategy


### class PollingStrategyConfig

- `static final PollingStrategyConfig DEFAULT_FETCH = new PollingStrategyConfig(Type.FETCH, 0.5, 3.0)`
- `static final PollingStrategyConfig DEFAULT_SELECT_FOR_UPDATE = new PollingStrategyConfig(Type.LOCK_AND_FETCH, 0.5, 1.0)`
- `int getUpperLimit(int threadpoolSize)`
- `int getLowerLimit(int threadpoolSize)`
- `final Type type`
- `final double lowerLimitFractionOfThreads`
- `final double upperLimitFractionOfThreads`
- `PollingStrategyConfig( Type type, double lowerLimitFractionOfThreads, double upperLimitFractionOfThreads)`
- `String describe()`

### interface Resolvable

- `Instant getExecutionTime()`
- `String getTaskName()`

### class RunAndLogErrors

- `RunAndLogErrors(Runnable toRun, SchedulerListener schedulerListener)`
- `void run()`

### class RunUntilShutdown

- `RunUntilShutdown( Runnable toRun, Waiter waitBetweenRuns, SchedulerState schedulerState, SchedulerListeners schedulerListeners)`
- `void run()`

### class ScheduledExecution

- `ScheduledExecution(Class<DATA_TYPE> dataClass, Execution execution)`
- `TaskInstanceId getTaskInstance()`
- `Instant getExecutionTime()`
- `DATA_TYPE getData()`
- `boolean hasRawData()`
- `byte[] getRawData()`
- `Instant getLastSuccess()`
- `Instant getLastFailure()`
- `int getConsecutiveFailures()`
- `boolean isPicked()`
- `String getPickedBy()`
- `boolean equals(Object o)`
- `int hashCode()`
- `String toString()`

### class ScheduledExecutionsFilter

- `static ScheduledExecutionsFilter all()`
- `static ScheduledExecutionsFilter onlyResolved()`
- `ScheduledExecutionsFilter withPicked(boolean pickedValue)`
- `ScheduledExecutionsFilter withIncludeUnresolved(boolean includeUnresolved)`
- `Optional<Boolean> getPickedValue()`
- `boolean getIncludeUnresolved()`
- `ScheduledExecutionsFilter limit(int limit)`
- `ScheduledExecutionsFilter after(ExecutionTimeAndId execution)`
- `ScheduledExecutionsFilter before(ExecutionTimeAndId execution)`
- `Optional<ExecutionTimeAndId> getAfterExecution()`
- `Optional<ExecutionTimeAndId> getBeforeExecution()`
- `Optional<Integer> getLimit()`

### class Scheduler

- `static final double TRIGGER_NEXT_BATCH_WHEN_AVAILABLE_THREADS_RATIO = 0.5`
- `static final String THREAD_PREFIX = "db-scheduler"`
- `final PollStrategy executeDueStrategy`
- `final Executor executor`
- `final List<OnStartup> onStartup`
- `Scheduler( Clock clock, TaskRepository schedulerTaskRepository, TaskRepository clientTaskRepository, TaskResolver taskResolver, int threadpoolSize, ExecutorService executorService, SchedulerName schedulerName, Waiter executeDueWaiter, Duration heartbeatInterval, int numberOfMissedHeartbeatsBeforeDead, List<SchedulerListener> schedulerListeners, List<ExecutionInterceptor> executionInterceptors, PollingStrategyConfig pollingStrategyConfig, Duration deleteUnresolvedAfter, Duration shutdownMaxWait, LogLevel logLevel, boolean logStackTrace, List<OnStartup> onStartup, ExecutorService dueExecutor, ScheduledExecutorService housekeeperExecutor)`
- `void registerSchedulerListener(SchedulerListener listener)`
- `void start()`
- `void executeDue()`
- `void executeOnStartup()`
- `void stop()`
- `void pause()`
- `void resume()`
- `SchedulerState getSchedulerState()`
- `<T> void schedule(SchedulableInstance<T> schedulableInstance)`
- `<T> boolean scheduleIfNotExists(TaskInstance<T> taskInstance, Instant executionTime)`
- `<T> boolean scheduleIfNotExists(SchedulableInstance<T> schedulableInstance)`
- `void scheduleBatch(List<TaskInstance<?>> taskInstances, Instant executionTime)`
- `void scheduleBatch(List<SchedulableInstance<?>> schedulableInstances)`
- `<T> void schedule(TaskInstance<T> taskInstance, Instant executionTime)`
- `boolean reschedule(TaskInstanceId taskInstanceId, Instant newExecutionTime)`
- `<T> boolean reschedule(SchedulableInstance<T> schedulableInstance)`
- `<T> boolean reschedule( TaskInstanceId taskInstanceId, Instant newExecutionTime, T newData)`
- `<T> boolean schedule( TaskInstance<T> taskInstance, Instant executionTime, ScheduleOptions scheduleOptions)`
- `<T> boolean schedule( SchedulableInstance<T> schedulableInstance, ScheduleOptions scheduleOptions)`
- `void cancel(TaskInstanceId taskInstanceId)`
- `void fetchScheduledExecutions(Consumer<ScheduledExecution<Object>> consumer)`
- `void fetchScheduledExecutions( ScheduledExecutionsFilter filter, Consumer<ScheduledExecution<Object>> consumer)`
- `<T> void fetchScheduledExecutionsForTask( String taskName, Class<T> dataClass, Consumer<ScheduledExecution<T>> consumer)`
- `<T> void fetchScheduledExecutionsForTask( String taskName, Class<T> dataClass, ScheduledExecutionsFilter filter, Consumer<ScheduledExecution<T>> consumer)`
- `Optional<ScheduledExecution<Object>> getScheduledExecution(TaskInstanceId taskInstanceId)`
- `List<TaskSummary> getScheduledExecutionsSummaryByTask()`
- `List<Execution> getFailingExecutions(Duration failingAtLeastFor)`
- `void triggerCheckForDueExecutions()`
- `List<CurrentlyExecuting> getCurrentlyExecuting()`
- `List<CurrentlyExecuting> getCurrentlyExecutingWithStaleHeartbeat()`
- `void detectDeadExecutions()`
- `void updateHeartbeatForExecution(Instant now, CurrentlyExecuting currentlyExecuting)`
- `static SchedulerBuilder create(DataSource dataSource, Task<?>... knownTasks)`
- `static SchedulerBuilder create(DataSource dataSource, List<Task<?>> knownTasks)`

### class SchedulerBuilder

- `static final double UPPER_LIMIT_FRACTION_OF_THREADS_FOR_FETCH = 3.0`
- `static final Duration DEFAULT_POLLING_INTERVAL = Duration.ofSeconds(10)`
- `static final Duration DEFAULT_HEARTBEAT_INTERVAL = Duration.ofMinutes(5)`
- `static final int DEFAULT_MISSED_HEARTBEATS_LIMIT = 6`
- `static final Duration DEFAULT_DELETION_OF_UNRESOLVED_TASKS_DURATION = Duration.ofDays(14)`
- `static final Duration SHUTDOWN_MAX_WAIT = Duration.ofMinutes(30)`
- `static final PollingStrategyConfig DEFAULT_POLLING_STRATEGY = new PollingStrategyConfig( PollingStrategyConfig.Type.FETCH, 0.5, UPPER_LIMIT_FRACTION_OF_THREADS_FOR_FETCH)`
- `static final LogLevel DEFAULT_FAILURE_LOG_LEVEL = LogLevel.WARN`
- `static final boolean LOG_STACK_TRACE_ON_FAILURE = true`
- `final DataSource dataSource`
- `final List<Task<?>> knownTasks = new ArrayList<>()`
- `final List<OnStartup> startTasks = new ArrayList<>()`
- `Clock clock = new SystemClock()`
- `SchedulerName schedulerName`
- `int executorThreads = 10`
- `Duration poolingInterval = DEFAULT_POLLING_INTERVAL`
- `StatsRegistry statsRegistry = StatsRegistry.NOOP`
- `Duration heartbeatInterval = DEFAULT_HEARTBEAT_INTERVAL`
- `Serializer serializer = Serializer.DEFAULT_JAVA_SERIALIZER`
- `String tableName = JdbcTaskRepository.DEFAULT_TABLE_NAME`
- `boolean enableImmediateExecution = false`
- `ExecutorService executorService`
- `ExecutorService dueExecutor`
- `ScheduledExecutorService housekeeperExecutor`
- `Duration deleteUnresolvedAfter = DEFAULT_DELETION_OF_UNRESOLVED_TASKS_DURATION`
- `JdbcCustomization jdbcCustomization = null`
- `Duration shutdownMaxWait = SHUTDOWN_MAX_WAIT`
- `boolean commitWhenAutocommitDisabled = false`
- `PollingStrategyConfig pollingStrategyConfig = DEFAULT_POLLING_STRATEGY`
- `LogLevel logLevel = DEFAULT_FAILURE_LOG_LEVEL`
- `boolean logStackTrace = LOG_STACK_TRACE_ON_FAILURE`
- `boolean enablePriority = false`
- `boolean registerShutdownHook = false`
- `int numberOfMissedHeartbeatsBeforeDead = DEFAULT_MISSED_HEARTBEATS_LIMIT`
- `boolean alwaysPersistTimestampInUTC = false`
- `List<SchedulerListener> schedulerListeners = new ArrayList<>()`
- `List<ExecutionInterceptor> executionInterceptors = new ArrayList<>()`
- `SchedulerBuilder(DataSource dataSource, List<Task<?>> knownTasks)`
- `final <T extends Task<?> & OnStartup> SchedulerBuilder startTasks(T... startTasks)`
- `<T extends Task<?> & OnStartup> SchedulerBuilder startTasks(List<T> startTasks)`
- _... 28 more_

### interface SchedulerClient

- `static final ScheduleOptions WHEN_EXISTS_DO_NOTHING = defaultOptions().whenExistsDoNothing()`
- `static final ScheduleOptions WHEN_EXISTS_RESCHEDULE = defaultOptions().whenExistsReschedule()`
- `static ScheduleOptions defaultOptions()`
- `ScheduleOptions whenExistsReschedule()`
- `ScheduleOptions whenExistsDoNothing()`
- `WhenExists getWhenExists()`
- `static Builder create(DataSource dataSource, Task<?>... knownTasks)`
- `static Builder create(DataSource dataSource, List<Task<?>> knownTasks)`
- `Builder serializer(Serializer serializer)`
- `Builder tableName(String tableName)`
- `Builder enablePriority()`
- `Builder jdbcCustomization(JdbcCustomization jdbcCustomization)`
- `SchedulerClient build()`
- `final TaskRepository taskRepository`
- `<T> void schedule(TaskInstance<T> taskInstance, Instant executionTime)`
- `<T> boolean scheduleIfNotExists(TaskInstance<T> taskInstance, Instant executionTime)`
- `<T> boolean scheduleIfNotExists(SchedulableInstance<T> schedulableInstance)`
- `void scheduleBatch(List<TaskInstance<?>> taskInstances, Instant executionTime)`
- `void scheduleBatch(List<SchedulableInstance<?>> schedulableInstances)`
- `<T> void schedule(SchedulableInstance<T> schedulableInstance)`
- `<T> boolean schedule( TaskInstance<T> taskInstance, Instant executionTime, ScheduleOptions scheduleOptions)`
- `<T> boolean schedule( SchedulableInstance<T> schedulableInstance, ScheduleOptions whenExists)`
- `boolean reschedule(TaskInstanceId taskInstanceId, Instant newExecutionTime)`
- `<T> boolean reschedule(SchedulableInstance<T> schedulableInstance)`
- `<T> boolean reschedule( TaskInstanceId taskInstanceId, Instant newExecutionTime, T newData)`
- `void cancel(TaskInstanceId taskInstanceId)`
- `void fetchScheduledExecutions(Consumer<ScheduledExecution<Object>> consumer)`
- `void fetchScheduledExecutions( ScheduledExecutionsFilter filter, Consumer<ScheduledExecution<Object>> consumer)`
- `<T> void fetchScheduledExecutionsForTask( String taskName, Class<T> dataClass, Consumer<ScheduledExecution<T>> consumer)`
- `<T> void fetchScheduledExecutionsForTask( String taskName, Class<T> dataClass, ScheduledExecutionsFilter filter, Consumer<ScheduledExecution<T>> consumer)`
- `Optional<ScheduledExecution<Object>> getScheduledExecution( TaskInstanceId taskInstanceId)`
- `List<TaskSummary> getScheduledExecutionsSummaryByTask()`
- `String getName()`

### interface SchedulerName

- `Fixed(String name)`
- `String getName()`
- `Hostname()`
- `String getName()`

### interface SchedulerState

- `boolean isShuttingDown()`
- `boolean isStarted()`
- `boolean isPaused()`
- `void setIsShuttingDown()`
- `void setStarted()`
- `void setPaused(boolean isPaused)`

### class StreamUtils

- `static <T> Stream<List<T>> chunkStream(Stream<T> stream, int chunkSize)`

### class StringUtils

- `static String truncate(String s, int length)`

### class SystemClock

- `Instant now()`

### interface TaskRepository


### class TaskResolver

- `TaskResolver( SchedulerListeners schedulerListeners, Clock clock, List<Task<?>> knownTasks)`
- `Optional<Task> resolve(Resolvable resolvable)`
- `Optional<Task> resolve(Resolvable resolvable, boolean addUnresolvedToExclusionFilter)`
- `void addTask(Task task)`
- `List<UnresolvedTask> getUnresolved()`
- `List<String> getUnresolvedTaskNames(Duration unresolvedFor)`
- `boolean isUnresolved(String taskName)`
- `void clearUnresolved(String taskName)`
- `UnresolvedTask(String taskName, Instant executionTime)`
- `String getTaskName()`

### record TaskSummary


### class Waiter

- `Waiter(Duration duration)`
- `Waiter(Duration duration, Clock clock)`
- `void doWait() throws InterruptedException`
- `boolean wake()`
- `void wakeOrSkipNextWait()`
- `Duration getWaitDuration()`
- `boolean isWaiting()`

## `scheduler/concurrent`


### class LoggingRunnable

- `abstract void runButLogExceptions()`
- `void run()`

## `scheduler/event`


### class AbstractSchedulerListener

- `void onExecutionScheduled(TaskInstanceId taskInstanceId, Instant executionTime)`
- `void onExecutionStart(CurrentlyExecuting currentlyExecuting)`
- `void onExecutionComplete(ExecutionComplete executionComplete)`
- `void onExecutionDead(Execution execution)`
- `void onExecutionFailedHeartbeat(CurrentlyExecuting currentlyExecuting)`
- `void onSchedulerEvent(SchedulerEventType type)`
- `void onCandidateEvent(CandidateEventType type)`

### class ExecutionChain

- `ExecutionChain( List<ExecutionInterceptor> interceptors, ExecutionHandler<?> executionHandler)`
- `CompletionHandler<?> proceed( TaskInstance taskInstance, ExecutionContext executionContext)`

### interface ExecutionInterceptor


### interface SchedulerListener

- `SchedulerStatsEvent toStatsRegistryEvent()`
- `CandidateStatsEvent toStatsRegistryEvent()`

### class SchedulerListeners

- `static final SchedulerListeners NOOP = new SchedulerListeners(List.of())`
- `SchedulerListeners(SchedulerListener... schedulerListeners)`
- `SchedulerListeners(List<SchedulerListener> schedulerListeners)`
- `void add(SchedulerListener listener)`
- `void onExecutionScheduled(TaskInstanceId taskInstanceId, Instant executionTime)`
- `void onExecutionStart(CurrentlyExecuting currentlyExecuting)`
- `void onExecutionComplete(ExecutionComplete executionComplete)`
- `void onExecutionDead(Execution execution)`
- `void onExecutionFailedHeartbeat(CurrentlyExecuting currentlyExecuting)`
- `void onSchedulerEvent(SchedulerEventType type)`
- `void onCandidateEvent(CandidateEventType type)`
- `void fireAndLogErrors(SchedulerListener listener, String method, Runnable r)`

## `scheduler/exceptions`


### class DataClassMismatchException

- `DataClassMismatchException(Class expectedClass, Class actualClass)`

### class DbSchedulerException

- `DbSchedulerException(String message)`
- `DbSchedulerException(String message, Throwable e)`

### class ExecutionException

- `ExecutionException(String message, Execution execution)`
- `ExecutionException(String message, String taskName, String instanceId, long version)`
- `ExecutionException(String message, Execution execution, Throwable ex)`
- `long getVersion()`

### class FailedToScheduleBatchException

- `FailedToScheduleBatchException(String message)`
- `FailedToScheduleBatchException(String message, Throwable cause)`

### class MissingRawDataException

- `MissingRawDataException(Class<?> dataClass)`

### class SerializationException

- `SerializationException(String msg, Throwable cause)`

### class TaskInstanceCurrentlyExecutingException

- `TaskInstanceCurrentlyExecutingException(String taskName, String instanceId)`

### class TaskInstanceException

- `TaskInstanceException(String message, String taskName, String instanceId, Throwable ex)`
- `TaskInstanceException(String message, String taskName, String instanceId)`
- `String getTaskName()`
- `String getInstanceId()`

### class TaskInstanceNotFoundException

- `TaskInstanceNotFoundException(String taskName, String instanceId)`

## `scheduler/jdbc`


### interface AndCondition


### class AutodetectJdbcCustomization

- `static final String MICROSOFT_SQL_SERVER = "Microsoft SQL Server"`
- `static final String POSTGRESQL = "PostgreSQL"`
- `static final String ORACLE = "Oracle"`
- `static final String MYSQL = "MySQL"`
- `static final String MARIADB = "MariaDB"`
- `static final String SQLITE = "SQLite"`
- `AutodetectJdbcCustomization(DataSource dataSource)`
- `AutodetectJdbcCustomization(DataSource dataSource, boolean persistTimestampInUTC)`
- `void setInstant(PreparedStatement p, int index, Instant value) throws SQLException`
- `Instant getInstant(ResultSet rs, String columnName) throws SQLException`
- `void setTaskData(PreparedStatement p, int index, byte[] value) throws SQLException`
- `byte[] getTaskData(ResultSet rs, String columnName) throws SQLException`
- `boolean supportsExplicitQueryLimitPart()`
- `String getQueryLimitPart(int limit)`
- `boolean supportsSingleStatementLockAndFetch()`
- `List<Execution> lockAndFetchSingleStatement( JdbcTaskRepositoryContext ctx, Instant now, int limit, boolean orderByPriority)`
- `boolean supportsGenericLockAndFetch()`
- `String createGenericSelectForUpdateQuery( String tableName, int limit, String requiredAndCondition, boolean orderByPriority)`
- `String createSelectDueQuery( String tableName, int limit, String andCondition, boolean orderByPriority)`
- `String getName()`

### class DefaultJdbcCustomization

- `static final Calendar UTC = GregorianCalendar.getInstance(TimeZone.getTimeZone("UTC"))`
- `final boolean persistTimestampInUTC`
- `DefaultJdbcCustomization(boolean persistTimestampInUTC)`
- `void setInstant(PreparedStatement p, int index, Instant value) throws SQLException`
- `Instant getInstant(ResultSet rs, String columnName) throws SQLException`
- `void setTaskData(PreparedStatement p, int index, byte[] value) throws SQLException`
- `byte[] getTaskData(ResultSet rs, String columnName) throws SQLException`
- `boolean supportsExplicitQueryLimitPart()`
- `String getQueryLimitPart(int limit)`
- `boolean supportsSingleStatementLockAndFetch()`
- `List<Execution> lockAndFetchSingleStatement( JdbcTaskRepositoryContext ctx, Instant now, int limit, boolean orderByPriority)`
- `boolean supportsGenericLockAndFetch()`
- `String createGenericSelectForUpdateQuery( String tableName, int limit, String requiredAndCondition, boolean orderByPriority)`
- `String createSelectDueQuery( String tableName, int limit, String andCondition, boolean orderByPriority)`
- `String getName()`
- `static void warnIfNotPersistingInUTC( boolean persistTimestampInUTC, Class<? extends JdbcCustomization> customization)`

### class ExecutionUpdate

- `static <T> NewValue<T> of(T value)`

### record JdbcConfig


### interface JdbcCustomization


### class JdbcTaskRepository

- `static final String DEFAULT_TABLE_NAME = "scheduled_tasks"`
- `JdbcTaskRepository( DataSource dataSource, boolean commitWhenAutocommitDisabled, String tableName, TaskResolver taskResolver, SchedulerName schedulerSchedulerName, boolean orderByPriority, Clock clock)`
- `JdbcTaskRepository( DataSource dataSource, boolean commitWhenAutocommitDisabled, JdbcCustomization jdbcCustomization, String tableName, TaskResolver taskResolver, SchedulerName schedulerSchedulerName, boolean orderByPriority, Clock clock)`
- `JdbcTaskRepository( DataSource dataSource, boolean commitWhenAutocommitDisabled, JdbcCustomization jdbcCustomization, String tableName, TaskResolver taskResolver, SchedulerName schedulerSchedulerName, Serializer serializer, boolean orderByPriority, Clock clock)`
- `JdbcTaskRepository( JdbcCustomization jdbcCustomization, String tableName, TaskResolver taskResolver, SchedulerName schedulerSchedulerName, Serializer serializer, JdbcRunner jdbcRunner, boolean orderByPriority, Clock clock)`
- `boolean createIfNotExists(SchedulableInstance instance)`
- `boolean createIfNotExists(ScheduledTaskInstance instance)`
- `void createBatch(List<ScheduledTaskInstance> executions)`
- `Instant replace(Execution toBeReplaced, SchedulableInstance newInstance)`
- `Instant replace(Execution toBeReplaced, ScheduledTaskInstance newInstance)`
- `void getScheduledExecutions( ScheduledExecutionsFilter filter, Consumer<Execution> consumer)`
- `void getScheduledExecutions( ScheduledExecutionsFilter filter, String taskName, Consumer<Execution> consumer)`
- `List<TaskSummary> getScheduledExecutionsSummaryByTask()`
- `List<Execution> getDue(Instant now, int limit)`
- `List<Execution> lockAndFetchGeneric(Instant now, int limit)`
- `List<Execution> lockAndGetDue(Instant now, int limit)`
- `void remove(Execution execution)`
- `boolean reschedule( Execution execution, Instant nextExecutionTime, Instant lastSuccess, Instant lastFailure, int consecutiveFailures)`
- `boolean reschedule( Execution execution, Instant nextExecutionTime, Object newData, Instant lastSuccess, Instant lastFailure, int consecutiveFailures)`
- `boolean reschedule(Execution execution, RescheduleUpdate rescheduleUpdate)`
- `Optional<Execution> pick(Execution e, Instant timePicked)`
- `void unpickPickedBatch(List<Execution> pickedExecutions)`
- `List<Execution> getDeadExecutions(Instant olderThan)`
- `boolean updateHeartbeatWithRetry(Execution execution, Instant newHeartbeat, int tries)`
- `boolean updateHeartbeat(Execution e, Instant newHeartbeat)`
- `List<Execution> getExecutionsFailingLongerThan(Duration interval)`
- `Optional<Execution> getExecution(TaskInstance taskInstance)`
- `Optional<Execution> getExecution(String taskName, String taskInstanceId)`
- `int removeExecutions(String taskName)`
- `void verifySupportsLockAndFetch()`
- `List<TaskSummary> map(ResultSet rs) throws SQLException`
- `List<Execution> map(ResultSet resultSet) throws SQLException`
- `Void map(ResultSet rs) throws SQLException`
- `T get()`
- `UnresolvedFilter(List<UnresolvedTask> unresolved)`
- `boolean isActive()`
- `String andCondition()`
- `String getQueryPart()`
- `int setParameters(PreparedStatement p, int index) throws SQLException`
- `PickedCondition(boolean value)`
- _... 14 more_

### class JdbcTaskRepositoryContext

- `final TaskResolver taskResolver`
- `final String tableName`
- `final SchedulerName schedulerName`
- `final JdbcRunner jdbcRunner`
- `final Supplier<ResultSetMapper<List<Execution>>> resultSetMapper`

### class MariaDBJdbcCustomization

- `MariaDBJdbcCustomization(boolean persistTimestampInUTC)`
- `String getName()`
- `boolean supportsGenericLockAndFetch()`
- `String createGenericSelectForUpdateQuery( String tableName, int limit, String requiredAndCondition, boolean orderByPriority)`

### class MssqlJdbcCustomization

- `MssqlJdbcCustomization()`
- `MssqlJdbcCustomization(boolean persistTimestampInUTC)`
- `void setTaskData(PreparedStatement p, int index, byte[] value) throws SQLException`
- `String getName()`
- `boolean supportsGenericLockAndFetch()`
- `String createSelectDueQuery( String tableName, int limit, String andCondition, boolean orderByPriority)`
- `String createGenericSelectForUpdateQuery( String tableName, int limit, String requiredAndCondition, boolean orderByPriority)`

### class MySQL8JdbcCustomization

- `MySQL8JdbcCustomization(boolean persistTimestampInUTC)`
- `String getName()`
- `boolean supportsGenericLockAndFetch()`
- `String createGenericSelectForUpdateQuery( String tableName, int limit, String requiredAndCondition, boolean orderByPriority)`

### class MySQLBaseJdbcCustomization

- `MySQLBaseJdbcCustomization(boolean persistTimestampInUTC)`
- `void setInstant(PreparedStatement p, int index, Instant value) throws SQLException`
- `Instant getInstant(ResultSet rs, String columnName) throws SQLException`
- `String getQueryLimitPart(int limit)`

### class MySQLJdbcCustomization

- `MySQLJdbcCustomization(boolean persistTimestampInUTC)`
- `String getName()`

### class OracleJdbcCustomization

- `OracleJdbcCustomization(boolean persistTimestampInUTC)`
- `String getName()`
- `boolean supportsGenericLockAndFetch()`
- `String createGenericSelectForUpdateQuery( String tableName, int limit, String requiredAndCondition, boolean orderByPriority)`

### class PostgreSqlJdbcCustomization

- `PostgreSqlJdbcCustomization( boolean useGenericLockAndFetch, boolean persistTimestampInUTC)`
- `String getName()`
- `String getQueryLimitPart(int limit)`
- `boolean supportsSingleStatementLockAndFetch()`
- `boolean supportsGenericLockAndFetch()`
- `String createGenericSelectForUpdateQuery( String tableName, int limit, String requiredAndCondition, boolean orderByPriority)`
- `List<Execution> lockAndFetchSingleStatement( JdbcTaskRepositoryContext ctx, Instant now, int limit, boolean orderByPriority)`

### class Queries

- `static String selectForUpdate( String tableName, String orderPart, String limitPart, String requiredAndCondition, String postgresOracleStyleForUpdate, String sqlServerStyleForUpdate, String mysqlOracleStyleForUpdate)`
- `static String postgresSqlLimitPart(int limit)`
- `static String ansiSqlLimitPart(int limit)`
- `static String ansiSqlOrderPart(boolean orderByPriority)`

### class QueryBuilder


### class SqliteJdbcCustomization

- `SqliteJdbcCustomization()`
- `String getName()`
- `String getQueryLimitPart(int limit)`

## `scheduler/logging`


### class ConfigurableLogger

- `void log(String format, Throwable cause, Object... arguments)`
- `static ConfigurableLogger create(Logger logger, LogLevel logLevel, boolean logStackTrace)`
- `void log(String format, Throwable cause, Object... arguments)`

### enum LogLevel


## `scheduler/serializer`


### class GsonSerializer

- `static final Charset CHARSET = StandardCharsets.UTF_8`
- `static GsonBuilder getDefaultGson()`
- `GsonSerializer()`
- `GsonSerializer(Gson gson)`
- `GsonSerializer(Consumer<GsonBuilder> gsonCustomizer)`
- `byte[] serialize(Object object)`
- `<T> T deserialize(Class<T> clazz, byte[] serializedData)`

### class Jackson3Serializer

- `Jackson3Serializer()`
- `Jackson3Serializer(JsonMapper jsonMapper)`
- `Jackson3Serializer(Consumer<JsonMapper> jsonMapperCustomizer)`
- `static JsonMapper getDefaultJsonMapper()`
- `byte[] serialize(Object object)`
- `<T> T deserialize(Class<T> clazz, byte[] serializedData)`

### class JacksonSerializer

- `static ObjectMapper getDefaultObjectMapper()`
- `JacksonSerializer()`
- `JacksonSerializer(ObjectMapper objectMapper)`
- `JacksonSerializer(Consumer<ObjectMapper> objectMapperCustomizer)`
- `byte[] serialize(Object object)`
- `<T> T deserialize(Class<T> clazz, byte[] serializedData)`

### class JavaSerializer

- `byte[] serialize(Object data)`
- `<T> T deserialize(Class<T> clazz, byte[] serializedData)`

### interface Serializer


### class SerializerWithFallbackDeserializers

- `SerializerWithFallbackDeserializers( Serializer serializer, Serializer fallbackDeserializer)`
- `byte[] serialize(Object data)`
- `<T> T deserialize(Class<T> clazz, byte[] serializedData)`

## `scheduler/serializer/gson`


### class DurationAdapter

- `void write(JsonWriter jsonWriter, Duration duration) throws IOException`
- `Duration read(JsonReader jsonReader) throws IOException`

### class InstantAdapter

- `void write(JsonWriter jsonWriter, Instant instant) throws IOException`
- `Instant read(JsonReader jsonReader) throws IOException`

### class LocalTimeAdapter

- `void write(JsonWriter jsonWriter, LocalTime localTime) throws IOException`
- `LocalTime read(JsonReader jsonReader) throws IOException`

### class RuntimeTypeAdapterFactory

- `static <T> RuntimeTypeAdapterFactory<T> of( Class<T> baseType, String typeFieldName, boolean maintainType)`
- `static <T> RuntimeTypeAdapterFactory<T> of(Class<T> baseType, String typeFieldName)`
- `static <T> RuntimeTypeAdapterFactory<T> of(Class<T> baseType)`
- `RuntimeTypeAdapterFactory<T> recognizeSubtypes()`
- `RuntimeTypeAdapterFactory<T> registerSubtype(Class<? extends T> type, String label)`
- `RuntimeTypeAdapterFactory<T> registerSubtype(Class<? extends T> type)`
- `<R> TypeAdapter<R> create(Gson gson, TypeToken<R> type)`
- `R read(JsonReader in) throws IOException`
- `void write(JsonWriter out, R value) throws IOException`

### class ZoneIdAdapter

- `void write(JsonWriter jsonWriter, ZoneId zoneId) throws IOException`
- `ZoneId read(JsonReader jsonReader) throws IOException`

## `scheduler/serializer/jackson`


### class InstantDeserializer

- `InstantDeserializer()`
- `InstantDeserializer(Class<Instant> t)`
- `Instant deserialize(JsonParser jsonParser, DeserializationContext deserializationContext) throws IOException`

### class InstantSerializer

- `InstantSerializer()`
- `InstantSerializer(Class<Instant> t)`
- `void serialize( Instant instant, JsonGenerator jsonGenerator, SerializerProvider serializerProvider) throws IOException`

### class ScheduleMixin


## `scheduler/serializer/jackson3`


### class InstantDeserializer

- `Instant deserialize( tools.jackson.core.JsonParser p, tools.jackson.databind.DeserializationContext ctxt) throws JacksonException`

### class InstantSerializer

- `void serialize( Instant value, tools.jackson.core.JsonGenerator gen, SerializationContext ctxt) throws JacksonException`

## `scheduler/stats`


### class MicrometerStatsRegistry

- `MicrometerStatsRegistry( MeterRegistry meterRegistry, List<? extends Task<?>> expectedTasks)`
- `void register(SchedulerStatsEvent e)`
- `void register(CandidateStatsEvent e)`
- `void register(ExecutionStatsEvent e)`
- `void registerSingleCompletedExecution(ExecutionComplete completeEvent)`

### interface StatsRegistry

- `void register(SchedulerStatsEvent e)`
- `void register(CandidateStatsEvent e)`
- `void register(ExecutionStatsEvent e)`
- `void registerSingleCompletedExecution(ExecutionComplete completeEvent)`

### class StatsRegistryAdapter

- `StatsRegistryAdapter(StatsRegistry statsRegistry)`
- `void onExecutionScheduled(TaskInstanceId taskInstanceId, Instant executionTime)`
- `void onExecutionStart(CurrentlyExecuting currentlyExecuting)`
- `void onExecutionComplete(ExecutionComplete executionComplete)`
- `void onExecutionDead(Execution execution)`
- `void onExecutionFailedHeartbeat(CurrentlyExecuting currentlyExecuting)`
- `void onSchedulerEvent(SchedulerEventType type)`
- `void onCandidateEvent(CandidateEventType type)`

## `scheduler/task`


### class AbstractTask

- `final String name`
- `AbstractTask( String name, Class<T> dataClass, FailureHandler<T> failureHandler, DeadExecutionHandler<T> deadExecutionHandler)`
- `AbstractTask( String name, Class<T> dataClass, FailureHandler<T> failureHandler, DeadExecutionHandler<T> deadExecutionHandler, int defaultPriority)`
- `int getDefaultPriority()`
- `String getName()`
- `Class<T> getDataClass()`
- `TaskInstance<T> instance(String id)`
- `TaskInstance<T> instance(String id, T data)`
- `TaskInstance.Builder<T> instanceBuilder(String id)`
- `FailureHandler<T> getFailureHandler()`
- `DeadExecutionHandler<T> getDeadExecutionHandler()`
- `String toString()`

### interface CompletionHandler

- `void complete( ExecutionComplete executionComplete, ExecutionOperations<T> executionOperations)`
- `OnCompleteReschedule(Schedule schedule)`
- `OnCompleteReschedule(Schedule schedule, T newData)`
- `void complete( ExecutionComplete executionComplete, ExecutionOperations<T> executionOperations)`
- `String toString()`
- `OnCompleteReplace(String newTaskName)`
- `OnCompleteReplace(String newTaskName, NEXT_TYPE newData)`
- `OnCompleteReplace(TaskDescriptor<NEXT_TYPE> newTask, NEXT_TYPE newData)`
- `OnCompleteReplace( Function<TaskInstance<CURRENT_TYPE>, SchedulableInstance<NEXT_TYPE>> newInstanceCreator)`
- `void complete( ExecutionComplete executionComplete, ExecutionOperations<CURRENT_TYPE> executionOperations)`
- `String toString()`

### interface DeadExecutionHandler

- `void deadExecution( ExecutionComplete executionComplete, ExecutionOperations<T> executionOperations)`
- `void deadExecution( ExecutionComplete executionComplete, ExecutionOperations<T> executionOperations)`

### class Execution

- `final TaskInstance taskInstance`
- `final Instant executionTime`
- `final boolean picked`
- `final String pickedBy`
- `int consecutiveFailures`
- `final Instant lastHeartbeat`
- `final long version`
- `final Instant lastFailure`
- `final Instant lastSuccess`
- `Execution(Instant executionTime, TaskInstance taskInstance)`
- `Execution( Instant executionTime, TaskInstance taskInstance, boolean picked, String pickedBy, Instant lastSuccess, Instant lastFailure, int consecutiveFailures, Instant lastHeartbeat, long version)`
- `Instant getExecutionTime()`
- `boolean isPicked()`
- `Execution updateToPicked(String newPickedBy, Instant newLastHeartbeat)`
- `boolean equals(Object o)`
- `int hashCode()`
- `String toString()`
- `String getTaskName()`
- `String getId()`

### class ExecutionComplete

- `static ExecutionComplete success( Execution execution, Instant timeStarted, Instant timeDone)`
- `static ExecutionComplete failure( Execution execution, Instant timeStarted, Instant timeDone, Throwable cause)`
- `static ExecutionComplete simulatedSuccess(Instant timeDone)`
- `Execution getExecution()`
- `Instant getTimeDone()`
- `Duration getDuration()`
- `Result getResult()`
- `Optional<Throwable> getCause()`

### class ExecutionContext

- `ExecutionContext( SchedulerState schedulerState, Execution execution, SchedulerClient schedulerClient, CurrentlyExecuting currentlyExecuting)`
- `SchedulerState getSchedulerState()`
- `SchedulerClient getSchedulerClient()`
- `Execution getExecution()`
- `CurrentlyExecuting getCurrentlyExecuting()`

### class ExecutionFailed

- `ExecutionFailed(Execution execution, Instant timeDone, Throwable cause)`
- `Execution getExecution()`
- `Instant getTimeDone()`
- `Optional<Throwable> getCause()`

### interface ExecutionHandler


### class ExecutionOperations

- `ExecutionOperations( TaskRepository taskRepository, SchedulerListeners schedulerListeners, Execution execution, Clock clock)`
- `void stop()`
- `void remove()`
- `void removeAndScheduleNew(SchedulableInstance<?> schedulableInstance)`
- `void reschedule(ExecutionComplete completed, Instant nextExecutionTime)`
- `void reschedule(ExecutionComplete completed, Instant nextExecutionTime, T newData)`

### interface FailureHandler

- `MaxRetriesBuilder<T> withBackoff(Duration initialDelay, double multiplier)`
- `MaxRetriesBuilder<T> withBackoff(Duration initialDelay)`
- `MaxRetriesBuilder<T> retryEvery(Duration delay)`
- `FailureHandler<T> thenRemove()`
- `FailureHandler<T> thenRemove(MaxRetriesExceededListener listener)`
- `FailureHandler<T> then(FailureHandler<T> handler)`
- `FailureHandler<T> then(FailureHandler<T> handler, MaxRetriesExceededListener listener)`
- `ExponentialBackoffFailureHandler(Duration sleepDuration)`
- `ExponentialBackoffFailureHandler(Duration sleepDuration, double exponentialRate)`
- `void onFailure( final ExecutionComplete executionComplete, final ExecutionOperations<T> executionOperations)`
- `MaxRetriesFailureHandler(int maxRetries, FailureHandler<T> failureHandler)`
- `MaxRetriesFailureHandler( int maxRetries, FailureHandler<T> failureHandler, BiConsumer<ExecutionComplete, ExecutionOperations<T>> maxRetriesExceededHandler)`
- `void onFailure( final ExecutionComplete executionComplete, final ExecutionOperations<T> executionOperations)`
- `OnFailureRetryLater(Duration sleepDuration)`
- `void onFailure( ExecutionComplete executionComplete, ExecutionOperations<T> executionOperations)`
- `OnFailureReschedule(Schedule schedule)`
- `void onFailure( ExecutionComplete executionComplete, ExecutionOperations<T> executionOperations)`
- `void onFailure( ExecutionComplete executionComplete, ExecutionOperations<T> executionOperations)`

### interface HasTaskName

- `SimpleTaskName(String name)`
- `String getTaskName()`

### interface MaxRetriesExceededListener

- `void onMaxRetriesExceeded(ExecutionComplete executionComplete)`

### record NewValue


### interface NextExecutionTime


### interface OnStartup


### class Priority

- `static final int HIGH = 90`
- `static final int MEDIUM = 50`
- `static final int LOW = 10`

### record RescheduleUpdate

- `static Builder toExecutionTime(Instant executionTime)`
- `static class Builder`
- `Builder(Instant executionTime)`
- `Builder data(@Nullable Object data)`
- `Builder lastSuccess(@Nullable Instant lastSuccess)`
- `Builder lastFailure(@Nullable Instant lastFailure)`
- `Builder consecutiveFailures(int count)`
- `Builder resetFailures()`
- `RescheduleUpdate build()`

### interface SchedulableInstance

- `Builder(String taskName, String id)`
- `SchedulableInstance.Builder<T> data(Supplier<T> dataSupplier)`
- `SchedulableInstance.Builder<T> data(T data)`
- `SchedulableInstance.Builder<T> priority(int priority)`
- `SchedulableInstance<T> scheduledTo(Instant executionTime)`

### class SchedulableTaskInstance

- `SchedulableTaskInstance(TaskInstance<T> taskInstance, NextExecutionTime executionTime)`
- `SchedulableTaskInstance(TaskInstance<T> taskInstance, Instant executionTime)`
- `TaskInstance<T> getTaskInstance()`
- `Instant getNextExecutionTime(Instant currentTime)`

### class ScheduledTaskInstance

- `ScheduledTaskInstance(TaskInstance<?> taskInstance, Instant executionTime)`
- `static ScheduledTaskInstance fixExecutionTime( SchedulableInstance<?> schedulableInstance, Clock clock)`
- `TaskInstance<?> getTaskInstance()`
- `Instant getExecutionTime()`
- `String getTaskName()`
- `String getId()`

### interface StateReturningExecutionHandler


### interface Task


### interface TaskDescriptor

- `SimpleTaskDescriptor(String name, Class<T> dataClass)`
- `String getTaskName()`
- `Class<T> getDataClass()`

### class TaskInstance

- `TaskInstance(String taskName, String id)`
- `TaskInstance(String taskName, String id, T data)`
- `TaskInstance(String taskName, String id, Supplier<T> dataSupplier, int priority)`
- `String getTaskAndInstance()`
- `String getTaskName()`
- `String getId()`
- `T getData()`
- `int getPriority()`
- `boolean equals(Object o)`
- `int hashCode()`
- `String toString()`
- `static class Builder<T>`
- `Builder(String taskName, String id)`
- `Builder<T> dataSupplier(Supplier<T> dataSupplier)`
- `Builder<T> data(T data)`
- `Builder<T> priority(int priority)`
- `TaskInstance<T> build()`
- `SchedulableInstance<T> scheduledTo(Instant executionTime)`
- `SchedulableInstance<T> scheduledAccordingToData()`

### interface TaskInstanceId

- `StandardTaskInstanceId(String taskName, String id)`
- `String getTaskName()`
- `String getId()`
- `boolean equals(Object o)`
- `int hashCode()`

### class TaskWithDataDescriptor

- `TaskWithDataDescriptor(String taskName, Class<T> dataClass)`
- `String getTaskName()`
- `Class<T> getDataClass()`
- `TaskInstanceId instanceId(String id)`

### class TaskWithoutDataDescriptor

- `TaskWithoutDataDescriptor(String taskName)`
- `String getTaskName()`
- `Class<Void> getDataClass()`
- `TaskInstanceId instanceId(String id)`

### interface VoidExecutionHandler


## `scheduler/task/helper`


### class CustomTask

- `static final int DEFAULT_PRIORITY = Priority.MEDIUM`
- `CustomTask( String name, Class<T> dataClass, ScheduleOnStartup<T> scheduleOnStartup, Function<Instant, Instant> defaultExecutionTime, FailureHandler<T> failureHandler, DeadExecutionHandler<T> deadExecutionHandler)`
- `CustomTask( String name, Class<T> dataClass, ScheduleOnStartup<T> scheduleOnStartup, Function<Instant, Instant> defaultExecutionTime, FailureHandler<T> failureHandler, DeadExecutionHandler<T> deadExecutionHandler, int defaultPriority)`
- `SchedulableInstance<T> schedulableInstance(String id)`
- `SchedulableInstance<T> schedulableInstance(String id, T data)`
- `void onStartup(SchedulerClient scheduler, Clock clock)`
- `String toString()`

### class OneTimeTask

- `static final int DEFAULT_PRIORITY = Priority.MEDIUM`
- `OneTimeTask(String name, Class<T> dataClass)`
- `OneTimeTask(String name, Class<T> dataClass, FailureHandler<T> failureHandler)`
- `OneTimeTask( String name, Class<T> dataClass, FailureHandler<T> failureHandler, DeadExecutionHandler<T> deadExecutionHandler)`
- `OneTimeTask( String name, Class<T> dataClass, FailureHandler<T> failureHandler, DeadExecutionHandler<T> deadExecutionHandler, int defaultPriority)`
- `SchedulableInstance<T> schedulableInstance(String id)`
- `SchedulableInstance<T> schedulableInstance(String id, T data)`
- `CompletionHandler<T> execute( TaskInstance<T> taskInstance, ExecutionContext executionContext)`
- `abstract void executeOnce(TaskInstance<T> taskInstance, ExecutionContext executionContext)`
- `String toString()`

### class PlainScheduleAndData

- `PlainScheduleAndData(Schedule schedule)`
- `PlainScheduleAndData(Schedule schedule, Object data)`
- `Schedule getSchedule()`
- `Object getData()`
- `boolean equals(Object o)`
- `int hashCode()`
- `String toString()`

### class RecurringTask

- `static final String INSTANCE = "recurring"`
- `static final int DEFAULT_PRIORITY = Priority.HIGH`
- `RecurringTask(String name, Schedule schedule, Class<T> dataClass)`
- `RecurringTask( String name, Schedule schedule, Class<T> dataClass, FailureHandler<T> failureHandler)`
- `RecurringTask(String name, Schedule schedule, Class<T> dataClass, T initialData)`
- `RecurringTask( String name, Schedule schedule, Class<T> dataClass, ScheduleRecurringOnStartup<T> scheduleOnStartup, FailureHandler<T> failureHandler, DeadExecutionHandler<T> deadExecutionHandler)`
- `RecurringTask( String name, Schedule schedule, Class<T> dataClass, ScheduleRecurringOnStartup<T> scheduleOnStartup, FailureHandler<T> failureHandler, DeadExecutionHandler<T> deadExecutionHandler, int defaultPriority)`
- `SchedulableInstance<T> schedulableInstance(String id)`
- `SchedulableInstance<T> schedulableInstance(String id, T data)`
- `void onStartup(SchedulerClient scheduler, Clock clock)`
- `CompletionHandler<T> execute( TaskInstance<T> taskInstance, ExecutionContext executionContext)`
- `abstract void executeRecurringly( TaskInstance<T> taskInstance, ExecutionContext executionContext)`
- `TaskInstanceId getDefaultTaskInstance()`
- `String toString()`

### class RecurringTaskWithPersistentSchedule

- `static final int DEFAULT_PRIORITY = RecurringTask.DEFAULT_PRIORITY`
- `RecurringTaskWithPersistentSchedule(String name, Class<T> dataClass)`
- `RecurringTaskWithPersistentSchedule( String name, Class<T> dataClass, FailureHandler<T> onFailure)`
- `RecurringTaskWithPersistentSchedule( String name, Class<T> dataClass, FailureHandler<T> onFailure, int defaultPriority)`
- `RecurringTaskWithPersistentSchedule( String name, Class<T> dataClass, FailureHandler<T> onFailure, DeadExecutionHandler<T> deadExecutionHandler, int defaultPriority)`
- `TaskInstance<T> instance(String id)`
- `SchedulableInstance<T> schedulableInstance(String id)`
- `SchedulableInstance<T> schedulableInstance(String id, T data)`
- `String toString()`

### interface ScheduleAndData


### interface ScheduleOnStartup


### class ScheduleOnceOnStartup

- `void apply(SchedulerClient scheduler, Clock clock, Task<T> task)`

### class ScheduleRecurringOnStartup

- `void apply(SchedulerClient scheduler, Clock clock, Task<T> task)`

### class Tasks

- `static final Duration DEFAULT_RETRY_INTERVAL = Duration.ofMinutes(5)`
- `static RecurringTaskBuilder<Void> recurring(String name, Schedule schedule)`
- `static <T> RecurringTaskBuilder<T> recurring( String name, Schedule schedule, Class<T> dataClass)`
- `static <T> RecurringTaskBuilder<T> recurring( TaskDescriptor<T> descriptor, Schedule schedule)`
- `static <T extends ScheduleAndData> RecurringTaskWithPersistentScheduleBuilder<T> recurringWithPersistentSchedule( String name, Class<T> dataClass)`
- `static <T extends ScheduleAndData> RecurringTaskWithPersistentScheduleBuilder<T> recurringWithPersistentSchedule( TaskDescriptor<T> descriptor)`
- `static OneTimeTaskBuilder<Void> oneTime(String name)`
- `static <T> OneTimeTaskBuilder<T> oneTime(String name, Class<T> dataClass)`
- `static <T> OneTimeTaskBuilder<T> oneTime(TaskDescriptor<T> descriptor)`
- `static <T> TaskBuilder<T> custom(String name, Class<T> dataClass)`
- `static <T> TaskBuilder<T> custom(TaskDescriptor<T> taskDescriptor)`
- `static class RecurringTaskBuilder<T>`
- `RecurringTaskBuilder(String name, Schedule schedule, Class<T> dataClass)`
- `RecurringTaskBuilder<T> onFailureReschedule()`
- `RecurringTaskBuilder<T> onDeadExecutionRevive()`
- `RecurringTaskBuilder<T> onFailure(FailureHandler<T> failureHandler)`
- `RecurringTaskBuilder<T> onDeadExecution(DeadExecutionHandler<T> deadExecutionHandler)`
- `RecurringTaskBuilder<T> initialData(T initialData)`
- `RecurringTaskBuilder<T> defaultPriority(int priority)`
- `RecurringTaskBuilder<T> doNotScheduleOnStartup()`
- `RecurringTask<T> execute(VoidExecutionHandler<T> executionHandler)`
- `RecurringTask<T> executeStateful(StateReturningExecutionHandler<T> executionHandler)`
- `static class RecurringTaskWithPersistentScheduleBuilder<T extends ScheduleAndData>`
- `RecurringTaskWithPersistentScheduleBuilder(String name, Class<T> dataClass)`
- `RecurringTaskWithPersistentScheduleBuilder<T> defaultPriority(int priority)`
- `RecurringTaskWithPersistentScheduleBuilder<T> onFailure( FailureHandler<T> failureHandler)`
- `RecurringTaskWithPersistentScheduleBuilder<T> onDeadExecutionRevive()`
- `RecurringTaskWithPersistentScheduleBuilder<T> onDeadExecution( DeadExecutionHandler<T> deadExecutionHandler)`
- `RecurringTaskWithPersistentSchedule<T> execute( VoidExecutionHandler<T> executionHandler)`
- `RecurringTaskWithPersistentSchedule<T> executeStateful( StateReturningExecutionHandler<T> executionHandler)`
- `static class OneTimeTaskBuilder<T>`
- `OneTimeTaskBuilder(String name, Class<T> dataClass)`
- `OneTimeTaskBuilder<T> onFailureRetryLater()`
- `OneTimeTaskBuilder<T> onDeadExecutionRevive()`
- `OneTimeTaskBuilder<T> onFailure(FailureHandler<T> failureHandler)`
- `OneTimeTaskBuilder<T> onDeadExecution(DeadExecutionHandler<T> deadExecutionHandler)`
- `OneTimeTaskBuilder<T> defaultPriority(int priority)`
- `OneTimeTask<T> execute(VoidExecutionHandler<T> executionHandler)`
- `static class TaskBuilder<T>`
- `TaskBuilder(String name, Class<T> dataClass)`
- _... 9 more_

## `scheduler/task/schedule`


### class CompositeParser

- `Optional<Schedule> parse(String scheduleString)`
- `List<String> examples()`

### class CronSchedule

- `CronSchedule(String pattern)`
- `CronSchedule(String pattern, ZoneId zoneId)`
- `CronSchedule(String pattern, ZoneId zoneId, CronStyle cronStyle)`
- `Instant getNextExecutionTime(ExecutionComplete executionComplete)`
- `boolean isDeterministic()`
- `final boolean equals(Object o)`
- `final int hashCode()`
- `String toString()`
- `String getPattern()`
- `ZoneId getZoneId()`
- `CronStyle getCronStyle()`
- `Optional<ZonedDateTime> nextExecution(ZonedDateTime date)`
- `Optional<Duration> timeToNextExecution(ZonedDateTime date)`
- `Optional<ZonedDateTime> lastExecution(ZonedDateTime date)`
- `Optional<Duration> timeFromLastExecution(ZonedDateTime date)`
- `boolean isMatch(ZonedDateTime date)`
- `boolean isDisabled()`

### enum CronStyle


### class Daily

- `Daily(LocalTime... times)`
- `Daily(List<LocalTime> times)`
- `Daily(ZoneId zone, LocalTime... times)`
- `Daily(ZoneId zone, List<LocalTime> times)`
- `Instant getNextExecutionTime(ExecutionComplete executionComplete)`
- `boolean isDeterministic()`
- `final boolean equals(Object o)`
- `final int hashCode()`
- `String toString()`

### class DailyParser

- `Schedule matchedSchedule(MatchResult matchResult)`

### class DisabledSchedule

- `Instant getNextExecutionTime(ExecutionComplete executionComplete)`
- `boolean isDeterministic()`
- `Instant getInitialExecutionTime(Instant now)`
- `boolean isDisabled()`

### class DisabledScheduleParser

- `Schedule matchedSchedule(MatchResult matchResult)`

### class FixedDelay

- `static FixedDelay of(Duration duration)`
- `static FixedDelay ofMillis(long millis)`
- `static FixedDelay ofSeconds(int seconds)`
- `static FixedDelay ofMinutes(int minutes)`
- `static FixedDelay ofHours(int hours)`
- `Instant getNextExecutionTime(ExecutionComplete executionComplete)`
- `Instant getInitialExecutionTime(Instant now)`
- `boolean isDeterministic()`
- `final boolean equals(Object o)`
- `final int hashCode()`
- `String toString()`

### class FixedDelayParser

- `Schedule matchedSchedule(MatchResult matchResult)`

### interface Parser


### class PersistentCronSchedule

- `PersistentCronSchedule(String cronPattern)`
- `PersistentCronSchedule(String cronPattern, Object data)`
- `PersistentCronSchedule(String cronPattern, ZoneId zoneId)`
- `PersistentCronSchedule(String cronPattern, ZoneId zoneId, Object data)`
- `String toString()`
- `Schedule getSchedule()`
- `Object getData()`

### class RegexBasedParser

- `RegexBasedParser(Pattern pattern, List<String> examples)`
- `Optional<Schedule> parse(String scheduleString)`
- `List<String> examples()`
- `abstract Schedule matchedSchedule(MatchResult result)`

### interface Schedule


### class Schedules

- `static Daily daily(LocalTime... times)`
- `static Daily daily(ZoneId zone, LocalTime... times)`
- `static FixedDelay fixedDelay(Duration delay)`
- `static CronSchedule cron(String cronPattern)`
- `static CronSchedule cron(String cronPattern, ZoneId zoneId)`
- `static Schedule parseSchedule(String scheduleString)`
- `static class UnrecognizableSchedule extends RuntimeException`
- `UnrecognizableSchedule(String inputSchedule)`
- `UnrecognizableSchedule(String inputSchedule, List<String> examples)`

## `scheduler/testhelper`


### class ManualScheduler

- `SettableClock getClock()`
- `void tick(Duration moveClockForward)`
- `void setTime(Instant newtime)`
- `void runAnyDueExecutions()`
- `void runDeadExecutionDetection()`
- `void start()`

### class SettableClock

- `Instant now`
- `SettableClock()`
- `Instant now()`
- `void set(Instant newNow)`
- `void tick(Duration toAdd)`

### class TestHelper

- `static ManualSchedulerBuilder createManualScheduler( DataSource dataSource, Task<?>... knownTasks)`
- `static ManualSchedulerBuilder createManualScheduler( DataSource dataSource, List<Task<?>> knownTasks)`
- `static class ManualSchedulerBuilder extends SchedulerBuilder`
- `ManualSchedulerBuilder(DataSource dataSource, List<Task<?>> knownTasks)`
- `ManualSchedulerBuilder clock(SettableClock clock)`
- `<T extends Task<?> & OnStartup> ManualSchedulerBuilder startTasks(List<T> startTasks)`
- `ManualSchedulerBuilder statsRegistry(StatsRegistry statsRegistry)`
- `ManualSchedulerBuilder addSchedulerListener(SchedulerListener listener)`
- `ManualSchedulerBuilder pollingStrategy(PollingStrategyConfig pollingStrategyConfig)`
- `ManualSchedulerBuilder tableName(String tableName)`
- `ManualSchedulerBuilder alwaysPersistTimestampInUTC()`
- `ManualScheduler build()`
- `ManualScheduler start()`
- `void shutdown()`
- `List<Runnable> shutdownNow()`
- `boolean isShutdown()`
- `boolean isTerminated()`
- `boolean awaitTermination(long timeout, TimeUnit unit)`
- `void execute(Runnable command)`
- `void execute(Runnable command)`
- `ScheduledFuture<?> schedule(Runnable command, long delay, TimeUnit unit)`
- `<V> ScheduledFuture<V> schedule(Callable<V> callable, long delay, TimeUnit unit)`
- `ScheduledFuture<?> scheduleAtFixedRate( Runnable command, long initialDelay, long period, TimeUnit unit)`
- `ScheduledFuture<?> scheduleWithFixedDelay( Runnable command, long initialDelay, long delay, TimeUnit unit)`
