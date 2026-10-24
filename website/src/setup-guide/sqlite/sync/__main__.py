"""Create a wallet, deposit into it, let the handler react, and print the outbox."""

from __future__ import annotations

import os
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from .actions import WalletCreateAction, WalletDepositMoneyAction
from .app import WalletService, build
from .schema import EVENTS


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        # `ekbatan.yaml` reads the database url from WALLET_DATABASE_URL.
        os.environ["WALLET_DATABASE_URL"] = f"sqlite:///{Path(directory) / 'wallet.db'}"
        service = build()
        try:
            run(service)
        finally:
            service.close()


def run(service: WalletService) -> None:
    service.start()
    wallet = service.executor.execute(
        WalletCreateAction,
        WalletCreateAction.Params(
            owner_id=uuid4(), currency="EUR", initial_balance=Decimal("100.00")
        ),
        principal="script",
    )
    wallet = service.executor.execute(
        WalletDepositMoneyAction,
        WalletDepositMoneyAction.Params(
            wallet_id=wallet.id, amount=Decimal("25.50"), recipient="ada@example.com"
        ),
        principal="script",
    )
    print(f"wallet {wallet.id}, balance {wallet.balance}")

    # Nobody calls the handler: the fan-out job turns the committed event into a delivery and
    # the handling job runs it, within a second or two.
    deadline = time.monotonic() + 15
    while not (found := service.notification_repository.find_all_by_wallet_id(wallet.id.value)):
        if time.monotonic() > deadline:
            raise RuntimeError("no notification after 15 seconds")
        time.sleep(0.1)
    for notification in found:
        print(f"{notification.kind.name} for {notification.recipient}: {notification.message}")

    with service.database_registry.default.connection(readonly=True) as conn:
        rows = conn.execute(select(EVENTS).order_by(EVENTS.c.event_date, EVENTS.c.id)).all()
    for row in rows:
        print(f"  {row.action_name:26} {row.event_type or '(none - a sentinel)'}")


if __name__ == "__main__":
    main()
