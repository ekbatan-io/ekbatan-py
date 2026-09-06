"""The `Scheduler`, end to end: a real table, real threads, real work.

These are the tests that say the library does its job. Everything else checks a piece; these
check that a task scheduled on one side comes out the other, that a machine dying does not
strand its work, and that two schedulers over one table run each execution once.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine

from db_scheduler.builder import SchedulerBuilder, create
from db_scheduler.clock import SettableClock, SystemClock
from db_scheduler.event import AbstractSchedulerListener, SchedulerEventType
from db_scheduler.exceptions import SerializationException
from db_scheduler.executor import PollingStrategyConfig
from db_scheduler.logging import LogLevel
from db_scheduler.scheduled_execution import ScheduledExecutionsFilter
from db_scheduler.scheduler import Scheduler
from db_scheduler.scheduler_name import Fixed
from db_scheduler.task import TaskInstance, TaskInstanceId
from db_scheduler.task.handlers import CancelDeadExecution, OnCompleteReplace, max_retries
from db_scheduler.task.helper import PlainScheduleAndData, Tasks
from db_scheduler.task.schedule import FixedDelay

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

#: Real threads need a real bound. Long enough not to flake on a loaded machine, short enough
#: that a genuine hang fails the run rather than stalling it.
PATIENCE = 10.0


def wait_until(predicate: Any, patience: float = PATIENCE, what: str = "condition") -> None:
    deadline = time.monotonic() + patience
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"{what} never became true within {patience}s")


class Recording(AbstractSchedulerListener):
    def __init__(self) -> None:
        self.events: list[SchedulerEventType] = []
        self.dead: list[Any] = []

    def on_scheduler_event(self, event_type: SchedulerEventType) -> None:
        self.events.append(event_type)

    def on_execution_dead(self, execution: Any) -> None:
        self.dead.append(execution)


@pytest.fixture
def builder(engine: Any, table: Any) -> SchedulerBuilder:
    """A scheduler over the same table the repository fixture created.

    Polling fast on purpose: these tests are about what happens across a poll, and ten seconds
    of the production default would only make them slow.
    """
    return (
        create(engine)
        .table_name(table.name)
        .threads(2)
        .polling_interval(timedelta(milliseconds=50))
        .heartbeat_interval(timedelta(seconds=1))
        .scheduler_name(Fixed("test-scheduler"))
        .shutdown_max_wait(timedelta(seconds=5))
        .failure_logging(LogLevel.DEBUG, False)
    )


class TestRunningWork:
    def test_a_scheduled_task_runs(self, builder: SchedulerBuilder) -> None:
        ran = threading.Event()
        task = Tasks.one_time("a-task").execute(lambda instance, context: ran.set())
        scheduler = builder.add_tasks(task).build()

        with scheduler:
            scheduler.schedule_if_not_exists(task.instance("an-id"), SystemClock().now())
            assert ran.wait(PATIENCE), "the task should have run"

    def test_its_row_is_gone_afterwards(self, builder: SchedulerBuilder) -> None:
        """A one-time task is done when it is done, and the row is what would run it again."""
        ran = threading.Event()
        task = Tasks.one_time("a-task").execute(lambda instance, context: ran.set())
        scheduler = builder.add_tasks(task).build()

        with scheduler:
            scheduler.schedule_if_not_exists(task.instance("an-id"), SystemClock().now())
            assert ran.wait(PATIENCE)
            wait_until(
                lambda: scheduler.get_scheduled_executions() == [],
                what="the row being removed",
            )

    def test_the_data_reaches_the_task(self, builder: SchedulerBuilder) -> None:
        seen: list[Any] = []
        done = threading.Event()

        def note(instance: TaskInstance[Any], context: Any) -> None:
            seen.append(instance.data)
            done.set()

        task = Tasks.one_time("a-task", dict).execute(note)
        scheduler = builder.add_tasks(task).build()

        with scheduler:
            scheduler.schedule_if_not_exists(
                task.instance("an-id", {"customer": 42}), SystemClock().now()
            )
            assert done.wait(PATIENCE)

        assert seen == [{"customer": 42}]

    def test_a_failing_task_is_rescheduled_rather_than_lost(
        self, builder: SchedulerBuilder
    ) -> None:
        attempted = threading.Event()

        def explode(instance: Any, context: Any) -> None:
            attempted.set()
            raise RuntimeError("boom")

        task = (
            Tasks.one_time("a-task")
            .on_failure_retry_later(timedelta(hours=1))
            .execute(explode)
        )
        scheduler = builder.add_tasks(task).build()

        with scheduler:
            scheduler.schedule_if_not_exists(task.instance("an-id"), SystemClock().now())
            assert attempted.wait(PATIENCE)
            wait_until(
                lambda: bool(scheduler.get_scheduled_executions()),
                what="the row being rescheduled rather than removed",
            )

        [found] = scheduler.get_scheduled_executions()
        assert found.consecutive_failures == 1
        assert found.execution_time > SystemClock().now()

    def test_a_recurring_task_schedules_itself_at_startup(
        self, builder: SchedulerBuilder
    ) -> None:
        """Which is what makes it safe to deploy the same code to twenty machines."""
        ran = threading.Event()
        task = Tasks.recurring("a-task", FixedDelay.of(timedelta(hours=1))).execute(
            lambda instance, context: ran.set()
        )
        scheduler = builder.add_start_tasks(task).build()

        with scheduler:
            assert ran.wait(PATIENCE)
            wait_until(
                lambda: bool(scheduler.get_scheduled_executions()),
                what="the recurring row being rescheduled",
            )

        [found] = scheduler.get_scheduled_executions()
        assert found.task_instance.id == "recurring"

    def test_many_tasks_all_run(self, builder: SchedulerBuilder) -> None:
        done = threading.Semaphore(0)
        task = Tasks.one_time("a-task").execute(lambda instance, context: done.release())
        scheduler = builder.add_tasks(task).build()

        with scheduler:
            scheduler.schedule_batch(
                [task.instance(f"{n}") for n in range(20)], SystemClock().now()
            )
            for _ in range(20):
                assert done.acquire(timeout=PATIENCE), "every one of them should have run"


class TestImmediateExecution:
    def test_work_scheduled_for_now_does_not_wait_out_the_poll(
        self, builder: SchedulerBuilder
    ) -> None:
        """The difference between "instant" and "up to ten seconds" for a request handler that
        schedules a job and expects it to start."""
        ran = threading.Event()
        task = Tasks.one_time("a-task").execute(lambda instance, context: ran.set())
        scheduler = (
            builder.add_tasks(task)
            .polling_interval(timedelta(seconds=30))
            .enable_immediate_execution()
            .build()
        )

        with scheduler:
            scheduler.schedule_if_not_exists(task.instance("an-id"), SystemClock().now())
            assert ran.wait(PATIENCE), "should not have waited 30s for the next poll"


class TestDeadExecutions:
    def test_an_abandoned_execution_is_revived(self, builder: SchedulerBuilder) -> None:
        """The machine that picked it died. Nothing will ever complete that row, and without
        this it stays picked for ever."""
        ran = threading.Event()
        task = Tasks.one_time("a-task").execute(lambda instance, context: ran.set())
        listener = Recording()
        scheduler = (
            builder.add_tasks(task)
            .heartbeat_interval(timedelta(milliseconds=100))
            .missed_heartbeats_limit(4)
            .add_scheduler_listener(listener)
            .build()
        )
        repository = scheduler.scheduler_task_repository

        # A row picked by an instance that then vanished: picked, with a heartbeat from long ago.
        scheduler.schedule_if_not_exists(task.instance("an-id"), SystemClock().now())
        [execution] = repository.get_due(SystemClock().now(), limit=10)
        repository.pick(execution, SystemClock().now() - timedelta(hours=1))

        with scheduler:
            assert ran.wait(PATIENCE), "another instance should have taken it over"

        assert listener.dead, "and it should have been reported as dead"

    def test_a_task_can_refuse_to_be_revived(self, builder: SchedulerBuilder) -> None:
        """For work that is not safe to repeat - a payment, say - `CancelDeadExecution` drops
        the row instead of running it a second time."""
        task = (
            Tasks.one_time("a-task")
            .on_dead_execution(CancelDeadExecution())
            .execute(lambda instance, context: None)
        )
        scheduler = (
            builder.add_tasks(task)
            .heartbeat_interval(timedelta(milliseconds=100))
            .missed_heartbeats_limit(4)
            .build()
        )
        repository = scheduler.scheduler_task_repository

        scheduler.schedule_if_not_exists(task.instance("an-id"), SystemClock().now())
        [execution] = repository.get_due(SystemClock().now(), limit=10)
        repository.pick(execution, SystemClock().now() - timedelta(hours=1))

        with scheduler:
            wait_until(
                lambda: scheduler.get_scheduled_executions(
                    __import__(
                        "db_scheduler.scheduled_execution", fromlist=["x"]
                    ).ScheduledExecutionsFilter.all()
                )
                == [],
                what="the row being cancelled rather than rerun",
            )

    def test_unresolvable_rows_are_deleted_after_the_grace_period(
        self, builder: SchedulerBuilder
    ) -> None:
        """Fourteen days by default. A name unresolvable during a deploy comes back; one
        unresolvable for a fortnight is dead code whose rows will never run."""
        task = Tasks.one_time("a-task").execute(lambda instance, context: None)
        scheduler = (
            builder.add_tasks(task).delete_unresolved_after(timedelta(seconds=0)).build()
        )
        repository = scheduler.scheduler_task_repository
        scheduler.schedule_if_not_exists(TaskInstance[Any]("gone-task", "an-id"), NOW)

        repository.get_due(SystemClock().now(), limit=10)  # notices the unresolvable name
        assert repository.task_resolver.is_unresolved("gone-task")

        scheduler.detect_dead_executions()

        assert repository.get_execution("gone-task", "an-id") is None


class TestHeartbeats:
    def test_a_running_execution_is_kept_alive(self, builder: SchedulerBuilder) -> None:
        """Otherwise a task that runs longer than the dead-threshold would be taken over while
        it is still working, and run twice."""
        started = threading.Event()
        release = threading.Event()

        def slow(instance: Any, context: Any) -> None:
            started.set()
            release.wait(PATIENCE)

        task = Tasks.one_time("a-task").execute(slow)
        scheduler = (
            builder.add_tasks(task).heartbeat_interval(timedelta(milliseconds=100)).build()
        )
        repository = scheduler.scheduler_task_repository

        with scheduler:
            scheduler.schedule_if_not_exists(task.instance("an-id"), SystemClock().now())
            assert started.wait(PATIENCE)

            first = repository.get_execution("a-task", "an-id")
            assert first is not None
            wait_until(
                lambda: (repository.get_execution("a-task", "an-id") or first).last_heartbeat
                > first.last_heartbeat,
                what="the heartbeat moving",
            )
            release.set()

    def test_currently_executing_is_visible(self, builder: SchedulerBuilder) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow(instance: Any, context: Any) -> None:
            started.set()
            release.wait(PATIENCE)

        task = Tasks.one_time("a-task").execute(slow)
        scheduler = builder.add_tasks(task).build()

        with scheduler:
            scheduler.schedule_if_not_exists(task.instance("an-id"), SystemClock().now())
            assert started.wait(PATIENCE)

            [running] = scheduler.currently_executing
            assert running.task_instance.id == "an-id"
            assert running.duration >= timedelta()
            release.set()


class TestPauseAndResume:
    def test_a_paused_scheduler_runs_nothing(self, builder: SchedulerBuilder) -> None:
        ran = threading.Event()
        task = Tasks.one_time("a-task").execute(lambda instance, context: ran.set())
        scheduler = builder.add_tasks(task).build()

        with scheduler:
            scheduler.pause()
            scheduler.schedule_if_not_exists(task.instance("an-id"), SystemClock().now())

            assert not ran.wait(0.5), "nothing should run while paused"

            scheduler.resume()
            assert ran.wait(PATIENCE), "and it should run once resumed"


class TestTwoSchedulers:
    def test_each_execution_runs_exactly_once(self, builder: SchedulerBuilder, engine: Any, table: Any) -> None:
        """The point of the whole library. Two instances over one table, and no row runs twice."""
        ran: list[str] = []
        lock = threading.Lock()
        done = threading.Semaphore(0)

        def note(instance: TaskInstance[Any], context: Any) -> None:
            with lock:
                ran.append(instance.id)
            done.release()

        def a_scheduler(name: str) -> Scheduler:
            task = Tasks.one_time("a-task").execute(note)
            return (
                create(engine)
                .table_name(table.name)
                .threads(4)
                .polling_interval(timedelta(milliseconds=20))
                .heartbeat_interval(timedelta(seconds=5))
                .scheduler_name(Fixed(name))
                .shutdown_max_wait(timedelta(seconds=5))
                .add_tasks(task)
                .build()
            )

        one, two = a_scheduler("one"), a_scheduler("two")
        one.schedule_batch(
            [TaskInstance[Any]("a-task", f"{n}") for n in range(30)], SystemClock().now()
        )

        with one, two:
            for _ in range(30):
                assert done.acquire(timeout=PATIENCE), "every row should have run"
            # Give any duplicate a chance to show up before asserting there is none.
            time.sleep(0.5)

        assert sorted(ran) == sorted(f"{n}" for n in range(30))
        assert len(ran) == len(set(ran)), f"an execution ran twice: {ran}"


class TestLockAndFetch:
    def test_it_runs_work_where_the_dialect_supports_it(
        self, builder: SchedulerBuilder, repository: Any
    ) -> None:
        if not repository.customization.supports_lock_and_fetch:
            pytest.skip(f"{repository.customization.name} cannot lock-and-fetch")

        ran = threading.Event()
        task = Tasks.one_time("a-task").execute(lambda instance, context: ran.set())
        scheduler = builder.add_tasks(task).poll_using_lock_and_fetch(0.5, 1.0).build()

        with scheduler:
            scheduler.schedule_if_not_exists(task.instance("an-id"), SystemClock().now())
            assert ran.wait(PATIENCE)

    def test_it_is_refused_where_the_dialect_cannot(
        self, builder: SchedulerBuilder, repository: Any
    ) -> None:
        """Refused rather than emulated: an emulation would be a claim that does not hold."""
        if repository.customization.supports_lock_and_fetch:
            pytest.skip(f"{repository.customization.name} can lock-and-fetch")

        task = Tasks.one_time("a-task").execute(lambda instance, context: None)

        with pytest.raises(Exception, match=r"(?i)lock.and.fetch|skip locked"):
            builder.add_tasks(task).poll_using_lock_and_fetch(0.5, 1.0).build()


class TestLifecycle:
    def test_stopping_twice_is_harmless(self, builder: SchedulerBuilder) -> None:
        scheduler = builder.build()
        scheduler.start()

        scheduler.stop()
        scheduler.stop()

    def test_the_state_reads_back(self, builder: SchedulerBuilder) -> None:
        scheduler = builder.build()

        assert scheduler.scheduler_state.is_started is False
        scheduler.start()
        assert scheduler.scheduler_state.is_started is True
        scheduler.stop()
        assert scheduler.scheduler_state.is_shutting_down is True

    def test_a_scheduler_is_also_a_client(self, builder: SchedulerBuilder) -> None:
        """So a worker can schedule follow-up work without a second object."""
        task = Tasks.one_time("a-task").execute(lambda instance, context: None)
        scheduler = builder.add_tasks(task).build()

        scheduler.schedule_if_not_exists(task.instance("an-id"), NOW)

        assert scheduler.get_scheduled_execution(TaskInstanceId("a-task", "an-id")) is not None


class TestBuilderValidation:
    def test_the_heartbeat_limit_has_a_floor(self, builder: SchedulerBuilder) -> None:
        """Below four, one slow write can have a running execution declared dead - and then two
        instances are running the same work."""
        with pytest.raises(ValueError, match="at least 4"):
            builder.missed_heartbeats_limit(3)

    def test_the_polling_strategy_defaults_to_fetch(self, builder: SchedulerBuilder) -> None:
        assert builder.polling_strategy_config.type is PollingStrategyConfig.Type.FETCH

    def test_a_clock_can_be_substituted(self, builder: SchedulerBuilder) -> None:
        clock = SettableClock(NOW)

        scheduler = builder.clock(clock).build()

        assert scheduler.clock is clock


class TestShutdownIsBounded:
    """Shutdown must terminate. Python cannot interrupt a running thread, so a handler that
    ignores `is_shutting_down` runs to completion - and the only safe design is to wait a
    bounded time and then *report* it, never to block for ever."""

    def test_a_hanging_task_does_not_block_stop_for_ever(
        self, builder: SchedulerBuilder
    ) -> None:
        started, never = threading.Event(), threading.Event()

        def hangs(instance: Any, context: Any) -> None:
            started.set()
            never.wait(60)

        task = Tasks.one_time("a-task").execute(hangs)
        scheduler = (
            builder.add_tasks(task).threads(1).shutdown_max_wait(timedelta(seconds=1)).build()
        )
        try:
            scheduler.start()
            scheduler.schedule_if_not_exists(task.instance("an-id"), SystemClock().now())
            assert started.wait(PATIENCE)

            stopped = threading.Event()

            def stop_it() -> None:
                scheduler.stop()
                stopped.set()

            threading.Thread(target=stop_it, daemon=True).start()

            assert stopped.wait(PATIENCE), "stop() ignored its own shutdown_max_wait"
        finally:
            never.set()

    def test_and_names_what_did_not_finish(
        self, builder: SchedulerBuilder, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An execution still running at the deadline is one whose handler never checks for
        shutdown. An operator needs to know which."""
        started, never = threading.Event(), threading.Event()

        def hangs(instance: Any, context: Any) -> None:
            started.set()
            never.wait(60)

        task = Tasks.one_time("a-task").execute(hangs)
        scheduler = (
            builder.add_tasks(task).threads(1).shutdown_max_wait(timedelta(seconds=1)).build()
        )
        try:
            scheduler.start()
            scheduler.schedule_if_not_exists(task.instance("an-id"), SystemClock().now())
            assert started.wait(PATIENCE)

            with caplog.at_level(logging.WARNING, logger="db_scheduler.executor"):
                scheduler.stop()

            assert "some tasks did not complete" in caplog.text
            assert "an-id" in caplog.text
        finally:
            never.set()

    def test_stopping_mid_poll_does_not_wait_out_the_poll_interval(
        self, builder: SchedulerBuilder
    ) -> None:
        """A wake-up delivered while the loop is running rather than sleeping has nobody to
        wake; without the skip-flag it is lost and the loop sleeps the whole interval."""
        running = threading.Event()

        def slow(instance: Any, context: Any) -> None:
            running.set()
            time.sleep(0.3)

        task = Tasks.one_time("a-task").execute(slow)
        scheduler = (
            builder.add_tasks(task)
            .polling_interval(timedelta(seconds=30))
            .enable_immediate_execution()
            .shutdown_max_wait(timedelta(seconds=5))
            .build()
        )
        scheduler.start()
        scheduler.schedule_if_not_exists(task.instance("an-id"), SystemClock().now())
        assert running.wait(PATIENCE)

        started = time.monotonic()
        scheduler.stop()

        assert time.monotonic() - started < 5, "stop() waited out the 30s poll interval"


