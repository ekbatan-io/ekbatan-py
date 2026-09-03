"""The guarantee, and the ways an implementation loses it.

Named after the scenarios in ekbatan-spec so the correspondence is visible; the spec is where
the reasoning lives.
"""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import uuid4

import pytest
from examples.wallet.actions import (
    CloseWallet,
    CreateWallet,
    CreateWalletParams,
    DepositMoney,
    DepositParams,
    DepositThenFail,
    Notify,
    NotifyParams,
    TransferMoney,
    TransferParams,
    WalletIdParams,
)
from examples.wallet.model import WalletState
from sqlalchemy import event

from ekbatan.errors import ValidationError


def a_wallet(executor, balance="100.00", currency="EUR"):
    return executor.execute(
        CreateWallet(),
        CreateWalletParams(owner_id=uuid4(), currency=currency, initial_balance=Decimal(balance)),
    )


def test_state_and_events_commit_together(executor, wallets, outbox, registry):
    wallet = a_wallet(executor, "100.00")

    commits: list[str] = []
    event.listen(registry.default.primary, "commit", lambda conn: commits.append("commit"))

    executor.execute(
        DepositMoney(wallets),
        DepositParams(wallet_id=wallet.id, amount=Decimal("50.00"), recipient="ada@example.com"),
    )

    assert wallets.get(wallet.id).balance == Decimal("150.0000")
    assert len(commits) == 1, "one unit of work is one transaction, whatever it touched"

    rows = outbox()
    assert len(rows) == 2
    deposited = rows[-1]
    assert deposited["namespace"] == "example.wallet"
    assert deposited["action_name"] == "DepositMoney"
    assert deposited["model_id"] == str(wallet.id)
    assert deposited["model_type"] == "Wallet"
    assert deposited["event_type"] == "WalletMoneyDeposited"
    assert deposited["delivered"] is False
    # The scale is the column's, not the caller's: the wallet came back from a
    # DECIMAL(19,4), so its balance carries four places and the sum does too.
    assert json.loads(deposited["payload"]) == {"amount": "50.00", "new_balance": "150.0000"}


def test_a_failed_unit_of_work_writes_neither(executor, wallets, outbox):
    wallet = a_wallet(executor, "100.00")
    before = len(outbox())

    with pytest.raises(ValidationError, match="amount"):
        executor.execute(
            DepositMoney(wallets),
            DepositParams(wallet_id=wallet.id, amount=Decimal("-50"), recipient="ada@example.com"),
        )

    assert wallets.get(wallet.id).balance == Decimal("100.0000")
    assert wallets.get(wallet.id).version == 1
    assert len(outbox()) == before, "no event for a change that did not happen"


def test_a_failure_after_staging_discards_the_plan(executor, wallets, outbox):
    wallet = a_wallet(executor, "100.00")
    before = len(outbox())

    with pytest.raises(ValidationError, match="changed my mind"):
        executor.execute(
            DepositThenFail(wallets),
            DepositParams(wallet_id=wallet.id, amount=Decimal("25"), recipient="ada@example.com"),
        )

    assert wallets.get(wallet.id).balance == Decimal("100.0000"), "staging is not writing"
    assert len(outbox()) == before


def test_events_of_one_unit_of_work_share_an_action_id(executor, wallets, outbox):
    source = a_wallet(executor, "100.00")
    destination = a_wallet(executor, "0.00")

    executor.execute(
        TransferMoney(wallets),
        TransferParams(
            from_wallet_id=source.id, to_wallet_id=destination.id, amount=Decimal("25.00")
        ),
    )

    transfer = [r for r in outbox() if r["action_name"] == "TransferMoney"]
    assert len(transfer) == 2
    assert len({r["action_id"] for r in transfer}) == 1, "one invocation, one action id"
    assert len({r["id"] for r in transfer}) == 2, "each event is its own row"

    assert wallets.get(source.id).balance == Decimal("75.0000")
    assert wallets.get(destination.id).balance == Decimal("25.0000")


