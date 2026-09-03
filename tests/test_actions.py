"""The unit of work itself: what it can reach, and how failure is handled around it."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from examples.wallet.actions import CreateWallet, CreateWalletParams, DepositParams
from examples.wallet.model import Wallet, WalletId

from ekbatan import Action, ExecutionConfig, RetryPolicy, current_clock, current_plan
from ekbatan.errors import (
    ConnectivityError,
    EkbatanError,
    LockError,
    StaleRecordError,
    ValidationError,
)


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
    config = ExecutionConfig(
        retries=(
            RetryPolicy(on=StaleRecordError, attempts=1, delay=0),
            RetryPolicy(on=LockError, attempts=3, delay=0),
        )
    )

    conflicted = FailsWith(StaleRecordError("moved"))
    assert executor.execute(conflicted, None, config=config) == "done"
    assert conflicted.attempts == 2

    locked = FailsWith(*[LockError("k", "backend down") for _ in range(3)])
    assert executor.execute(locked, None, config=config) == "done"
    assert locked.attempts == 4, "three retries, as its own policy allows"

    stubborn = FailsWith(*[StaleRecordError("moved") for _ in range(2)])
    with pytest.raises(StaleRecordError):
        executor.execute(stubborn, None, config=config)
    assert stubborn.attempts == 2, "its policy allowed one retry, not three"


def test_the_first_matching_policy_wins(executor):
    config = ExecutionConfig(
        retries=(
            RetryPolicy(on=StaleRecordError, attempts=0, delay=0),
            RetryPolicy(on=EkbatanError, attempts=5, delay=0),
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
        executor.execute(action, None, config=ExecutionConfig.no_retry())
    assert action.attempts == 1


def test_a_policy_cannot_ask_for_negative_time():
    with pytest.raises(ValidationError, match="delay"):
        RetryPolicy(delay=-1.0)
    with pytest.raises(ValidationError, match="attempts"):
        RetryPolicy(attempts=-1)


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
            config=ExecutionConfig(retries=(RetryPolicy(on=BaseException, attempts=3, delay=0),)),
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
    before = len(outbox())

    result = executor.execute(
        ComposesThenStages(wallets),
        DepositParams(wallet_id=wallet.id, amount=Decimal("5.00"), recipient="ada@x"),
    )

    stored = wallets.get(wallet.id)
    assert stored.balance == Decimal("10.0000"), "both changes landed"
    assert stored.version == 2, "one write, one version"
    assert stored == result, "and what the action returned is what the database holds"

    events = [r["event_type"] for r in outbox()[before:] if r["event_type"]]
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


def test_staging_the_same_row_twice_in_one_batch_is_refused(clock):
    from ekbatan.action import ActionPlan

    plan = ActionPlan(clock)
    wallet = Wallet.open(
        id=WalletId.random(), owner_id=uuid4(), currency="EUR", balance=Decimal("1.00")
    )
    with pytest.raises(ValidationError, match="already staged"):
        plan.add_all([wallet, wallet])


def test_a_plan_reports_what_it_is_holding(clock):
    from ekbatan.action import ActionPlan

    plan = ActionPlan(clock)
    assert not plan

    wallet = Wallet.open(
        id=WalletId.random(), owner_id=uuid4(), currency="EUR", balance=Decimal("1.00")
    )
    plan.add(wallet)

    assert plan
    assert len(plan.additions(Wallet)) == 1
    assert plan.updates(Wallet) == []
    assert list(plan.changes()) == [Wallet]


# -------------------------------------------------------------------------------- tracing


def test_the_framework_runs_without_a_tracing_stack(executor):
    """OpenTelemetry is not a dependency. A persistence framework that drags a tracing stack
    into every project installing it has made a decision it had no standing to make."""
    from ekbatan._tracing import span, tracing_enabled

    with span("probe", thing="value") as s:
        s.set(other=1)
        s.event("something", count=1)
        s.failed(ValueError("boom"))

    assert executor.execute(ReadsTheClock(), None) is not None
    assert isinstance(tracing_enabled(), bool)
