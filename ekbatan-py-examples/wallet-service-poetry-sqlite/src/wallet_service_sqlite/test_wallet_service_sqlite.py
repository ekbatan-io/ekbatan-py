"""The example's own test, on two SQLite files - the global shard and the Mexico shard.

    make

It asks what the HTTP examples' tests ask, without HTTP: each wallet lands on the shard its
country names, a deposit's event reaches the handler and leaves a notification, and closing a
wallet closes it. And the script runs.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from .actions import WalletCloseAction, WalletCreateAction, WalletDepositMoneyAction
from .app import WalletService, build
from .model import NotificationKind, Wallet, WalletState

GLOBAL_SHARD = WalletCreateAction.GLOBAL_SHARD
MEXICO_SHARD = WalletCreateAction.MEXICO_SHARD


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[WalletService]:
    monkeypatch.setenv("WALLET_GLOBAL_URL", f"sqlite:///{tmp_path / 'wallet_global.db'}")
    monkeypatch.setenv("WALLET_MEXICO_URL", f"sqlite:///{tmp_path / 'wallet_mexico.db'}")
    built = build()
    built.start()
    try:
        yield built
    finally:
        built.close()


def create_wallet(
    service: WalletService, country_code: str, currency: str, initial_balance: str
) -> Wallet:
    return service.executor.execute(
        WalletCreateAction,
        WalletCreateAction.Params(
            country_code=country_code,
            owner_id=uuid4(),
            currency=currency,
            initial_balance=Decimal(initial_balance),
        ),
        principal="test-user",
    )


def deposit(service: WalletService, wallet: Wallet, amount: str, recipient: str) -> Wallet:
    return service.executor.execute(
        WalletDepositMoneyAction,
        WalletDepositMoneyAction.Params(
            wallet_id=wallet.id, amount=Decimal(amount), recipient=recipient
        ),
        principal="test-user",
    )


def eventually[T](read: Callable[[], T], within: float = 15.0) -> T:
    """Polled: fan-out and dispatch are real background jobs on a real schedule."""
    deadline = time.monotonic() + within
    while True:
        found = read()
        if found or time.monotonic() > deadline:
            return found
        time.sleep(0.1)


def test_separate_wallets_route_to_their_own_shards(service: WalletService) -> None:
    global_wallet = create_wallet(service, "US", "USD", "10.00")
    mexico_wallet = create_wallet(service, "MX", "MXN", "20.00")

    assert global_wallet.id.shard == GLOBAL_SHARD
    assert mexico_wallet.id.shard == MEXICO_SHARD
    assert deposit(service, global_wallet, "5.00", "global@example.com").balance == Decimal("15.00")
    assert deposit(service, mexico_wallet, "7.00", "mexico@example.com").balance == Decimal("27.00")

    wallet_repository = service.wallet_repository
    assert wallet_repository.exists_on_shard(GLOBAL_SHARD, global_wallet.id.value)
    assert not wallet_repository.exists_on_shard(MEXICO_SHARD, global_wallet.id.value)
    assert wallet_repository.exists_on_shard(MEXICO_SHARD, mexico_wallet.id.value)
    assert not wallet_repository.exists_on_shard(GLOBAL_SHARD, mexico_wallet.id.value)


def test_deposit_emits_event_and_handler_creates_notification(service: WalletService) -> None:
    wallet = create_wallet(service, "US", "USD", "0.00")

    assert deposit(service, wallet, "100.00", "alice@example.com").balance == Decimal("100.00")

    notifications = eventually(
        lambda: service.notification_repository.find_all_by_wallet_id(wallet.id.value)
    )
    assert len(notifications) == 1, "the handler never ran; fan-out or dispatch is not running"
    assert notifications[0].kind is NotificationKind.MONEY_DEPOSITED
    assert notifications[0].recipient == "alice@example.com"
    assert "100" in notifications[0].message


def test_close_transitions_wallet_to_closed_state(service: WalletService) -> None:
    wallet = create_wallet(service, "US", "EUR", "0.00")

    service.executor.execute(
        WalletCloseAction, WalletCloseAction.Params(wallet_id=wallet.id), principal="test-user"
    )

    assert service.wallet_repository.get(wallet.id).state is WalletState.CLOSED


def test_the_script_runs(capsys: pytest.CaptureFixture[str]) -> None:
    from .__main__ import main

    main()

    shown = capsys.readouterr().out
    assert "MONEY_DEPOSITED for ada@example.com" in shown
    assert "every one written in the same transaction" in shown
