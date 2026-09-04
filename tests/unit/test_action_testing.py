"""Testing a unit of work without a database.

The business logic of an action - what it stages, what it emits, what it refuses - needs no
database, and a test that starts one to find out whether a deposit emits one event is a slow
test of the wrong thing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from ekbatan import Action, FixedClock, run_action
from ekbatan.errors import EkbatanError, ValidationError
from fixtures.actions import (
    CreateWallet,
    CreateWalletParams,
    DepositParams,
    Notify,
    NotifyParams,
)
from fixtures.domain import (
    Notification,
    Wallet,
    WalletCreated,
    WalletId,
    WalletMoneyDeposited,
    WalletMoneyWithdrawn,
)


class Deposit(Action[DepositParams, Wallet]):
    """Takes a repository, the way a real one does - stubbed here, since the point is that no
    database is involved."""

    def __init__(self, wallet: Wallet) -> None:
        self.wallet = wallet

    def perform(self, params: DepositParams) -> Wallet:
        return self.plan.update(self.wallet.deposit(params.amount))


def a_wallet(balance: str = "100.00") -> Wallet:
    return Wallet.open(
        id=WalletId.random(), owner_id=uuid4(), currency="EUR", balance=Decimal(balance)
    ).without_events()


def test_running_an_action_reports_what_it_staged():
    wallet = a_wallet()

    staged = run_action(
        Deposit(wallet),
        DepositParams(wallet_id=wallet.id, amount=Decimal("50.00"), recipient="ada@x"),
    )

    assert staged.result.balance == Decimal("150.00")
    assert staged.added(Wallet) == []
    assert staged, "something was staged"

    [pending] = staged.updated(Wallet)
    assert pending.balance == Decimal("150.00")
    assert (pending.version, staged.result.version) == (1, 2), (
        "staged at the version it was read at, because that is what the UPDATE guards on; "
        "the action is handed the version the write will leave behind"
    )


def test_it_reports_what_was_emitted():
    wallet = a_wallet()

    staged = run_action(
        Deposit(wallet),
        DepositParams(wallet_id=wallet.id, amount=Decimal("50.00"), recipient="ada@x"),
    )

    assert [type(e).__name__ for e in staged.events] == ["WalletMoneyDeposited"]
    assert staged.emitted(WalletMoneyDeposited)[0].amount == Decimal("50.00")
    assert staged.emitted(WalletMoneyWithdrawn) == []


def test_an_insert_and_its_event_are_both_visible():
    staged = run_action(
        CreateWallet(),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("10.00")),
    )

    assert len(staged.added(Wallet)) == 1
    assert staged.updated(Wallet) == []
    assert staged.emitted(WalletCreated)[0].currency == "EUR"


def test_an_entity_stages_without_emitting():
    staged = run_action(
        Notify(),
        NotifyParams(wallet_id=WalletId.random(), kind="X", recipient="ada@x"),
    )

    assert len(staged.added(Notification)) == 1
    assert staged.events == [], "an entity's changes are not events"


def test_the_action_sees_the_clock_it_was_given():
    clock = FixedClock(datetime(2026, 6, 1, 9, 0, tzinfo=UTC))

    class ReadsTheClock(Action[None, datetime]):
        def perform(self, params: None) -> datetime:
            return self.now()

    assert run_action(ReadsTheClock(), None, clock=clock).result == clock.now()

    clock.advance(timedelta(hours=2))
    assert run_action(ReadsTheClock(), None, clock=clock).result == clock.now()


def test_the_action_sees_the_principal_it_was_given():
    class ReadsThePrincipal(Action[None, str | None]):
        def perform(self, params: None) -> str | None:
            return self.principal

    assert run_action(ReadsThePrincipal(), None, principal="ada").result == "ada"
    assert run_action(ReadsThePrincipal(), None).result is None


def test_a_refusal_is_an_ordinary_exception():
    """No assertThrows helper: pytest.raises already does this, and better."""
    wallet = a_wallet()

    with pytest.raises(ValidationError, match="amount"):
        run_action(
            Deposit(wallet),
            DepositParams(wallet_id=wallet.id, amount=Decimal("-1"), recipient="ada@x"),
        )


def test_the_plan_behaves_as_it_does_in_production():
    wallet = a_wallet()

    class StagesTwice(Action[None, Wallet]):
        def perform(self, params: None) -> Wallet:
            once = self.plan.update(wallet.deposit(Decimal("1.00")))
            return self.plan.update(once.deposit(Decimal("1.00")))

    with pytest.raises(ValidationError, match="already staged"):
        run_action(StagesTwice(), None)


def test_the_binding_is_gone_afterwards():
    from ekbatan import current_plan

    run_action(
        CreateWallet(),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1.00")),
    )
    with pytest.raises(EkbatanError, match="no unit of work here"):
        current_plan()


def test_the_plan_cannot_be_emptied_from_outside():
    """A caller holding the real map could empty the plan, and the executor would then commit a
    unit of work that had quietly become a no-op."""
    staged = run_action(
        CreateWallet(),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1.00")),
    )

    with pytest.raises(AttributeError):
        staged.plan.changes().clear()  # type: ignore[attr-defined]
    assert staged.plan, "still holding what it was given"
