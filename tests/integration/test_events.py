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
from fixtures.domain import WalletCreated, WalletId, WalletMoneyDeposited
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

    # As a set: the rows come back ordered by `event_date`, and the frozen test clock stamps
    # every invocation with the same one. What is being asserted is that all three ran and each
    # was its own unit of work, which the action ids below say.
    assert {r["action_name"] for r in outbox()} == {"CreateWallet", "DepositMoney", "Notify"}
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


# -- a batch is more than one row -------------------------------------------------------------
#
# Java devotes three cases to this. Everything above works on one notification at a time, and
# the interesting failures only appear when several share a batch.


def test_several_events_past_retention_expire_together(
    executor, wallets, pipeline, notifications_in, clock
):
    """Java's `preflight_expiration_handles_multiple_events_in_one_batch`.

    The pre-flight check runs per row, so a batch that is entirely too old must expire entirely
    - not the first row, and not one per poll.
    """
    email = succeeding("email")
    fanout, dispatch = pipeline(email, retention=timedelta(seconds=7))

    wallet = a_wallet(executor)
    for _ in range(4):
        deposit(executor, wallets, wallet)
    fanout.drain_once()
    assert notifications_in() == {"PENDING": 4}

    clock.advance(timedelta(seconds=60))
    dispatch.drain_once()

    assert notifications_in() == {"EXPIRED": 4}, "all four, in one round"
    assert email.calls == 0


def test_several_failing_handlers_expire_together_after_their_window(
    executor, wallets, pipeline, notifications_in, clock
):
    """Java's `post_failure_expiration_handles_multiple_events_in_one_batch`.

    The expire-or-retry decision is made per row against the clock *after* the invocation, so a
    batch whose window closed while its handlers ran expires as a batch.
    """
    never = failing("never")
    fanout, dispatch = pipeline(
        never, max_backoff=timedelta(milliseconds=1), retention=timedelta(seconds=10)
    )

    wallet = a_wallet(executor)
    for _ in range(3):
        deposit(executor, wallets, wallet)
    fanout.drain_once()

    dispatch.drain_once()
    assert notifications_in() == {"FAILED": 3}

    clock.advance(timedelta(seconds=60))
    dispatch.drain_once()

    assert notifications_in() == {"EXPIRED": 3}
    assert never.calls == 3, "the second round expired them without invoking anything again"


def test_a_handler_slower_than_the_window_expires_rather_than_retrying_into_the_past(
    executor, wallets, pipeline, notifications_in, clock
):
    """Java's `slow_failing_handler_expires_using_post_invocation_time`.

    The decision is taken against the clock *after* the handler returned, not before it ran. A
    handler that fails after outliving the retention window has no future to be retried in, and
    scheduling one against the pre-invocation clock would put `next_retry_at` in the past - so
    the row would be re-fetched immediately, every poll, until the window closed on its own.
    """
    slow = failing("slow")
    # Every call moves the clock past the retention window before returning.
    original = slow._on_call

    def burn_the_window() -> None:
        # The clock moves first: the original raises, so anything after it never runs.
        clock.advance(timedelta(seconds=30))
        if original is not None:
            original()

    slow._on_call = burn_the_window

    fanout, dispatch = pipeline(
        slow, max_backoff=timedelta(milliseconds=5), retention=timedelta(seconds=10)
    )

    wallet = a_wallet(executor)
    deposit(executor, wallets, wallet)
    fanout.drain_once()

    dispatch.drain_once()

    assert slow.calls == 1
    assert notifications_in() == {"EXPIRED": 1}, (
        "judged against the clock after the handler ran, so there was no future to retry in"
    )


