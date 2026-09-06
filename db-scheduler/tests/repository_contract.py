"""The repository contract, once, for every dialect.

db-scheduler proves its repository against each database with a `CompatibilityTest` subclass per
dialect - Postgres, MySQL 5 and 8, MariaDB, MSSQL, Oracle, SQLite. Here the contract is written
once and the fixture is parametrised, so a dialect cannot be quietly left out of a scenario: the
suite runs the same assertions against SQLite by default and against real servers in containers
under `-m compatibility`.

These are the behaviours the whole scheduler rests on. The one that matters most is the pick:
two schedulers race for a row and exactly one may win.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from db_scheduler.task import TaskInstance
from db_scheduler.task.helper import SchedulableInstance

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def an_instance(task: str = "a-task", id: str = "an-id", data: Any = None) -> TaskInstance[Any]:
    return TaskInstance[Any](task, id, data)


def scheduled(
    instance: TaskInstance[Any], at: datetime = NOW
) -> SchedulableInstance[Any]:
    return SchedulableInstance(instance, at)


class RepositoryContract:
    """Subclassed per dialect. `repository` and `clock` come from the fixture."""

    # ------------------------------------------------------------------ creating

    def test_it_creates_a_row_and_reads_it_back(self, repository: Any) -> None:
        assert repository.create_if_not_exists(scheduled(an_instance())) is True

        found = repository.get_execution("a-task", "an-id")
        assert found is not None
        assert found.execution_time == NOW
        assert found.picked is False
        assert found.version == 1
        assert found.consecutive_failures == 0

    def test_creating_the_same_instance_twice_is_declined_not_an_error(
        self, repository: Any
    ) -> None:
        """Every instance in the cluster registers its recurring tasks at boot. The row already
        being there is the normal case, not a failure."""
        assert repository.create_if_not_exists(scheduled(an_instance())) is True
        assert repository.create_if_not_exists(scheduled(an_instance())) is False
        assert len(repository.get_scheduled_executions()) == 1

    def test_a_batch_is_created_in_one_statement(self, repository: Any) -> None:
        repository.create_batch(
            [scheduled(an_instance(id=f"id-{n}"), NOW + timedelta(seconds=n)) for n in range(5)]
        )
        assert len(repository.get_scheduled_executions()) == 5

    def test_data_survives_the_round_trip(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance(data={"n": 1, "s": "x"})))
        found = repository.get_execution("a-task", "an-id")
        assert found is not None
        assert found.task_instance.data == {"n": 1, "s": "x"}

    def test_a_null_data_survives_as_null(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance(data=None)))
        found = repository.get_execution("a-task", "an-id")
        assert found is not None
        assert found.task_instance.data is None

    # ------------------------------------------------------------------ due

    def test_only_executions_that_are_due_are_returned(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance(id="past"), NOW - timedelta(days=1)))
        repository.create_if_not_exists(scheduled(an_instance(id="now"), NOW))
        repository.create_if_not_exists(
            scheduled(an_instance(id="future"), NOW + timedelta(days=1))
        )

        due = repository.get_due(NOW, limit=10)

        assert {e.id for e in due} == {"past", "now"}

    def test_due_comes_back_oldest_first(self, repository: Any) -> None:
        for n in (3, 1, 2):
            repository.create_if_not_exists(
                scheduled(an_instance(id=f"id-{n}"), NOW - timedelta(minutes=n))
            )

        due = repository.get_due(NOW, limit=10)

        assert [e.id for e in due] == ["id-3", "id-2", "id-1"]

    def test_the_limit_is_respected(self, repository: Any) -> None:
        for n in range(10):
            repository.create_if_not_exists(
                scheduled(an_instance(id=f"id-{n}"), NOW - timedelta(minutes=n))
            )
        assert len(repository.get_due(NOW, limit=3)) == 3

    def test_a_picked_execution_is_not_due(self, repository: Any) -> None:
        """It is already somebody's. Returning it would be handing the same work out twice."""
        repository.create_if_not_exists(scheduled(an_instance()))
        [execution] = repository.get_due(NOW, limit=10)
        repository.pick(execution, NOW)

        assert repository.get_due(NOW, limit=10) == []

    def test_priority_orders_before_time_when_asked(self, repository_by_priority: Any) -> None:
        repository = repository_by_priority
        from db_scheduler.task import Priority

        repository.create_if_not_exists(
            scheduled(
                TaskInstance[Any]("a-task", "low", priority=Priority.LOW),
                NOW - timedelta(hours=1),
            )
        )
        repository.create_if_not_exists(
            scheduled(TaskInstance[Any]("a-task", "high", priority=Priority.HIGH), NOW)
        )

        due = repository.get_due(NOW, limit=10)

        assert [e.id for e in due] == ["high", "low"], "priority first, then time"

    # ------------------------------------------------------------------ picking

    def test_picking_marks_the_row_and_moves_the_version(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance()))
        [execution] = repository.get_due(NOW, limit=10)

        picked = repository.pick(execution, NOW)

        assert picked is not None
        assert picked.picked is True
        assert picked.picked_by is not None
        assert picked.last_heartbeat == NOW
        assert picked.version == execution.version + 1

    def test_a_second_pick_of_the_same_row_is_declined(self, repository: Any) -> None:
        """The whole coordination, in one assertion: the version guard means the loser's
        UPDATE matches nothing."""
        repository.create_if_not_exists(scheduled(an_instance()))
        [execution] = repository.get_due(NOW, limit=10)

        assert repository.pick(execution, NOW) is not None
        assert repository.pick(execution, NOW) is None, "the version has moved under it"

    def test_concurrent_picks_produce_exactly_one_winner(self, repository: Any) -> None:
        """Threads rather than a stub, because the guarantee is the database's, not ours."""
        repository.create_if_not_exists(scheduled(an_instance()))
        [execution] = repository.get_due(NOW, limit=10)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: repository.pick(execution, NOW), range(8)))

        assert sum(1 for r in results if r is not None) == 1, "eight racers, one winner"

    def test_unpicking_a_batch_puts_them_back(self, repository: Any) -> None:
        for n in range(3):
            repository.create_if_not_exists(scheduled(an_instance(id=f"id-{n}")))
        picked = [repository.pick(e, NOW) for e in repository.get_due(NOW, limit=10)]

        repository.unpick_picked_batch([p for p in picked if p])

        assert len(repository.get_due(NOW, limit=10)) == 3

    # ------------------------------------------------------------------ rescheduling

    def test_rescheduling_moves_the_time_and_clears_the_pick(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance()))
        [execution] = repository.get_due(NOW, limit=10)
        picked = repository.pick(execution, NOW)
        later = NOW + timedelta(hours=1)

        assert repository.reschedule(picked, later, last_success=NOW, consecutive_failures=0)

        found = repository.get_execution("a-task", "an-id")
        assert found is not None
        assert found.execution_time == later
        assert found.picked is False
        assert found.picked_by is None
        assert found.last_heartbeat is None
        assert found.last_success == NOW
        assert found.consecutive_failures == 0

    def test_rescheduling_a_failure_records_it(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance()))
        [execution] = repository.get_due(NOW, limit=10)
        picked = repository.pick(execution, NOW)

        repository.reschedule(
            picked, NOW + timedelta(minutes=5), last_failure=NOW, consecutive_failures=1
        )

        found = repository.get_execution("a-task", "an-id")
        assert found is not None
        assert found.last_failure == NOW
        assert found.consecutive_failures == 1
        assert found.last_success is None

    def test_rescheduling_can_replace_the_data(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance(data={"n": 1})))
        [execution] = repository.get_due(NOW, limit=10)
        picked = repository.pick(execution, NOW)

        repository.reschedule(picked, NOW + timedelta(hours=1), new_data={"n": 2})

        found = repository.get_execution("a-task", "an-id")
        assert found is not None
        assert found.task_instance.data == {"n": 2}

    def test_rescheduling_a_stale_execution_is_declined(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance()))
        [execution] = repository.get_due(NOW, limit=10)
        repository.pick(execution, NOW)

        # `execution` still carries the version from before the pick
        assert repository.reschedule(execution, NOW + timedelta(hours=1)) is False

    # ------------------------------------------------------------------ removing

    def test_removing_deletes_the_row(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance()))
        [execution] = repository.get_due(NOW, limit=10)

        repository.remove(execution)

        assert repository.get_execution("a-task", "an-id") is None

    def test_removing_a_stale_execution_is_refused(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance()))
        [execution] = repository.get_due(NOW, limit=10)
        repository.pick(execution, NOW)

        with pytest.raises(Exception, match=r"(?i)version|remove|moved"):
            repository.remove(execution)

    def test_removing_every_execution_of_a_task(self, repository: Any) -> None:
        for n in range(3):
            repository.create_if_not_exists(scheduled(an_instance(id=f"id-{n}")))
        repository.create_if_not_exists(scheduled(an_instance(task="other", id="x")))

        assert repository.remove_executions("a-task") == 3
        assert len(repository.get_scheduled_executions()) == 1

    def test_replace_removes_one_and_schedules_another(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance()))
        [execution] = repository.get_due(NOW, limit=10)
        later = NOW + timedelta(hours=2)

        at = repository.replace(execution, scheduled(an_instance(id="the-next-one"), later))

        assert at == later
        assert repository.get_execution("a-task", "an-id") is None
        assert repository.get_execution("a-task", "the-next-one") is not None

    # ------------------------------------------------------------------ heartbeats

    def test_a_heartbeat_is_recorded(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance()))
        [execution] = repository.get_due(NOW, limit=10)
        picked = repository.pick(execution, NOW)
        later = NOW + timedelta(seconds=30)

        assert repository.update_heartbeat(picked, later) is True

        found = repository.get_execution("a-task", "an-id")
        assert found is not None
        assert found.last_heartbeat == later

    def test_a_heartbeat_for_a_row_that_moved_is_declined(self, repository: Any) -> None:
        """The completion handler finished a moment before the heartbeat thread got there.
        A race that happens, and must not be an error."""
        repository.create_if_not_exists(scheduled(an_instance()))
        [execution] = repository.get_due(NOW, limit=10)
        repository.pick(execution, NOW)

        assert repository.update_heartbeat(execution, NOW + timedelta(seconds=30)) is False

    def test_a_heartbeat_for_a_deleted_row_is_declined(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance()))
        [execution] = repository.get_due(NOW, limit=10)
        repository.remove(execution)

        assert repository.update_heartbeat(execution, NOW) is False

    # ------------------------------------------------------------------ dead executions

    def test_a_picked_row_with_an_old_heartbeat_is_dead(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance()))
        [execution] = repository.get_due(NOW, limit=10)
        repository.pick(execution, NOW - timedelta(hours=1))

        dead = repository.get_dead_executions(older_than=NOW - timedelta(minutes=30))

        assert [e.id for e in dead] == ["an-id"]

    def test_a_fresh_heartbeat_is_not_dead(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance()))
        [execution] = repository.get_due(NOW, limit=10)
        repository.pick(execution, NOW)

        assert repository.get_dead_executions(older_than=NOW - timedelta(minutes=30)) == []

    def test_an_unpicked_row_is_never_dead(self, repository: Any) -> None:
        """Nobody is holding it, so there is nothing to take over."""
        repository.create_if_not_exists(scheduled(an_instance(), NOW - timedelta(days=7)))

        assert repository.get_dead_executions(older_than=NOW) == []

    # ------------------------------------------------------------------ reporting

    def test_executions_failing_longer_than(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance(id="failing")))
        repository.create_if_not_exists(scheduled(an_instance(id="fine")))
        failing = repository.get_execution("a-task", "failing")
        repository.reschedule(
            failing, NOW, last_failure=NOW - timedelta(hours=2), consecutive_failures=3
        )

        found = repository.get_executions_failing_longer_than(timedelta(hours=1), now=NOW)

        assert [e.id for e in found] == ["failing"]

    def test_scheduled_executions_can_be_filtered_by_task(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance(task="a", id="1")))
        repository.create_if_not_exists(scheduled(an_instance(task="b", id="2")))

        assert [e.id for e in repository.get_scheduled_executions(task_name="a")] == ["1"]

    def test_scheduled_executions_can_be_filtered_by_picked(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance(id="1")))
        repository.create_if_not_exists(scheduled(an_instance(id="2")))
        [first, _] = repository.get_due(NOW, limit=10)
        repository.pick(first, NOW)

        assert [e.id for e in repository.get_scheduled_executions(picked=True)] == ["1"]
        assert [e.id for e in repository.get_scheduled_executions(picked=False)] == ["2"]

    def test_a_summary_by_task(self, repository: Any) -> None:
        for n in range(3):
            repository.create_if_not_exists(scheduled(an_instance(task="a", id=f"{n}")))
        repository.create_if_not_exists(scheduled(an_instance(task="b", id="x")))

        summary = {s.task_name: s.count for s in repository.get_scheduled_executions_summary()}

        assert summary == {"a": 3, "b": 1}


