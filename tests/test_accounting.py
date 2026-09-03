"""What a unit of work costs, in the only unit that survives translation.

Wall-clock numbers measure the machine. Statement counts measure the framework - and they are
exactly what an implementation loses first.
"""

from __future__ import annotations

import contextlib
from decimal import Decimal
from uuid import uuid4

from examples.wallet.actions import (
    CreateWallet,
    CreateWalletParams,
    DepositMoney,
    DepositParams,
    TransferMoney,
    TransferParams,
)
from examples.wallet.model import Wallet

from ekbatan import Action, count_statements
from ekbatan.ids import Id


def a_wallet(executor, balance="100.00"):
    return executor.execute(
        CreateWallet(),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal(balance)),
    )


def test_one_unit_of_work_is_one_transaction(executor, wallets, registry):
    wallet = a_wallet(executor)

    with count_statements(registry) as cost:
        executor.execute(
            DepositMoney(wallets),
            DepositParams(wallet_id=wallet.id, amount=Decimal("1"), recipient="ada@x"),
        )

    assert cost.transactions == 1
    assert cost.writes["wallets"] == 1
    assert cost.writes["events"] == 1, (
        "the cost of the outbox is one insert, not a second transaction"
    )


def test_two_models_still_cost_one_transaction(executor, wallets, registry):
    source, destination = a_wallet(executor), a_wallet(executor, "0.00")

    with count_statements(registry) as cost:
        executor.execute(
            TransferMoney(wallets),
            TransferParams(
                from_wallet_id=source.id, to_wallet_id=destination.id, amount=Decimal("5")
            ),
        )

    assert cost.transactions == 1
    assert cost.writes["wallets"] == 1, "two rows, one batched statement"
    assert cost.writes["events"] == 1, "two events, one batched statement"


def test_staging_many_rows_costs_one_statement(executor, wallets, registry):
    class CreateMany(Action[int, list[Wallet]]):
        def perform(self, params: int) -> list[Wallet]:
            return self.plan.add_all(
                Wallet.open(
                    id=Id[Wallet].random(),
                    owner_id=uuid4(),
                    currency="EUR",
                    balance=Decimal("1.00"),
                )
                for _ in range(params)
            )

    with count_statements(registry) as cost:
        executor.execute(CreateMany(), 25)

    assert cost.transactions == 1
    assert cost.writes["wallets"] == 1, "twenty-five rows is one statement, not twenty-five"
    assert cost.writes["events"] == 1
    assert wallets.count() == 25


def test_a_failed_unit_of_work_rolls_back_rather_than_committing(executor, wallets, registry):
    wallet = a_wallet(executor)

    with count_statements(registry) as cost, contextlib.suppress(Exception):
        executor.execute(
            DepositMoney(wallets),
            DepositParams(wallet_id=wallet.id, amount=Decimal("-1"), recipient="ada@x"),
        )

    assert cost.transactions == 0
    assert cost.writes == {}
