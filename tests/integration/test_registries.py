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
    WalletCreateAction,
    WalletDepositMoneyAction,
)
from fixtures.domain import Notification, Wallet
from fixtures.repositories import WalletRepository

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")

# ------------------------------------------------------------------ repositories


def test_the_builder_sorts_repositories_by_what_they_hold(
    wallet_repository, notification_repository
):
    built = (
        RepositoryRegistry.builder()
        .with_model_repository(wallet_repository)
        .with_entity_repository(notification_repository)
        .build()
    )

    assert built.repository(Wallet) is wallet_repository
    assert built.repository(Notification) is notification_repository
    assert set(built.models) == {Wallet, Notification}
    assert len(built) == 2


def test_declaring_the_wrong_kind_is_refused(wallet_repository, notification_repository):
    """A Model registered as an Entity would write its rows and drop its events."""
    with pytest.raises(ValidationError, match="Wallet is not an Entity"):
        RepositoryRegistry.builder().with_entity_repository(wallet_repository)

    with pytest.raises(ValidationError, match="Notification is not a Model"):
        RepositoryRegistry.builder().with_model_repository(notification_repository)


def test_a_collection_is_sorted_without_being_declared(wallet_repository, notification_repository):
    built = (
        RepositoryRegistry.builder()
        .with_repositories([wallet_repository, notification_repository])
        .build()
    )

    assert set(built.models) == {Wallet, Notification}
    assert list(built) == [wallet_repository, notification_repository]


def test_two_repositories_for_one_model_are_refused(registry, clock, wallet_repository):
    """Keeping the last silently means half the application writes through a repository the
    other half has never heard of - different clock, different sharding, same table."""
    from fixtures.schema import WALLETS

    other = WalletRepository(Wallet, WALLETS, registry, clock=clock)

    with pytest.raises(ValidationError, match="two repositories were given for Wallet"):
        RepositoryRegistry([wallet_repository, other])


def test_asking_for_an_unregistered_model_names_it(wallet_repository):
    built = RepositoryRegistry([wallet_repository])

    with pytest.raises(ValidationError, match="no repository is registered for Notification"):
        built.repository(Notification)

    assert Wallet in built
    assert Notification not in built


# ------------------------------------------------------------------ actions


def test_an_action_can_be_run_by_type(registry, clock, wallet_repository, notification_repository):
    """The point of the registry: the caller names the action and knows nothing about what it
    was built with."""
    executor = ActionExecutor(
        namespace="example.wallet",
        registry=registry,
        repositories=RepositoryRegistry([wallet_repository, notification_repository]),
        actions=ActionRegistry.builder()
        .with_action(WalletCreateAction(clock))
        .with_action(WalletDepositMoneyAction(wallet_repository, clock))
        .build(),
        clock=clock,
    )

    wallet = executor.execute(
        None,
        WalletCreateAction,
        WalletCreateAction.Params(
            owner_id=uuid4(), currency="EUR", initial_balance=Decimal("10.00")
        ),
    )
    deposited = executor.execute(
        None,
        WalletDepositMoneyAction,
        WalletDepositMoneyAction.Params(
            wallet_id=wallet.id, amount=Decimal("5.00"), recipient="ada@x"
        ),
    )

    assert deposited.balance == Decimal("15.00")
    assert wallet_repository.get_by_id(wallet.id.value).balance == Decimal("15.0000")


def test_an_instance_is_refused_and_the_message_says_what_to_do(executor):
    """Java's `execute` takes a class, so an unregistered action cannot run. Taking an instance
    here let one run anyway - registered or not."""
    instance = WalletCreateAction(executor.clock)
    with pytest.raises(ValidationError) as refused:
        executor.execute(None, instance, None)  # type: ignore[arg-type]

    message = str(refused.value)
    assert f"pass {type(instance).__name__}, not an instance of it" in message
    assert "ActionSpec.of(action)" in message, "and where a test should go instead"


