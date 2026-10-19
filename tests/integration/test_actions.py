"""The unit of work itself: what it can reach, and how failure is handled around it."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from ekbatan import Action, Clock, ExecutionConfiguration, RetryConfig, current_plan
from ekbatan.action import ActionExecutor
from ekbatan.errors import (
    ConnectivityError,
    EkbatanError,
    LockAcquisitionError,
    StaleRecordError,
    ValidationError,
)
from ekbatan.ids import ShardedId
from fixtures.actions import (
    CreateWalletAction,
    CreateWalletParams,
    DepositMoneyAction,
    DepositParams,
    TwinAction,
)
from fixtures.domain import NOW, Wallet
from fixtures.executing import run
from modes import stager_twin, twin

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")


def a_wallet(executor):
    return run(
        executor,
        CreateWalletAction(executor.clock),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("0.00")),
    )


# ------------------------------------------------------------------ what an action reaches


class ReadsTheClockAction(Action[None, datetime]):
    def perform(self, principal: str | None, params: None) -> datetime:
        return self.now()


def test_an_action_reads_the_clock_it_was_built_with(executor, clock):
    """An action calling datetime.now() directly is one that cannot be tested at a fixed
    instant, and whose timestamps disagree with the row it is writing."""
    assert run(executor, ReadsTheClockAction(executor.clock), None) == datetime(2026, 1, 1, 10, 0)

    clock.advance(timedelta(hours=3))
    assert run(executor, ReadsTheClockAction(executor.clock), None) == datetime(2026, 1, 1, 13, 0)


@dataclass(frozen=True, kw_only=True)
class NoteParams:
    note: object


class OpensWithANoteAction(Action[NoteParams, Wallet]):
    def perform(self, principal: str | None, params: NoteParams) -> Wallet:
        return self.plan.add(
            Wallet.open(
                id=ShardedId[Wallet].random(),
                owner_id=uuid4(),
                currency="EUR",
                balance=Decimal("1"),
                created_date=self.clock.now(),
            )
        )


stager_twin(ReadsTheClockAction)
stager_twin(OpensWithANoteAction)


class _PlainNote:
    def __init__(self) -> None:
        self.text = "hello"


def test_params_holding_a_plain_object_fail_and_store_nothing(executor, wallets, registry):
    """A plain object in the params used to be stored as its repr, `<_PlainNote object at
    0x...>`: the action committed and the data was gone. Now it is refused, and nothing is
    written - neither the wallet nor the outbox row."""
    from sqlalchemy import func, select

    from fixtures.schema import EVENTS

    with pytest.raises(ValidationError, match="_PlainNote cannot be written as JSON"):
        run(executor, OpensWithANoteAction(executor.clock), NoteParams(note=_PlainNote()))

    assert wallets.count() == 0
    with registry.default.connection() as conn:
        assert conn.execute(select(func.count()).select_from(EVENTS)).scalar_one() == 0


class ReadsThePrincipalAction(Action[None, str | None]):
    def perform(self, principal: str | None, params: None) -> str | None:
        return principal


stager_twin(ReadsThePrincipalAction)


def test_an_action_knows_who_asked(executor):
    assert run(executor, ReadsThePrincipalAction(executor.clock), None, principal="ada") == "ada"
    assert run(executor, ReadsThePrincipalAction(executor.clock), None) is None


def test_the_plan_is_gone_afterwards(executor, clock):
    run(executor, ReadsTheClockAction(clock), None)
    with pytest.raises(EkbatanError, match="no unit of work here"):
        current_plan()


def test_an_action_holding_a_different_clock_is_accepted_as_in_java(executor, clock, outbox):
    """Java's executor never compares the two clocks: the action reads its own, and the rows are
    stamped from the executor's. Python refused the executor at build (Dracula 12)."""
    from ekbatan import ActionRegistry, VirtualClock
    from fixtures.schema import EVENTS

    elsewhere = VirtualClock(datetime(1999, 1, 1))
    other = ActionExecutor(
        namespace="example.wallet",
        registry=executor.registry,
        repositories=executor.changes.repositories,
        actions=ActionRegistry([ReadsTheClockAction(elsewhere)]),
        events=EVENTS,
        clock=clock,
    )

    assert other.execute(ReadsTheClockAction, None) == datetime(1999, 1, 1)
    (row,) = outbox()
    assert row["completion_date"] == datetime(2026, 1, 1, 10, 0)


