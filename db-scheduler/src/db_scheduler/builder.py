"""`SchedulerBuilder` - how a `Scheduler` is actually constructed.

Everything here has a default that works, and the defaults are db-scheduler's: ten threads, a
ten-second poll, a five-minute heartbeat with six misses before an execution is declared dead,
and unresolvable task names deleted after fourteen days.

The one worth understanding before changing it is `missed_heartbeats_limit`. Multiplied by the
heartbeat interval it is how long a machine can be gone before another instance takes its work -
thirty minutes by default. Lower it and a slow database write can hand a running execution to a
second instance; the builder refuses anything below four for that reason.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import timedelta
from typing import Any, Self

from sqlalchemy import Engine

from .clock import Clock, SystemClock
from .event import SchedulerListener, SchedulerListeners
from .executor import PollingStrategyConfig, Waiter
from .jdbc import JdbcCustomization, JdbcTaskRepository, autodetect
from .jdbc.schema import DEFAULT_TABLE_NAME
from .logging import LogLevel
from .pool import DaemonThreadPool, WorkerPool
from .resolver import TaskResolver
from .scheduler import THREAD_PREFIX, ImmediateCheckForDueExecutions, Scheduler
from .scheduler_name import Hostname, SchedulerName
from .serializer import Serializer
from .stats import DefaultStatsRegistry, StatsRegistry
from .stats.adapter import StatsRegistryAdapter
from .task.helper import Task

__all__ = ["SchedulerBuilder", "create"]

LOG = logging.getLogger("db_scheduler.builder")

UPPER_LIMIT_FRACTION_OF_THREADS_FOR_FETCH = 3.0
DEFAULT_POLLING_INTERVAL = timedelta(seconds=10)
DEFAULT_HEARTBEAT_INTERVAL = timedelta(minutes=5)
DEFAULT_MISSED_HEARTBEATS_LIMIT = 6
DEFAULT_DELETION_OF_UNRESOLVED_TASKS_DURATION = timedelta(days=14)
SHUTDOWN_MAX_WAIT = timedelta(minutes=30)
DEFAULT_POLLING_STRATEGY = PollingStrategyConfig(
    PollingStrategyConfig.Type.FETCH, 0.5, UPPER_LIMIT_FRACTION_OF_THREADS_FOR_FETCH
)
DEFAULT_FAILURE_LOG_LEVEL = LogLevel.WARN
LOG_STACK_TRACE_ON_FAILURE = True

#: Below four, a single slow heartbeat write can have an execution declared dead while it is
#: still running - and then two instances are running the same work.
MINIMUM_MISSED_HEARTBEATS_LIMIT = 4


def _must_be_positive(name: str, value: timedelta) -> timedelta:
    """An interval of zero is never what anybody meant, and it is not harmless.

    Both loops wait for their interval and then run. At zero the wait returns instantly, so the
    loop becomes a spin: measured at ~10,000 polls per second (each one a database query) and
    ~900,000 heartbeat rounds per second (a whole core). db-scheduler does not check this;
    refusing a value that can only ever hammer the database costs nothing.
    """
    if value.total_seconds() <= 0:
        raise ValueError(f"{name} must be greater than zero, was: {value}")
    return value


def _must_not_be_negative(name: str, value: timedelta) -> timedelta:
    if value.total_seconds() < 0:
        raise ValueError(f"{name} cannot be negative, was: {value}")
    return value


#: The scheduler's own threads - poll, heartbeat, dead-execution detection - each need a
#: connection when they run, on top of whatever the workers are using.
LOOP_THREADS = 3


def warn_if_the_connection_pool_is_too_small(engine: Engine, executor_threads: int) -> None:
    """A pool with no headroom past the workers is a correctness problem, not a slow one.

    The heartbeat is how a running execution proves it is alive. If every connection is held by
    a task body, the heartbeat cannot write, the row's timestamp stops moving, and another
    instance declares the execution dead and runs it again - two instances doing the same work,
    which is the one thing this library exists to prevent.

    Measured, with three workers each holding a connection: at capacity 3 *every* execution was
    stolen; at capacity 4, none. The boundary is that sharp, so it is worth saying out loud at
    startup rather than leaving to be discovered in production.
    """
    pool = getattr(engine, "pool", None)
    size = getattr(pool, "size", None)
    overflow = getattr(pool, "_max_overflow", None)
    if not callable(size) or not isinstance(overflow, int):
        return  # NullPool, StaticPool and friends: no fixed ceiling to compare against
    if overflow < 0:
        return  # negative max_overflow means unlimited
    capacity = size() + overflow
    if capacity <= 0 or capacity > executor_threads:
        return
    LOG.warning(
        "The connection pool holds at most %d connection(s) but the scheduler runs %d worker "
        "thread(s). If task bodies hold connections, the heartbeat cannot write and running "
        "executions will be declared dead and run a second time by another instance. Give the "
        "engine at least %d connections (pool_size + max_overflow), reduce threads(), or give "
        "the scheduler an engine of its own so task bodies cannot compete with its heartbeat.",
        capacity,
        executor_threads,
        executor_threads + LOOP_THREADS,
    )


class SchedulerBuilder:
    """Fluent, and every setter returns self, as Java's does."""

    def __init__(self, engine: Engine, known_tasks: Iterable[Task[Any]] = ()) -> None:
        self.engine = engine
        self.known_tasks: list[Task[Any]] = list(known_tasks)
        self.start_tasks: list[Any] = []
        self._clock: Clock = SystemClock()
        self._scheduler_name: SchedulerName | None = None
        self.executor_threads = 10
        self._polling_interval = DEFAULT_POLLING_INTERVAL
        self._stats_registry: StatsRegistry | None = DefaultStatsRegistry()
        self._heartbeat_interval = DEFAULT_HEARTBEAT_INTERVAL
        self._serializer: Serializer | None = None
        self._table_name = DEFAULT_TABLE_NAME
        self._enable_immediate_execution = False
        self._executor_service: WorkerPool | None = None
        self._delete_unresolved_after = DEFAULT_DELETION_OF_UNRESOLVED_TASKS_DURATION
        self._jdbc_customization: JdbcCustomization | None = None
        self._shutdown_max_wait = SHUTDOWN_MAX_WAIT
        self.polling_strategy_config = DEFAULT_POLLING_STRATEGY
        self.log_level = DEFAULT_FAILURE_LOG_LEVEL
        self.log_stack_trace = LOG_STACK_TRACE_ON_FAILURE
        self._enable_priority = False
        self.number_of_missed_heartbeats_before_dead = DEFAULT_MISSED_HEARTBEATS_LIMIT
        self._always_persist_timestamp_in_utc = False
        self.scheduler_listeners: list[SchedulerListener] = []
        self.execution_interceptors: list[Any] = []

    # ------------------------------------------------------------------ tasks

    def add_start_tasks(self, *start_tasks: Any) -> Self:
        """Tasks that put themselves in the table at boot - recurring ones, mostly."""
        tasks = list(start_tasks[0]) if len(start_tasks) == 1 and isinstance(
            start_tasks[0], list | tuple
        ) else list(start_tasks)
        self.known_tasks.extend(tasks)
        self.start_tasks.extend(tasks)
        return self

    def add_tasks(self, *tasks: Any) -> Self:
        self.known_tasks.extend(
            list(tasks[0]) if len(tasks) == 1 and isinstance(tasks[0], list | tuple)
            else list(tasks)
        )
        return self

    # ------------------------------------------------------------------ timing

    def polling_interval(self, polling_interval: timedelta) -> Self:
        self._polling_interval = _must_be_positive("polling_interval", polling_interval)
        return self

    def heartbeat_interval(self, duration: timedelta) -> Self:
        self._heartbeat_interval = _must_be_positive("heartbeat_interval", duration)
        return self

    def missed_heartbeats_limit(self, number_of_missed_heartbeats_before_dead: int) -> Self:
        if number_of_missed_heartbeats_before_dead < MINIMUM_MISSED_HEARTBEATS_LIMIT:
            raise ValueError("Heartbeat-limit must be at least 4")
        self.number_of_missed_heartbeats_before_dead = number_of_missed_heartbeats_before_dead
        return self

    def threads(self, number_of_threads: int) -> Self:
        if number_of_threads <= 0:
            raise ValueError(
                f"threads must be greater than zero, was: {number_of_threads}"
            )
        self.executor_threads = number_of_threads
        return self

    def executor_service(self, executor_service: WorkerPool) -> Self:
        """Substitute the pool. A `ThreadPoolExecutor` works, but its threads are non-daemon and
        the interpreter joins them at exit - so a handler that never returns will keep the
        process alive. The default pool is deliberately not that."""
        self._executor_service = executor_service
        return self

    def shutdown_max_wait(self, shutdown_max_wait: timedelta) -> Self:
        # Zero is allowed: "do not wait for running executions" is a legitimate choice.
        self._shutdown_max_wait = _must_not_be_negative(
            "shutdown_max_wait", shutdown_max_wait
        )
        return self

    def delete_unresolved_after(self, delete_after: timedelta) -> Self:
        # Zero is allowed: it means "delete as soon as the name is seen to be unresolvable",
        # which is what a test wants and what someone certain of their deployment may choose.
        self._delete_unresolved_after = _must_not_be_negative(
            "delete_unresolved_after", delete_after
        )
        return self

    # ------------------------------------------------------------------ behaviour

    def add_scheduler_listener(self, scheduler_listener: SchedulerListener) -> Self:
        self.scheduler_listeners.append(scheduler_listener)
        return self

    def add_execution_interceptor(self, interceptor: Any) -> Self:
        self.execution_interceptors.append(interceptor)
        return self

    def stats_registry(self, stats_registry: StatsRegistry) -> Self:
        self._stats_registry = stats_registry
        return self

    def scheduler_name(self, scheduler_name: SchedulerName) -> Self:
        self._scheduler_name = scheduler_name
        return self

    def serializer(self, serializer: Serializer) -> Self:
        self._serializer = serializer
        return self

    def table_name(self, table_name: str) -> Self:
        self._table_name = table_name
        return self

    def enable_immediate_execution(self) -> Self:
        """Wake the poll when something is scheduled to run now, rather than waiting it out."""
        self._enable_immediate_execution = True
        return self

    def jdbc_customization(self, jdbc_customization: JdbcCustomization) -> Self:
        self._jdbc_customization = jdbc_customization
        return self

    def always_persist_timestamp_in_utc(self) -> Self:
        self._always_persist_timestamp_in_utc = True
        return self

    def poll_using_fetch(
        self, lower_limit_fraction_of_threads: float, executions_per_batch_fraction_of_threads: float
    ) -> Self:
        self.polling_strategy_config = PollingStrategyConfig(
            PollingStrategyConfig.Type.FETCH,
            lower_limit_fraction_of_threads,
            executions_per_batch_fraction_of_threads,
        )
        return self

    def poll_using_lock_and_fetch(
        self, lower_limit_fraction_of_threads: float, upper_limit_fraction_of_threads: float
    ) -> Self:
        self.polling_strategy_config = PollingStrategyConfig(
            PollingStrategyConfig.Type.LOCK_AND_FETCH,
            lower_limit_fraction_of_threads,
            upper_limit_fraction_of_threads,
        )
        return self

    def failure_logging(self, log_level: LogLevel, log_stack_trace: bool) -> Self:
        if log_level is None:
            raise ValueError("Log level must not be null")
        self.log_level = log_level
        self.log_stack_trace = log_stack_trace
        return self

    def enable_priority(self) -> Self:
        self._enable_priority = True
        return self

    def clock(self, clock: Clock) -> Self:
        self._clock = clock
        return self

    # ------------------------------------------------------------------ building

    def build(self) -> Scheduler:
        scheduler_name = self._scheduler_name or Hostname()

        task_resolver = TaskResolver(
            SchedulerListeners(self.scheduler_listeners), self._clock, self.known_tasks
        )
        jdbc_customization = self._jdbc_customization or autodetect(
            self.engine.dialect.name,
            getattr(self.engine.dialect, "server_version_info", None),
            self._always_persist_timestamp_in_utc,
        )

        # Two repositories over one engine, as in Java. They differ only in transaction
        # handling there; here they are separate objects for the same reason the Java ones are -
        # so a change to how the client writes cannot affect how the scheduler polls.
        def repository() -> JdbcTaskRepository:
            return JdbcTaskRepository(
                self.engine,
                scheduler_name=scheduler_name.name,
                serializer=self._serializer,
                table_name=self._table_name,
                clock=self._clock,
                customization=jdbc_customization,
                order_by_priority=self._enable_priority,
                persist_timestamp_in_utc=jdbc_customization.persist_timestamp_in_utc,
                task_resolver=task_resolver,
            )

        scheduler_task_repository = repository()
        client_task_repository = repository()

        warn_if_the_connection_pool_is_too_small(self.engine, self.executor_threads)

        executor_service = self._executor_service or DaemonThreadPool(
            max_workers=self.executor_threads, thread_name_prefix=f"{THREAD_PREFIX}-worker-"
        )

        if self._stats_registry is not None:
            self.add_scheduler_listener(StatsRegistryAdapter(self._stats_registry))

        waiter = self.build_waiter()

        LOG.info(
            "Creating scheduler with configuration: threads=%d, pollInterval=%ss, "
            "heartbeat=%ss, enable-immediate-execution=%s, enable-priority=%s, table-name=%s, "
            "name=%s",
            self.executor_threads,
            waiter.duration.total_seconds(),
            self._heartbeat_interval.total_seconds(),
            self._enable_immediate_execution,
            self._enable_priority,
            self._table_name,
            scheduler_name.name,
        )

        scheduler = Scheduler(
            clock=self._clock,
            scheduler_task_repository=scheduler_task_repository,
            client_task_repository=client_task_repository,
            task_resolver=task_resolver,
            threadpool_size=self.executor_threads,
            executor_service=executor_service,
            scheduler_name=scheduler_name,
            execute_due_waiter=waiter,
            heartbeat_interval=self._heartbeat_interval,
            number_of_missed_heartbeats_before_dead=(
                self.number_of_missed_heartbeats_before_dead
            ),
            scheduler_listeners=self.scheduler_listeners,
            execution_interceptors=self.execution_interceptors,
            polling_strategy_config=self.polling_strategy_config,
            delete_unresolved_after=self._delete_unresolved_after,
            shutdown_max_wait=self._shutdown_max_wait,
            log_level=self.log_level,
            log_stack_trace=self.log_stack_trace,
            on_startup=self.start_tasks,
        )

        if self._enable_immediate_execution:
            scheduler.register_scheduler_listener(
                ImmediateCheckForDueExecutions(scheduler, self._clock)
            )
        return scheduler

    def build_waiter(self) -> Waiter:
        return Waiter(self._polling_interval, self._clock)

    def __repr__(self) -> str:
        return (
            f"SchedulerBuilder(threads={self.executor_threads}, "
            f"tasks={len(self.known_tasks)})"
        )


def create(engine: Engine, *known_tasks: Any) -> SchedulerBuilder:
    """`Scheduler.create(dataSource, tasks...)` in Java. The usual entry point."""
    tasks: Sequence[Any]
    if len(known_tasks) == 1 and isinstance(known_tasks[0], list | tuple):
        tasks = known_tasks[0]
    else:
        tasks = known_tasks
    return SchedulerBuilder(engine, tasks)
