"""The two polling strategies.

`FetchCandidates` reads due rows and then races other instances to claim each one. It works on
every database, because the claim is an optimistic-lock UPDATE and needs no locking primitive at
all. The cost is the race: it fetches three times the pool by default and expects to lose some.

`LockAndFetchCandidates` claims as it selects, using `SELECT ... FOR UPDATE SKIP LOCKED`. There
is no race and no wasted fetch, so it asks for exactly what it can run - but only PostgreSQL,
MySQL 8 and SQL Server can do it.

The generation number is what makes the first one safe. A fetched candidate sits in the queue
until a thread is free, and by then the row may be minutes stale; comparing generations lets a
worker notice that a newer poll has superseded its batch and drop the candidate rather than
claiming a row it is no longer the right instance for.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from typing import Any

from .clock import Clock
from .event import CandidateEventType, SchedulerEventType, SchedulerListeners
from .executor import (
    DueExecutionsBatch,
    ExecutePicked,
    Executor,
    PollingStrategyConfig,
    PollStrategy,
)
from .logging import ConfigurableLogger
from .state import HeartbeatConfig, SchedulerState
from .task import Execution

__all__ = ["FetchCandidates", "LockAndFetchCandidates"]

LOG = logging.getLogger("db_scheduler.poll")


class FetchCandidates(PollStrategy):
    """Fetch due rows, then compete for each one. Correct on every database."""

    __slots__ = (
        "_clock",
        "_current_generation_number",
        "_execution_interceptors",
        "_executor",
        "_failure_logger",
        "_heartbeat_config",
        "_lock",
        "_lower_limit",
        "_scheduler_client",
        "_scheduler_listeners",
        "_scheduler_state",
        "_task_repository",
        "_task_resolver",
        "_upper_limit",
        "trigger_check_for_new_executions",
    )

    def __init__(
        self,
        executor: Executor,
        task_repository: Any,
        scheduler_client: Any,
        threadpool_size: int,
        scheduler_listeners: SchedulerListeners,
        execution_interceptors: Sequence[Any],
        scheduler_state: SchedulerState,
        failure_logger: ConfigurableLogger,
        task_resolver: Any,
        clock: Clock,
        polling_strategy_config: PollingStrategyConfig,
        trigger_check_for_new_executions: Callable[[], None],
        heartbeat_config: HeartbeatConfig,
    ) -> None:
        self._executor = executor
        self._task_repository = task_repository
        self._scheduler_client = scheduler_client
        self._scheduler_listeners = scheduler_listeners
        self._execution_interceptors = execution_interceptors
        self._scheduler_state = scheduler_state
        self._failure_logger = failure_logger
        self._task_resolver = task_resolver
        self._clock = clock
        self.trigger_check_for_new_executions = trigger_check_for_new_executions
        self._heartbeat_config = heartbeat_config
        self._lower_limit = polling_strategy_config.lower_limit(threadpool_size)
        # Not really an upper limit: it is how many to *fetch*. Anything already queued from an
        # earlier poll becomes stale and is discarded rather than run.
        self._upper_limit = polling_strategy_config.upper_limit(threadpool_size)
        self._current_generation_number = 0
        self._lock = threading.Lock()

    @property
    def current_generation_number(self) -> int:
        return self._current_generation_number

    def run(self) -> None:
        now = self._clock.now()
        executions_to_fetch = self._upper_limit
        fetched = self._task_repository.get_due(now, executions_to_fetch)
        LOG.debug(
            "Fetched %d task instances due for execution at %s", len(fetched), now
        )

        with self._lock:
            self._current_generation_number += 1
            generation = self._current_generation_number

        new_due_batch = DueExecutionsBatch(
            generation,
            len(fetched),
            # A full batch means there were probably more waiting. A short one means the table
            # is drained, and asking again immediately would be a wasted query.
            executions_to_fetch == len(fetched),
            lambda left_in_batch: left_in_batch <= self._lower_limit,
        )

        for candidate in fetched:
            self._executor.add_to_queue(
                _RunCandidate(self, candidate, new_due_batch),
                _AfterCandidate(self, new_due_batch),
            )
        self._scheduler_listeners.on_scheduler_event(SchedulerEventType.RAN_EXECUTE_DUE)

    def pick_due(
        self, candidate: Execution, added_due_executions_batch: DueExecutionsBatch
    ) -> Execution | None:
        """Claim one candidate, or explain why not.

        Three ways to decline, and each is normal rather than exceptional: the scheduler is
        shutting down, the batch has been superseded, or another instance claimed the row first.
        """
        if self._scheduler_state.is_shutting_down:
            LOG.info(
                "Scheduler has been shutdown. Skipping fetched due execution: %s",
                candidate.task_instance.task_and_instance,
            )
            return None

        if added_due_executions_batch.is_older_generation_than(
            self._current_generation_number
        ):
            added_due_executions_batch.mark_batch_as_stale()
            self._scheduler_listeners.on_candidate_event(CandidateEventType.STALE)
            LOG.debug(
                "Skipping queued execution (current generationNumber: %d, execution "
                "generationNumber: %d)",
                self._current_generation_number,
                added_due_executions_batch.generation_number,
            )
            return None

        picked: Execution | None = self._task_repository.pick(candidate, self._clock.now())
        if picked is None:
            LOG.debug("Execution picked by another scheduler. Continuing to next due execution.")
            self._scheduler_listeners.on_candidate_event(CandidateEventType.ALREADY_PICKED)
            return None
        return picked

    def run_picked(self, picked: Execution) -> None:
        ExecutePicked(
            executor=self._executor,
            task_repository=self._task_repository,
            scheduler_client=self._scheduler_client,
            scheduler_listeners=self._scheduler_listeners,
            execution_interceptors=self._execution_interceptors,
            task_resolver=self._task_resolver,
            scheduler_state=self._scheduler_state,
            failure_logger=self._failure_logger,
            clock=self._clock,
            heartbeat_config=self._heartbeat_config,
            picked_execution=picked,
        ).run()

    def __repr__(self) -> str:
        return f"FetchCandidates(generation={self._current_generation_number})"


class _RunCandidate:
    """Pick a candidate and, if it was won, run it. A class rather than a closure so the loop
    that creates one per candidate cannot capture the wrong variable."""

    __slots__ = ("_batch", "_candidate", "_strategy")

    def __init__(
        self, strategy: FetchCandidates, candidate: Execution, batch: DueExecutionsBatch
    ) -> None:
        self._strategy = strategy
        self._candidate = candidate
        self._batch = batch

    def __call__(self) -> None:
        picked = self._strategy.pick_due(self._candidate, self._batch)
        if picked is not None:
            self._strategy.run_picked(picked)


class _AfterCandidate:
    __slots__ = ("_batch", "_strategy")

    def __init__(self, strategy: FetchCandidates, batch: DueExecutionsBatch) -> None:
        self._strategy = strategy
        self._batch = batch

    def __call__(self) -> None:
        self._batch.one_execution_done(self._strategy.trigger_check_for_new_executions)


class LockAndFetchCandidates(PollStrategy):
    """Claim in the statement that selects. No race, and no wasted fetch."""

    __slots__ = (
        "_clock",
        "_execution_interceptors",
        "_executor",
        "_failure_logger",
        "_heartbeat_config",
        "_lower_limit",
        "_more_executions_in_database",
        "_scheduler_client",
        "_scheduler_listeners",
        "_scheduler_state",
        "_task_repository",
        "_task_resolver",
        "_upper_limit",
        "trigger_check_for_new_executions",
    )

    def __init__(
        self,
        executor: Executor,
        task_repository: Any,
        scheduler_client: Any,
        threadpool_size: int,
        scheduler_listeners: SchedulerListeners,
        execution_interceptors: Sequence[Any],
        scheduler_state: SchedulerState,
        failure_logger: ConfigurableLogger,
        task_resolver: Any,
        clock: Clock,
        polling_strategy_config: PollingStrategyConfig,
        trigger_check_for_new_executions: Callable[[], None],
        heartbeat_config: HeartbeatConfig,
    ) -> None:
        self._executor = executor
        self._task_repository = task_repository
        self._scheduler_client = scheduler_client
        self._scheduler_listeners = scheduler_listeners
        self._execution_interceptors = execution_interceptors
        self._scheduler_state = scheduler_state
        self._failure_logger = failure_logger
        self._task_resolver = task_resolver
        self._clock = clock
        self.trigger_check_for_new_executions = trigger_check_for_new_executions
        self._heartbeat_config = heartbeat_config
        self._lower_limit = polling_strategy_config.lower_limit(threadpool_size)
        self._upper_limit = polling_strategy_config.upper_limit(threadpool_size)
        self._more_executions_in_database = False

    def run(self) -> None:
        now = self._clock.now()

        # Only fetch what there is room to run. Every row this claims is *ours* the moment the
        # statement returns, so fetching more than the pool can start would leave rows picked
        # and idle - and picked rows are invisible to every other instance.
        executions_to_fetch = self._upper_limit - self._executor.number_in_queue_or_processing
        if executions_to_fetch <= 0:
            LOG.debug("No executions to fetch.")
            return

        picked_executions = self._task_repository.lock_and_get_due(now, executions_to_fetch)
        LOG.debug("Picked %d task instances due for execution", len(picked_executions))

        # A full batch means there is probably more. Shared across the callbacks so that once
        # the table is known to be drained, they stop asking for another poll.
        self._more_executions_in_database = len(picked_executions) == executions_to_fetch

        if not picked_executions:
            LOG.debug("No executions due.")
            return

        for picked in picked_executions:
            self._executor.add_to_queue(
                ExecutePicked(
                    executor=self._executor,
                    task_repository=self._task_repository,
                    scheduler_client=self._scheduler_client,
                    scheduler_listeners=self._scheduler_listeners,
                    execution_interceptors=self._execution_interceptors,
                    task_resolver=self._task_resolver,
                    scheduler_state=self._scheduler_state,
                    failure_logger=self._failure_logger,
                    clock=self._clock,
                    heartbeat_config=self._heartbeat_config,
                    picked_execution=picked,
                ),
                self._maybe_trigger_check,
            )
        self._scheduler_listeners.on_scheduler_event(SchedulerEventType.RAN_EXECUTE_DUE)

    def _maybe_trigger_check(self) -> None:
        if (
            self._more_executions_in_database
            and self._executor.number_in_queue_or_processing <= self._lower_limit
        ):
            self.trigger_check_for_new_executions()

    def __repr__(self) -> str:
        return "LockAndFetchCandidates()"