# --------------------------------------------------------------------------------- retries


class FailsWithAction(Action[None, str]):
    """Raises the given errors in turn, then succeeds."""

    def __init__(self, *errors: BaseException, clock: Clock) -> None:
        super().__init__(clock)
        self.errors = list(errors)
        self.attempts = 0

    def perform(self, principal: str | None, params: None) -> str:
        self.attempts += 1
        if self.errors:
            raise self.errors.pop(0)
        return "done"


stager_twin(FailsWithAction)


def test_each_kind_of_failure_gets_its_own_policy(executor):
    """A lost version race deserves one quick retry; an unreachable lock service deserves a few
    slower ones. One policy for everything has to pick the wrong number for something."""
    config = ExecutionConfiguration(
        retry_configs=(
            RetryConfig(on=StaleRecordError, max_retries=1, delay=0),
            RetryConfig(on=LockAcquisitionError, max_retries=3, delay=0),
        )
    )

    conflicted = FailsWithAction(StaleRecordError("moved"), clock=executor.clock)
    assert run(executor, conflicted, None, config=config) == "done"
    assert conflicted.attempts == 2

    locked = FailsWithAction(
        *[LockAcquisitionError("k", "backend down") for _ in range(3)], clock=executor.clock
    )
    assert run(executor, locked, None, config=config) == "done"
    assert locked.attempts == 4, "three retries, as its own policy allows"

    stubborn = FailsWithAction(*[StaleRecordError("moved") for _ in range(2)], clock=executor.clock)
    with pytest.raises(StaleRecordError):
        run(executor, stubborn, None, config=config)
    assert stubborn.attempts == 2, "its policy allowed one retry, not three"


def test_the_first_matching_policy_wins(executor):
    config = ExecutionConfiguration(
        retry_configs=(
            RetryConfig(on=StaleRecordError, max_retries=0, delay=0),
            RetryConfig(on=EkbatanError, max_retries=5, delay=0),
        )
    )
    action = FailsWithAction(StaleRecordError("moved"), clock=executor.clock)

    with pytest.raises(StaleRecordError):
        run(executor, action, None, config=config)
    assert action.attempts == 1, "the narrow policy came first and said no retries"


def test_a_failure_is_recognised_through_what_it_was_raised_from(executor):
    """A driver wrapping a conflict, or a handler re-raising one, would otherwise be
    unrecognisable - the retryable failure is there, one link down."""
    wrapped = ConnectivityError("the driver gave up")
    wrapped.__cause__ = StaleRecordError("moved")

    action = FailsWithAction(wrapped, clock=executor.clock)
    assert run(executor, action, None) == "done"
    assert action.attempts == 2


def test_a_cycle_in_the_cause_chain_does_not_hang(executor):
    first = ValidationError("a")
    second = ValidationError("b")
    first.__cause__ = second
    second.__cause__ = first

    with pytest.raises(ValidationError):
        run(executor, FailsWithAction(first, clock=executor.clock), None)


def test_max_retries_counts_retries_and_not_attempts(executor):
    """Java's `execute_retries_exact_number_of_times`, and the reason for the field's name.

    `max_retries=3` means one run plus three more. Read as "attempts" it would run three times,
    and the difference is the sort of off-by-one nobody catches in review because both readings
    look right on the page.
    """
    for allowed in range(4):
        config = ExecutionConfiguration(
            retry_configs=(RetryConfig(on=StaleRecordError, max_retries=allowed, delay=0),)
        )
        action = FailsWithAction(
            *[StaleRecordError("moved") for _ in range(allowed)], clock=executor.clock
        )

        assert run(executor, action, None, config=config) == "done"
        assert action.attempts == allowed + 1, f"{allowed} retries is {allowed + 1} runs"


def test_when_the_retries_run_out_the_original_failure_is_what_escapes(executor):
    """Java's `execute_throws_after_max_attempts_exhausted`. The caller sees what went wrong,
    not a wrapper saying that retrying did not help."""
    config = ExecutionConfiguration(
        retry_configs=(RetryConfig(on=StaleRecordError, max_retries=2, delay=0),)
    )
    action = FailsWithAction(
        *[StaleRecordError(f"attempt {n}") for n in range(5)], clock=executor.clock
    )

    with pytest.raises(StaleRecordError, match="attempt 2"):
        run(executor, action, None, config=config)

    assert action.attempts == 3, "one run and two retries"


