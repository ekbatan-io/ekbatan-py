"""A wallet, end to end, on two SQLite files.

    make run

Creates a wallet on each shard, deposits into one, lets the in-process handler react, closes a
wallet twice, and prints the outbox - everything a consumer of the event stream would read.
"""

from __future__ import annotations

import os
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from .actions import WalletCloseAction, WalletCreateAction, WalletDepositMoneyAction
from .app import WalletService, build
from .model import Wallet
from .schema import EVENTS


def say(step: str) -> None:
    print(f"\n-- {step}")


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        # `ekbatan.yaml` reads the two files from WALLET_GLOBAL_URL and WALLET_MEXICO_URL.
        os.environ["WALLET_GLOBAL_URL"] = f"sqlite:///{Path(directory) / 'wallet_global.db'}"
        os.environ["WALLET_MEXICO_URL"] = f"sqlite:///{Path(directory) / 'wallet_mexico.db'}"
        service = build()
        try:
            run(service)
        finally:
            service.close()


def create_wallet(service: WalletService, country_code: str, currency: str) -> Wallet:
    return service.executor.execute(
        "script",
        WalletCreateAction,
        WalletCreateAction.Params(
            country_code=country_code,
            owner_id=uuid4(),
            currency=currency,
            initial_balance=Decimal("100.00"),
        ),
    )


def run(service: WalletService) -> None:
    say("start the jobs")
    # Fan-out and handling start polling here, as they would in any service. Nothing below
    # calls them.
    service.start()
    print("   fan-out and handling are polling")

    say("create a wallet on each shard")
    wallet = create_wallet(service, "US", "USD")
    mexican = create_wallet(service, "MX", "MXN")
    print(f"   {wallet.id}  balance {wallet.balance}  on shard {wallet.id.shard}")
    print(f"   {mexican.id}  balance {mexican.balance}  on shard {mexican.id.shard}")

    say("deposit into the first")
    wallet = service.executor.execute(
        "script",
        WalletDepositMoneyAction,
        WalletDepositMoneyAction.Params(
            wallet_id=wallet.id, amount=Decimal("25.50"), recipient="ada@example.com"
        ),
    )
    print(f"   balance {wallet.balance}, version {wallet.version}")
    print("   the row and its event were written by one transaction")

    say("let the in-process handler react")
    # Nobody calls the handler: the fan-out job turns the committed event into a delivery and
    # the handling job runs it, each on its own thread, within a second or two.
    deadline = time.monotonic() + 15
    while not service.notification_repository.find_all_by_wallet_id(wallet.id.value):
        if time.monotonic() > deadline:
            raise RuntimeError(
                "no notification after 15 seconds: fan-out or handling is not running"
            )
        time.sleep(0.1)
    for notification in service.notification_repository.find_all_by_wallet_id(wallet.id.value):
        print(f"   {notification.kind.name} for {notification.recipient}: {notification.message}")
    print("   the handler read the recipient off the action's parameters, not the event")

    say("close it")
    for _ in range(2):
        service.executor.execute(
            "script",
            WalletCloseAction,
            WalletCloseAction.Params(wallet_id=wallet.id),
        )
    print("   closed twice; the second time changed nothing and emitted nothing")

    say("what a consumer reads from the global shard's outbox")
    with service.database_registry.default.connection(readonly=True) as conn:
        rows = conn.execute(select(EVENTS).order_by(EVENTS.c.event_date, EVENTS.c.id)).all()
    print(f"   {'action':26} {'event':26} delivered  payload")
    for row in rows:
        print(
            f"   {row.action_name:26} {(row.event_type or '(none - a sentinel)'):26} "
            f"{row.delivered!s:9}  {row.payload or ''}"
        )
    print()
    print(
        f"   {len(rows)} rows, every one written in the same transaction"
        f" as the change it describes."
    )


if __name__ == "__main__":
    main()
