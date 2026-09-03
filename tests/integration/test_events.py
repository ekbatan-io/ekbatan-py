"""The local event handler, against the scenarios in ekbatan-spec."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from fixtures.actions import (
    CloseWallet,
    CreateWallet,
    CreateWalletParams,
    DepositMoney,
    DepositParams,
    Notify,
    NotifyParams,
    WalletIdParams,
)
from fixtures.domain import WalletId
from fixtures.handlers import NotifyOnDeposit, failing, fatal, on_close, succeeding


def a_wallet(executor, balance="100.00"):
    return executor.execute(
        CreateWallet(),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal(balance)),
    )


def deposit(executor, wallets, wallet, amount="10.00"):
    return executor.execute(
        DepositMoney(wallets),
        DepositParams(wallet_id=wallet.id, amount=Decimal(amount), recipient="ada@example.com"),
    )


def test_an_event_reaches_every_subscribed_handler(executor, wallets, pipeline, notifications_in):
    email, indexer = succeeding("email"), succeeding("indexer")
    fanout, dispatch = pipeline(email, indexer)

    wallet = a_wallet(executor)
    deposit(executor, wallets, wallet)

    assert fanout.drain_once() == 1
    assert notifications_in() == {"PENDING": 2}, "one row per subscriber, not one shared"

    dispatch.drain_once()
    assert notifications_in() == {"SUCCEEDED": 2}
    assert email.calls == 1 and indexer.calls == 1


def test_fan_out_marks_delivered_and_dispatch_does_not(executor, wallets, pipeline, outbox):
    email = succeeding("email")
    fanout, _ = pipeline(email)

    wallet = a_wallet(executor)
    deposit(executor, wallets, wallet)
    fanout.drain_once()

    delivered = {r["event_type"]: r["delivered"] for r in outbox()}
    assert delivered["WalletMoneyDeposited"] is True
    assert email.calls == 0, "fan-out creates work; it does not do it"


def test_an_event_nobody_subscribes_to_stays_available(executor, wallets, pipeline, outbox):
    fanout, _ = pipeline(on_close("closer"))  # subscribed to something else entirely

    wallet = a_wallet(executor)
    deposit(executor, wallets, wallet)
    assert fanout.drain_once() == 0

    deposits = [r for r in outbox() if r["event_type"] == "WalletMoneyDeposited"]
    assert deposits[0]["delivered"] is False

    # ... and a handler registered later still picks it up.
    email = succeeding("email")
    later_fanout, later_dispatch = pipeline(email)
    assert later_fanout.drain_once() == 1
    later_dispatch.drain_once()
    assert email.calls == 1


def test_a_sentinel_row_is_never_fanned_out(executor, pipeline, notifications_in):
    fanout, _ = pipeline(succeeding("email"))
    executor.execute(
        Notify(), NotifyParams(wallet_id=WalletId.random(), kind="X", recipient="ada@example.com")
    )
    assert fanout.drain_once() == 0, "a row with no event type belongs on no topic"
    assert notifications_in() == {}


def test_a_handler_that_fails_a_few_times_is_retried(
    executor, wallets, pipeline, notifications_in, clock
):
    flaky = failing("flaky", times=2)
    fanout, dispatch = pipeline(flaky, max_backoff=timedelta(seconds=60))

    wallet = a_wallet(executor)
    deposit(executor, wallets, wallet)
    fanout.drain_once()

    dispatch.drain_once()
    assert flaky.calls == 1
    assert notifications_in() == {"FAILED": 1}

    # Nothing is due yet. A dispatcher that retried anyway would turn a capped backoff into a
    # hot loop against whatever the handler was failing to reach.
    dispatch.drain_once()
    assert flaky.calls == 1, "the backoff is honoured, not merely recorded"

    clock.advance(timedelta(seconds=31))
    dispatch.drain_once()
    assert flaky.calls == 2

    clock.advance(timedelta(seconds=61))
    dispatch.drain_once()
    assert flaky.calls == 3
    assert notifications_in() == {"SUCCEEDED": 1}


def test_a_handler_that_never_succeeds_eventually_stops(
    executor, wallets, pipeline, notifications_in, clock
):
    never = failing("never")
    fanout, dispatch = pipeline(
        never, max_backoff=timedelta(milliseconds=1), retention=timedelta(seconds=10)
    )

    wallet = a_wallet(executor)
    deposit(executor, wallets, wallet)
    fanout.drain_once()

    dispatch.drain_once()
    assert notifications_in() == {"FAILED": 1}

    clock.advance(timedelta(seconds=60))
    dispatch.drain_once()
    assert notifications_in() == {"EXPIRED": 1}

    calls_before = never.calls
    dispatch.drain_once()
    assert never.calls == calls_before, "an expired row is terminal"


def test_an_event_already_past_retention_is_never_handed_over(
    executor, wallets, pipeline, notifications_in, clock
):
    email = succeeding("email")
    fanout, dispatch = pipeline(email, retention=timedelta(seconds=7))

    wallet = a_wallet(executor)
    deposit(executor, wallets, wallet)
    fanout.drain_once()

    clock.advance(timedelta(seconds=60))
    dispatch.drain_once()

    assert notifications_in() == {"EXPIRED": 1}
    assert email.calls == 0, "the check is before invocation, not after"


def test_a_fatal_handler_does_not_discard_its_batch(executor, wallets, pipeline, notifications_in):
    exploder, email = fatal("exploder"), succeeding("email")
    fanout, dispatch = pipeline(exploder, email, max_backoff=timedelta(milliseconds=1))

    wallet = a_wallet(executor)
    deposit(executor, wallets, wallet)
    fanout.drain_once()
    dispatch.drain_once()

    assert email.calls == 1
    assert notifications_in() == {"SUCCEEDED": 1, "FAILED": 1}

    # And the next poll makes progress rather than re-running what already worked.
    dispatch.drain_once()
    assert email.calls == 1, (
        "a success that is never recorded is re-delivered on every poll, forever"
    )


def test_a_succeeded_notification_is_not_delivered_again(
    executor, wallets, pipeline, notifications_in
):
    email = succeeding("email")
    fanout, dispatch = pipeline(email)

    wallet = a_wallet(executor)
    deposit(executor, wallets, wallet)
    fanout.drain_once()
    for _ in range(3):
        dispatch.drain_once()

    assert email.calls == 1
    assert notifications_in() == {"SUCCEEDED": 1}


def test_each_event_and_handler_pair_produces_one_notification(
    executor, wallets, pipeline, notifications_in
):
    email = succeeding("email")
    fanout, _ = pipeline(email)

    wallet = a_wallet(executor)
    deposit(executor, wallets, wallet)
    for _ in range(3):
        fanout.drain_once()

    assert notifications_in() == {"PENDING": 1}, (
        "fan-out will pass over rows it has already seen; the constraint decides, not the reader"
    )


def test_handlers_only_see_the_event_type_they_subscribed_to(
    executor, wallets, pipeline, notifications_in
):
    deposits, closes = succeeding("on_deposit"), on_close("on_close")
    fanout, dispatch = pipeline(deposits, closes)

    wallet = a_wallet(executor)
    deposit(executor, wallets, wallet)
    executor.execute(CloseWallet(wallets), WalletIdParams(wallet_id=wallet.id))

    fanout.drain_until_idle()
    dispatch.drain_until_idle()

    assert deposits.calls == 1 and closes.calls == 1
    assert [e.event.__class__.__name__ for e in deposits.received] == ["WalletMoneyDeposited"]
    assert [e.event.__class__.__name__ for e in closes.received] == ["WalletClosed"]


def test_a_handler_receives_a_typed_event_and_its_producing_context(executor, wallets, pipeline):
    email = succeeding("email")
    fanout, dispatch = pipeline(email)

    wallet = a_wallet(executor)
    deposit(executor, wallets, wallet, "12.50")
    fanout.drain_once()
    dispatch.drain_once()

    envelope = email.received[0]
    assert envelope.event.amount == Decimal("12.50"), "rebuilt as a Decimal, not left as a string"
    assert envelope.event.new_balance == Decimal("112.5000")
    assert envelope.model_id == str(wallet.id)
    assert envelope.model_type == "Wallet"
    assert envelope.namespace == "example.wallet"
    assert envelope.action_name == "DepositMoney"
    assert envelope.action_params["recipient"] == "ada@example.com", (
        "readable off the envelope, so the event need not carry a routing concern"
    )
    assert envelope.attempt == 1


def test_a_handler_may_start_another_unit_of_work(
    executor, wallets, pipeline, notifications, outbox
):
    notifier = NotifyOnDeposit(executor)
    fanout, dispatch = pipeline(notifier)

    wallet = a_wallet(executor)
    deposit(executor, wallets, wallet, "12.50")
    fanout.drain_once()
    dispatch.drain_once()

    assert notifier.calls == 1
    written = notifications.all()
    assert len(written) == 1
    assert written[0].recipient == "ada@example.com"
    assert written[0].message == "deposited 12.50, balance 112.5000"

    names = [r["action_name"] for r in outbox()]
    assert names == ["CreateWallet", "DepositMoney", "Notify"]
    assert len({r["action_id"] for r in outbox()}) == 3, "the chained work is its own unit"


def test_a_renamed_handler_still_drains_its_old_queue(
    executor, wallets, pipeline, notifications_in
):
    old = succeeding("wallet-emailer")
    fanout, _ = pipeline(old)
    wallet = a_wallet(executor)
    deposit(executor, wallets, wallet)
    fanout.drain_once()

    renamed = succeeding("wallet-notifier")
    renamed.aliases = ("wallet-emailer",)
    _, dispatch = pipeline(renamed)
    dispatch.drain_once()

    assert renamed.calls == 1, "rows queued under the old name still route home"
    assert notifications_in() == {"SUCCEEDED": 1}
