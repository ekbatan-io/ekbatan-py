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
from ekbatan.ids import ShardedId
from fixtures.actions import (
    WalletCreateAction,
    WalletDepositMoneyAction,
    WalletTransferMoneyAction,
)
from fixtures.domain import Wallet
from fixtures.executing import run

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")


def a_wallet(executor, balance="100.00"):
    return run(
        executor,
        WalletCreateAction(executor.clock),
        WalletCreateAction.Params(
            owner_id=uuid4(), currency="EUR", initial_balance=Decimal(balance)
        ),
    )


def test_one_unit_of_work_is_one_transaction(executor, wallet_repository, registry):
    wallet = a_wallet(executor)

    with count_statements(registry) as cost:
        run(
            executor,
            WalletDepositMoneyAction(wallet_repository, executor.clock),
            WalletDepositMoneyAction.Params(
                wallet_id=wallet.id, amount=Decimal("1"), recipient="ada@x"
            ),
        )

    assert cost.transactions == 1
    assert cost.writes["wallets"] == 1
    assert cost.writes["events"] == 1, (
        "the cost of the outbox is one insert, not a second transaction"
    )


def test_two_models_still_cost_one_transaction(executor, wallet_repository, registry):
    source, destination = a_wallet(executor), a_wallet(executor, "0.00")

    with count_statements(registry) as cost:
        run(
            executor,
            WalletTransferMoneyAction(wallet_repository, executor.clock),
            WalletTransferMoneyAction.Params(
                from_wallet_id=source.id, to_wallet_id=destination.id, amount=Decimal("5")
            ),
        )

    assert cost.transactions == 1
    assert cost.writes["wallets"] == 1, "two rows, one batched statement"
    assert cost.writes["events"] == 1, "two events, one batched statement"


def test_staging_many_rows_costs_one_statement(executor, wallet_repository, registry):
    @dataclass(frozen=True, kw_only=True)
    class HowMany:
        count: int

    class CreateMany(Action[HowMany, list[Wallet]]):
        def perform(self, principal: str | None, params: HowMany) -> list[Wallet]:
            return self.plan.add_all(
                Wallet.open(
                    id=ShardedId[Wallet].random(),
                    owner_id=uuid4(),
                    currency="EUR",
                    balance=Decimal("1.00"),
                    created_date=self.clock.instant(),
                )
                for _ in range(params.count)
            )

    with count_statements(registry) as cost:
        run(executor, CreateMany(executor.clock), HowMany(count=25))

    assert cost.transactions == 1
    assert cost.writes["wallets"] == 1, "twenty-five rows is one statement, not twenty-five"
    assert cost.writes["events"] == 1
    assert wallet_repository.count() == 25


def test_a_failed_unit_of_work_rolls_back_rather_than_committing(
    executor, wallet_repository, registry
):
    wallet = a_wallet(executor)

    with count_statements(registry) as cost, contextlib.suppress(Exception):
        run(
            executor,
            WalletDepositMoneyAction(wallet_repository, executor.clock),
            WalletDepositMoneyAction.Params(
                wallet_id=wallet.id, amount=Decimal("-1"), recipient="ada@x"
            ),
        )

    assert cost.transactions == 0
    assert cost.writes == {}