class TestConfigurationIsValidated:
    """Both loops wait for their interval and then run. At zero the wait returns instantly and
    the loop becomes a spin - measured at ~10,000 polls per second, each one a database query,
    and ~900,000 heartbeat rounds per second. One typo would hammer the database and burn a
    core, and the library would get the blame. db-scheduler does not check these; refusing a
    value that can only ever do harm costs nothing."""

    @pytest.mark.parametrize("bad", [timedelta(), timedelta(seconds=-1)])
    def test_a_poll_interval_of_zero_or_less_is_refused(
        self, builder: SchedulerBuilder, bad: timedelta
    ) -> None:
        with pytest.raises(ValueError, match="polling_interval must be greater than zero"):
            builder.polling_interval(bad)

    @pytest.mark.parametrize("bad", [timedelta(), timedelta(seconds=-1)])
    def test_a_heartbeat_interval_of_zero_or_less_is_refused(
        self, builder: SchedulerBuilder, bad: timedelta
    ) -> None:
        with pytest.raises(ValueError, match="heartbeat_interval must be greater than zero"):
            builder.heartbeat_interval(bad)

    @pytest.mark.parametrize("bad", [0, -1])
    def test_a_thread_count_of_zero_or_less_is_refused(
        self, builder: SchedulerBuilder, bad: int
    ) -> None:
        with pytest.raises(ValueError, match="threads must be greater than zero"):
            builder.threads(bad)

    def test_a_negative_shutdown_wait_is_refused(self, builder: SchedulerBuilder) -> None:
        with pytest.raises(ValueError, match="shutdown_max_wait cannot be negative"):
            builder.shutdown_max_wait(timedelta(seconds=-1))

    def test_but_a_zero_shutdown_wait_is_allowed(self, builder: SchedulerBuilder) -> None:
        """"Do not wait for running executions" is a legitimate choice."""
        assert builder.shutdown_max_wait(timedelta()) is builder

    def test_a_negative_unresolved_deletion_window_is_refused(
        self, builder: SchedulerBuilder
    ) -> None:
        with pytest.raises(ValueError, match="delete_unresolved_after cannot be negative"):
            builder.delete_unresolved_after(timedelta(seconds=-1))

    def test_but_zero_is_allowed(self, builder: SchedulerBuilder) -> None:
        """It means "delete as soon as the name is seen to be unresolvable"."""
        assert builder.delete_unresolved_after(timedelta()) is builder

    def test_a_valid_configuration_still_polls_at_its_interval(
        self, builder: SchedulerBuilder
    ) -> None:
        """The guard must not have broken normal operation."""
        ran = threading.Event()
        task = Tasks.one_time("a-task").execute(lambda instance, context: ran.set())
        scheduler = builder.add_tasks(task).polling_interval(timedelta(milliseconds=50)).build()

        with scheduler:
            scheduler.schedule_if_not_exists(task.instance("an-id"), SystemClock().now())
            assert ran.wait(PATIENCE)


