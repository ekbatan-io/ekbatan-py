"""The guarantee, and the ways an implementation loses it.

Named after the scenarios in ekbatan-spec so the correspondence is visible; the spec is where
the reasoning lives.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import event

from ekbatan.errors import ValidationError
from ekbatan.ids import ShardedId
from fixtures.actions import (
    CreateNotificationAction,
    WalletCloseAction,
    WalletCreateAction,
    WalletDepositMoneyAction,
    WalletDepositThenFailAction,
    WalletTransferMoneyAction,
)
from fixtures.domain import Wallet, WalletState
from fixtures.executing import run

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")


def a_wallet(executor, balance="100.00", currency="EUR"):
    return run(
        executor,
        WalletCreateAction(executor.clock),
        WalletCreateAction.Params(
            owner_id=uuid4(), currency=currency, initial_balance=Decimal(balance)
        ),
    )


def test_state_and_events_commit_together(executor, wallet_repository, outbox, registry):
    wallet = a_wallet(executor, "100.00")

    commits: list[str] = []
    event.listen(
        registry.default.primary.sqlalchemy_engine, "commit", lambda conn: commits.append("commit")
    )

    run(
        executor,
        WalletDepositMoneyAction(wallet_repository, executor.clock),
        WalletDepositMoneyAction.Params(
            wallet_id=wallet.id, amount=Decimal("50.00"), recipient="ada@example.com"
        ),
    )

    assert wallet_repository.get_by_id(wallet.id).balance == Decimal("150.0000")
    assert len(commits) == 1, "one unit of work is one transaction, whatever it touched"

    rows = outbox()
    assert len(rows) == 2
    (deposited,) = [r for r in rows if r["action_name"] == "WalletDepositMoneyAction"]
    assert deposited["namespace"] == "example.wallet"
    assert deposited["action_name"] == "WalletDepositMoneyAction"
    assert deposited["model_id"] == str(wallet.id)
    assert deposited["model_type"] == "Wallet"
    assert deposited["event_type"] == "WalletMoneyDepositedEvent"
    assert deposited["delivered"] is False
    # The scale is the column's, not the caller's: the wallet came back from a
    # DECIMAL(19,4), so its balance carries four places and the sum does too.
    #
    # `modelId` and `modelName` are in the payload as well as in their own columns, as Java
    # writes them: Java's local event handler deserialises this payload into the handler's event
    # class, and every event class there refuses one without them. The keys are Java's field
    # names and the decimals are JSON numbers, as Jackson writes them.
    assert deposited["payload"] == {
        "modelId": str(wallet.id),
        "modelName": "Wallet",
        "amount": Decimal("50.00"),
        "newBalance": Decimal("150.0000"),
    }


def test_a_failed_unit_of_work_writes_neither(executor, wallet_repository, outbox):
    wallet = a_wallet(executor, "100.00")
    before = len(outbox())

    with pytest.raises(ValidationError, match="amount"):
        run(
            executor,
            WalletDepositMoneyAction(wallet_repository, executor.clock),
            WalletDepositMoneyAction.Params(
                wallet_id=wallet.id, amount=Decimal("-50"), recipient="ada@example.com"
            ),
        )

    assert wallet_repository.get_by_id(wallet.id).balance == Decimal("100.0000")
    assert wallet_repository.get_by_id(wallet.id).version == 1
    assert len(outbox()) == before, "no event for a change that did not happen"


def test_a_failure_after_staging_discards_the_plan(executor, wallet_repository, outbox):
    wallet = a_wallet(executor, "100.00")
    before = len(outbox())

    with pytest.raises(ValidationError, match="changed my mind"):
        run(
            executor,
            WalletDepositThenFailAction(wallet_repository, executor.clock),
            WalletDepositMoneyAction.Params(
                wallet_id=wallet.id, amount=Decimal("25"), recipient="ada@example.com"
            ),
        )

    assert wallet_repository.get_by_id(wallet.id).balance == Decimal("100.0000"), (
        "staging is not writing"
    )
    assert len(outbox()) == before


def test_events_of_one_unit_of_work_share_an_action_id(executor, wallet_repository, outbox):
    source = a_wallet(executor, "100.00")
    destination = a_wallet(executor, "0.00")

    run(
        executor,
        WalletTransferMoneyAction(wallet_repository, executor.clock),
        WalletTransferMoneyAction.Params(
            from_wallet_id=source.id, to_wallet_id=destination.id, amount=Decimal("25.00")
        ),
    )

    transfer = [r for r in outbox() if r["action_name"] == "WalletTransferMoneyAction"]
    assert len(transfer) == 2
    assert len({r["action_id"] for r in transfer}) == 1, "one invocation, one action id"
    assert len({r["id"] for r in transfer}) == 2, "each event is its own row"

    assert wallet_repository.get_by_id(source.id).balance == Decimal("75.0000")
    assert wallet_repository.get_by_id(destination.id).balance == Decimal("25.0000")


def test_the_rows_of_one_invocation_share_its_context_and_do_not_promise_an_order(
    executor, wallet_repository, outbox
):
    """Both halves of what the outbox actually says about a multi-event invocation.

    The context is a guarantee: same action id, same names, same timestamps, one row each.
    The *order* is not, in either port. Every row carries the invocation's completion instant
    in `event_date`, `eventlog.events` has no ordinal column, and both `EventEntityRepository`
    and this port's reads order by that column alone - so within one invocation the tie falls
    to the storage engine. Postgres and SQLite happen to return insertion order; MySQL returns
    primary-key order, and the key is random in both ports.

    So this asserts set-wise, which is what Java's `BaseSingleTableJsonEventPersisterTest` does
    for the same reason - `allSatisfy` and `distinct()`, never `get(0)` against `get(1)`. A
    consumer that needs a withdrawal to precede the deposit it paid for needs an ordinal column
    added to the schema, in both ports at once.
    """
    source = a_wallet(executor, "100.00")
    destination = a_wallet(executor, "0.00")
    run(
        executor,
        WalletTransferMoneyAction(wallet_repository, executor.clock),
        WalletTransferMoneyAction.Params(
            from_wallet_id=source.id, to_wallet_id=destination.id, amount=Decimal("10.00")
        ),
    )

    transfer = [r for r in outbox() if r["action_name"] == "WalletTransferMoneyAction"]

    assert {r["event_type"] for r in transfer} == {
        "WalletMoneyWithdrawnEvent",
        "WalletMoneyDepositedEvent",
    }
    assert len({r["action_id"] for r in transfer}) == 1, "one invocation, one action id"
    assert len({r["id"] for r in transfer}) == 2, "one row per event"
    assert {r["event_date"] for r in transfer} == {transfer[0]["event_date"]}, (
        "and one instant between them, which is why the date cannot order them"
    )


def test_a_unit_of_work_that_emits_nothing_still_writes_a_sentinel(
    executor, outbox, notification_repository
):
    run(
        executor,
        CreateNotificationAction(executor.clock),
        CreateNotificationAction.Params(
            wallet_id=ShardedId[Wallet].random(),
            kind="MONEY_DEPOSITED",
            recipient="ada@example.com",
        ),
    )

    rows = outbox()
    assert len(rows) == 1
    sentinel = rows[0]
    assert sentinel["action_name"] == "CreateNotificationAction"
    assert sentinel["event_type"] is None
    assert sentinel["model_id"] is None
    assert sentinel["model_type"] is None
    assert sentinel["payload"] is None
    assert sentinel["event_date"] is not None
    assert len(notification_repository.find_all()) == 1, "the entity itself was written"


def test_the_sentinels_payload_is_sql_null_not_json_null(executor, registry):
    """Java leaves the column NULL. A JSON ``null`` reads back as ``None`` just the same, so only
    SQL can tell them apart - and a consumer's ``WHERE payload IS NULL`` is SQL."""
    from sqlalchemy import func, select, text

    from fixtures.schema import EVENTS, in_sql

    run(
        executor,
        CreateNotificationAction(executor.clock),
        CreateNotificationAction.Params(
            wallet_id=ShardedId[Wallet].random(), kind="MONEY_DEPOSITED", recipient="a@b.c"
        ),
    )
    with registry.default.connection(readonly=True) as conn:
        assert conn.execute(select(func.count()).select_from(EVENTS)).scalar() == 1
        table = in_sql(EVENTS, registry.default.dialect)
        assert (
            conn.execute(text(f"SELECT count(*) FROM {table} WHERE payload IS NULL")).scalar() == 1
        )


