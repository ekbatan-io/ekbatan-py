"""Running what the poll picked up.

Four pieces, and the interesting one is `DueExecutionsBatch`. A poll fetches a batch of due
executions and hands them to a thread pool. If the scheduler waited for the whole batch before
polling again, the last few slow executions would leave every other thread idle; if it polled
constantly it would hammer the table. So the batch counts itself down and, once few enough are
left, asks for the next one - overlapping the fetch with the tail of the current batch.

`Waiter` is the other one worth reading. It is a sleep that can be cut short, because an
execution scheduled to run *now* should not have to wait out a poll interval - and it remembers
a wake-up that arrived while nobody was waiting, so the notification is not lost.
"""

from __future__ import annotations

import enum
import logging
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .clock import Clock, SystemClock
from .event import CandidateEventType, SchedulerEventType, SchedulerListeners
from .logging import ConfigurableLogger, LogLevel
from .state import CurrentlyExecuting, HeartbeatConfig, SchedulerState
from .task import Execution, ExecutionComplete, ExecutionContext
from .util import describe

__all__ = [
    "DueExecutionsBatch",
    "ExecutePicked",
    "Executor",
    "PollStrategy",
    "PollingStrategyConfig",
    "Waiter",
]

LOG = logging.getLogger("db_scheduler.executor")

#: How often shutdown checks whether the running executions have finished.
SHUTDOWN_POLL_INTERVAL = 0.05


class Waiter:
    """A sleep that can be cut short, and that does not lose a wake-up.

    `wake_or_skip_next_wait` is the part that matters: a notification arriving while nobody is
    waiting sets a flag instead of vanishing, so the *next* wait returns immediately. Without
    it, work scheduled in the gap between two polls would sit until the interval expired.
    """

    __slots__ = ("_clock", "_condition", "_is_waiting", "_skip_next_wait", "_woken", "duration")

    def __init__(self, duration: timedelta, clock: Clock | None = None) -> None:
        self.duration = duration
        self._clock = clock or SystemClock()
        self._condition = threading.Condition()
        self._woken = False
        self._is_waiting = False
        self._skip_next_wait = False

    def do_wait(self) -> None:
        seconds = self.duration.total_seconds()
        if seconds <= 0:
            return
        wait_until = self._clock.now() + self.duration
        while self._clock.now() < wait_until:
            with self._condition:
                if self._skip_next_wait:
                    LOG.debug(
                        "Waiter has been notified to skip next wait-period. Skipping wait."
                    )
                    self._skip_next_wait = False
                    return
                self._woken = False
                LOG.debug("Waiter start wait.")
                self._is_waiting = True
                self._condition.wait(seconds)
                self._is_waiting = False
                if self._woken:
                    LOG.debug(
                        "Waiter woken, it had %s left to wait.",
                        wait_until - self._clock.now(),
                    )
                    return

    def wake(self) -> bool:
        """Cut the current wait short. False if nobody was waiting."""
        with self._condition:
            if not self._is_waiting:
                return False
            self._woken = True
            self._condition.notify()
            return True

    def wake_or_skip_next_wait(self) -> None:
        with self._condition:
            if not self.wake():
                LOG.debug("Waiter not waiting, instructing to skip next wait.")
                self._skip_next_wait = True

    @property
    def is_waiting(self) -> bool:
        return self._is_waiting

    def __repr__(self) -> str:
        return f"Waiter(duration={self.duration}, waiting={self._is_waiting})"


class PollStrategy:
    """One poll. `FetchCandidates` and `LockAndFetchCandidates` are the two."""

    __slots__ = ()

    def run(self) -> None:
        raise NotImplementedError

    def __call__(self) -> None:
        self.run()


