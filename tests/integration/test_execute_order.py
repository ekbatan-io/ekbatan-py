"""`execute(principal, action, params)`, in Java's order - and a call in any other order refused.

Java's is `execute(Principal principal, Class<A> actionClass, P params)`, so a call reads the same
in both ports. The old order put the action first: run as it was, it would hand the action class
in as the principal. So it is refused with a `TypeError` that names Java's order, before anything
runs and before anything is written.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from ekbatan.action.action import Action
from fixtures.actions import WalletCreateAction
from fixtures.executing import run
from modes import stager_twin

MODES = ("sync", "async")


class ReadsThePrincipalAction(Action[None, str | None]):
    def perform(self, principal: str | None, params: None) -> str | None:
        return principal


stager_twin(ReadsThePrincipalAction)


def opening() -> WalletCreateAction.Params:
    return WalletCreateAction.Params(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1"))


def test_the_principal_comes_first(executor, wallet_repository):
    wallet = executor.execute("ada", WalletCreateAction, opening())
    assert wallet_repository.get_by_id(wallet.id.value).balance == Decimal("1.0000")
    assert executor.execute(None, WalletCreateAction, opening()).balance == Decimal("1")


def test_perform_is_given_the_principal_execute_was_given(executor):
    assert run(executor, ReadsThePrincipalAction(executor.clock), None, principal="ada") == "ada"
    assert run(executor, ReadsThePrincipalAction(executor.clock), None) is None


def test_a_call_in_the_old_order_is_refused_naming_java_s_and_writes_nothing(executor, outbox):
    with pytest.raises(
        TypeError,
        match=r"execute takes the principal first, in Java's order - "
        r"execute\(principal, WalletDepositMoneyAction, params\), the principal a str or None and "
        r"the action its class - not the class WalletCreateAction and then a Params",
    ):
        executor.execute(WalletCreateAction, opening(), None)
    with pytest.raises(TypeError):
        executor.execute(WalletCreateAction, opening())  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        executor.execute(WalletCreateAction, opening(), principal="ada")  # type: ignore[call-arg]
    assert outbox() == []
