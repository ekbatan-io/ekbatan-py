"""`Scheduler` - the thing that actually runs work.

Three loops, on three threads:

    due          poll for work, claim it, hand it to the pool
    detect dead  find rows whose holder stopped heartbeating, and revive them
    heartbeat    say "still working" for everything running here

The dead-execution loop is what makes the whole thing survive a machine dying. An instance that
picks a row and then loses power leaves that row picked for ever; the heartbeat is the evidence
of life, and its absence for `heartbeat_interval * missed_heartbeats_before_dead` is what lets
another instance take the work back.

A `Scheduler` is also a `SchedulerClient` - it can schedule work as well as run it - which is
why nearly half of this delegates.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from .client import ScheduleOptions, SchedulerClient, StandardSchedulerClient
from .clock import Clock, SystemClock
from .event import (
    AbstractSchedulerListener,
    SchedulerEventType,
    SchedulerListener,
    SchedulerListeners,
)
from .executor import (
    Executor,
    PollingStrategyConfig,
    PollStrategy,
    Waiter,
)
from .logging import ConfigurableLogger, LogLevel
from .poll import FetchCandidates, LockAndFetchCandidates
from .scheduled_execution import (
    ScheduledExecution,
    ScheduledExecutionsFilter,
    TaskSummary,
)
from .state import CurrentlyExecuting, HeartbeatConfig, SettableSchedulerState
from .task import (
    Execution,
    ExecutionComplete,
    SchedulableInstance,
    TaskInstance,
    TaskInstanceId,
)

__all__ = [
    "THREAD_PREFIX",
    "TRIGGER_NEXT_BATCH_WHEN_AVAILABLE_THREADS_RATIO",
    "ImmediateCheckForDueExecutions",
    "RunUntilShutdown",
    "Scheduler",
]

LOG = logging.getLogger("db_scheduler.scheduler")

TRIGGER_NEXT_BATCH_WHEN_AVAILABLE_THREADS_RATIO = 0.5
THREAD_PREFIX = "db-scheduler"


class RunUntilShutdown:
    """A loop that keeps going, whatever the body throws.

    The `except` is the point. An unhandled exception in the due-loop - a database that went
    away, a listener that raised - must not end the loop, or the scheduler quietly stops
    scheduling while the process stays up and healthy-looking.
    """

    __slots__ = ("_scheduler_listeners", "_scheduler_state", "_to_run", "_wait_between_runs")

    def __init__(
        self,
        to_run: Callable[[], None],
        wait_between_runs: Waiter,
        scheduler_state: SettableSchedulerState,
        scheduler_listeners: SchedulerListeners,
    ) -> None:
        self._to_run = to_run
        self._wait_between_runs = wait_between_runs
        self._scheduler_state = scheduler_state
        self._scheduler_listeners = scheduler_listeners

    def run(self) -> None:
        while not self._scheduler_state.is_shutting_down:
            if not self._scheduler_state.is_paused:
                try:
                    self._to_run()
                except BaseException:
                    LOG.error("Unhandled exception. Will keep running.", exc_info=True)
                    self._scheduler_listeners.on_scheduler_event(
                        SchedulerEventType.UNEXPECTED_ERROR
                    )
            self._wait_between_runs.do_wait()

    def __call__(self) -> None:
        self.run()


class RunAndLogErrors:
    """The same guarantee for the housekeeping loops, which run on a timer."""

    __slots__ = ("_scheduler_listener", "_to_run")

    def __init__(
        self, to_run: Callable[[], None], scheduler_listener: SchedulerListener
    ) -> None:
        self._to_run = to_run
        self._scheduler_listener = scheduler_listener

    def run(self) -> None:
        try:
            self._to_run()
        except BaseException:
            LOG.error("Unhandled exception. Will keep running.", exc_info=True)
            self._scheduler_listener.on_scheduler_event(SchedulerEventType.UNEXPECTED_ERROR)

    def __call__(self) -> None:
        self.run()


class ImmediateCheckForDueExecutions(AbstractSchedulerListener):
    """Wakes the due-loop when something is scheduled to run *now*.

    Without it, work scheduled for immediately would still wait out the poll interval - which
    for a request handler that schedules a job and expects it to start is the difference between
    "instant" and "up to ten seconds".
    """

    __slots__ = ("_clock", "_scheduler")

    def __init__(self, scheduler: Scheduler, clock: Clock) -> None:
        self._scheduler = scheduler
        self._clock = clock

    def on_execution_scheduled(
        self, task_instance_id: TaskInstanceId, execution_time: datetime
    ) -> None:
        state = self._scheduler.scheduler_state
        if not state.is_started or state.is_shutting_down:
            LOG.debug(
                "Will not act on scheduling event for execution (task: '%s', id: '%s') as "
                "scheduler is starting or shutting down.",
                task_instance_id.task_name,
                task_instance_id.id,
            )
            return
        if execution_time <= self._clock.now():
            LOG.debug(
                "Task-instance scheduled to run directly, triggering check for due executions "
                "(unless it is already running). Task: %s, instance: %s",
                task_instance_id.task_name,
                task_instance_id.id,
            )
            self._scheduler.trigger_check_for_due_executions()


class Scheduler(SchedulerClient):
    """Polls, claims, runs, heartbeats and revives. Also a client."""

    def __init__(
        self,
        clock: Clock,
        scheduler_task_repository: Any,
        client_task_repository: Any,
        task_resolver: Any,
        threadpool_size: int,
        executor_service: ThreadPoolExecutor,
        scheduler_name: Any,
        execute_due_waiter: Waiter,
        heartbeat_interval: timedelta,
        number_of_missed_heartbeats_before_dead: int,
        scheduler_listeners: Sequence[SchedulerListener],
        execution_interceptors: Sequence[Any],
        polling_strategy_config: PollingStrategyConfig,
        delete_unresolved_after: timedelta,
        shutdown_max_wait: timedelta,
        log_level: LogLevel,
        log_stack_trace: bool,
        on_startup: Sequence[Any],
    ) -> None:
        self.clock = clock
        self.scheduler_task_repository = scheduler_task_repository
        self.task_resolver = task_resolver
        self.threadpool_size = threadpool_size
        self.executor = Executor(executor_service, clock)
        self._execute_due_waiter = execute_due_waiter
        self._delete_unresolved_after = delete_unresolved_after
        self._shutdown_max_wait = shutdown_max_wait
        self.on_startup = list(on_startup)
        # Twice the heartbeat interval: checking for dead executions more often than they can
        # possibly appear is pure load on the table.
        self._detect_dead_waiter = Waiter(heartbeat_interval * 2, clock)
        self._heartbeat_interval = heartbeat_interval
        self._number_of_missed_heartbeats_before_dead = number_of_missed_heartbeats_before_dead
        self._heartbeat_waiter = Waiter(heartbeat_interval, clock)
        self._heartbeat_config = HeartbeatConfig(
            heartbeat_interval,
            number_of_missed_heartbeats_before_dead,
            self.max_age_before_considered_dead,
        )
        self.scheduler_listeners = SchedulerListeners(scheduler_listeners)
        self.scheduler_state = SettableSchedulerState()
        self.failure_logger = ConfigurableLogger.create(LOG, log_level, log_stack_trace)
        self._delegate = StandardSchedulerClient(
            client_task_repository, self.scheduler_listeners, clock
        )
        self._threads: list[threading.Thread] = []
        self._stopped = threading.Event()

        if polling_strategy_config.type is PollingStrategyConfig.Type.LOCK_AND_FETCH:
            scheduler_task_repository.verify_supports_lock_and_fetch()
            self.execute_due_strategy: PollStrategy = LockAndFetchCandidates(
                self.executor,
                scheduler_task_repository,
                self,
                threadpool_size,
                self.scheduler_listeners,
                execution_interceptors,
                self.scheduler_state,
                self.failure_logger,
                task_resolver,
                clock,
                polling_strategy_config,
                self.trigger_check_for_due_executions,
                self._heartbeat_config,
            )
        elif polling_strategy_config.type is PollingStrategyConfig.Type.FETCH:
            self.execute_due_strategy = FetchCandidates(
                self.executor,
                scheduler_task_repository,
                self,
                threadpool_size,
                self.scheduler_listeners,
                execution_interceptors,
                self.scheduler_state,
                self.failure_logger,
                task_resolver,
                clock,
                polling_strategy_config,
                self.trigger_check_for_due_executions,
                self._heartbeat_config,
            )
        else:
            raise ValueError(
                f"Unknown polling-strategy type: {polling_strategy_config.type}"
            )
        LOG.info("Using polling-strategy: %s", polling_strategy_config.describe())

    # ------------------------------------------------------------------ lifecycle

    def register_scheduler_listener(self, listener: SchedulerListener) -> None:
        self.scheduler_listeners.add(listener)

    def start(self) -> None:
        LOG.info("Starting scheduler.")
        self.execute_on_startup()

        self._start_thread(
            "due",
            RunUntilShutdown(
                self.execute_due_strategy.run,
                self._execute_due_waiter,
                self.scheduler_state,
                self.scheduler_listeners,
            ),
        )
        self._start_thread(
            "detect-dead",
            _EveryUntilStopped(
                RunAndLogErrors(self.detect_dead_executions, self.scheduler_listeners),
                self._detect_dead_waiter.duration,
                self._stopped,
            ),
        )
        self._start_thread(
            "heartbeat",
            _EveryUntilStopped(
                RunAndLogErrors(self.update_heartbeats, self.scheduler_listeners),
                self._heartbeat_waiter.duration,
                self._stopped,
            ),
        )
        self.scheduler_state.set_started()

    def _start_thread(self, name: str, target: Callable[[], None]) -> None:
        thread = threading.Thread(target=target, name=f"{THREAD_PREFIX}-{name}", daemon=True)
        thread.start()
        self._threads.append(thread)

    def execute_due(self) -> None:
        self.execute_due_strategy.run()

    def execute_on_startup(self) -> None:
        """Let every task that wants to put itself in the table do so.

        One failing task must not stop the others, and must not stop the scheduler starting: a
        recurring task whose schedule cannot be parsed should not take down the instance that
        also runs twenty healthy ones.
        """
        on_startup_client = StandardSchedulerClient(
            self.scheduler_task_repository, clock=self.clock
        )
        for task in self.on_startup:
            try:
                task.on_startup(on_startup_client, self.clock)
            except Exception:
                LOG.error(
                    "Unexpected error while executing OnStartup tasks. Continuing.",
                    exc_info=True,
                )
                self.scheduler_listeners.on_scheduler_event(
                    SchedulerEventType.UNEXPECTED_ERROR
                )

    def stop(self, shutdown_max_wait: timedelta | None = None) -> None:
        if self.scheduler_state.is_shutting_down:
            LOG.warning("Multiple calls to 'stop()'. Scheduler is already stopping.")
            return
        self.scheduler_state.set_is_shutting_down()
        LOG.info("Shutting down Scheduler.")

        self._stopped.set()
        # Wake the loops so they notice the shutdown rather than sleeping out their interval.
        self._execute_due_waiter.wake()
        for thread in self._threads:
            thread.join(timeout=(shutdown_max_wait or self._shutdown_max_wait).total_seconds())
        self.executor.stop(shutdown_max_wait or self._shutdown_max_wait)

    def pause(self) -> None:
        self.scheduler_state.set_paused(True)

    def resume(self) -> None:
        self.scheduler_state.set_paused(False)

    def trigger_check_for_due_executions(self) -> None:
        self._execute_due_waiter.wake_or_skip_next_wait()

    # ------------------------------------------------------------------ housekeeping

    @property
    def max_age_before_considered_dead(self) -> timedelta:
        return self._heartbeat_interval * self._number_of_missed_heartbeats_before_dead

    @property
    def currently_executing(self) -> list[CurrentlyExecuting]:
        return self.executor.currently_executing

    @property
    def currently_executing_with_stale_heartbeat(self) -> list[CurrentlyExecuting]:
        return [
            one
            for one in self.executor.currently_executing
            if one.heartbeat_state.has_stale_heartbeat
        ]

    def get_failing_executions(self, failing_at_least_for: timedelta) -> list[Execution]:
        executions: list[Execution] = (
            self.scheduler_task_repository.get_executions_failing_longer_than(
                failing_at_least_for
            )
        )
        return executions

    def detect_dead_executions(self) -> None:
        """Two jobs: forget task names nobody deploys any more, and revive abandoned rows."""
        from .task.operations import ExecutionOperations

        LOG.debug("Deleting executions with unresolved tasks.")
        for task_name in self.task_resolver.unresolved_task_names(self._delete_unresolved_after):
            # Only after a long grace period - fourteen days by default. A task name that is
            # unresolvable during a deploy comes back; one unresolvable for a fortnight is dead
            # code, and its rows are never going to run.
            LOG.warning(
                "Deleting all executions for task with name '%s'. They have been unresolved for "
                "more than %s",
                task_name,
                self._delete_unresolved_after,
            )
            removed = self.scheduler_task_repository.remove_executions(task_name)
            LOG.info("Removed %d executions", removed)
            self.task_resolver.clear_unresolved(task_name)

        LOG.debug("Checking for dead executions.")
        now = self.clock.now()
        old_age_limit = now - self.max_age_before_considered_dead
        old_executions = self.scheduler_task_repository.get_dead_executions(old_age_limit)

        if not old_executions:
            LOG.debug("No dead executions found.")
        for execution in old_executions:
            LOG.info("Found dead execution. Delegating handling to task. Execution: %s", execution)
            try:
                task = self.task_resolver.resolve(execution)
                if task is None:
                    LOG.error(
                        "Failed to find implementation for task with name '%s' for detected "
                        "dead execution. Either delete the execution from the database, or add "
                        "an implementation for it.",
                        execution.task_name,
                    )
                    continue
                self.scheduler_listeners.on_scheduler_event(SchedulerEventType.DEAD_EXECUTION)
                self.scheduler_listeners.on_execution_dead(execution)
                task.dead_execution_handler.dead_execution(
                    ExecutionComplete.failure(execution, now, now, None),
                    ExecutionOperations[Any](
                        self.scheduler_task_repository,
                        self.scheduler_listeners,
                        execution,
                        self.clock,
                    ),
                )
            except BaseException:
                LOG.error(
                    "Failed while handling dead execution %s. Will be tried again later.",
                    execution,
                    exc_info=True,
                )
                self.scheduler_listeners.on_scheduler_event(
                    SchedulerEventType.UNEXPECTED_ERROR
                )
        self.scheduler_listeners.on_scheduler_event(SchedulerEventType.RAN_DETECT_DEAD)

    def update_heartbeats(self) -> None:
        currently_processing = self.executor.currently_executing
        if not currently_processing:
            LOG.debug("No executions to update heartbeats for. Skipping.")
            return
        LOG.debug(
            "Updating heartbeats for %d executions being processed.", len(currently_processing)
        )
        now = self.clock.now()
        for execution in currently_processing:
            self.update_heartbeat_for_execution(now, execution)
        self.scheduler_listeners.on_scheduler_event(SchedulerEventType.RAN_UPDATE_HEARTBEATS)

    def update_heartbeat_for_execution(
        self, now: datetime, currently_executing: CurrentlyExecuting
    ) -> None:
        """Say this instance is still working on one execution.

        A single failure is expected and survivable: there is a race where the execution
        finished and its row moved between the two. Failing *twice* means something else, and is
        worth a warning - the row is on its way to being declared dead while this instance is
        still working on it.
        """
        execution = currently_executing.execution
        LOG.debug("Updating heartbeat for execution: %s", execution)
        try:
            successful = self.scheduler_task_repository.update_heartbeat_with_retry(
                execution, now, 3
            )
            currently_executing.heartbeat(successful, now)

            if not successful:
                self.scheduler_listeners.on_scheduler_event(SchedulerEventType.FAILED_HEARTBEAT)
                self.scheduler_listeners.on_execution_failed_heartbeat(currently_executing)

            heartbeat_state = currently_executing.heartbeat_state
            if heartbeat_state.failed_heartbeats > 1:
                LOG.warning(
                    "Execution has more than 1 failed heartbeats. Should not happen. Risk of "
                    "being considered dead. See heartbeat-state. Heartbeat-state=%s, "
                    "Execution=%s",
                    heartbeat_state.describe(),
                    execution,
                )
                self.scheduler_listeners.on_scheduler_event(
                    SchedulerEventType.FAILED_MULTIPLE_HEARTBEATS
                )
        except BaseException:
            # Just in case, to avoid a poison-pill execution stopping every other heartbeat.
            LOG.error(
                "Unexpected failure while updating heartbeat for execution %s.",
                execution,
                exc_info=True,
            )
            self.scheduler_listeners.on_scheduler_event(SchedulerEventType.FAILED_HEARTBEAT)
            self.scheduler_listeners.on_scheduler_event(SchedulerEventType.UNEXPECTED_ERROR)
            self.scheduler_listeners.on_execution_failed_heartbeat(currently_executing)

    # ------------------------------------------------------------------ client, delegated

    def schedule(
        self,
        task_instance: TaskInstance[Any] | SchedulableInstance[Any],
        execution_time: datetime | None = None,
        schedule_options: ScheduleOptions | None = None,
    ) -> bool:
        return self._delegate.schedule(task_instance, execution_time, schedule_options)

    def schedule_if_not_exists(
        self,
        task_instance: TaskInstance[Any] | SchedulableInstance[Any],
        execution_time: datetime | None = None,
    ) -> bool:
        return self._delegate.schedule_if_not_exists(task_instance, execution_time)

    def schedule_batch(
        self,
        task_instances: Any,
        execution_time: datetime | None = None,
    ) -> None:
        self._delegate.schedule_batch(task_instances, execution_time)

    def reschedule(
        self,
        task_instance_id: TaskInstanceId | SchedulableInstance[Any],
        new_execution_time: datetime | None = None,
        new_data: Any = None,
    ) -> bool:
        return self._delegate.reschedule(task_instance_id, new_execution_time, new_data)

    def cancel(self, task_instance_id: TaskInstanceId) -> None:
        self._delegate.cancel(task_instance_id)

    def get_scheduled_execution(
        self, task_instance_id: TaskInstanceId
    ) -> ScheduledExecution[Any] | None:
        return self._delegate.get_scheduled_execution(task_instance_id)

    def get_scheduled_executions(
        self, filter: ScheduledExecutionsFilter | None = None
    ) -> list[ScheduledExecution[Any]]:
        return self._delegate.get_scheduled_executions(filter)

    def get_scheduled_executions_for_task(
        self,
        task_name: str,
        data_class: type[Any] | None = None,
        filter: ScheduledExecutionsFilter | None = None,
    ) -> list[ScheduledExecution[Any]]:
        return self._delegate.get_scheduled_executions_for_task(task_name, data_class, filter)

    def get_scheduled_executions_summary_by_task(self) -> list[TaskSummary]:
        return self._delegate.get_scheduled_executions_summary_by_task()

    def fetch_scheduled_executions(
        self,
        consumer: Callable[[ScheduledExecution[Any]], None],
        filter: ScheduledExecutionsFilter | None = None,
    ) -> None:
        self._delegate.fetch_scheduled_executions(consumer, filter)

    def __enter__(self) -> Scheduler:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def __repr__(self) -> str:
        return f"Scheduler(threads={self.threadpool_size}, state={self.scheduler_state!r})"


class _EveryUntilStopped:
    """Java schedules the housekeeping on a `ScheduledExecutorService`; here it is a thread with
    an `Event.wait`, which stops promptly on shutdown rather than sleeping out its period."""

    __slots__ = ("_interval", "_stopped", "_to_run")

    def __init__(
        self, to_run: Callable[[], None], interval: timedelta, stopped: threading.Event
    ) -> None:
        self._to_run = to_run
        self._interval = interval
        self._stopped = stopped

    def __call__(self) -> None:
        while True:
            self._to_run()
            if self._stopped.wait(self._interval.total_seconds()):
                return


#: The default clock, so a caller that does not care need not build one.
DEFAULT_CLOCK = SystemClock()