def test_an_event_nobody_wanted_is_picked_up_once_someone_does(
    executor, wallets, pipeline, notifications_in, outbox
):
    """The second half of Java's `zero_subscribers...leaves_events_available_for_later_fanout`.

    An event with no subscriber is left undelivered rather than marked done, so registering a
    handler in a later deploy still drains what was already written.
    """
    nobody_fanout, _ = pipeline()
    wallet = a_wallet(executor)
    deposit(executor, wallets, wallet)

    nobody_fanout.drain_once()
    assert [r["delivered"] for r in outbox() if r["event_type"]] == [False, False], (
        "nothing subscribed, so nothing was consumed"
    )
    assert notifications_in() == {}

    email = succeeding("email")
    later_fanout, dispatch = pipeline(email)
    later_fanout.drain_once()
    dispatch.drain_once()

    assert email.calls == 1, "the event was still there when a handler turned up"
    assert notifications_in() == {"SUCCEEDED": 1}


def test_a_chained_action_s_own_event_is_fanned_out_and_dispatched_in_turn(
    executor, wallets, notifications, pipeline, notifications_in, outbox
):
    """Java's `widget_handler_chains_into_note_action_and_note_handler_writes_audit_entry`.

    The existing chaining test stops one level short: `Notify` writes an entity, and an entity
    emits nothing, so nothing downstream can subscribe. This goes the distance Java's does - a
    handler starts an action, that action's model emits its own event, and a *second* handler
    is dispatched from it, which writes a row of its own. Which is the shape every saga has,
    and the one where a pipeline that only half works still looks fine.

    Both hops do real work on purpose. A second handler that only counted its calls would prove
    the dispatch reached it and nothing about whether the unit of work it starts can commit -
    and it is the second hop, running inside the handling job rather than inside a request,
    where that is worth doubting.
    """

    class OpenASecondWalletOnDeposit:
        name = "second-wallet-opener"
        event_type = WalletMoneyDeposited

        def __init__(self) -> None:
            self.calls = 0

        def handle(self, envelope) -> None:
            self.calls += 1
            executor.execute(
                CreateWallet(),
                CreateWalletParams(
                    owner_id=uuid4(), currency="USD", initial_balance=Decimal("0.00")
                ),
            )

    class AuditTheOpening:
        """Java's `NoteCreatedAuditEntryHandler`: the second hop writes an entity."""

        name = "wallet-open-auditor"
        event_type = WalletCreated

        def __init__(self) -> None:
            self.received = []

        @property
        def calls(self) -> int:
            return len(self.received)

        def handle(self, envelope) -> None:
            self.received.append(envelope)
            executor.execute(
                Notify(),
                NotifyParams(
                    wallet_id=WalletId.of(envelope.model_id or ""),
                    kind="WALLET_OPENED",
                    recipient="auditor@example.com",
                    message=f"opened in {envelope.event.currency}",
                ),
            )

    opener = OpenASecondWalletOnDeposit()
    auditor = AuditTheOpening()
    fanout, dispatch = pipeline(opener, auditor)

    wallet = a_wallet(executor)
    deposit(executor, wallets, wallet, "12.50")

    # Round one: the deposit reaches the opener, which opens a second wallet.
    fanout.drain_once()
    dispatch.drain_once()

    assert opener.calls == 1
    assert wallets.count() == 2, "the chained action really wrote a row"
    first_pass = auditor.calls

    # Round two: the wallet the *handler* created emits its own event, which is fanned out and
    # dispatched like any other. Nothing about it is special to the pipeline.
    fanout.drain_once()
    dispatch.drain_once()

    assert auditor.calls > first_pass, "the second level was dispatched too"
    opened = [e for e in auditor.received if e.event.currency == "USD"]
    assert len(opened) == 1, "and it is the wallet the handler opened, not the original"
    assert opened[0].action_name == "CreateWallet"
    assert opened[0].model_type == "Wallet"

    audited = [n for n in notifications.all() if n.kind == "WALLET_OPENED"]
    assert len(audited) == auditor.calls, (
        "the second hop's own unit of work committed, one row per dispatch"
    )
    assert any(n.recipient == "auditor@example.com" for n in audited)

    assert len({r["action_id"] for r in outbox()}) == 3 + auditor.calls, (
        "the first wallet, the deposit, the handler's own - and one per audit"
    )
    assert notifications_in().get("FAILED", 0) == 0
