"""`TaskResolver`, ported from `TaskResolverTest.java`, plus what that test leaves implicit.

The table stores a task *name*. A name this instance has no task for is not an error - a rolling
deploy makes it the normal case for a minute - so the resolver remembers it instead, and the
poll excludes it rather than fetching the same unrunnable rows for ever.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from db_scheduler.clock import SettableClock
from db_scheduler.event import (
    AbstractSchedulerListener,
    SchedulerEventType,
    SchedulerListeners,
)
from db_scheduler.resolver import Resolvable, SimpleResolvable, TaskResolver
from db_scheduler.task import Execution, TaskInstance
from db_scheduler.task.helper import Tasks

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class RecordingListener(AbstractSchedulerListener):
    def __init__(self) -> None:
        self.scheduler_events: list[SchedulerEventType] = []

    def on_scheduler_event(self, event_type: SchedulerEventType) -> None:
        self.scheduler_events.append(event_type)


def a_resolver(*tasks: Any, listener: RecordingListener | None = None) -> TaskResolver:
    return TaskResolver(
        SchedulerListeners([listener or RecordingListener()]), SettableClock(NOW), tasks
    )


def a_task(name: str) -> Any:
    return Tasks.one_time(name).execute(lambda i, c: None)


class TestResolving:
    def test_a_known_name_resolves_to_its_task(self) -> None:
        task = a_task("known")
        resolver = a_resolver(task)

        assert resolver.resolve(Resolvable.of("known", NOW)) is task

    def test_an_unknown_name_resolves_to_nothing(self) -> None:
        assert a_resolver().resolve(Resolvable.of("unknown", NOW)) is None

    def test_an_unresolved_name_raises_the_event(self) -> None:
        listener = RecordingListener()

        a_resolver(listener=listener).resolve(Resolvable.of("unresolved", NOW))

        assert listener.scheduler_events == [SchedulerEventType.UNRESOLVED_TASK]

    def test_it_can_be_asked_not_to_remember(self) -> None:
        """The client resolves names too, and a lookup that is only asking a question should not
        add to the poll's exclusion filter."""
        listener = RecordingListener()
        resolver = a_resolver(listener=listener)

        resolver.resolve(Resolvable.of("unresolved", NOW), add_unresolved_to_exclusion_filter=False)

        assert resolver.unresolved == []
        assert listener.scheduler_events == []

    def test_a_task_can_be_added_later(self) -> None:
        resolver = a_resolver()
        task = a_task("late")

        resolver.add_task(task)

        assert resolver.resolve(Resolvable.of("late", NOW)) is task


class TestUnresolved:
    def test_it_lists_what_it_could_not_resolve(self) -> None:
        resolver = a_resolver()

        resolver.resolve(Resolvable.of("unresolved1", NOW))
        resolver.resolve(Resolvable.of("unresolved2", NOW + timedelta(seconds=10)))

        assert [one.task_name for one in resolver.unresolved] == ["unresolved1", "unresolved2"]

    def test_only_those_unresolvable_for_long_enough(self) -> None:
        """A grace period, not an immediate exclusion: during a rolling deploy a name is briefly
        unknown to half the cluster, and excluding it at once would delay the new task."""
        resolver = a_resolver()

        resolver.resolve(Resolvable.of("old-unresolved", NOW - timedelta(seconds=20)))
        resolver.resolve(Resolvable.of("new-unresolved", NOW - timedelta(seconds=5)))

        assert resolver.unresolved_task_names(timedelta(seconds=10)) == ["old-unresolved"]

    def test_the_first_sighting_is_the_one_that_counts(self) -> None:
        """Otherwise a name seen every poll would keep resetting its own clock and never age
        into the exclusion filter."""
        resolver = a_resolver()

        resolver.resolve(Resolvable.of("unresolved", NOW - timedelta(seconds=20)))
        resolver.resolve(Resolvable.of("unresolved", NOW))

        assert resolver.unresolved_task_names(timedelta(seconds=10)) == ["unresolved"]
        assert len(resolver.unresolved) == 1

    def test_is_unresolved(self) -> None:
        resolver = a_resolver()
        resolver.resolve(Resolvable.of("unresolved", NOW))

        assert resolver.is_unresolved("unresolved")
        assert not resolver.is_unresolved("something-else")

    def test_clearing_one(self) -> None:
        resolver = a_resolver()
        resolver.resolve(Resolvable.of("unresolved", NOW))

        resolver.clear_unresolved("unresolved")

        assert resolver.unresolved == []

    def test_clearing_one_leaves_the_others(self) -> None:
        resolver = a_resolver()
        resolver.resolve(Resolvable.of("unresolved1", NOW))
        resolver.resolve(Resolvable.of("unresolved2", NOW))

        resolver.clear_unresolved("unresolved1")

        assert [one.task_name for one in resolver.unresolved] == ["unresolved2"]

    def test_clearing_one_that_was_never_there_is_fine(self) -> None:
        a_resolver().clear_unresolved("never-seen")


class TestResolvable:
    def test_of_builds_a_simple_one(self) -> None:
        one = Resolvable.of("t", NOW)

        assert isinstance(one, SimpleResolvable)
        assert (one.task_name, one.execution_time) == ("t", NOW)

    def test_equality_and_repr(self) -> None:
        assert Resolvable.of("t", NOW) == Resolvable.of("t", NOW)
        assert Resolvable.of("t", NOW) != Resolvable.of("u", NOW)
        assert "SimpleResolvable" in repr(Resolvable.of("t", NOW))

    def test_an_execution_is_resolvable(self) -> None:
        """Which is the point - the poll resolves the rows it just read."""
        execution = Execution(NOW, TaskInstance[Any]("t", "i"))

        assert isinstance(execution, Resolvable)
        assert (execution.task_name, execution.execution_time) == ("t", NOW)
