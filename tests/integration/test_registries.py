"""The two registries: what maps to what, and what they refuse.

Java needs these because it dispatches on class tokens. They earn their place here for a
different reason: they are the only object that knows what an action was built with, so a
caller holding the executor needs nothing else to run one.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from ekbatan import (
    ActionExecutor,
    ActionRegistry,
    RepositoryRegistry,
)
from ekbatan.errors import ValidationError
from fixtures.actions import (
    CreateWalletAction,
    CreateWalletParams,
    DepositMoneyAction,
    DepositParams,
)
from fixtures.domain import Notification, Wallet
from fixtures.repositories import WalletRepository
from fixtures.schema import EVENTS

# ------------------------------------------------------------------ repositories


def test_the_builder_sorts_repositories_by_what_they_hold(wallets, notifications):
    built = (
        RepositoryRegistry.builder()
        .with_model_repository(wallets)
        .with_entity_repository(notifications)
        .build()
    )

    assert built.repository(Wallet) is wallets
    assert built.repository(Notification) is notifications
    assert set(built.models) == {Wallet, Notification}
    assert len(built) == 2


def test_declaring_the_wrong_kind_is_refused(wallets, notifications):
    """A Model registered as an Entity would write its rows and drop its events."""
    with pytest.raises(ValidationError, match="Wallet is not an Entity"):
        RepositoryRegistry.builder().with_entity_repository(wallets)

    with pytest.raises(ValidationError, match="Notification is not a Model"):
        RepositoryRegistry.builder().with_model_repository(notifications)


def test_a_collection_is_sorted_without_being_declared(wallets, notifications):
    built = RepositoryRegistry.builder().with_repositories([wallets, notifications]).build()

    assert set(built.models) == {Wallet, Notification}
    assert list(built) == [wallets, notifications]


def test_two_repositories_for_one_model_are_refused(registry, clock, wallets):
    """Keeping the last silently means half the application writes through a repository the
    other half has never heard of - different clock, different sharding, same table."""
    from fixtures.schema import WALLETS

    other = WalletRepository(Wallet, WALLETS, registry, clock=clock)

    with pytest.raises(ValidationError, match="two repositories were given for Wallet"):
        RepositoryRegistry([wallets, other])


def test_asking_for_an_unregistered_model_names_it(wallets):
    built = RepositoryRegistry([wallets])

    with pytest.raises(ValidationError, match="no repository is registered for Notification"):
        built.repository(Notification)

    assert Wallet in built
    assert Notification not in built


# ------------------------------------------------------------------ actions


def test_an_action_can_be_run_by_type(registry, clock, wallets, notifications):
    """The point of the registry: the caller names the action and knows nothing about what it
    was built with."""
    executor = ActionExecutor(
        namespace="example.wallet",
        registry=registry,
        repositories=RepositoryRegistry([wallets, notifications]),
        actions=ActionRegistry.builder()
        .with_action(CreateWalletAction(clock))
        .with_action(DepositMoneyAction(wallets, clock))
        .build(),
        events=EVENTS,
        clock=clock,
    )

    wallet = executor.execute(
        CreateWalletAction,
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("10.00")),
    )
    deposited = executor.execute(
        DepositMoneyAction,
        DepositParams(wallet_id=wallet.id, amount=Decimal("5.00"), recipient="ada@x"),
    )

    assert deposited.balance == Decimal("15.00")
    assert wallets.get(wallet.id).balance == Decimal("15.0000")


def test_an_instance_is_refused_and_the_message_says_what_to_do(executor):
    """Java's `execute` takes a class, so an unregistered action cannot run. Taking an instance
    here let one run anyway - registered or not."""
    with pytest.raises(ValidationError) as refused:
        executor.execute(CreateWalletAction(executor.clock), None)  # type: ignore[arg-type]

    message = str(refused.value)
    assert "pass CreateWalletAction, not an instance of it" in message
    assert "ActionSpec.of(action)" in message, "and where a test should go instead"


def test_an_action_with_another_clock_reaches_the_executor_as_in_java(
    registry, wallets, notifications
):
    """Java's executor builds with actions holding any clock; so does this one now."""
    from datetime import UTC, datetime

    from ekbatan import VirtualClock

    executor_clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
    elsewhere = VirtualClock(datetime(1999, 1, 1, tzinfo=UTC))

    built = ActionExecutor(
        namespace="example.wallet",
        registry=registry,
        repositories=[wallets, notifications],
        actions=[CreateWalletAction(elsewhere)],
        events=EVENTS,
        clock=executor_clock,
    )
    assert built.clock is executor_clock


def test_running_an_unregistered_type_names_it(executor):
    """`DepositThenFailAction` is the one fixture action the executor fixture leaves out."""
    from fixtures.actions import DepositThenFailAction

    with pytest.raises(ValidationError, match="no instance of DepositThenFailAction is registered"):
        executor.execute(DepositThenFailAction, None)  # type: ignore[arg-type]


def test_the_unregistered_message_gives_only_advice_that_works(executor):
    """It used to add "or pass the instance to execute()", which `execute` then refuses - a
    clause left over from before `EKB-0224` removed that form (Dracula bug 5)."""
    from fixtures.actions import DepositThenFailAction

    with pytest.raises(ValidationError) as raised:
        executor.execute(DepositThenFailAction, None)  # type: ignore[arg-type]
    message = str(raised.value)
    assert "Register it on the ActionRegistry" in message
    assert "pass the instance" not in message


def test_registering_the_class_rather_than_an_instance_is_refused():
    """The commonest way to get this wrong, and it would otherwise fail much later."""
    with pytest.raises(ValidationError, match="CreateWalletAction is a class, not an instance"):
        ActionRegistry([CreateWalletAction])  # type: ignore[list-item]


def test_two_instances_of_one_action_are_refused(wallets, clock):
    with pytest.raises(ValidationError, match="two instances of DepositMoneyAction"):
        ActionRegistry([DepositMoneyAction(wallets, clock), DepositMoneyAction(wallets, clock)])


def test_the_same_instance_twice_is_not_a_conflict(wallets, clock):
    once = DepositMoneyAction(wallets, clock)
    built = ActionRegistry([once, once])

    assert len(built) == 1
    assert built.get(DepositMoneyAction) is once


# -- what the executor refuses to be built or run without ---------------------------------------


def test_the_executor_names_the_argument_it_was_not_given(registry, wallets):
    """Java validates each of these in its builder, by name.

    `registry=None` was the one that mattered: it built without complaint and the first unit of
    work failed inside shard routing, naming a lookup rather than the argument nobody passed.
    """
    from ekbatan import ActionExecutor
    from ekbatan.errors import ValidationError
    from fixtures.schema import EVENTS

    with pytest.raises(ValidationError, match="registry is required"):
        ActionExecutor(namespace="x.y", registry=None, repositories=[wallets], events=EVENTS)

    with pytest.raises(ValidationError, match="repositories is required"):
        ActionExecutor(namespace="x.y", registry=registry, repositories=None, events=EVENTS)

    # Neither events= nor outbox=: the default persister, as Java's builder makes one.
    built = ActionExecutor(namespace="x.y", registry=registry, repositories=[wallets])
    assert built.outbox._event_repository._table.name == "events"  # type: ignore[attr-defined]


def test_running_nothing_says_so(executor):
    """Java's `execute_rejects_null_action_class`. Left alone this arrived as
    `'NoneType' object has no attribute 'name'`, from inside the executor."""
    from ekbatan.errors import ValidationError

    with pytest.raises(ValidationError, match="an action type is required"):
        executor.execute(None, None)