def test_a_policy_on_a_superclass_does_not_catch_a_subclass_as_javas_does_not(executor):
    """Java's `execute_does_not_retry_configured_superclass_when_only_subclass_is_thrown`.

    Java looks the thrown class up in a `Map<Class, RetryConfig>`, so a policy on `EkbatanError`
    does nothing for a `StaleRecordError`. This port matched with `isinstance` until `EKB-0198`,
    which retried strictly more than Java did for the same configuration.

    The cost of matching Java is the case below: name a class that is only ever raised as one of
    its subclasses and the policy is silently dead. That is Java's cost too.
    """
    config = ExecutionConfiguration(
        retry_configs=(RetryConfig(on=EkbatanError, max_retries=1, delay=0),)
    )
    action = FailsWithAction(StaleRecordError("a subclass of EkbatanError"), clock=executor.clock)

    with pytest.raises(StaleRecordError):
        run(executor, action, None, config=config)
    assert action.attempts == 1, "the superclass's policy did not cover its subclass"


def test_a_policy_on_the_exact_class_catches_it(executor):
    """The other side of the same rule: name the class that is thrown and it matches."""
    config = ExecutionConfiguration(
        retry_configs=(RetryConfig(on=StaleRecordError, max_retries=1, delay=0),)
    )
    action = FailsWithAction(StaleRecordError("moved"), clock=executor.clock)

    assert run(executor, action, None, config=config) == "done"
    assert action.attempts == 2


def test_a_cause_deliberately_suppressed_is_not_looked_through(executor):
    """`raise ... from None` says the earlier failure is not part of this one's story.

    Java's nearest case is `execute_does_not_retry_configured_exception_when_only_suppressed`,
    which is about `addSuppressed` - a mechanism Python has no equivalent of.

    Since `EKB-0198` the walk follows `__cause__` only, so this is really the same assertion as
    the implicit-context test above: `from None` leaves `__cause__` unset and there is nothing
    to follow. Kept because it is the case a reader reaches for, and a test that says "still
    no" is cheaper than working it out from the rule.
    """
    hidden = _raised_from_none()
    action = FailsWithAction(hidden, clock=executor.clock)

    with pytest.raises(ConnectivityError):
        run(executor, action, None)
    assert action.attempts == 1, "nothing looked past the suppression"


def test_an_implicit_context_is_not_looked_through_because_java_has_no_such_link(executor):
    """Raised while handling a retryable failure, without `from`.

    Python sets `__context__` there for free and Java has no equivalent, so this port followed
    a link Java cannot see - and retried failures Java would not. Since `EKB-0198` the walk is
    `__cause__` only, which is what `getCause()` reaches. Write `raise ... from err` and it is
    followed again; that is the same `raise` that makes Java's `getCause()` non-null.
    """
    action = FailsWithAction(_raised_while_handling(), clock=executor.clock)

    with pytest.raises(ConnectivityError):
        run(executor, action, None)
    assert action.attempts == 1, "nothing followed the implicit context"


def test_an_explicit_cause_is_looked_through_as_javas_getcause_is(executor):
    """`raise ... from err` is the link both ports follow."""
    action = FailsWithAction(_raised_from_a_cause(), clock=executor.clock)

    assert run(executor, action, None) == "done"
    assert action.attempts == 2


def _raised_from_none() -> BaseException:
    try:
        try:
            raise StaleRecordError("moved")
        except StaleRecordError:
            raise ConnectivityError("the driver gave up") from None
    except ConnectivityError as caught:
        return caught


def _raised_while_handling() -> BaseException:
    try:
        try:
            raise StaleRecordError("moved")
        except StaleRecordError:
            # No `from`, deliberately: the implicit `__context__` Python sets here is exactly
            # what this pair of tests is about.
            raise ConnectivityError("the driver gave up")  # noqa: B904
    except ConnectivityError as caught:
        return caught


def _raised_from_a_cause() -> BaseException:
    try:
        try:
            raise StaleRecordError("moved")
        except StaleRecordError as conflict:
            raise ConnectivityError("the driver gave up") from conflict
    except ConnectivityError as caught:
        return caught