def test_an_action_with_another_clock_reaches_the_executor_as_in_java(
    registry, wallet_repository, notification_repository
):
    """Java's executor builds with actions holding any clock; so does this one now."""
    from datetime import datetime

    from ekbatan import VirtualClock

    executor_clock = VirtualClock(datetime(2026, 1, 1))
    elsewhere = VirtualClock(datetime(1999, 1, 1))

    built = ActionExecutor(
        namespace="example.wallet",
        registry=registry,
        repositories=[wallet_repository, notification_repository],
        actions=[WalletCreateAction(elsewhere)],
        clock=executor_clock,
    )
    assert built.clock is executor_clock


def test_running_an_unregistered_type_names_it(executor):
    """`WalletDepositThenFailAction` is the one fixture action the executor fixture leaves out."""
    from fixtures.actions import WalletDepositThenFailAction

    with pytest.raises(
        ValidationError, match="no instance of WalletDepositThenFailAction is registered"
    ):
        executor.execute(None, WalletDepositThenFailAction, None)  # type: ignore[arg-type]


def test_the_unregistered_message_gives_only_advice_that_works(executor):
    """It used to add "or pass the instance to execute()", which `execute` then refuses - a
    clause left over from before `EKB-0224` removed that form (Dracula bug 5)."""
    from fixtures.actions import WalletDepositThenFailAction

    with pytest.raises(ValidationError) as raised:
        executor.execute(None, WalletDepositThenFailAction, None)  # type: ignore[arg-type]
    message = str(raised.value)
    assert "Register it on the ActionRegistry" in message
    assert "pass the instance" not in message


def test_registering_the_class_rather_than_an_instance_is_refused():
    """The commonest way to get this wrong, and it would otherwise fail much later."""
    with pytest.raises(ValidationError, match="WalletCreateAction is a class, not an instance"):
        ActionRegistry([WalletCreateAction])  # type: ignore[list-item]


def test_two_instances_of_one_action_are_refused(wallet_repository, clock):
    first, second = (
        WalletDepositMoneyAction(wallet_repository, clock),
        WalletDepositMoneyAction(wallet_repository, clock),
    )
    with pytest.raises(ValidationError, match=f"two instances of {type(first).__name__}"):
        ActionRegistry([first, second])


def test_the_same_instance_twice_is_not_a_conflict(wallet_repository, clock):
    once = WalletDepositMoneyAction(wallet_repository, clock)
    built = ActionRegistry([once, once])

    assert len(built) == 1
    assert built.get(WalletDepositMoneyAction) is once


# -- what the executor refuses to be built or run without ---------------------------------------


def test_the_executor_names_the_argument_it_was_not_given(registry, wallet_repository):
    """Java validates each of these in its builder, by name.

    `registry=None` was the one that mattered: it built without complaint and the first unit of
    work failed inside shard routing, naming a lookup rather than the argument nobody passed.
    """
    from ekbatan import ActionExecutor
    from ekbatan.errors import ValidationError

    with pytest.raises(ValidationError, match="registry is required"):
        ActionExecutor(namespace="x.y", registry=None, repositories=[wallet_repository])

    with pytest.raises(ValidationError, match="repositories is required"):
        ActionExecutor(namespace="x.y", registry=registry, repositories=None)

    # No outbox=: the default persister, over `eventlog.events`, as Java's builder makes one.
    built = ActionExecutor(namespace="x.y", registry=registry, repositories=[wallet_repository])
    table = built.outbox._event_repository._table  # type: ignore[attr-defined]
    assert table.fullname == "eventlog.events"


def test_running_nothing_says_so(executor):
    """Java's `execute_rejects_null_action_class`. Left alone this arrived as
    `'NoneType' object has no attribute 'name'`, from inside the executor."""
    from ekbatan.errors import ValidationError

    with pytest.raises(ValidationError, match="an action type is required"):
        executor.execute(None, None, None)