def test_a_no_op_close_changes_nothing_and_emits_nothing(executor, wallet_repository, outbox):
    wallet = a_wallet(executor, "0.00")
    closed = run(
        executor,
        WalletCloseAction(wallet_repository, executor.clock),
        WalletCloseAction.Params(wallet_id=wallet.id),
    )
    before = {r["id"] for r in outbox()}

    run(
        executor,
        WalletCloseAction(wallet_repository, executor.clock),
        WalletCloseAction.Params(wallet_id=wallet.id),
    )

    again = wallet_repository.get_by_id(wallet.id)
    assert again.state is WalletState.CLOSED
    assert again.version == closed.version, "closing a closed wallet moves nothing"

    # By id rather than by slicing the tail: the rows come back ordered by `event_date`, which
    # a frozen clock makes identical for every invocation in the test.
    rows = [r for r in outbox() if r["id"] not in before]
    assert [r["event_type"] for r in rows] == [None], "recorded, but nothing happened"


def test_every_row_carries_the_full_action_params(executor, wallet_repository, outbox):
    wallet = a_wallet(executor, "0.00")
    run(
        executor,
        WalletDepositMoneyAction(wallet_repository, executor.clock),
        WalletDepositMoneyAction.Params(
            wallet_id=wallet.id, amount=Decimal("1.00"), recipient="ada@example.com"
        ),
    )

    deposited = next(r for r in outbox() if r["event_type"] == "WalletMoneyDepositedEvent")
    # A typed id is written as Jackson writes Java's ShardedId: through its two getters.
    assert deposited["action_params"] == {
        "walletId": {"id": str(wallet.id), "value": str(wallet.id)},
        "amount": Decimal("1.00"),
        "recipient": "ada@example.com",
    }, "a consumer can read what the event deliberately does not carry"