class LockAndFetchContract:
    """The other polling strategy: claim in the same statement that selects.

    Only for dialects that can do `SELECT ... FOR UPDATE SKIP LOCKED`. db-scheduler refuses the
    strategy on the rest rather than emulating it, and so does this - which is why the first
    two here skip where it is unsupported and the third asserts the refusal.
    """

    @pytest.fixture(autouse=True)
    def _only_where_supported(self, request: Any, repository: Any) -> None:
        if request.function.__name__ == "test_it_is_refused_where_the_dialect_cannot_do_it":
            return
        if not repository.supports_lock_and_fetch:
            pytest.skip(f"{repository.customization.name} cannot do SELECT FOR UPDATE SKIP LOCKED")

    def test_lock_and_fetch_returns_rows_already_picked(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance()))

        [execution] = repository.lock_and_get_due(NOW, limit=10)

        assert execution.picked is True
        assert execution.picked_by is not None

    def test_a_second_caller_gets_nothing(self, repository: Any) -> None:
        repository.create_if_not_exists(scheduled(an_instance()))

        assert len(repository.lock_and_get_due(NOW, limit=10)) == 1
        assert repository.lock_and_get_due(NOW, limit=10) == []

    def test_it_is_refused_where_the_dialect_cannot_do_it(self, repository: Any) -> None:
        if repository.supports_lock_and_fetch:
            pytest.skip("this dialect supports it")
        with pytest.raises(Exception, match=r"(?i)lock.and.fetch|skip locked"):
            repository.verify_supports_lock_and_fetch()