class TestDataIntegrity:
    """What the table will and will not accept, so a limit is discovered here rather than in
    production."""

    def test_a_task_name_over_the_column_width_is_never_silently_truncated(
        self, repository: Any
    ) -> None:
        """Refused or stored whole - never cut short. A truncated name would let two different
        tasks collide on one primary key, which is silent, permanent corruption.

        The dialects disagree about *which*: Postgres and MySQL reject the write, while SQLite
        does not enforce `varchar` lengths at all and keeps the whole thing. Both are safe; only
        truncation would not be. Worth knowing when developing on SQLite, because a name the
        laptop accepts is one production will reject.
        """
        from db_scheduler.task import ScheduledTaskInstance

        too_long = "n" * 101
        repository.task_resolver.add_task(
            Tasks.one_time(too_long).execute(lambda instance, context: None)
        )

        try:
            repository.create_if_not_exists(
                ScheduledTaskInstance(TaskInstance[Any](too_long, "an-id"), NOW)
            )
        except Exception:
            return  # refused outright, which is the safe answer

        found = repository.get_execution(too_long, "an-id")
        assert found is not None, "written but not readable back"
        assert found.task_name == too_long, "the name was silently truncated"

    def test_unicode_survives_a_round_trip(self, repository: Any) -> None:
        from db_scheduler.task import ScheduledTaskInstance

        name, data = "unicode-任务-задача", {"é": "üñî"}
        repository.task_resolver.add_task(
            Tasks.one_time(name).execute(lambda instance, context: None)
        )

        repository.create_if_not_exists(
            ScheduledTaskInstance(TaskInstance[Any](name, "an-id", data), NOW)
        )

        found = repository.get_execution(name, "an-id")
        assert found is not None
        assert found.task_name == name
        assert found.task_instance.data == data

    def test_task_data_that_cannot_be_deserialized_does_not_break_the_poll(
        self, repository: Any, table: Any, engine: Any
    ) -> None:
        """A single corrupt row must not stop the scheduler scheduling everything else. The
        cost is deferred to whoever actually reads the data."""
        from db_scheduler.task import ScheduledTaskInstance

        repository.create_if_not_exists(
            ScheduledTaskInstance(TaskInstance[Any]("a-task", "healthy"), NOW)
        )
        with engine.begin() as conn:
            conn.execute(
                table.insert().values(
                    task_name="a-task",
                    task_instance="corrupt",
                    task_data=b"\xff\xfe not json",
                    execution_time=repository._bind(NOW),
                    picked=False,
                    version=1,
                )
            )

        due = repository.get_due(NOW, limit=10)

        assert "healthy" in [e.id for e in due], "the poll survived the corrupt row"
        [corrupt] = [e for e in due if e.id == "corrupt"]
        with pytest.raises(SerializationException):
            _ = corrupt.task_instance.data


