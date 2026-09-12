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
    Repository,
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

    other = Repository(Wallet, WALLETS, registry, clock=clock)

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
        .with_action(CreateWalletAction())
        .with_action(DepositMoneyAction(wallets))
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


def test_an_instance_still_works_with_no_registry(executor, wallets):
    """The registry is not compulsory - a test usually wants the instance it just built."""
    wallet = executor.execute(
        CreateWalletAction(),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1.00")),
    )
    assert wallets.get(wallet.id) is not None


def test_running_an_unregistered_type_names_it(executor):
    with pytest.raises(ValidationError, match="no instance of CreateWalletAction is registered"):
        executor.execute(
            CreateWalletAction,
            CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1.00")),
        )


def test_registering_the_class_rather_than_an_instance_is_refused():
    """The commonest way to get this wrong, and it would otherwise fail much later."""
    with pytest.raises(ValidationError, match="CreateWalletAction is a class, not an instance"):
        ActionRegistry([CreateWalletAction])  # type: ignore[list-item]


def test_two_instances_of_one_action_are_refused(wallets):
    with pytest.raises(ValidationError, match="two instances of DepositMoneyAction"):
        ActionRegistry([DepositMoneyAction(wallets), DepositMoneyAction(wallets)])


def test_the_same_instance_twice_is_not_a_conflict(wallets):
    once = DepositMoneyAction(wallets)
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

    with pytest.raises(ValidationError, match=r"either events=.* or outbox="):
        ActionExecutor(namespace="x.y", registry=registry, repositories=[wallets])


def test_running_nothing_says_so(executor):
    """Java's `execute_rejects_null_action_class`. Left alone this arrived as
    `'NoneType' object has no attribute 'name'`, from inside the executor."""
    from ekbatan.errors import ValidationError

    with pytest.raises(ValidationError, match="an action is required"):
        executor.execute(None, None)
