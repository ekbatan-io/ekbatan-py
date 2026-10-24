"""The example's own test, on `ekbatan.aio`, on two SQLite files - the global shard and the
Mexico shard.

    make

It asks what the HTTP examples' tests ask, without HTTP: each wallet lands on the shard its
country names, a deposit's event reaches the handler and leaves a notification, and closing a
wallet closes it. And the script runs.

An async pool belongs to the loop it is used on, so each test is one `asyncio.run`: the service
is built, used and closed on that one loop.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
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
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WALLET_GLOBAL_URL", f"sqlite:///{tmp_path / 'wallet_global.db'}")
    monkeypatch.setenv("WALLET_MEXICO_URL", f"sqlite:///{tmp_path / 'wallet_mexico.db'}")


def scenario(test: Callable[[WalletService], Awaitable[None]]) -> None:
    """Build the service, start it, run `test` on it and close it - all on one loop."""

    async def run() -> None:
        service = build()
        await service.start()
        try:
            await test(service)
        finally:
            await service.aclose()

    asyncio.run(run())


async def create_wallet(
    service: WalletService, country_code: str, currency: str, initial_balance: str
) -> Wallet:
    return await service.executor.execute(
        WalletCreateAction,
        WalletCreateAction.Params(
            country_code=country_code,
            owner_id=uuid4(),
            currency=currency,
            initial_balance=Decimal(initial_balance),
        ),
        principal="test-user",
    )


async def deposit(service: WalletService, wallet: Wallet, amount: str, recipient: str) -> Wallet:
    return await service.executor.execute(
        WalletDepositMoneyAction,
        WalletDepositMoneyAction.Params(
            wallet_id=wallet.id, amount=Decimal(amount), recipient=recipient
        ),
        principal="test-user",
    )


async def eventually[T](read: Callable[[], Awaitable[T]], within: float = 15.0) -> T:
    """Polled: fan-out and dispatch are real background jobs on a real schedule."""
    deadline = time.monotonic() + within
    while True:
        found = await read()
        if found or time.monotonic() > deadline:
            return found
        await asyncio.sleep(0.1)


@pytest.mark.usefixtures("files")
def test_separate_wallets_route_to_their_own_shards() -> None:
    async def test(service: WalletService) -> None:
        global_wallet = await create_wallet(service, "US", "USD", "10.00")
        mexico_wallet = await create_wallet(service, "MX", "MXN", "20.00")

        assert global_wallet.id.shard == GLOBAL_SHARD
        assert mexico_wallet.id.shard == MEXICO_SHARD
        global_deposit = await deposit(service, global_wallet, "5.00", "global@example.com")
        mexico_deposit = await deposit(service, mexico_wallet, "7.00", "mexico@example.com")
        assert global_deposit.balance == Decimal("15.00")
        assert mexico_deposit.balance == Decimal("27.00")

        wallets = service.wallet_repository
        assert await wallets.exists_on_shard(GLOBAL_SHARD, global_wallet.id.value)
        assert not await wallets.exists_on_shard(MEXICO_SHARD, global_wallet.id.value)
        assert await wallets.exists_on_shard(MEXICO_SHARD, mexico_wallet.id.value)
        assert not await wallets.exists_on_shard(GLOBAL_SHARD, mexico_wallet.id.value)

    scenario(test)


@pytest.mark.usefixtures("files")
def test_deposit_emits_event_and_handler_creates_notification() -> None:
    async def test(service: WalletService) -> None:
        wallet = await create_wallet(service, "US", "USD", "0.00")

        updated = await deposit(service, wallet, "100.00", "alice@example.com")
        assert updated.balance == Decimal("100.00")

        notifications = await eventually(
            lambda: service.notification_repository.find_all_by_wallet_id(wallet.id.value)
        )
        assert len(notifications) == 1, "the handler never ran; fan-out or dispatch is not running"
        assert notifications[0].kind is NotificationKind.MONEY_DEPOSITED
        assert notifications[0].recipient == "alice@example.com"
        assert "100" in notifications[0].message

    scenario(test)


@pytest.mark.usefixtures("files")
def test_close_transitions_wallet_to_closed_state() -> None:
    async def test(service: WalletService) -> None:
        wallet = await create_wallet(service, "US", "EUR", "0.00")

        await service.executor.execute(
            WalletCloseAction, WalletCloseAction.Params(wallet_id=wallet.id), principal="test-user"
        )

        assert (await service.wallet_repository.get(wallet.id)).state is WalletState.CLOSED

    scenario(test)


def test_the_script_runs(capsys: pytest.CaptureFixture[str]) -> None:
    from .__main__ import main

    main()

    shown = capsys.readouterr().out
    assert "MONEY_DEPOSITED for ada@example.com" in shown
    assert "every one written in the same transaction" in shown