def test_retries_can_be_switched_off_entirely(executor):
    action = FailsWithAction(StaleRecordError("moved"), clock=executor.clock)
    with pytest.raises(StaleRecordError):
        run(executor, action, None, config=ExecutionConfiguration.no_retry())
    assert action.attempts == 1


# --------------------------------------------------------------------- failure that is not an error


class CollapsesAction(Action[None, None]):
    def __init__(self, failure: BaseException, clock: Clock) -> None:
        super().__init__(clock)
        self.failure = failure

    def perform(self, principal: str | None, params: None) -> None:
        raise self.failure


stager_twin(CollapsesAction)


def not_an_error(mode: str, message: str) -> BaseException:
    """What is not an ordinary error: a KeyboardInterrupt on a thread; on asyncio a cancelled
    task - a KeyboardInterrupt raised inside a task stops the event loop itself."""
    return KeyboardInterrupt(message) if mode == "sync" else asyncio.CancelledError(message)


def test_a_failure_that_is_not_an_ordinary_error_is_still_reported(executor, caplog, outbox, mode):
    """Catching only Exception meant one of these was recorded as neither success nor failure:
    no log line, no span status, nothing - while the unit of work had certainly failed."""
    failure = not_an_error(mode, "the runtime pulled the rug")
    with (
        caplog.at_level(logging.ERROR, logger="io.ekbatan.core.action.ActionExecutor"),
        pytest.raises(type(failure)),
    ):
        run(executor, CollapsesAction(failure, executor.clock), None)

    assert any("CollapsesAction failed" in r.getMessage() for r in caplog.records)
    assert outbox() == [], "and nothing was committed"


def test_such_a_failure_is_never_retried(executor, mode):
    failure = not_an_error(mode, "stop")
    action = FailsWithAction(failure, clock=executor.clock)
    with pytest.raises(type(failure)):
        run(
            executor,
            action,
            None,
            config=ExecutionConfiguration(
                retry_configs=(RetryConfig(on=BaseException, max_retries=3, delay=0),)
            ),
        )
    assert action.attempts == 1, "a cancellation is not a transient fault to sit through"


# ------------------------------------------------------------------------------- the plan


