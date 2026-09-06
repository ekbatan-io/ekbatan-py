"""What happens when a run finishes, fails, or is found dead.

Ported from the Java suite's handler coverage. A `FakeExecutionOperations` stands in for the
repository, as `ExecutionOperations` is the seam every handler acts through - so these tests
assert what the handler *decided*, which is the whole of its behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from db_scheduler.task import Execution, ExecutionComplete, TaskInstance
from db_scheduler.task.handlers import (
    CancelDeadExecution,
    CompletionHandler,
    DeadExecutionHandler,
    ExponentialBackoffFailureHandler,
    FailureHandler,
    MaxRetriesBuilder,
    OnCompleteRemove,
    OnCompleteReschedule,
    OnFailureReschedule,
    OnFailureRetryLater,
    ReviveDeadExecution,
    max_retries,
)
from db_scheduler.task.schedule import Daily, FixedDelay

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class FakeExecutionOperations:
    """Records the decision instead of touching a database."""

    def __init__(self, execution: Execution) -> None:
        self.execution = execution
        self.removed = False
        self.rescheduled_to: datetime | None = None
        self.rescheduled_with_data: Any = None
        self.replaced_with: Any = None

    def stop(self) -> None:
        self.removed = True

    def remove(self) -> None:
        self.removed = True

    def reschedule(
        self, completion: ExecutionComplete, next_time: datetime, new_data: Any = None
    ) -> None:
        self.rescheduled_to = next_time
        self.rescheduled_with_data = new_data

    def remove_and_schedule_new(self, schedulable: Any) -> None:
        self.replaced_with = schedulable


def an_execution(consecutive_failures: int = 0) -> Execution:
    return Execution(
        execution_time=NOW,
        task_instance=TaskInstance[Any]("a-task", "an-id"),
        consecutive_failures=consecutive_failures,
    )


def success(execution: Execution, done: datetime = NOW) -> ExecutionComplete:
    return ExecutionComplete.success(execution, done - timedelta(seconds=1), done)


def failure(execution: Execution, done: datetime = NOW) -> ExecutionComplete:
    return ExecutionComplete.failure(execution, done - timedelta(seconds=1), done, RuntimeError("x"))


# ------------------------------------------------------------------ completion


class TestCompletionHandlers:
    def test_on_complete_remove_deletes_the_row(self) -> None:
        """A one-time task is done when it is done; the row is what would make it run again."""
        execution = an_execution()
        ops = FakeExecutionOperations(execution)

        OnCompleteRemove[Any]().complete(success(execution), ops)

        assert ops.removed is True
        assert ops.rescheduled_to is None

    def test_on_complete_reschedule_uses_the_schedule(self) -> None:
        execution = an_execution()
        ops = FakeExecutionOperations(execution)

        OnCompleteReschedule[Any](FixedDelay.of_seconds(10)).complete(success(execution), ops)

        assert ops.rescheduled_to == NOW + timedelta(seconds=10)
        assert ops.removed is False

    def test_on_complete_reschedule_can_replace_the_data(self) -> None:
        execution = an_execution()
        ops = FakeExecutionOperations(execution)

        OnCompleteReschedule[Any](FixedDelay.of_seconds(10), new_data={"n": 2}).complete(
            success(execution), ops
        )

        assert ops.rescheduled_with_data == {"n": 2}

    def test_a_completion_handler_is_a_protocol_anything_can_satisfy(self) -> None:
        calls: list[str] = []

        class Custom:
            def complete(self, completion: ExecutionComplete, ops: Any) -> None:
                calls.append("mine")

        assert isinstance(Custom(), CompletionHandler)
        Custom().complete(success(an_execution()), None)
        assert calls == ["mine"]


# ------------------------------------------------------------------ failure


class TestFailureHandlers:
    def test_retry_later_adds_a_fixed_delay(self) -> None:
        execution = an_execution()
        ops = FakeExecutionOperations(execution)

        OnFailureRetryLater[Any](timedelta(minutes=5)).on_failure(failure(execution), ops)

        assert ops.rescheduled_to == NOW + timedelta(minutes=5)

    def test_reschedule_on_failure_follows_the_schedule(self) -> None:
        """A recurring task that failed still runs at its next scheduled time, not sooner."""
        execution = an_execution()
        ops = FakeExecutionOperations(execution)

        OnFailureReschedule[Any](Daily(datetime(2026, 1, 1, 8, 0).time(), zone=UTC)).on_failure(
            failure(execution), ops
        )

        assert ops.rescheduled_to == datetime(2026, 1, 2, 8, 0, tzinfo=UTC)

    @pytest.mark.parametrize(
        ("failures", "expected_seconds"),
        # sleep * rate ** consecutive_failures, which is the Java formula exactly
        [(0, 10), (1, 15), (2, 22.5), (3, 33.75)],
    )
    def test_exponential_backoff_grows_with_the_failure_count(
        self, failures: int, expected_seconds: float
    ) -> None:
        execution = an_execution(consecutive_failures=failures)
        ops = FakeExecutionOperations(execution)

        ExponentialBackoffFailureHandler[Any](timedelta(seconds=10)).on_failure(
            failure(execution), ops
        )

        assert ops.rescheduled_to == NOW + timedelta(seconds=expected_seconds)

    def test_the_backoff_multiplier_can_be_set(self) -> None:
        execution = an_execution(consecutive_failures=2)
        ops = FakeExecutionOperations(execution)

        ExponentialBackoffFailureHandler[Any](timedelta(seconds=1), rate=2.0).on_failure(
            failure(execution), ops
        )

        assert ops.rescheduled_to == NOW + timedelta(seconds=4)


class TestMaxRetries:
    def test_it_retries_until_the_cap_then_removes(self) -> None:
        handler: FailureHandler[Any] = max_retries(3).retry_every(timedelta(minutes=1)).then_remove()

        # consecutive_failures is what happened *before* this one, so 2 means this is the third
        for before in (0, 1, 2):
            execution = an_execution(consecutive_failures=before)
            ops = FakeExecutionOperations(execution)
            handler.on_failure(failure(execution), ops)
            assert ops.rescheduled_to == NOW + timedelta(minutes=1), f"attempt {before + 1} retries"
            assert ops.removed is False

        execution = an_execution(consecutive_failures=3)
        ops = FakeExecutionOperations(execution)
        handler.on_failure(failure(execution), ops)
        assert ops.removed is True, "the fourth failure exceeds max_retries=3"
        assert ops.rescheduled_to is None

    def test_it_can_back_off_rather_than_retry_at_a_fixed_delay(self) -> None:
        handler: FailureHandler[Any] = max_retries(5).with_backoff(timedelta(seconds=10)).then_remove()
        execution = an_execution(consecutive_failures=2)
        ops = FakeExecutionOperations(execution)

        handler.on_failure(failure(execution), ops)

        assert ops.rescheduled_to == NOW + timedelta(seconds=22.5)

    def test_the_listener_is_told_when_the_cap_is_reached(self) -> None:
        seen: list[ExecutionComplete] = []
        handler: FailureHandler[Any] = max_retries(1).retry_every(timedelta(minutes=1)).then_remove(seen.append)

        execution = an_execution(consecutive_failures=1)
        ops = FakeExecutionOperations(execution)
        handler.on_failure(failure(execution), ops)

        assert len(seen) == 1
        assert ops.removed is True

    def test_a_terminal_operation_without_a_retry_behaviour_is_refused(self) -> None:
        """Otherwise the handler silently does nothing on the failures before the cap."""
        with pytest.raises(ValueError, match="retry behaviour"):
            max_retries(3).then_remove()

    def test_then_takes_an_arbitrary_terminal_handler(self) -> None:
        called: list[str] = []

        def give_up(completion: ExecutionComplete, ops: Any) -> None:
            called.append("gave up")

        handler: FailureHandler[Any] = max_retries(1).retry_every(timedelta(minutes=1)).then(give_up)
        execution = an_execution(consecutive_failures=5)
        handler.on_failure(failure(execution), FakeExecutionOperations(execution))

        assert called == ["gave up"]


# ------------------------------------------------------------------ dead executions


class TestDeadExecutionHandlers:
    def test_revive_reschedules_to_now(self) -> None:
        """The default. An instance died holding the row; another picks the work up rather
        than leaving it stuck picked forever."""
        execution = an_execution()
        ops = FakeExecutionOperations(execution)

        ReviveDeadExecution[Any]().dead_execution(failure(execution), ops, now=NOW)

        assert ops.rescheduled_to == NOW
        assert ops.removed is False

    def test_cancel_removes_the_row(self) -> None:
        """For work that must not be repeated - the opposite trade, and the caller's to make."""
        execution = an_execution()
        ops = FakeExecutionOperations(execution)

        CancelDeadExecution[Any]().dead_execution(failure(execution), ops, now=NOW)

        assert ops.removed is True
        assert ops.rescheduled_to is None

    def test_the_protocols_are_structural(self) -> None:
        assert isinstance(ReviveDeadExecution[Any](), DeadExecutionHandler)
        assert isinstance(OnFailureRetryLater[Any](timedelta(seconds=1)), FailureHandler)
        assert isinstance(MaxRetriesBuilder, type)