class PollingStrategyConfig:
    """When to fetch the next batch, as fractions of the thread pool.

    The two defaults differ because the strategies cost different amounts. `FETCH` fetches
    candidates and then competes to pick each one, so it asks for three times the pool and
    expects to lose some races. `LOCK_AND_FETCH` claims as it selects, so every row it gets back
    is already this instance's and fetching more than the pool would leave rows picked and idle.
    """

    __slots__ = ("lower_limit_fraction_of_threads", "type", "upper_limit_fraction_of_threads")

    class Type(enum.Enum):
        LOCK_AND_FETCH = "LOCK_AND_FETCH"
        FETCH = "FETCH"

    #: db-scheduler's own defaults, and the constants it exposes them as.
    DEFAULT_FETCH: PollingStrategyConfig
    DEFAULT_SELECT_FOR_UPDATE: PollingStrategyConfig

    def __init__(
        self,
        type: PollingStrategyConfig.Type,
        lower_limit_fraction_of_threads: float,
        upper_limit_fraction_of_threads: float,
    ) -> None:
        if lower_limit_fraction_of_threads >= upper_limit_fraction_of_threads:
            raise ValueError(
                "lower_limit_fraction_of_threads should be lower than "
                "upper_limit_fraction_of_threads"
            )
        if upper_limit_fraction_of_threads < 1.0:
            raise ValueError(
                "upper_limit should be equal to the number of threads or higher, i.e. a "
                "fraction higher than 1"
            )
        self.type = type
        self.lower_limit_fraction_of_threads = lower_limit_fraction_of_threads
        self.upper_limit_fraction_of_threads = upper_limit_fraction_of_threads

    def upper_limit(self, threadpool_size: int) -> int:
        return int(self.upper_limit_fraction_of_threads * threadpool_size)

    def lower_limit(self, threadpool_size: int) -> int:
        return int(self.lower_limit_fraction_of_threads * threadpool_size)

    def describe(self) -> str:
        return (
            f"type={self.type.name}, lowerLimit={self.lower_limit_fraction_of_threads}, "
            f"upperLimit={self.upper_limit_fraction_of_threads}"
        )

    def __repr__(self) -> str:
        return f"PollingStrategyConfig({self.describe()})"


PollingStrategyConfig.DEFAULT_FETCH = PollingStrategyConfig(
    PollingStrategyConfig.Type.FETCH, 0.5, 3.0
)
PollingStrategyConfig.DEFAULT_SELECT_FOR_UPDATE = PollingStrategyConfig(
    PollingStrategyConfig.Type.LOCK_AND_FETCH, 0.5, 1.0
)


class DueExecutionsBatch:
    """One poll's worth of executions, counting itself down.

    The generation number is how a batch knows it has been superseded. A poll that starts while
    an older batch is still draining marks that one stale, so its last execution does not
    trigger a *third* fetch on top of the one already in flight.
    """

    __slots__ = (
        "_executions_left_in_batch",
        "_lock",
        "_possibly_more_executions_in_db",
        "_stale",
        "_triggered_execute_due",
        "_when_to_trigger_check_for_new_batch",
        "generation_number",
    )

    def __init__(
        self,
        generation_number: int,
        executions_added: int,
        possibly_more_executions_in_db: bool,
        when_to_trigger_check_for_new_batch: Callable[[int], bool],
    ) -> None:
        self.generation_number = generation_number
        self._executions_left_in_batch = executions_added
        self._possibly_more_executions_in_db = possibly_more_executions_in_db
        self._when_to_trigger_check_for_new_batch = when_to_trigger_check_for_new_batch
        self._stale = False
        self._triggered_execute_due = False
        self._lock = threading.Lock()

    def mark_batch_as_stale(self) -> None:
        self._stale = True

    def one_execution_done(self, trigger_check_for_new_batch: Callable[[], None]) -> None:
        """Called from the worker threads as each execution finishes.

        Deliberately not synchronized around the trigger: two threads both deciding to ask for a
        new batch is harmless - the waiter they wake is what serializes it - and holding a lock
        across a callback that reaches into the scheduler would be worse.
        """
        with self._lock:
            self._executions_left_in_batch -= 1
            left = self._executions_left_in_batch

        LOG.debug(
            "Batch state: generationNumber:%d, stale:%s, triggeredExecuteDue:%s, "
            "possiblyMoreExecutionsInDb:%s, executionsLeftInBatch:%d",
            self.generation_number,
            self._stale,
            self._triggered_execute_due,
            self._possibly_more_executions_in_db,
            left,
        )
        if (
            not self._stale
            and not self._triggered_execute_due
            and self._possibly_more_executions_in_db
            and self._when_to_trigger_check_for_new_batch(left)
        ):
            LOG.debug("Triggering check for new batch.")
            trigger_check_for_new_batch()
            self._triggered_execute_due = True

    def is_older_generation_than(self, compare_to: int) -> bool:
        return self.generation_number < compare_to

    def __repr__(self) -> str:
        return (
            f"DueExecutionsBatch(generation={self.generation_number}, "
            f"left={self._executions_left_in_batch}, stale={self._stale})"
        )


