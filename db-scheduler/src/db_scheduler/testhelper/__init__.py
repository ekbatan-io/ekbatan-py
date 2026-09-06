"""Running a scheduler without threads or waiting.

A port of `com.github.kagkarlsson.scheduler.testhelper`. A `ManualScheduler` is a real scheduler
against a real table, with two things replaced: the clock is settable, and there are no
background threads - work runs on the calling thread when you say so.

That combination is what makes scheduling behaviour testable at all. A retry with an hour's
backoff is one `tick(timedelta(hours=1))` rather than an hour; a dead execution is
`set_time(...)` and `run_dead_execution_detection()` rather than killing a process and waiting;
and because nothing runs in the background, a test that fails does so at its assertion rather
than somewhere in a worker thread.

The housekeeping executors are deliberately hostile: calling one raises, because the whole point
is that the test says when things happen. If something in a test triggers one, the test is
relying on background behaviour it has explicitly opted out of.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any, Self

from sqlalchemy import Engine

from ..builder import SchedulerBuilder
from ..clock import SettableClock
from ..executor import PollingStrategyConfig
from ..jdbc import JdbcTaskRepository, autodetect
from ..logging import LogLevel
from ..resolver import TaskResolver
from ..scheduler import Scheduler
from ..scheduler_name import Fixed
from ..stats.adapter import StatsRegistryAdapter
from ..task.helper import Task

__all__ = ["ManualScheduler", "ManualSchedulerBuilder", "create_manual_scheduler"]

LOG = logging.getLogger("db_scheduler.testhelper")


class DirectExecutorService:
    """Runs the work on the calling thread, immediately.

    Stands in for the worker pool, so `run_any_due_executions()` has finished executing
    everything by the time it returns - which is what lets the next line of a test assert on
    the result instead of waiting for it.
    """

    __slots__ = ()

    def submit(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        fn(*args, **kwargs)
        return None

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        """Nothing to shut down."""

    def __repr__(self) -> str:
        return "DirectExecutorService()"


class ManualScheduler(Scheduler):
    """A scheduler that does nothing until told to."""

    def __init__(self, clock: SettableClock, **kwargs: Any) -> None:
        super().__init__(clock=clock, **kwargs)
        self.clock: SettableClock = clock

    def start(self) -> None:
        """On-startup tasks only. No threads: everything else is triggered by hand."""
        LOG.info("Starting manual scheduler. Executing on-startup tasks.")
        self.execute_on_startup()
        self.scheduler_state.set_started()

    def stop(self, shutdown_max_wait: timedelta | None = None) -> None:
        self.scheduler_state.set_is_shutting_down()

    def tick(self, move_clock_forward: timedelta) -> None:
        """Move time forward. An hour's backoff is one call rather than an hour."""
        self.clock.set(self.clock.now() + move_clock_forward)

    def set_time(self, new_time: datetime) -> None:
        self.clock.set(new_time)

    def run_any_due_executions(self) -> None:
        self.execute_due_strategy.run()

    def run_dead_execution_detection(self) -> None:
        self.detect_dead_executions()

    def __repr__(self) -> str:
        return f"ManualScheduler(now={self.clock.now().isoformat()})"


class ManualSchedulerBuilder(SchedulerBuilder):
    """`SchedulerBuilder`, but it builds a `ManualScheduler` and insists on a settable clock."""

    def __init__(self, engine: Engine, known_tasks: Iterable[Task[Any]] = ()) -> None:
        super().__init__(engine, known_tasks)
        # Kept alongside the inherited `_clock` so the type is known here: the base accepts any
        # Clock, and a ManualScheduler needs the settable one.
        self._settable_clock = SettableClock()
        self._clock = self._settable_clock

    def clock(self, clock: Any) -> Self:
        if not isinstance(clock, SettableClock):
            raise TypeError("a ManualScheduler needs a SettableClock")
        self._settable_clock = clock
        self._clock = clock
        return self

    def polling_strategy(self, polling_strategy_config: PollingStrategyConfig) -> Self:
        self.polling_strategy_config = polling_strategy_config
        return self

    def build(self) -> ManualScheduler:
        clock = self._settable_clock
        task_resolver = TaskResolver(
            _listeners(self.scheduler_listeners), clock, self.known_tasks
        )
        jdbc_customization = self._jdbc_customization or autodetect(
            self.engine.dialect.name,
            getattr(self.engine.dialect, "server_version_info", None),
            self._always_persist_timestamp_in_utc,
        )

        def repository() -> JdbcTaskRepository:
            return JdbcTaskRepository(
                self.engine,
                scheduler_name="manual",
                serializer=self._serializer,
                table_name=self._table_name,
                clock=clock,
                customization=jdbc_customization,
                order_by_priority=self._enable_priority,
                persist_timestamp_in_utc=jdbc_customization.persist_timestamp_in_utc,
                task_resolver=task_resolver,
            )

        listeners = list(self.scheduler_listeners)
        if self._stats_registry is not None:
            listeners.append(StatsRegistryAdapter(self._stats_registry))

        return ManualScheduler(
            clock=clock,
            scheduler_task_repository=repository(),
            client_task_repository=repository(),
            task_resolver=task_resolver,
            threadpool_size=self.executor_threads,
            executor_service=DirectExecutorService(),
            scheduler_name=Fixed("manual"),
            execute_due_waiter=self.build_waiter(),
            heartbeat_interval=self._heartbeat_interval,
            number_of_missed_heartbeats_before_dead=(
                self.number_of_missed_heartbeats_before_dead
            ),
            scheduler_listeners=listeners,
            execution_interceptors=self.execution_interceptors,
            polling_strategy_config=self.polling_strategy_config,
            delete_unresolved_after=self._delete_unresolved_after,
            # No grace period: nothing runs in the background, so there is nothing to wait for.
            shutdown_max_wait=timedelta(0),
            log_level=LogLevel.DEBUG,
            log_stack_trace=True,
            on_startup=self.start_tasks,
        )

    def start(self) -> ManualScheduler:
        scheduler = self.build()
        scheduler.start()
        return scheduler


def _listeners(listeners: Any) -> Any:
    from ..event import SchedulerListeners

    return SchedulerListeners(listeners)


def create_manual_scheduler(engine: Engine, *known_tasks: Any) -> ManualSchedulerBuilder:
    """`TestHelper.createManualScheduler(dataSource, tasks...)`."""
    tasks = (
        list(known_tasks[0])
        if len(known_tasks) == 1 and isinstance(known_tasks[0], list | tuple)
        else list(known_tasks)
    )
    return ManualSchedulerBuilder(engine, tasks)
