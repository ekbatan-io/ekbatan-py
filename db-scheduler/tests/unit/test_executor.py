"""`Waiter`, `DueExecutionsBatch`, `Executor`, `ExecutePicked` and `PollingStrategyConfig`.

These run with real threads. The behaviours being asserted - a wake-up that arrives while nobody
is waiting, a batch counting itself down from several worker threads - only exist because more
than one thread is involved, and a fake would assert the fake.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from db_scheduler.clock import SettableClock, SystemClock
from db_scheduler.event import (
    CandidateEventType,
    SchedulerEventType,
    SchedulerListeners,
)
from db_scheduler.executor import (
    DueExecutionsBatch,
    ExecutePicked,
    Executor,
    PollingStrategyConfig,
    Waiter,
)
from db_scheduler.logging import ConfigurableLogger, LogLevel
from db_scheduler.resolver import TaskResolver
from db_scheduler.state import (
    CurrentlyExecuting,
    HeartbeatConfig,
    SettableSchedulerState,
)
from db_scheduler.task import Execution, ExecutionComplete, TaskInstance
from db_scheduler.task.helper import Tasks

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
HEARTBEAT = HeartbeatConfig(timedelta(seconds=5), 6, timedelta(seconds=30))

#: Real threads need a real bound. Long enough not to be flaky on a loaded machine, short enough
#: that a genuine hang fails the run rather than stalling it.
PATIENCE = 5.0


class TestWaiter:
    def test_a_zero_duration_does_not_wait(self) -> None:
        started = time.monotonic()

        Waiter(timedelta()).do_wait()

        assert time.monotonic() - started < 0.5

    def test_waking_cuts_the_wait_short(self) -> None:
        waiter = Waiter(timedelta(seconds=30))
        finished = threading.Event()

        def wait() -> None:
            waiter.do_wait()
            finished.set()

        threading.Thread(target=wait, daemon=True).start()
        _wait_until(lambda: waiter.is_waiting)

        assert waiter.wake() is True
        assert finished.wait(PATIENCE), "the wait should have ended immediately"

    def test_waking_when_nobody_is_waiting_says_so(self) -> None:
        assert Waiter(timedelta(seconds=30)).wake() is False

    def test_a_wake_up_that_arrives_early_is_not_lost(self) -> None:
        """The whole reason this class exists rather than a `sleep`: work scheduled in the gap
        between two polls must not sit until the interval expires."""
        waiter = Waiter(timedelta(seconds=30))

        waiter.wake_or_skip_next_wait()

        started = time.monotonic()
        waiter.do_wait()
        assert time.monotonic() - started < 0.5

    def test_and_is_only_honoured_once(self) -> None:
        waiter = Waiter(timedelta(milliseconds=200))
        waiter.wake_or_skip_next_wait()
        waiter.do_wait()

        started = time.monotonic()
        waiter.do_wait()

        assert time.monotonic() - started >= 0.15, "the second wait should really wait"

    def test_wake_or_skip_prefers_waking_when_it_can(self) -> None:
        waiter = Waiter(timedelta(seconds=30))
        finished = threading.Event()
        def wait_then_signal() -> None:
            waiter.do_wait()
            finished.set()

        threading.Thread(target=wait_then_signal, daemon=True).start()
        _wait_until(lambda: waiter.is_waiting)

        waiter.wake_or_skip_next_wait()

        assert finished.wait(PATIENCE)
        started = time.monotonic()
        waiter.duration = timedelta(milliseconds=200)
        waiter.do_wait()
        assert time.monotonic() - started >= 0.15, "no skip flag should have been left behind"


def _wait_until(predicate: Any, patience: float = PATIENCE) -> None:
    deadline = time.monotonic() + patience
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition never became true")


class TestPollingStrategyConfig:
    def test_the_fetch_default_asks_for_more_than_the_pool(self) -> None:
        """It competes for each row it fetched, so it expects to lose some races."""
        config = PollingStrategyConfig.DEFAULT_FETCH

        assert config.type is PollingStrategyConfig.Type.FETCH
        assert config.upper_limit(10) == 30
        assert config.lower_limit(10) == 5

    def test_the_lock_and_fetch_default_asks_for_exactly_the_pool(self) -> None:
        """Every row it gets back is already claimed, so fetching more would leave rows picked
        and idle."""
        config = PollingStrategyConfig.DEFAULT_SELECT_FOR_UPDATE

        assert config.type is PollingStrategyConfig.Type.LOCK_AND_FETCH
        assert config.upper_limit(10) == 10
        assert config.lower_limit(10) == 5

    def test_the_limits_must_be_the_right_way_round(self) -> None:
        with pytest.raises(ValueError, match="lower_limit"):
            PollingStrategyConfig(PollingStrategyConfig.Type.FETCH, 2.0, 1.0)

    def test_the_upper_limit_must_be_at_least_the_pool(self) -> None:
        """Fetching fewer rows than there are threads would leave threads idle by construction."""
        with pytest.raises(ValueError, match="upper_limit"):
            PollingStrategyConfig(PollingStrategyConfig.Type.FETCH, 0.1, 0.5)

    def test_describe(self) -> None:
        assert "type=FETCH" in PollingStrategyConfig.DEFAULT_FETCH.describe()


class TestDueExecutionsBatch:
    def test_it_asks_for_more_once_few_enough_are_left(self) -> None:
        """Overlapping the next fetch with the tail of this batch is what keeps the pool busy."""
        asked: list[str] = []
        batch = DueExecutionsBatch(1, 10, True, lambda left: left <= 5)

        for _ in range(4):
            batch.one_execution_done(lambda: asked.append("fetch"))
        assert asked == [], "six still in flight, so no need yet"

        batch.one_execution_done(lambda: asked.append("fetch"))
        assert asked == ["fetch"], "five left, which is the lower limit"

    def test_it_only_asks_once(self) -> None:
        asked: list[str] = []
        batch = DueExecutionsBatch(1, 3, True, lambda left: left <= 2)

        for _ in range(3):
            batch.one_execution_done(lambda: asked.append("fetch"))

        assert asked == ["fetch"]

    def test_it_does_not_ask_when_the_database_had_no_more(self) -> None:
        asked: list[str] = []
        batch = DueExecutionsBatch(1, 3, False, lambda left: True)

        batch.one_execution_done(lambda: asked.append("fetch"))

        assert asked == []

    def test_a_stale_batch_asks_for_nothing(self) -> None:
        """A newer poll is already in flight; a third fetch on top of it would be waste."""
        asked: list[str] = []
        batch = DueExecutionsBatch(1, 3, True, lambda left: True)

        batch.mark_batch_as_stale()
        batch.one_execution_done(lambda: asked.append("fetch"))

        assert asked == []

    def test_generations_are_comparable(self) -> None:
        assert DueExecutionsBatch(1, 0, True, lambda left: False).is_older_generation_than(2)
        assert not DueExecutionsBatch(2, 0, True, lambda left: False).is_older_generation_than(2)

    def test_the_countdown_is_safe_from_several_threads(self) -> None:
        """Worker threads call this as they finish, all at once."""
        asked: list[str] = []
        lock = threading.Lock()

        def note() -> None:
            with lock:
                asked.append("fetch")

        batch = DueExecutionsBatch(1, 100, True, lambda left: left == 0)
        barrier = threading.Barrier(10)

        def finish_ten() -> None:
            barrier.wait()
            for _ in range(10):
                batch.one_execution_done(note)

        with ThreadPoolExecutor(max_workers=10) as pool:
            for _ in range(10):
                pool.submit(finish_ten)

        assert asked == ["fetch"], "exactly one trigger, from exactly one thread reaching zero"


class TestExecutor:
    def test_it_runs_what_it_is_given_and_then_the_callback(self) -> None:
        order: list[str] = []
        done = threading.Event()

        with ThreadPoolExecutor(max_workers=2) as pool:
            executor = Executor(pool, SystemClock())
            def after() -> None:
                order.append("after")
                done.set()

            executor.add_to_queue(lambda: order.append("work"), after)
            assert done.wait(PATIENCE)

        assert order == ["work", "after"]

    def test_the_callback_runs_even_when_the_work_raises(self) -> None:
        """Otherwise one failing execution would leave the in-flight count wrong for ever, and
        the scheduler would stop fetching."""
        done = threading.Event()

        def explode() -> None:
            raise RuntimeError("boom")

        with ThreadPoolExecutor(max_workers=2) as pool:
            executor = Executor(pool, SystemClock())
            executor.add_to_queue(explode, done.set)
            assert done.wait(PATIENCE)
            assert executor.number_in_queue_or_processing == 0

    def test_the_in_flight_count_is_decremented_before_the_callback(self) -> None:
        """The callback decides whether to fetch more, and this one is no longer in flight."""
        seen: list[int] = []
        done = threading.Event()

        with ThreadPoolExecutor(max_workers=1) as pool:
            executor = Executor(pool, SystemClock())
            def note_and_signal() -> None:
                seen.append(executor.number_in_queue_or_processing)
                done.set()

            executor.add_to_queue(lambda: None, note_and_signal)
            assert done.wait(PATIENCE)

        assert seen == [0]

    def test_it_tracks_what_is_currently_executing(self) -> None:
        clock = SettableClock(NOW)
        with ThreadPoolExecutor(max_workers=1) as pool:
            executor = Executor(pool, clock)
            running = CurrentlyExecuting(
                Execution(NOW, TaskInstance[Any]("t", "i")), clock, HEARTBEAT
            )

            execution_id = executor.add_currently_processing(running)
            assert executor.currently_executing == [running]

            executor.remove_currently_processing(execution_id)
            assert executor.currently_executing == []

    def test_removing_something_untracked_is_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import uuid

        with ThreadPoolExecutor(max_workers=1) as pool:
            executor = Executor(pool, SystemClock())
            with caplog.at_level("WARNING", logger="db_scheduler.executor"):
                executor.remove_currently_processing(uuid.uuid4())

        assert "Should never happen" in caplog.text


class RecordingListeners(SchedulerListeners):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[str] = []
        self.completions: list[ExecutionComplete] = []

    def on_candidate_event(self, event_type: CandidateEventType) -> None:
        self.events.append(f"candidate:{event_type.name}")

    def on_scheduler_event(self, event_type: SchedulerEventType) -> None:
        self.events.append(f"scheduler:{event_type.name}")

    def on_execution_start(self, currently_executing: Any) -> None:
        self.events.append("start")

    def on_execution_complete(self, execution_complete: ExecutionComplete) -> None:
        self.events.append("complete")
        self.completions.append(execution_complete)


class FakeRepository:
    def __init__(self) -> None:
        self.removed: list[Execution] = []
        self.rescheduled: list[Any] = []

    def remove(self, execution: Execution) -> None:
        self.removed.append(execution)

    def reschedule(self, execution: Execution, next_execution_time: Any, **kwargs: Any) -> bool:
        self.rescheduled.append(next_execution_time)
        return True


def an_execution() -> Execution:
    return Execution(NOW, TaskInstance[Any]("a-task", "an-id"), picked=True, version=2)


def execute_picked(
    task: Any,
    repository: FakeRepository,
    listeners: RecordingListeners,
    execution: Execution | None = None,
    interceptors: list[Any] | None = None,
) -> ExecutePicked:
    clock = SettableClock(NOW)
    resolver = TaskResolver(SchedulerListeners(), clock, [task] if task else [])
    with ThreadPoolExecutor(max_workers=1) as pool:
        executor = Executor(pool, clock)
    return ExecutePicked(
        executor=executor,
        task_repository=repository,
        scheduler_client=None,
        scheduler_listeners=listeners,
        execution_interceptors=interceptors or [],
        task_resolver=resolver,
        scheduler_state=SettableSchedulerState(),
        failure_logger=ConfigurableLogger.create(
            __import__("logging").getLogger("test"), LogLevel.WARN, False
        ),
        clock=clock,
        heartbeat_config=HEARTBEAT,
        picked_execution=execution or an_execution(),
    )


class TestExecutePicked:
    def test_a_one_time_task_runs_and_its_row_is_removed(self) -> None:
        ran: list[str] = []
        task = Tasks.one_time("a-task").execute(lambda i, c: ran.append(i.id))
        repository, listeners = FakeRepository(), RecordingListeners()

        execute_picked(task, repository, listeners).run()

        assert ran == ["an-id"]
        assert len(repository.removed) == 1
        assert listeners.events == ["candidate:EXECUTED", "start", "complete"]

    def test_the_completion_event_says_it_succeeded(self) -> None:
        task = Tasks.one_time("a-task").execute(lambda i, c: None)
        listeners = RecordingListeners()

        execute_picked(task, FakeRepository(), listeners).run()

        [completion] = listeners.completions
        assert completion.result.name == "OK"

    def test_a_body_that_raises_becomes_a_failure_not_a_crash(self) -> None:
        """The failure handler decides what happens next; the exception must not escape into
        the thread pool, where it would silently kill a worker."""

        def explode(instance: Any, context: Any) -> None:
            raise RuntimeError("boom")

        task = Tasks.one_time("a-task").execute(explode)
        repository, listeners = FakeRepository(), RecordingListeners()

        execute_picked(task, repository, listeners).run()

        assert repository.rescheduled == [NOW + timedelta(minutes=5)], "retried later"
        [completion] = listeners.completions
        assert completion.result.name == "FAILED"

    def test_an_unresolvable_task_is_reported_rather_than_run(self) -> None:
        """The repository should have excluded it, so reaching here at all is a bug worth an
        UNEXPECTED_ERROR."""
        repository, listeners = FakeRepository(), RecordingListeners()

        execute_picked(None, repository, listeners).run()

        assert "scheduler:UNEXPECTED_ERROR" in listeners.events
        assert repository.removed == []

    def test_a_completion_handler_that_raises_leaves_the_row_picked(self) -> None:
        """Deliberately: the row will be found by the dead-execution detector, which is safer
        than guessing at what the handler meant to do."""

        class Exploding:
            def complete(self, completion: Any, operations: Any) -> None:
                raise RuntimeError("boom")

        task = Tasks.custom("a-task").execute(lambda i, c: Exploding())
        repository, listeners = FakeRepository(), RecordingListeners()

        execute_picked(task, repository, listeners).run()

        assert repository.removed == []
        assert repository.rescheduled == []
        assert "scheduler:COMPLETIONHANDLER_ERROR" in listeners.events
        assert "complete" in listeners.events, "the completion event is still published"

    def test_a_failure_handler_that_raises_is_reported_too(self) -> None:
        class ExplodingFailureHandler:
            def on_failure(self, completion: Any, operations: Any) -> None:
                raise RuntimeError("boom")

        def explode(instance: Any, context: Any) -> None:
            raise RuntimeError("the original")

        task = Tasks.one_time("a-task").on_failure(ExplodingFailureHandler()).execute(explode)
        repository, listeners = FakeRepository(), RecordingListeners()

        execute_picked(task, repository, listeners).run()

        assert "scheduler:FAILUREHANDLER_ERROR" in listeners.events

    def test_interceptors_wrap_the_body(self) -> None:
        order: list[str] = []

        class Around:
            def execute(self, instance: Any, context: Any, chain: Any) -> Any:
                order.append("before")
                result = chain.proceed(instance, context)
                order.append("after")
                return result

        task = Tasks.one_time("a-task").execute(lambda i, c: order.append("body"))
        execute_picked(task, FakeRepository(), RecordingListeners(), interceptors=[Around()]).run()

        assert order == ["before", "body", "after"]

    def test_the_context_carries_the_scheduler_state(self) -> None:
        """So a long-running handler can check whether it is being shut down."""
        seen: list[Any] = []
        task = Tasks.one_time("a-task").execute(
            lambda i, c: seen.append(c.scheduler_state.is_shutting_down)
        )

        execute_picked(task, FakeRepository(), RecordingListeners()).run()

        assert seen == [False]

    def test_it_is_tracked_while_it_runs(self) -> None:
        seen: list[int] = []
        picked = execute_picked(
            Tasks.one_time("a-task").execute(lambda i, c: None),
            FakeRepository(),
            RecordingListeners(),
        )
        tracked = picked.executor

        def note(instance: Any, context: Any) -> None:
            seen.append(len(tracked.currently_executing))

        picked = execute_picked(
            Tasks.one_time("a-task").execute(note), FakeRepository(), RecordingListeners()
        )
        picked.run()

        assert len(picked.executor.currently_executing) == 0, "and released afterwards"


class TestConfigurableLogger:
    def test_it_logs_at_the_level_it_was_given(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging as stdlib_logging

        logger = ConfigurableLogger.create(
            stdlib_logging.getLogger("test.configurable"), LogLevel.INFO, False
        )

        with caplog.at_level(stdlib_logging.INFO, logger="test.configurable"):
            logger.log("failed: %s", RuntimeError("boom"), "a-task")

        assert "failed: a-task" in caplog.text

    def test_off_says_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        """A task expected to fail sometimes produces one ERROR per attempt by default, and an
        operator who sees a hundred a day stops reading them."""
        import logging as stdlib_logging

        logger = ConfigurableLogger.create(
            stdlib_logging.getLogger("test.configurable"), LogLevel.OFF, False
        )

        with caplog.at_level(stdlib_logging.DEBUG, logger="test.configurable"):
            logger.log("failed: %s", RuntimeError("boom"), "a-task")

        assert caplog.text == ""

    def test_the_stack_trace_is_optional(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging as stdlib_logging

        with_trace = ConfigurableLogger.create(
            stdlib_logging.getLogger("test.configurable"), LogLevel.ERROR, True
        )

        with caplog.at_level(stdlib_logging.ERROR, logger="test.configurable"):
            try:
                raise RuntimeError("boom")
            except RuntimeError as exc:
                with_trace.log("failed", exc)

        assert "RuntimeError: boom" in caplog.text