class Executor:
    """The thread pool, and what is running on it right now.

    `currently_processing` is not bookkeeping for its own sake: it is what the heartbeat thread
    walks, and what a shutdown reports when something refuses to finish.
    """

    __slots__ = (
        "_clock",
        "_currently_in_queue_or_processing",
        "_executor_service",
        "_lock",
        "currently_processing",
    )

    def __init__(self, executor_service: ThreadPoolExecutor, clock: Clock) -> None:
        self._executor_service = executor_service
        self._clock = clock
        self.currently_processing: dict[uuid.UUID, CurrentlyExecuting] = {}
        self._currently_in_queue_or_processing = 0
        self._lock = threading.Lock()

    def add_to_queue(self, run: Callable[[], None], after_done: Callable[[], None]) -> None:
        with self._lock:
            self._currently_in_queue_or_processing += 1

        def wrapped() -> None:
            try:
                run()
            finally:
                # Decrement *before* the callback: the callback may ask how many are in flight
                # in order to decide whether to fetch more, and this one is no longer one of
                # them.
                with self._lock:
                    self._currently_in_queue_or_processing -= 1
                after_done()

        try:
            self._executor_service.submit(wrapped)
        except RuntimeError:
            # The pool is already shut down, so `wrapped` will never run to decrement the
            # count. Left as it was, the in-flight number would stay high for ever and
            # lock-and-fetch would compute "no room to run anything" on every later poll.
            with self._lock:
                self._currently_in_queue_or_processing -= 1
            raise

    @property
    def currently_executing(self) -> list[CurrentlyExecuting]:
        return list(self.currently_processing.values())

    @property
    def number_in_queue_or_processing(self) -> int:
        return self._currently_in_queue_or_processing

    def add_currently_processing(self, currently_executing: CurrentlyExecuting) -> uuid.UUID:
        execution_id = uuid.uuid4()
        with self._lock:
            self.currently_processing[execution_id] = currently_executing
        return execution_id

    def remove_currently_processing(self, execution_id: uuid.UUID) -> None:
        with self._lock:
            removed = self.currently_processing.pop(execution_id, None)
        if removed is None:
            LOG.warning(
                "Released execution was not found in collection of executions currently being "
                "processed. Should never happen. Execution-id: %s",
                execution_id,
            )

    def stop(self, shutdown_max_wait: timedelta) -> None:
        """Let running executions finish, within a bound, then say what did not.

        The bound is the point. Python cannot interrupt a running thread - there is no
        counterpart to Java's `shutdownNow()` - so waiting for a handler that ignores
        `is_shutting_down` is waiting for ever. This stops accepting work, drops what is queued
        but not yet started, waits up to `shutdown_max_wait` for the rest, and then *reports*
        the stragglers rather than blocking on them. An execution still running at that point is
        one whose handler never checks for shutdown, and an operator needs to know which.
        """
        LOG.info("Letting running executions finish. Will wait up to %s.", shutdown_max_wait)
        start_shutdown = self._clock.now()
        self._executor_service.shutdown(wait=False, cancel_futures=True)

        deadline = time.monotonic() + max(shutdown_max_wait.total_seconds(), 0.0)
        while self.currently_processing and time.monotonic() < deadline:
            time.sleep(SHUTDOWN_POLL_INTERVAL)

        still_running = [one.execution for one in self.currently_executing]
        if not still_running:
            LOG.info("Scheduler stopped.")
        else:
            LOG.warning(
                "Scheduler stopped, but some tasks did not complete. Was currently running the "
                "following executions:\n%s",
                "\n".join(str(one) for one in still_running),
            )

        shutdown_time = self._clock.now() - start_shutdown
        if shutdown_max_wait > timedelta(minutes=1) and shutdown_time >= shutdown_max_wait:
            LOG.info(
                "Shutdown of the scheduler executor service took %s. Consider regularly "
                "checking for 'execution_context.scheduler_state.is_shutting_down' in the task "
                "execution-handler and aborting when the scheduler is shutting down.",
                shutdown_time,
            )

    def __repr__(self) -> str:
        return f"Executor(in_flight={self._currently_in_queue_or_processing})"