def test_outbox_rows_follow_the_order_events_were_attached(executor, wallets, outbox):
    source = a_wallet(executor, "100.00")
    destination = a_wallet(executor, "0.00")
    executor.execute(
        TransferMoney(wallets),
        TransferParams(
            from_wallet_id=source.id, to_wallet_id=destination.id, amount=Decimal("10.00")
        ),
    )

    transfer = [r for r in outbox() if r["action_name"] == "TransferMoney"]
    assert [r["event_type"] for r in transfer] == [
        "WalletMoneyWithdrawn",
        "WalletMoneyDeposited",
    ], "a balance rebuilt from the stream must never go negative in the middle"


def test_a_unit_of_work_that_emits_nothing_still_writes_a_sentinel(executor, outbox, notifications):
    executor.execute(
        Notify(),
        NotifyParams(wallet_id=uuid4(), kind="MONEY_DEPOSITED", recipient="ada@example.com"),
    )

    rows = outbox()
    assert len(rows) == 1
    sentinel = rows[0]
    assert sentinel["action_name"] == "Notify"
    assert sentinel["event_type"] is None
    assert sentinel["model_id"] is None
    assert sentinel["model_type"] is None
    assert sentinel["payload"] is None
    assert sentinel["event_date"] is not None
    assert len(notifications.all()) == 1, "the entity itself was written"


def test_a_no_op_close_changes_nothing_and_emits_nothing(executor, wallets, outbox):
    wallet = a_wallet(executor, "0.00")
    closed = executor.execute(CloseWallet(wallets), WalletIdParams(wallet_id=wallet.id))
    before = len(outbox())

    executor.execute(CloseWallet(wallets), WalletIdParams(wallet_id=wallet.id))

    again = wallets.get(wallet.id)
    assert again.state is WalletState.CLOSED
    assert again.version == closed.version, "closing a closed wallet moves nothing"

    rows = outbox()[before:]
    assert [r["event_type"] for r in rows] == [None], "recorded, but nothing happened"


def test_every_row_carries_the_full_action_params(executor, wallets, outbox):
    wallet = a_wallet(executor, "0.00")
    executor.execute(
        DepositMoney(wallets),
        DepositParams(wallet_id=wallet.id, amount=Decimal("1.00"), recipient="ada@example.com"),
    )

    deposited = next(r for r in outbox() if r["event_type"] == "WalletMoneyDeposited")
    assert json.loads(deposited["action_params"]) == {
        "wallet_id": str(wallet.id),
        "amount": "1.00",
        "recipient": "ada@example.com",
    }, "a consumer can read what the event deliberately does not carry"


def test_the_namespace_is_on_every_row_and_events_are_undelivered(executor, wallets, outbox):
    wallet = a_wallet(executor, "0.00")
    executor.execute(
        DepositMoney(wallets),
        DepositParams(wallet_id=wallet.id, amount=Decimal("1.00"), recipient="ada@example.com"),
    )

    rows = outbox()
    assert {r["namespace"] for r in rows} == {"example.wallet"}
    assert all(r["delivered"] is False for r in rows)


def test_one_event_per_row_and_a_new_action_id_each_time(executor, wallets, outbox):
    wallet = a_wallet(executor, "0.00")
    for amount in ("1.00", "2.00"):
        executor.execute(
            DepositMoney(wallets),
            DepositParams(wallet_id=wallet.id, amount=Decimal(amount), recipient="ada@example.com"),
        )

    deposits = [r for r in outbox() if r["event_type"] == "WalletMoneyDeposited"]
    assert len(deposits) == 2
    assert len({r["action_id"] for r in deposits}) == 2, "two invocations are two units of work"
    assert [json.loads(r["payload"])["amount"] for r in deposits] == ["1.00", "2.00"]


def test_the_timestamps_bracket_the_unit_of_work(executor, wallets, outbox, clock):
    wallet = a_wallet(executor, "0.00")
    executor.execute(
        DepositMoney(wallets),
        DepositParams(wallet_id=wallet.id, amount=Decimal("1.00"), recipient="ada@example.com"),
    )

    row = next(r for r in outbox() if r["event_type"] == "WalletMoneyDeposited")
    assert row["started_date"] <= row["completion_date"]
    assert row["event_date"] == row["completion_date"], (
        "an event becomes true when its transaction commits, not when the object was built"
    )
    assert row["started_date"].tzinfo is None
