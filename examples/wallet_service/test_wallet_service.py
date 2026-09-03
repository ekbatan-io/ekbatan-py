"""The example's own test.

Lives with the example, runs separately from the framework's suite, and exists for one reason:
an example nobody runs is an example that rots. It asserts what the README claims, so a change
that makes the prose false makes this red.

    uv run pytest examples
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from ekbatan.clock import FixedClock, SystemClock

from .actions import (
    CloseWallet,
    DepositMoney,
    DepositParams,
    OpenWallet,
    OpenWalletParams,
    WalletIdParams,
)
from .app import WalletService, build
from .schema import EVENTS


@pytest.fixture
def service(tmp_path: Path) -> Iterator[WalletService]:
    built = build(f"sqlite:///{tmp_path / 'wallet.db'}", clock=FixedClock(SystemClock().now()))
    try:
        yield built
    finally:
        built.close()


def a_wallet(service: WalletService, balance: str = "100.00"):
    return service.executor.execute(
        OpenWallet(),
        OpenWalletParams(owner_id=uuid4(), currency="EUR", opening_balance=Decimal(balance)),
    )


def outbox(service: WalletService) -> list:
    with service.registry.default.connection(readonly=True) as conn:
        return list(conn.execute(select(EVENTS).order_by(EVENTS.c.event_date, EVENTS.c.id)))


def test_a_deposit_writes_the_balance_and_its_event_together(service):
    wallet = a_wallet(service)

    service.executor.execute(
        DepositMoney(service.wallets),
        DepositParams(wallet_id=wallet.id, amount=Decimal("25.50"), recipient="ada@example.com"),
    )

    assert service.wallets.get(wallet.id).balance == Decimal("125.5000")
    deposited = [r for r in outbox(service) if r.event_type == "MoneyDeposited"]
    assert len(deposited) == 1
    assert deposited[0].payload == {"amount": "25.50", "new_balance": "125.5000"}


def test_the_handler_reacts_and_reads_what_the_event_does_not_carry(service):
    """The README's claim: the recipient travels as an action parameter, not in the event."""
    wallet = a_wallet(service)
    service.executor.execute(
        DepositMoney(service.wallets),
        DepositParams(wallet_id=wallet.id, amount=Decimal("10.00"), recipient="ada@example.com"),
    )

    service.deliver_pending_events()

    alerts = service.alerts.all()
    assert len(alerts) == 1
    assert alerts[0].recipient == "ada@example.com"
    assert "10.00" in alerts[0].message

    deposited = next(r for r in outbox(service) if r.event_type == "MoneyDeposited")
    assert "recipient" not in deposited.payload, "the event stays clean"
    assert deposited.action_params["recipient"] == "ada@example.com"


def test_writing_an_entity_emits_nothing_and_is_still_recorded(service):
    wallet = a_wallet(service)
    service.executor.execute(
        DepositMoney(service.wallets),
        DepositParams(wallet_id=wallet.id, amount=Decimal("1.00"), recipient="ada@example.com"),
    )
    service.deliver_pending_events()

    alert_rows = [r for r in outbox(service) if r.action_name == "RaiseAlert"]
    assert len(alert_rows) == 1
    assert alert_rows[0].event_type is None, "an entity's changes are not events"
    assert len(service.alerts.all()) == 1, "and the row was still written"


def test_closing_twice_changes_nothing_the_second_time(service):
    wallet = a_wallet(service, "0.00")
    service.executor.execute(CloseWallet(service.wallets), WalletIdParams(wallet_id=wallet.id))
    before = service.wallets.get(wallet.id)

    service.executor.execute(CloseWallet(service.wallets), WalletIdParams(wallet_id=wallet.id))

    assert service.wallets.get(wallet.id).version == before.version
    closed = [r for r in outbox(service) if r.event_type == "WalletClosed"]
    assert len(closed) == 1, "the second close emitted nothing"


def test_the_stipend_job_pays_every_open_wallet(service):
    wallet = a_wallet(service, "0.00")
    service.jobs.start()

    assert service.jobs.run_due_once() == 1
    assert service.wallets.get(wallet.id).balance == Decimal("1.0000")
    assert service.jobs.run_due_once() == 0, "the next slot is a day away"


def test_a_rejected_deposit_leaves_nothing_behind(service):
    from ekbatan.errors import ValidationError

    wallet = a_wallet(service)
    before = len(outbox(service))

    with pytest.raises(ValidationError, match="amount"):
        service.executor.execute(
            DepositMoney(service.wallets),
            DepositParams(wallet_id=wallet.id, amount=Decimal("-1"), recipient="ada@example.com"),
        )

    assert service.wallets.get(wallet.id).balance == Decimal("100.0000")
    assert len(outbox(service)) == before


def test_the_script_runs(tmp_path, capsys):
    """The README says `python -m examples.wallet_service` works. This is that promise."""
    from .__main__ import run

    built = build(f"sqlite:///{tmp_path / 'script.db'}")
    try:
        run(built)
    finally:
        built.close()

    printed = capsys.readouterr().out
    assert "open a wallet" in printed
    assert "what a consumer reads from the outbox" in printed
    assert "sentinel" in printed
