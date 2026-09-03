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


def test_staging_one_row_twice_leaves_one_row(executor, wallets, outbox):
    wallet = a_wallet(executor)
    before = len(outbox())

    result = executor.execute(
        StagesTwice(wallets),
        DepositParams(wallet_id=wallet.id, amount=Decimal("5.00"), recipient="ada@x"),
    )

    assert result.version == 2, (
        "a row moves forward one version per unit of work, not per call - it is written once, "
        "so it is one version later"
    )
    stored = wallets.get(wallet.id)
    assert stored.balance == Decimal("10.0000"), "both changes landed"
    assert stored.version == 2
    assert stored == result, "and what the action returned is what the database holds"

    events = [r for r in outbox()[before:] if r["event_type"]]
    assert len(events) == 2, "both events ride along, because both changes happened"


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


def test_a_row_changed_after_being_staged_for_insert_is_still_one_insert(executor, wallets, outbox):
    """Inserting then changing in the same unit of work is one row, with the later values -
    not an insert followed by an update against a version the database has never seen."""

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

    result = executor.execute(CreateThenTopUp(), None)

    stored = wallets.get(result.id)
    assert stored.balance == Decimal("15.0000")
    assert stored.version == 1, "it was inserted once; nothing has updated it yet"
    assert wallets.count() == 1
    assert [r["event_type"] for r in outbox() if r["event_type"]] == [
        "WalletCreated",
        "WalletMoneyDeposited",
    ]