class StagesTwiceAction(Action[DepositParams, Wallet]):
    def __init__(self, wallets, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        wallet = self.wallets.get(params.wallet_id)
        once = self.plan.update(wallet.deposit(params.amount))
        return self.plan.update(once.deposit(params.amount))


class ComposesThenStagesAction(Action[DepositParams, Wallet]):
    """The same two changes, written the way they should be."""

    def __init__(self, wallets, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        wallet = self.wallets.get(params.wallet_id)
        return self.plan.update(wallet.deposit(params.amount).deposit(params.amount))


@twin(StagesTwiceAction)
class AsyncStagesTwiceAction(TwinAction[DepositParams, Wallet]):
    def __init__(self, wallets, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    async def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        wallet = await self.wallets.get(params.wallet_id)
        once = self.plan.update(wallet.deposit(params.amount))
        return self.plan.update(once.deposit(params.amount))


@twin(ComposesThenStagesAction)
class AsyncComposesThenStagesAction(TwinAction[DepositParams, Wallet]):
    def __init__(self, wallets, clock: Clock) -> None:
        super().__init__(clock)
        self.wallets = wallets

    async def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        wallet = await self.wallets.get(params.wallet_id)
        return self.plan.update(wallet.deposit(params.amount).deposit(params.amount))


def test_staging_one_row_twice_is_refused(executor, wallets, outbox):
    """A unit of work writes each row once, so two stagings cannot mean two writes. Collapsing
    them silently would leave the value handed back by the first call claiming a version it no
    longer has and a balance nobody is going to write."""
    wallet = a_wallet(executor)
    before = len(outbox())

    with pytest.raises(ValidationError, match="already staged"):
        run(
            executor,
            StagesTwiceAction(wallets, executor.clock),
            DepositParams(wallet_id=wallet.id, amount=Decimal("5.00"), recipient="ada@x"),
        )

    assert wallets.get(wallet.id).version == 1, "and nothing was written"
    assert len(outbox()) == before


def test_the_error_says_what_to_do_instead(executor, wallets):
    wallet = a_wallet(executor)
    with pytest.raises(ValidationError) as raised:
        run(
            executor,
            StagesTwiceAction(wallets, executor.clock),
            DepositParams(wallet_id=wallet.id, amount=Decimal("5.00"), recipient="ada@x"),
        )
    message = str(raised.value)
    assert "already staged" in message
    assert "composed" in message, "an error that does not say what to do instead is half an error"
    assert "Wallet" in message and str(wallet.id) in message


def test_composing_first_then_staging_once_works(executor, wallets, outbox):
    wallet = a_wallet(executor)
    before = {r["id"] for r in outbox()}

    result = run(
        executor,
        ComposesThenStagesAction(wallets, executor.clock),
        DepositParams(wallet_id=wallet.id, amount=Decimal("5.00"), recipient="ada@x"),
    )

    stored = wallets.get(wallet.id)
    assert stored.balance == Decimal("10.0000"), "both changes landed"
    assert stored.version == 2, "one write, one version"
    assert stored == result, "and what the action returned is what the database holds"

    # By id, not by slicing the tail: rows come back ordered by `event_date`, which the frozen
    # test clock makes identical across invocations.
    events = [r["event_type"] for r in outbox() if r["id"] not in before and r["event_type"]]
    assert events == ["WalletMoneyDepositedEvent", "WalletMoneyDepositedEvent"], (
        "both events ride along, because both changes happened"
    )


def test_inserting_and_then_changing_the_same_row_is_refused(executor, wallets):
    class CreateThenTopUp(Action[None, Wallet]):
        def perform(self, principal: str | None, params: None) -> Wallet:
            fresh = self.plan.add(
                Wallet.open(
                    id=ShardedId[Wallet].random(),
                    owner_id=uuid4(),
                    currency="EUR",
                    balance=Decimal("10.00"),
                    created_date=self.clock.now(),
                )
            )
            return self.plan.update(fresh.deposit(Decimal("5.00")))

    with pytest.raises(ValidationError, match="already staged"):
        run(executor, CreateThenTopUp(executor.clock), None)

    assert wallets.count() == 0


# -------------------------------------------------------------------------------- tracing


def test_the_framework_runs_without_a_tracing_sdk(executor):
    """OpenTelemetry's API is a dependency, as in Java; the SDK is the application's choice.
    Without one, spans go nowhere and cost nothing - Java's no-op `GlobalOpenTelemetry`."""
    from ekbatan._tracing import span

    with span("probe", {"ekbatan.thing": "value"}) as s:
        s.set({"ekbatan.other": 1})
        s.event("something", {"count": 1})
        s.failed(ValueError("boom"), outcome="ekbatan.action.outcome")

    assert run(executor, ReadsTheClockAction(executor.clock), None) is not None


# -- the shape of a trace, not only its names ---------------------------------------------------


@contextlib.contextmanager
def collecting_spans():
    """Every span emitted inside the block, in the order they finished."""
    sdk = pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

    collected: list = []

    class Collect(SpanExporter):
        def export(self, spans):
            collected.extend(spans)
            return SpanExportResult.SUCCESS

        def shutdown(self):
            pass

    provider = sdk.TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(Collect()))
    previous = trace.get_tracer_provider()
    trace._TRACER_PROVIDER = provider  # set_tracer_provider refuses a second call
    try:
        import importlib

        import ekbatan._tracing as tracing

        importlib.reload(tracing)
        yield collected
    finally:
        trace._TRACER_PROVIDER = previous


def test_every_span_of_one_unit_of_work_belongs_to_one_trace(executor, wallets):
    """Java's `all_spans_share_same_trace_id`, `perform_span_is_child_of_execute_span` and
    `persist_span_is_child_of_execute_span`.

    Names alone do not make a trace usable. An operator opening one wants the perform and the
    persist to sit under the execute, so the shape of the failure is readable at a glance.
    """
    with collecting_spans() as collected:
        wallet = wallets.add(
            Wallet.open(
                id=ShardedId[Wallet].random(),
                owner_id=uuid4(),
                currency="EUR",
                balance=Decimal("100.00"),
                created_date=NOW,
            )
        )
        run(
            executor,
            DepositMoneyAction(wallets, executor.clock),
            DepositParams(wallet_id=wallet.id, amount=Decimal("1.00"), recipient="ada@x"),
        )

    by_name = {s.name: s for s in collected}
    execute = by_name["ekbatan.action.execute"]

    assert len({s.context.trace_id for s in collected if s.name.startswith("ekbatan.action")}) == 1
    # Java's scope, so a filter on it catches both ports' spans.
    assert {s.instrumentation_scope.name for s in collected} == {"io.ekbatan.core"}
    for child in ("ekbatan.action.perform", "ekbatan.action.persist"):
        assert by_name[child].parent.span_id == execute.context.span_id, (
            f"{child} has to sit under the execute span"
        )


def test_the_retry_count_is_on_the_action_span_whether_or_not_it_retried(executor):
    """Java's `retry_records_events_and_count_on_action_span` and `no_retry_records_zero_count`.

    Zero is written as well as one, so a dashboard can average the field rather than treating
    its absence as a zero it cannot distinguish from a missing span.
    """
    config = ExecutionConfiguration(
        retry_configs=(RetryConfig(on=StaleRecordError, max_retries=2, delay=0),)
    )

    with collecting_spans() as collected:
        run(executor, FailsWithAction(clock=executor.clock), None, config=config)
    quiet = next(s for s in collected if s.name == "ekbatan.action.execute")
    assert quiet.attributes["ekbatan.action.retry.count"] == 0

    with collecting_spans() as collected:
        run(
            executor,
            FailsWithAction(StaleRecordError("moved"), clock=executor.clock),
            None,
            config=config,
        )
    retried = next(s for s in collected if s.name == "ekbatan.action.execute")
    assert retried.attributes["ekbatan.action.retry.count"] == 1
    assert [e.name for e in retried.events].count("retry") == 1


def test_the_spans_are_the_ones_the_java_implementation_emits(executor, wallets):
    """An operator filtering a trace on `ekbatan.action.name` is filtering across both
    implementations. A name invented here would quietly not be there."""
    sdk = pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

    collected = []

    class Collect(SpanExporter):
        def export(self, spans):
            collected.extend(spans)
            return SpanExportResult.SUCCESS

        def shutdown(self):
            pass

    provider = sdk.TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(Collect()))
    previous = trace.get_tracer_provider()
    trace._TRACER_PROVIDER = provider  # set_tracer_provider refuses a second call
    try:
        import importlib

        import ekbatan._tracing as tracing

        importlib.reload(tracing)
        wallet = wallets.add(
            Wallet.open(
                id=ShardedId[Wallet].random(),
                owner_id=uuid4(),
                currency="EUR",
                balance=Decimal("100.00"),
                created_date=NOW,
            )
        )
        run(
            executor,
            DepositMoneyAction(wallets, executor.clock),
            DepositParams(
                wallet_id=wallet.id, amount=Decimal("50.00"), recipient="ada@example.com"
            ),
        )
    finally:
        trace._TRACER_PROVIDER = previous

    emitted = {s.name: dict(s.attributes or {}) for s in collected}
    assert {
        "ekbatan.action.execute",
        "ekbatan.action.perform",
        "ekbatan.action.persist",
        "ekbatan.repository",
        "ekbatan.event.persist",
    } <= set(emitted)
    assert emitted["ekbatan.action.execute"]["ekbatan.action.name"] == "DepositMoneyAction"
    assert emitted["ekbatan.action.execute"]["ekbatan.action.outcome"] == "success"
    assert emitted["ekbatan.repository"]["db.operation.name"] == "BATCH_UPDATE"
    assert emitted["ekbatan.repository"]["ekbatan.entity.type"] == "Wallet"
    assert emitted["ekbatan.repository"]["ekbatan.batch.size"] == 1
    assert emitted["ekbatan.event.persist"]["ekbatan.event.count"] == 1

    tx = next(s for s in collected if s.name == "ekbatan.transaction")
    assert tx.attributes["ekbatan.shard.group"] == 0
    assert tx.attributes["ekbatan.shard.member"] == 0

    for recorded in collected:
        assert [e.name for e in recorded.events].count("exception") <= 1, (
            "OpenTelemetry records an exception leaving a `with` itself; recording it again "
            "puts the same traceback on the span twice"
        )
