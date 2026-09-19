"""What a unit of work costs, in the only unit that survives translation.

Wall-clock numbers measure the machine. Statement counts measure the framework - and they are
exactly what an implementation loses first.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

from ekbatan import Action, count_statements
from fixtures.actions import (
    CreateWalletAction,
    CreateWalletParams,
    DepositMoneyAction,
    DepositParams,
    TransferMoneyAction,
    TransferParams,
)
from fixtures.domain import Wallet, WalletId


def a_wallet(executor, balance="100.00"):
    return executor.execute(
        CreateWalletAction(executor.clock),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal(balance)),
    )


def test_one_unit_of_work_is_one_transaction(executor, wallets, registry):
    wallet = a_wallet(executor)

    with count_statements(registry) as cost:
        executor.execute(
            DepositMoneyAction(wallets, executor.clock),
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
            TransferMoneyAction(wallets, executor.clock),
            TransferParams(
                from_wallet_id=source.id, to_wallet_id=destination.id, amount=Decimal("5")
            ),
        )

    assert cost.transactions == 1
    assert cost.writes["wallets"] == 1, "two rows, one batched statement"
    assert cost.writes["events"] == 1, "two events, one batched statement"


def test_staging_many_rows_costs_one_statement(executor, wallets, registry):
    @dataclass(frozen=True, kw_only=True)
    class HowMany:
        count: int

    class CreateMany(Action[HowMany, list[Wallet]]):
        def perform(self, principal: str | None, params: HowMany) -> list[Wallet]:
            return self.plan.add_all(
                Wallet.open(
                    id=WalletId.random(),
                    owner_id=uuid4(),
                    currency="EUR",
                    balance=Decimal("1.00"),
                    created_date=self.clock.now(),
                )
                for _ in range(params.count)
            )

    with count_statements(registry) as cost:
        executor.execute(CreateMany(executor.clock), HowMany(count=25))

    assert cost.transactions == 1
    assert cost.writes["wallets"] == 1, "twenty-five rows is one statement, not twenty-five"
    assert cost.writes["events"] == 1
    assert wallets.count() == 25


def test_a_failed_unit_of_work_rolls_back_rather_than_committing(executor, wallets, registry):
    wallet = a_wallet(executor)

    with count_statements(registry) as cost, contextlib.suppress(Exception):
        executor.execute(
            DepositMoneyAction(wallets, executor.clock),
            DepositParams(wallet_id=wallet.id, amount=Decimal("-1"), recipient="ada@x"),
        )

    assert cost.transactions == 0
    assert cost.writes == {}