class TestALiveExecutionIsNeverStolen:
    """The worst bug this library could have: two instances running the same job at once.

    Only the heartbeat stands between "one run" and "two". This runs a task for ten times the
    dead-threshold while a *second* scheduler hunts for dead executions the entire time.
    """

    def test_a_long_running_execution_is_not_taken_over_while_it_lives(
        self, engine: Any, table: Any
    ) -> None:
        runs: list[str] = []
        lock = threading.Lock()
        started, release = threading.Event(), threading.Event()

        heartbeat = timedelta(milliseconds=100)
        limit = 4  # dead after 400ms of silence
        task_seconds = 3.0  # more than seven times that

        def slow(instance: Any, context: Any) -> None:
            with lock:
                runs.append(instance.id)
            started.set()
            release.wait(task_seconds)

        def a_scheduler(name: str) -> Scheduler:
            task = Tasks.one_time("slow").execute(slow)
            return (
                create(engine)
                .table_name(table.name)
                .add_tasks(task)
                .threads(2)
                .polling_interval(timedelta(milliseconds=30))
                .heartbeat_interval(heartbeat)
                .missed_heartbeats_limit(limit)
                .scheduler_name(Fixed(name))
                .shutdown_max_wait(timedelta(seconds=3))
                .build()
            )

        one, two = a_scheduler("one"), a_scheduler("two")
        one.schedule_if_not_exists(TaskInstance[Any]("slow", "the-one"), SystemClock().now())

        try:
            one.start()
            two.start()
            assert started.wait(PATIENCE), "it never started"
            # Long past the dead-threshold, with both schedulers hunting the whole time.
            time.sleep(task_seconds)
        finally:
            release.set()
            time.sleep(0.5)
            one.stop()
            two.stop()

        assert runs == ["the-one"], f"a live execution was taken over and run again: {runs}"

    def test_but_one_that_stopped_heartbeating_is(self, engine: Any, table: Any) -> None:
        """The other half of the same guarantee - otherwise the heartbeat would just be a way
        of never recovering anything."""
        ran = threading.Event()
        task = Tasks.one_time("a-task").execute(lambda instance, context: ran.set())
        scheduler = (
            create(engine)
            .table_name(table.name)
            .add_tasks(task)
            .threads(1)
            .polling_interval(timedelta(milliseconds=30))
            .heartbeat_interval(timedelta(milliseconds=100))
            .missed_heartbeats_limit(4)
            .scheduler_name(Fixed("survivor"))
            .shutdown_max_wait(timedelta(seconds=3))
            .build()
        )
        repository = scheduler.scheduler_task_repository
        scheduler.schedule_if_not_exists(task.instance("abandoned"), SystemClock().now())
        [execution] = repository.get_due(SystemClock().now(), limit=10)
        repository.pick(execution, SystemClock().now() - timedelta(hours=1))

        with scheduler:
            assert ran.wait(PATIENCE), "an abandoned execution was never revived"


