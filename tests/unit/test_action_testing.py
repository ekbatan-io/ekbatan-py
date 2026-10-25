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

from ekbatan import Action, Clock, VirtualClock, run_action
from ekbatan.errors import EkbatanError, ValidationError
from ekbatan.ids import Id, ShardedId
from fixtures.actions import (
    CreateNotificationAction,
    WalletCreateAction,
    WalletDepositMoneyAction,
)
from fixtures.domain import (
    NOW,
    Notification,
    Wallet,
    WalletCreatedEvent,
    WalletMoneyDepositedEvent,
    WalletMoneyWithdrawnEvent,
)


class DepositAction(Action[WalletDepositMoneyAction.Params, Wallet]):
    """Takes a repository, the way a real one does - stubbed here, since the point is that no
    database is involved."""

    def __init__(self, wallet: Wallet, clock: Clock) -> None:
        super().__init__(clock)
        self.wallet = wallet

    def perform(self, principal: str | None, params: WalletDepositMoneyAction.Params) -> Wallet:
        return self.plan.update(self.wallet.deposit(params.amount))


def a_wallet(balance: str = "100.00") -> Wallet:
    return Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal(balance),
        created_date=NOW,
    ).without_events()


def test_running_an_action_reports_what_it_staged(clock):
    wallet = a_wallet()

    staged = run_action(
        None,
        DepositAction(wallet, clock),
        WalletDepositMoneyAction.Params(
            wallet_id=wallet.id, amount=Decimal("50.00"), recipient="ada@x"
        ),
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


def test_it_reports_what_was_emitted(clock):
    wallet = a_wallet()

    staged = run_action(
        None,
        DepositAction(wallet, clock),
        WalletDepositMoneyAction.Params(
            wallet_id=wallet.id, amount=Decimal("50.00"), recipient="ada@x"
        ),
    )

    assert [type(e).__name__ for e in staged.events] == ["WalletMoneyDepositedEvent"]
    assert staged.emitted(WalletMoneyDepositedEvent)[0].amount == Decimal("50.00")
    assert staged.emitted(WalletMoneyWithdrawnEvent) == []


def test_an_insert_and_its_event_are_both_visible(clock):
    staged = run_action(
        None,
        WalletCreateAction(clock),
        WalletCreateAction.Params(
            owner_id=uuid4(), currency="EUR", initial_balance=Decimal("10.00")
        ),
    )

    assert len(staged.added(Wallet)) == 1
    assert staged.updated(Wallet) == []
    assert staged.emitted(WalletCreatedEvent)[0].currency == "EUR"


def test_an_entity_stages_without_emitting(clock):
    staged = run_action(
        None,
        CreateNotificationAction(clock),
        CreateNotificationAction.Params(
            wallet_id=ShardedId[Wallet].random(), kind="X", recipient="ada@x"
        ),
    )

    assert len(staged.added(Notification)) == 1
    assert staged.events == [], "an entity's changes are not events"


def test_the_action_sees_the_clock_it_was_given(clock):
    clock = VirtualClock(datetime(2026, 6, 1, 9, 0, tzinfo=UTC))

    class ReadsTheClockAction(Action[None, datetime]):
        def perform(self, principal: str | None, params: None) -> datetime:
            return self.clock.instant()

    assert run_action(None, ReadsTheClockAction(clock), None).result == clock.instant()

    clock.advance(timedelta(hours=2))
    assert run_action(None, ReadsTheClockAction(clock), None).result == clock.instant()


def test_the_action_sees_the_principal_it_was_given(clock):
    class ReadsThePrincipalAction(Action[None, str | None]):
        def perform(self, principal: str | None, params: None) -> str | None:
            return principal

    assert run_action("ada", ReadsThePrincipalAction(clock), None).result == "ada"
    assert run_action(None, ReadsThePrincipalAction(clock), None).result is None


def test_run_action_takes_the_principal_first_and_refuses_the_old_order(clock):
    """As `execute` does, in Java's order: run_action(principal, action, params)."""

    class ReadsThePrincipalAction(Action[None, str | None]):
        def perform(self, principal: str | None, params: None) -> str | None:
            return principal

    with pytest.raises(TypeError, match=r"run_action takes the principal first, as execute does"):
        run_action(ReadsThePrincipalAction(clock), None, None)  # type: ignore[arg-type]


def test_a_refusal_is_an_ordinary_exception(clock):
    """No assertThrows helper: pytest.raises already does this, and better."""
    wallet = a_wallet()

    with pytest.raises(ValidationError, match="amount"):
        run_action(
            None,
            DepositAction(wallet, clock),
            WalletDepositMoneyAction.Params(
                wallet_id=wallet.id, amount=Decimal("-1"), recipient="ada@x"
            ),
        )


def test_the_plan_behaves_as_it_does_in_production(clock):
    wallet = a_wallet()

    class StagesTwiceAction(Action[None, Wallet]):
        def perform(self, principal: str | None, params: None) -> Wallet:
            once = self.plan.update(wallet.deposit(Decimal("1.00")))
            return self.plan.update(once.deposit(Decimal("1.00")))

    with pytest.raises(ValidationError, match="already staged"):
        run_action(None, StagesTwiceAction(clock), None)


def test_the_binding_is_gone_afterwards(clock):
    from ekbatan import current_plan

    run_action(
        None,
        WalletCreateAction(clock),
        WalletCreateAction.Params(
            owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1.00")
        ),
    )
    with pytest.raises(EkbatanError, match="no unit of work here"):
        current_plan()


def test_the_plan_cannot_be_emptied_from_outside(clock):
    """A caller holding the real map could empty the plan, and the executor would then commit a
    unit of work that had quietly become a no-op."""
    staged = run_action(
        None,
        WalletCreateAction(clock),
        WalletCreateAction.Params(
            owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1.00")
        ),
    )

    with pytest.raises(AttributeError):
        staged.plan.changes().clear()  # type: ignore[attr-defined]
    assert staged.plan, "still holding what it was given"


def test_a_unit_of_work_touching_two_types_is_countable_across_both(clock):
    """Java's `assertTotalAdditions_counts_across_types` and `createBoth_full_verification`.

    A per-type count stops telling you whether anything *extra* was staged the moment a unit of
    work touches two types, which is when it matters most.
    """
    staged = run_action(None, CreatesBothAction(clock), None)

    assert len(staged.additions) == 2, "one of each, and nothing else"
    assert staged.updates == []
    assert len(staged.added(Wallet)) == 1
    assert len(staged.added(Notification)) == 1
    assert [type(e).__name__ for e in staged.events] == ["WalletCreatedEvent"], (
        "the entity stages without emitting; only the model has events"
    )


class CreatesBothAction(Action[None, Wallet]):
    """A model and an entity in one unit of work."""

    def perform(self, principal: str | None, params: None) -> Wallet:
        wallet = self.plan.add(
            Wallet.open(
                id=ShardedId[Wallet].random(),
                owner_id=uuid4(),
                currency="EUR",
                balance=Decimal("1.00"),
                created_date=self.clock.instant(),
            )
        )
        self.plan.add(
            Notification.create(
                id=Id[Notification].random(),
                wallet_id=wallet.id,
                kind="WALLET_CREATED",
                recipient="ada@example.com",
            )
        )
        return wallet