@dataclass(frozen=True, slots=True)
class ExecutePicked:
    """Run one already-picked execution, and write down what happened.

    Nothing here raises. A handler that throws becomes a failure the task's `FailureHandler`
    decides about; a *handler* that throws becomes a logged error and a row left picked, which
    the dead-execution detector will pick up later. The one thing that must not happen is an
    exception escaping into the thread pool, where it would silently kill a worker.
    """

    executor: Executor
    task_repository: Any
    scheduler_client: Any
    scheduler_listeners: SchedulerListeners
    execution_interceptors: Sequence[Any]
    task_resolver: Any
    scheduler_state: SchedulerState
    failure_logger: ConfigurableLogger
    clock: Clock
    heartbeat_config: HeartbeatConfig
    picked_execution: Execution

    def run(self) -> None:
        currently_executing = CurrentlyExecuting(
            self.picked_execution, self.clock, self.heartbeat_config
        )
        execution_id = self.executor.add_currently_processing(currently_executing)
        try:
            self.scheduler_listeners.on_candidate_event(CandidateEventType.EXECUTED)
            self.scheduler_listeners.on_execution_start(currently_executing)
            self._execute_picked_execution(self.picked_execution, currently_executing)
        finally:
            self.executor.remove_currently_processing(execution_id)

    def __call__(self) -> None:
        self.run()

    def _execute_picked_execution(
        self, execution: Execution, currently_executing: CurrentlyExecuting
    ) -> None:
        task = self.task_resolver.resolve(execution)
        if task is None:
            LOG.error(
                "Failed to find implementation for task with name '%s'. Should have been "
                "excluded in the repository.",
                execution.task_name,
            )
            self.scheduler_listeners.on_scheduler_event(SchedulerEventType.UNEXPECTED_ERROR)
            return

        execution_started = self.clock.now()
        try:
            LOG.debug("Executing: %s", execution)
            from .event import ExecutionChain

            context = ExecutionContext(
                execution=execution,
                scheduler_state=self.scheduler_state,
                scheduler_client=self.scheduler_client,
                currently_executing=currently_executing,
            )
            chain = ExecutionChain(list(self.execution_interceptors), task.execute)
            completion = chain.proceed(execution.task_instance, context)
            LOG.debug("Execution done: %s", execution)
            self._complete(completion, execution, execution_started)
        except BaseException as unhandled:
            self._failure(task, execution, unhandled, execution_started, "Unhandled exception")

    def _complete(self, completion: Any, execution: Execution, execution_started: datetime) -> None:
        from .task.operations import ExecutionOperations

        complete_event = ExecutionComplete.success(
            execution, execution_started, self.clock.now()
        )
        try:
            completion.complete(
                complete_event,
                ExecutionOperations[Any](
                    self.task_repository, self.scheduler_listeners, execution, self.clock
                ),
            )
        except BaseException as exc:
            self.scheduler_listeners.on_scheduler_event(
                SchedulerEventType.COMPLETIONHANDLER_ERROR
            )
            self.scheduler_listeners.on_scheduler_event(SchedulerEventType.UNEXPECTED_ERROR)
            LOG.error(
                "Failed while completing execution %s, because %s. Execution will likely remain "
                "scheduled and locked/picked. The execution should be detected as dead after a "
                "while, and handled according to the task's DeadExecutionHandler.",
                execution,
                describe(exc),
                exc_info=exc,
            )
        finally:
            self.scheduler_listeners.on_execution_complete(complete_event)

    def _failure(
        self,
        task: Any,
        execution: Execution,
        cause: BaseException,
        execution_started: datetime,
        error_message_prefix: str,
    ) -> None:
        from .task.operations import ExecutionOperations

        self.failure_logger.log(
            "%s %s during execution of task with name '%s'. Treating as failure.",
            cause,
            error_message_prefix,
            describe(cause),
            task.name,
        )
        complete_event = ExecutionComplete.failure(
            execution, execution_started, self.clock.now(), cause
        )
        try:
            task.failure_handler.on_failure(
                complete_event,
                ExecutionOperations[Any](
                    self.task_repository, self.scheduler_listeners, execution, self.clock
                ),
            )
        except BaseException as exc:
            self.scheduler_listeners.on_scheduler_event(SchedulerEventType.FAILUREHANDLER_ERROR)
            self.scheduler_listeners.on_scheduler_event(SchedulerEventType.UNEXPECTED_ERROR)
            LOG.error(
                "Failed while completing execution %s, because %s. Execution will likely remain "
                "scheduled and locked/picked. The execution should be detected as dead after a "
                "while, and handled according to the task's DeadExecutionHandler.",
                execution,
                describe(cause),
                exc_info=exc,
            )
        finally:
            self.scheduler_listeners.on_execution_complete(complete_event)


#: The failure logger a scheduler gets unless it is configured otherwise.
DEFAULT_FAILURE_LOGGER = ConfigurableLogger.create(LOG, LogLevel.WARN, True)
