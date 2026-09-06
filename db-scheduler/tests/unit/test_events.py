"""`SchedulerListeners`, `ExecutionChain`, `StatsRegistryAdapter` and `SchedulerName`.

The rule that matters here: a listener runs in the executing thread, and a listener that raises
must not fail the execution it was only watching.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from db_scheduler.event import (
    NOOP,
    AbstractSchedulerListener,
    CandidateEventType,
    ExecutionChain,
    SchedulerEventType,
    SchedulerListener,
    SchedulerListeners,
)
from db_scheduler.scheduler_name import Fixed, Hostname, SchedulerName
from db_scheduler.stats import (
    CandidateStatsEvent,
    DefaultStatsRegistry,
    ExecutionStatsEvent,
    SchedulerStatsEvent,
    StatsRegistry,
)
from db_scheduler.stats.adapter import StatsRegistryAdapter
from db_scheduler.task import Execution, ExecutionComplete, TaskInstance, TaskInstanceId
from db_scheduler.task.handlers import OnCompleteRemove

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class RecordingListener(AbstractSchedulerListener):
    def __init__(self) -> None:
        self.seen: list[str] = []

    def on_execution_scheduled(
        self, task_instance_id: TaskInstanceId, execution_time: datetime
    ) -> None:
        self.seen.append(f"scheduled:{task_instance_id}")

    def on_execution_start(self, currently_executing: Any) -> None:
        self.seen.append("start")

    def on_execution_complete(self, execution_complete: ExecutionComplete) -> None:
        self.seen.append("complete")

    def on_execution_dead(self, execution: Execution) -> None:
        self.seen.append("dead")

    def on_execution_failed_heartbeat(self, currently_executing: Any) -> None:
        self.seen.append("failed-heartbeat")

    def on_scheduler_event(self, event_type: SchedulerEventType) -> None:
        self.seen.append(f"scheduler:{event_type.name}")

    def on_candidate_event(self, event_type: CandidateEventType) -> None:
        self.seen.append(f"candidate:{event_type.name}")


class ExplodingListener(AbstractSchedulerListener):
    def on_execution_complete(self, execution_complete: ExecutionComplete) -> None:
        raise RuntimeError("third-party code, doing what it does")

    def on_scheduler_event(self, event_type: SchedulerEventType) -> None:
        raise RuntimeError("again")


def a_completion() -> ExecutionComplete:
    return ExecutionComplete.simulated_success(NOW)


class TestSchedulerListeners:
    def test_every_callback_reaches_every_listener(self) -> None:
        one, two = RecordingListener(), RecordingListener()
        listeners = SchedulerListeners([one, two])

        listeners.on_execution_scheduled(TaskInstanceId("t", "i"), NOW)
        listeners.on_execution_start(object())
        listeners.on_execution_complete(a_completion())
        listeners.on_execution_dead(Execution(NOW, TaskInstance[Any]("t", "i")))
        listeners.on_execution_failed_heartbeat(object())
        listeners.on_scheduler_event(SchedulerEventType.RAN_EXECUTE_DUE)
        listeners.on_candidate_event(CandidateEventType.EXECUTED)

        expected = [
            "scheduled:t_i",
            "start",
            "complete",
            "dead",
            "failed-heartbeat",
            "scheduler:RAN_EXECUTE_DUE",
            "candidate:EXECUTED",
        ]
        assert one.seen == expected
        assert two.seen == expected

    def test_a_listener_that_raises_does_not_stop_the_others(self) -> None:
        """Observation code must never be able to fail the execution it is observing."""
        good = RecordingListener()
        listeners = SchedulerListeners([ExplodingListener(), good])

        listeners.on_execution_complete(a_completion())
        listeners.on_scheduler_event(SchedulerEventType.UNEXPECTED_ERROR)

        assert good.seen == ["complete", "scheduler:UNEXPECTED_ERROR"]

    def test_listeners_can_be_added_afterwards(self) -> None:
        listeners = SchedulerListeners()
        late = RecordingListener()

        listeners.add(late)
        listeners.on_execution_complete(a_completion())

        assert late.seen == ["complete"]

    def test_the_noop_ones_do_nothing_and_raise_nothing(self) -> None:
        NOOP.on_execution_complete(a_completion())
        SchedulerListeners.NOOP.on_execution_complete(a_completion())

    def test_it_is_itself_a_listener(self) -> None:
        """So a group can be nested inside another group."""
        assert isinstance(SchedulerListeners(), SchedulerListener)


class TestEventTypes:
    def test_every_scheduler_event_maps_to_a_stats_event(self) -> None:
        for event in SchedulerEventType:
            assert isinstance(event.to_stats_registry_event(), SchedulerStatsEvent)

    def test_every_candidate_event_maps_to_a_stats_event(self) -> None:
        for event in CandidateEventType:
            assert isinstance(event.to_stats_registry_event(), CandidateStatsEvent)

    def test_the_names_line_up(self) -> None:
        assert {event.name for event in SchedulerEventType} == {
            event.name for event in SchedulerStatsEvent
        }
        assert {event.name for event in CandidateEventType} == {
            event.name for event in CandidateStatsEvent
        }


class TestExecutionChain:
    def test_with_no_interceptors_it_just_runs_the_body(self) -> None:
        handler = OnCompleteRemove[Any]()
        chain = ExecutionChain([], lambda instance, context: handler)

        assert chain.proceed(TaskInstance[Any]("t", "i"), object()) is handler  # type: ignore[arg-type]

    def test_interceptors_run_outermost_first(self) -> None:
        order: list[str] = []

        class Named:
            def __init__(self, name: str) -> None:
                self.name = name

            def execute(self, instance: Any, context: Any, chain: ExecutionChain) -> Any:
                order.append(f"before:{self.name}")
                result = chain.proceed(instance, context)
                order.append(f"after:{self.name}")
                return result

        def body(instance: Any, context: Any) -> Any:
            order.append("body")
            return OnCompleteRemove[Any]()

        ExecutionChain([Named("one"), Named("two")], body).proceed(
            TaskInstance[Any]("t", "i"), object()  # type: ignore[arg-type]
        )

        assert order == ["before:one", "before:two", "body", "after:two", "after:one"]

    def test_an_interceptor_can_decide_not_to_proceed(self) -> None:
        """Which is what separates an interceptor from a listener."""
        instead = OnCompleteRemove[Any]()

        class Short:
            def execute(self, instance: Any, context: Any, chain: ExecutionChain) -> Any:
                return instead

        def body(instance: Any, context: Any) -> Any:
            raise AssertionError("should not run")

        assert ExecutionChain([Short()], body).proceed(
            TaskInstance[Any]("t", "i"), object()  # type: ignore[arg-type]
        ) is instead


class CountingRegistry:
    def __init__(self) -> None:
        self.events: list[Any] = []
        self.completions: list[ExecutionComplete] = []

    def register(self, event: Any) -> None:
        self.events.append(event)

    def register_single_completed_execution(self, completion: ExecutionComplete) -> None:
        self.completions.append(completion)


class TestStatsRegistryAdapter:
    def test_a_success_counts_as_completed(self) -> None:
        registry = CountingRegistry()

        StatsRegistryAdapter(registry).on_execution_complete(a_completion())

        assert registry.events == [ExecutionStatsEvent.COMPLETED]
        assert len(registry.completions) == 1

    def test_a_failure_counts_as_failed(self) -> None:
        registry = CountingRegistry()
        execution = Execution(NOW, TaskInstance[Any]("t", "i"))
        failed = ExecutionComplete.failure(execution, NOW, NOW, RuntimeError("boom"))

        StatsRegistryAdapter(registry).on_execution_complete(failed)

        assert registry.events == [ExecutionStatsEvent.FAILED]

    def test_scheduler_and_candidate_events_are_translated(self) -> None:
        registry = CountingRegistry()
        adapter = StatsRegistryAdapter(registry)

        adapter.on_scheduler_event(SchedulerEventType.DEAD_EXECUTION)
        adapter.on_candidate_event(CandidateEventType.ALREADY_PICKED)

        assert registry.events == [
            SchedulerStatsEvent.DEAD_EXECUTION,
            CandidateStatsEvent.ALREADY_PICKED,
        ]

    def test_no_registry_is_allowed_and_counts_nothing(self) -> None:
        adapter = StatsRegistryAdapter(None)

        adapter.on_execution_complete(a_completion())
        adapter.on_scheduler_event(SchedulerEventType.DEAD_EXECUTION)
        adapter.on_candidate_event(CandidateEventType.STALE)

    def test_the_default_registry_counts_nothing(self) -> None:
        registry = DefaultStatsRegistry()

        assert isinstance(registry, StatsRegistry)
        registry.register(ExecutionStatsEvent.COMPLETED)
        registry.register_single_completed_execution(a_completion())


class TestSchedulerName:
    def test_a_fixed_name(self) -> None:
        assert Fixed("worker-1").name == "worker-1"

    def test_the_hostname(self) -> None:
        """Whatever it is, it must be a non-empty string - it goes in `picked_by`, which is how
        an operator finds the instance running a stuck task."""
        assert Hostname().name

    def test_they_are_reachable_under_their_java_names(self) -> None:
        assert SchedulerName.Fixed is Fixed
        assert SchedulerName.Hostname is Hostname
