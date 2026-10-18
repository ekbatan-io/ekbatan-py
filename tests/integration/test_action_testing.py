"""`run_action` against a real database - to show it does not touch it.

The rest of the module's behaviour is settled in the unit suite, which is the point of it. This
one needs a database precisely so it can prove nothing was written to one.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from ekbatan import Action, Clock, run_action
from ekbatan.ids import ShardedId
from fixtures.actions import DepositParams
from fixtures.domain import NOW, Wallet
from fixtures.repositories import WalletRepository
from fixtures.schema import WALLETS
from modes import stager_twin

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")


def a_wallet(balance: str = "100.00") -> Wallet:
    return Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal(balance),
        created_date=NOW,
    ).without_events()


class DepositAction(Action[DepositParams, Wallet]):
    def __init__(self, wallet: Wallet, clock: Clock) -> None:
        super().__init__(clock)
        self.wallet = wallet

    def perform(self, principal: str | None, params: DepositParams) -> Wallet:
        return self.plan.update(self.wallet.deposit(params.amount))


stager_twin(DepositAction)


def test_nothing_reaches_a_database(registry, clock):
    """The point of the whole module: the action runs, the plan fills, and the row is not
    written anywhere."""

    wallets = WalletRepository(Wallet, WALLETS, registry, clock=clock)
    stored = wallets.add(a_wallet("100.00"))

    staged = run_action(
        DepositAction(stored, clock),
        DepositParams(wallet_id=stored.id, amount=Decimal("50.00"), recipient="ada@x"),
    )

    assert staged.result.balance == Decimal("150.0000")
    assert wallets.get(stored.id).balance == Decimal("100.0000"), "the database is untouched"
    assert wallets.get(stored.id).version == 1
