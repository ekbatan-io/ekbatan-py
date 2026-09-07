"""The unit of work itself: what it can reach, and how failure is handled around it."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from ekbatan import Action, ExecutionConfiguration, RetryConfig, current_clock, current_plan
from ekbatan.errors import (
    ConnectivityError,
    EkbatanError,
    LockAcquisitionError,
    StaleRecordError,
    ValidationError,
)
from fixtures.actions import CreateWallet, CreateWalletParams, DepositMoney, DepositParams
from fixtures.domain import Wallet, WalletId


def a_wallet(executor):
    return executor.execute(
        CreateWallet(),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("0.00")),
    )


# ------------------------------------------------------------------ what an action reaches


class ReadsTheClock(Action[None, datetime]):
    def perform(self, params: None) -> datetime:
        return self.now()


def test_an_action_reads_the_executor_s_clock(executor, clock):
    """An action calling datetime.now() directly is one that cannot be tested at a fixed
    instant, and whose timestamps disagree with the row it is writing."""
    assert executor.execute(ReadsTheClock(), None) == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    clock.advance(timedelta(hours=3))
    assert executor.execute(ReadsTheClock(), None) == datetime(2026, 1, 1, 13, 0, tzinfo=UTC)


class ReadsThePrincipal(Action[None, str | None]):
    def perform(self, params: None) -> str | None:
        return self.principal


def test_an_action_knows_who_asked(executor):
    assert executor.execute(ReadsThePrincipal(), None, principal="ada") == "ada"
    assert executor.execute(ReadsThePrincipal(), None) is None


def test_the_per_invocation_state_is_gone_afterwards(executor):
    executor.execute(ReadsTheClock(), None)
    with pytest.raises(EkbatanError, match="no unit of work here"):
        current_plan()
    assert current_clock() is not None, "but a clock outside one is still a clock"


# --------------------------------------------------------------------------------- retries


class FailsWith(Action[None, str]):
    """Raises the given errors in turn, then succeeds."""

    def __init__(self, *errors: BaseException) -> None:
        self.errors = list(errors)
        self.attempts = 0

    def perform(self, params: None) -> str:
        self.attempts += 1
        if self.errors:
            raise self.errors.pop(0)
        return "done"


def test_each_kind_of_failure_gets_its_own_policy(executor):
    """A lost version race deserves one quick retry; an unreachable lock service deserves a few
    slower ones. One policy for everything has to pick the wrong number for something."""
    config = ExecutionConfiguration(
        retry_configs=(
            RetryConfig(on=StaleRecordError, max_retries=1, delay=0),
            RetryConfig(on=LockAcquisitionError, max_retries=3, delay=0),
        )
    )

    conflicted = FailsWith(StaleRecordError("moved"))
    assert executor.execute(conflicted, None, config=config) == "done"
    assert conflicted.attempts == 2

    locked = FailsWith(*[LockAcquisitionError("k", "backend down") for _ in range(3)])
    assert executor.execute(locked, None, config=config) == "done"
    assert locked.attempts == 4, "three retries, as its own policy allows"

    stubborn = FailsWith(*[StaleRecordError("moved") for _ in range(2)])
    with pytest.raises(StaleRecordError):
        executor.execute(stubborn, None, config=config)
    assert stubborn.attempts == 2, "its policy allowed one retry, not three"


def test_the_first_matching_policy_wins(executor):
    config = ExecutionConfiguration(
        retry_configs=(
            RetryConfig(on=StaleRecordError, max_retries=0, delay=0),
            RetryConfig(on=EkbatanError, max_retries=5, delay=0),
        )
    )
    action = FailsWith(StaleRecordError("moved"))

    with pytest.raises(StaleRecordError):
        executor.execute(action, None, config=config)
    assert action.attempts == 1, "the narrow policy came first and said no retries"


def test_a_failure_is_recognised_through_what_it_was_raised_from(executor):
    """A driver wrapping a conflict, or a handler re-raising one, would otherwise be
    unrecognisable - the retryable failure is there, one link down."""
    wrapped = ConnectivityError("the driver gave up")
    wrapped.__cause__ = StaleRecordError("moved")

    action = FailsWith(wrapped)
    assert executor.execute(action, None) == "done"
    assert action.attempts == 2


def test_a_cycle_in_the_cause_chain_does_not_hang(executor):
    first = ValidationError("a")
    second = ValidationError("b")
    first.__cause__ = second
    second.__cause__ = first

    with pytest.raises(ValidationError):
        executor.execute(FailsWith(first), None)


def test_retries_can_be_switched_off_entirely(executor):
    action = FailsWith(StaleRecordError("moved"))
    with pytest.raises(StaleRecordError):
        executor.execute(action, None, config=ExecutionConfiguration.no_retry())
    assert action.attempts == 1


# --------------------------------------------------------------------- failure that is not an error


class Collapses(Action[None, None]):
    def perform(self, params: None) -> None:
        raise KeyboardInterrupt("the runtime pulled the rug")


def test_a_failure_that_is_not_an_ordinary_error_is_still_reported(executor, caplog, outbox):
    """Catching only Exception meant one of these was recorded as neither success nor failure:
    no log line, no span status, nothing - while the unit of work had certainly failed."""
    with caplog.at_level(logging.ERROR, logger="ekbatan.action"), pytest.raises(KeyboardInterrupt):
        executor.execute(Collapses(), None)

    assert any("Collapses failed" in r.getMessage() for r in caplog.records)
    assert outbox() == [], "and nothing was committed"


def test_such_a_failure_is_never_retried(executor):
    action = FailsWith(KeyboardInterrupt("stop"))
    with pytest.raises(KeyboardInterrupt):
        executor.execute(
            action,
            None,
            config=ExecutionConfiguration(
                retry_configs=(RetryConfig(on=BaseException, max_retries=3, delay=0),)
            ),
        )
    assert action.attempts == 1, "a cancellation is not a transient fault to sit through"


# ------------------------------------------------------------------------------- the plan


class StagesTwice(Action[DepositParams, Wallet]):
    def __init__(self, wallets) -> None:
        self.wallets = wallets

    def perform(self, params: DepositParams) -> Wallet:
        wallet = self.wallets.get(params.wallet_id)
        once = self.plan.update(wallet.deposit(params.amount))
        return self.plan.update(once.deposit(params.amount))


class ComposesThenStages(Action[DepositParams, Wallet]):
    """The same two changes, written the way they should be."""

    def __init__(self, wallets) -> None:
        self.wallets = wallets

    def perform(self, params: DepositParams) -> Wallet:
        wallet = self.wallets.get(params.wallet_id)
        return self.plan.update(wallet.deposit(params.amount).deposit(params.amount))


def test_staging_one_row_twice_is_refused(executor, wallets, outbox):
    """A unit of work writes each row once, so two stagings cannot mean two writes. Collapsing
    them silently would leave the value handed back by the first call claiming a version it no
    longer has and a balance nobody is going to write."""
    wallet = a_wallet(executor)
    before = len(outbox())

    with pytest.raises(ValidationError, match="already staged"):
        executor.execute(
            StagesTwice(wallets),
            DepositParams(wallet_id=wallet.id, amount=Decimal("5.00"), recipient="ada@x"),
        )

    assert wallets.get(wallet.id).version == 1, "and nothing was written"
    assert len(outbox()) == before


def test_the_error_says_what_to_do_instead(executor, wallets):
    wallet = a_wallet(executor)
    with pytest.raises(ValidationError) as raised:
        executor.execute(
            StagesTwice(wallets),
            DepositParams(wallet_id=wallet.id, amount=Decimal("5.00"), recipient="ada@x"),
        )
    message = str(raised.value)
    assert "already staged" in message
    assert "composed" in message, "an error that does not say what to do instead is half an error"
    assert "Wallet" in message and str(wallet.id) in message


def test_composing_first_then_staging_once_works(executor, wallets, outbox):
    wallet = a_wallet(executor)
    before = {r["id"] for r in outbox()}

    result = executor.execute(
        ComposesThenStages(wallets),
        DepositParams(wallet_id=wallet.id, amount=Decimal("5.00"), recipient="ada@x"),
    )

    stored = wallets.get(wallet.id)
    assert stored.balance == Decimal("10.0000"), "both changes landed"
    assert stored.version == 2, "one write, one version"
    assert stored == result, "and what the action returned is what the database holds"

    # By id, not by slicing the tail: rows come back ordered by `event_date`, which the frozen
    # test clock makes identical across invocations.
    events = [r["event_type"] for r in outbox() if r["id"] not in before and r["event_type"]]
    assert events == ["WalletMoneyDeposited", "WalletMoneyDeposited"], (
        "both events ride along, because both changes happened"
    )


def test_inserting_and_then_changing_the_same_row_is_refused(executor, wallets):
    class CreateThenTopUp(Action[None, Wallet]):
        def perform(self, params: None) -> Wallet:
            fresh = self.plan.add(
                Wallet.open(
                    id=WalletId.random(),
                    owner_id=uuid4(),
                    currency="EUR",
                    balance=Decimal("10.00"),
                )
            )
            return self.plan.update(fresh.deposit(Decimal("5.00")))

    with pytest.raises(ValidationError, match="already staged"):
        executor.execute(CreateThenTopUp(), None)

    assert wallets.count() == 0


# -------------------------------------------------------------------------------- tracing


def test_the_framework_runs_without_a_tracing_stack(executor):
    """OpenTelemetry is not a dependency. A persistence framework that drags a tracing stack
    into every project installing it has made a decision it had no standing to make."""
    from ekbatan._tracing import span, tracing_enabled

    with span("probe", {"ekbatan.thing": "value"}) as s:
        s.set({"ekbatan.other": 1})
        s.event("something", {"count": 1})
        s.failed(ValueError("boom"), outcome="ekbatan.action.outcome")

    assert executor.execute(ReadsTheClock(), None) is not None
    assert isinstance(tracing_enabled(), bool)


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
                id=WalletId.random(), owner_id=uuid4(), currency="EUR", balance=Decimal("100.00")
            )
        )
        executor.execute(
            DepositMoney(wallets),
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
    assert emitted["ekbatan.action.execute"]["ekbatan.action.name"] == "DepositMoney"
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