def test_the_namespace_is_on_every_row_and_events_are_undelivered(
    executor, wallet_repository, outbox
):
    wallet = a_wallet(executor, "0.00")
    run(
        executor,
        WalletDepositMoneyAction(wallet_repository, executor.clock),
        WalletDepositMoneyAction.Params(
            wallet_id=wallet.id, amount=Decimal("1.00"), recipient="ada@example.com"
        ),
    )

    rows = outbox()
    assert {r["namespace"] for r in rows} == {"example.wallet"}
    assert all(r["delivered"] is False for r in rows)


def test_one_event_per_row_and_a_new_action_id_each_time(executor, wallet_repository, outbox):
    wallet = a_wallet(executor, "0.00")
    for amount in ("1.00", "2.00"):
        run(
            executor,
            WalletDepositMoneyAction(wallet_repository, executor.clock),
            WalletDepositMoneyAction.Params(
                wallet_id=wallet.id, amount=Decimal(amount), recipient="ada@example.com"
            ),
        )

    deposits = [r for r in outbox() if r["event_type"] == "WalletMoneyDepositedEvent"]
    assert len(deposits) == 2
    assert len({r["action_id"] for r in deposits}) == 2, "two invocations are two units of work"
    # By value, not by position: the clock is frozen for these tests, so both invocations stamp
    # the same event_date and even the across-invocation order is a tie.
    assert {r["payload"]["amount"] for r in deposits} == {Decimal("1.00"), Decimal("2.00")}


def test_the_timestamps_bracket_the_unit_of_work(executor, wallet_repository, outbox, clock):
    wallet = a_wallet(executor, "0.00")
    run(
        executor,
        WalletDepositMoneyAction(wallet_repository, executor.clock),
        WalletDepositMoneyAction.Params(
            wallet_id=wallet.id, amount=Decimal("1.00"), recipient="ada@example.com"
        ),
    )

    row = next(r for r in outbox() if r["event_type"] == "WalletMoneyDepositedEvent")
    assert row["started_date"] <= row["completion_date"]
    assert row["event_date"] == row["completion_date"], (
        "an event becomes true when its transaction commits, not when the object was built"
    )
    assert row["started_date"].tzinfo is None, "read back as the clock gave it: no zone"
    assert row["started_date"] <= clock.instant(), "and so comparable with the clock"


def test_decimals_are_json_numbers_and_keep_their_scale_except_on_mysql(
    executor, wallet_repository, outbox, registry
):
    """Java writes a BigDecimal as a JSON number, so this port does too, and reads every number
    back as a Decimal - nothing is lost on the way in.

    MySQL is the exception, and it is the database's, not the port's: its JSON type keeps a
    number with a fraction as a double, so ``50.00`` is stored as ``50.0`` (and digits past the
    17th would go). Java sends the same text, so a Java service sees the same thing.
    """
    wallet = a_wallet(executor, "100.00")
    run(
        executor,
        WalletDepositMoneyAction(wallet_repository, executor.clock),
        WalletDepositMoneyAction.Params(
            wallet_id=wallet.id, amount=Decimal("50.00"), recipient="ada@example.com"
        ),
    )
    row = next(r for r in outbox() if r["event_type"] == "WalletMoneyDepositedEvent")
    amount, param = row["payload"]["amount"], row["action_params"]["amount"]
    assert type(amount) is Decimal and type(param) is Decimal
    expected = "50.0" if registry.default.primary.dialect == "mysql" else "50.00"
    assert (str(amount), str(param)) == (expected, expected)