class TestConnectionPoolSizing:
    """A pool with no headroom past the workers is a correctness problem, not a slow one.

    Measured with three workers each holding a connection: at capacity 3 *every* execution was
    stolen by another instance and run twice; at capacity 4, none were. The heartbeat is how a
    running execution proves it is alive, and it needs a connection to do it.
    """

    def _warnings_building_with(self, engine: Any, threads: int, caplog: Any) -> str:
        with caplog.at_level(logging.WARNING, logger="db_scheduler.builder"):
            create(engine).threads(threads).build()
        logged: str = caplog.text
        return logged

    def test_it_warns_when_the_pool_cannot_outnumber_the_workers(
        self, engine_url: str, table: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        engine = create_engine(engine_url, pool_size=3, max_overflow=0)
        try:
            text = self._warnings_building_with(engine, 3, caplog)
        finally:
            engine.dispose()

        assert "heartbeat cannot write" in text
        assert "at least 6 connections" in text, "the fix is spelled out with numbers"

    def test_it_is_quiet_when_there_is_headroom(
        self, engine_url: str, table: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        engine = create_engine(engine_url, pool_size=3, max_overflow=1)
        try:
            assert "heartbeat" not in self._warnings_building_with(engine, 3, caplog)
        finally:
            engine.dispose()

    def test_it_catches_the_default_pool_with_too_many_threads(
        self, engine_url: str, table: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """SQLAlchemy's default pool holds 15. `threads(20)` is a perfectly reasonable-looking
        setting that would silently run executions twice."""
        engine = create_engine(engine_url)
        try:
            assert "heartbeat cannot write" in self._warnings_building_with(engine, 20, caplog)
        finally:
            engine.dispose()

    def test_a_pool_with_no_ceiling_is_not_second_guessed(
        self, engine_url: str, table: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`NullPool` opens a connection per checkout, so there is nothing to compare against."""
        from sqlalchemy.pool import NullPool

        engine = create_engine(engine_url, poolclass=NullPool)
        try:
            assert "heartbeat" not in self._warnings_building_with(engine, 50, caplog)
        finally:
            engine.dispose()


class TestFeaturesEndToEnd:
    """Features that were only ever exercised against fakes. Each one goes through a real table
    here: serialize, write, poll, resolve, deserialize, run, reschedule."""

    def test_a_recurring_task_carries_state_to_its_next_run(
        self, builder: SchedulerBuilder
    ) -> None:
        """`execute_stateful` returns the data the *next* execution should carry - it has to
        survive a serialize/write/read/deserialize round trip to work at all."""
        seen: list[Any] = []
        progressed = threading.Semaphore(0)

        def count(instance: TaskInstance[Any], context: Any) -> Any:
            seen.append(instance.data)
            progressed.release()
            return {"n": (instance.data or {"n": 0})["n"] + 1}

        task = Tasks.recurring(
            "counter", FixedDelay.of(timedelta(milliseconds=50))
        ).execute_stateful(count)
        scheduler = builder.add_start_tasks(task).build()

        with scheduler:
            for _ in range(3):
                assert progressed.acquire(timeout=PATIENCE)

        assert seen[:3] == [None, {"n": 1}, {"n": 2}], f"state did not carry: {seen[:3]}"

    def test_a_task_whose_schedule_lives_in_its_own_row(
        self, builder: SchedulerBuilder
    ) -> None:
        """`RecurringTaskWithPersistentSchedule`: the cron is in `task_data`, so changing when
        a task runs is an UPDATE rather than a deploy. The schedule itself has to round-trip
        through the database."""
        ran = threading.Semaphore(0)
        task = Tasks.recurring_with_persistent_schedule("persistent").execute(
            lambda instance, context: ran.release()
        )
        scheduler = builder.add_tasks(task).build()
        data = PlainScheduleAndData(FixedDelay.of(timedelta(milliseconds=50)))

        with scheduler:
            scheduler.schedule_if_not_exists(
                task.instance("the-one", data), SystemClock().now()
            )
            assert ran.acquire(timeout=PATIENCE), "it never ran"
            assert ran.acquire(timeout=PATIENCE), "it did not reschedule itself"

        [found] = scheduler.get_scheduled_executions(
            ScheduledExecutionsFilter.all().with_picked(False)
        )
        assert isinstance(found.data, PlainScheduleAndData), (
            f"the schedule did not survive the round trip: {type(found.data).__name__}"
        )

    def test_a_task_gives_up_after_its_retry_limit(self, builder: SchedulerBuilder) -> None:
        attempts = threading.Semaphore(0)

        def explode(instance: Any, context: Any) -> None:
            attempts.release()
            raise RuntimeError("boom")

        task = (
            Tasks.one_time("give-up")
            .on_failure(max_retries(2).retry_every(timedelta(milliseconds=50)).then_remove())
            .execute(explode)
        )
        scheduler = builder.add_tasks(task).build()

        with scheduler:
            scheduler.schedule_if_not_exists(task.instance("an-id"), SystemClock().now())
            for _ in range(3):  # the original plus two retries
                assert attempts.acquire(timeout=PATIENCE)
            wait_until(
                lambda: scheduler.get_scheduled_executions(
                    ScheduledExecutionsFilter.all()
                )
                == [],
                what="the row being removed after the retry limit",
            )

    def test_a_task_can_hand_off_to_another(self, builder: SchedulerBuilder) -> None:
        """`OnCompleteReplace`: one row becomes the next step, in a single statement, so a crash
        between them cannot lose the work."""
        second_ran = threading.Event()
        second = Tasks.one_time("step-two").execute(
            lambda instance, context: second_ran.set()
        )
        first = Tasks.custom("step-one").execute(
            lambda instance, context: OnCompleteReplace("step-two", {"from": instance.id})
        )
        scheduler = builder.add_tasks([first, second]).build()

        with scheduler:
            scheduler.schedule_if_not_exists(first.instance("an-id"), SystemClock().now())
            assert second_ran.wait(PATIENCE), "the second step never ran"


@pytest.mark.skipif_sqlite
class TestGivingTheSchedulerItsOwnConnections:
    """The remedy for connection starvation, and it is the shape db-scheduler already has.

    db-scheduler takes a `DataSource` and never pools anything itself - HikariCP is a *test*
    dependency in its own build. Pooling belongs to the client. The Python equivalent of a
    `DataSource` is an `Engine`, so the same remedy is available: give the scheduler an engine
    of its own and task bodies can no longer starve its heartbeat.

    Measured here: the application's pool is drained to nothing by task bodies. Sharing it,
    every execution is stolen and run twice. On a separate engine, none are.
    """

    WORKERS = 3

    def _run(self, engine_url: str, *, share: bool) -> int:
        """Returns how many executions were run more than once."""
        runs: dict[str, int] = {}
        lock = threading.Lock()
        started, release = threading.Semaphore(0), threading.Event()

        app_pool = create_engine(engine_url, pool_size=self.WORKERS, max_overflow=0, pool_timeout=2)
        scheduler_engine = (
            app_pool if share else create_engine(engine_url, pool_size=self.WORKERS + 3)
        )
        hunter_engine = create_engine(engine_url, pool_size=5, max_overflow=5)

        def body(instance: TaskInstance[Any], context: Any) -> None:
            with lock:
                runs[instance.id] = runs.get(instance.id, 0) + 1
            with app_pool.connect():  # a task body doing its own work, holding a connection
                started.release()
                release.wait(PATIENCE)

        def a_scheduler(engine: Any, name: str) -> Scheduler:
            return (
                create(engine)
                .add_tasks(Tasks.one_time("hog").execute(body))
                .threads(self.WORKERS)
                .polling_interval(timedelta(milliseconds=50))
                .heartbeat_interval(timedelta(milliseconds=200))
                .missed_heartbeats_limit(4)
                .scheduler_name(Fixed(name))
                .shutdown_max_wait(timedelta(seconds=3))
                .build()
            )

        worker, hunter = a_scheduler(scheduler_engine, "worker"), a_scheduler(
            hunter_engine, "hunter"
        )
        worker.schedule_batch(
            [TaskInstance[Any]("hog", f"{n}") for n in range(self.WORKERS)], SystemClock().now()
        )
        try:
            worker.start()
            hunter.start()
            for _ in range(self.WORKERS):
                assert started.acquire(timeout=PATIENCE), "the workers never all started"
            # Far past the 800ms dead-threshold, with the hunter looking the whole time.
            time.sleep(3.0)
        finally:
            release.set()
            time.sleep(0.5)
            worker.stop()
            hunter.stop()
            app_pool.dispose()
            hunter_engine.dispose()
            if not share:
                scheduler_engine.dispose()

        return sum(1 for count in runs.values() if count > 1)

    def test_sharing_an_exhausted_pool_loses_the_heartbeat(
        self, engine_url: str, table: Any
    ) -> None:
        """Stated as a test so the hazard cannot quietly stop being true. The heartbeat needs a
        connection to say an execution is alive; without one, another instance takes it over."""
        assert self._run(engine_url, share=True) == self.WORKERS

    def test_a_dedicated_engine_prevents_it_entirely(
        self, engine_url: str, table: Any
    ) -> None:
        assert self._run(engine_url, share=False) == 0
