"""Run the service against a throwaway SQLite database and narrate what happens.

make run
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from .actions import (
    CloseWalletAction,
    DepositMoneyAction,
    DepositParams,
    OpenWalletAction,
    OpenWalletParams,
    WalletIdParams,
)
from .app import WalletService, build
from .schema import EVENTS


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        # `ekbatan.yaml` reads the database url from WALLET_DATABASE_URL.
        url = f"sqlite:///{Path(directory) / 'wallet.db'}"
        asyncio.run(serve(build({**os.environ, "WALLET_DATABASE_URL": url})))


async def serve(service: WalletService) -> None:
    try:
        await run(service)
    finally:
        await service.aclose()


async def run(service: WalletService) -> None:
    say("start the jobs")
    # Fan-out and handling start polling here, on their own threads, as they would in any
    # service. Nothing below calls them.
    service.start()
    print("   fan-out and handling are polling")

    say("open a wallet")
    wallet = await service.executor.execute(
        OpenWalletAction,
        OpenWalletParams(owner_id=uuid4(), currency="EUR", opening_balance=Decimal("100.00")),
    )
    print(f"   {wallet.id}  balance {wallet.balance}  on shard {wallet.id.shard}")

    say("deposit into it")
    wallet = await service.executor.execute(
        DepositMoneyAction,
        DepositParams(wallet_id=wallet.id, amount=Decimal("25.50"), recipient="ada@example.com"),
    )
    print(f"   balance {wallet.balance}, version {wallet.version}")
    print("   the row and its event were written by one transaction")

    say("let the in-process handler react")
    # Nobody calls the handler: the fan-out job turns the committed event into a delivery and
    # the handling job runs it - the handler awaits on dispatch's handler loop - within a
    # second or two.
    deadline = time.monotonic() + 15
    while not await service.alerts.all():
        if time.monotonic() > deadline:
            raise RuntimeError("no alert after 15 seconds: fan-out or handling is not running")
        await asyncio.sleep(0.1)
    for alert in await service.alerts.all():
        print(f"   alert for {alert.recipient}: {alert.message}")
    print("   the handler read the recipient off the action's parameters, not the event")

    say("close it")
    await service.executor.execute(CloseWalletAction, WalletIdParams(wallet_id=wallet.id))
    await service.executor.execute(CloseWalletAction, WalletIdParams(wallet_id=wallet.id))
    print("   closed twice; the second time changed nothing and emitted nothing")

    say("what a consumer reads from the outbox")
    async with service.registry.default.connection(readonly=True) as conn:
        rows = (await conn.execute(select(EVENTS).order_by(EVENTS.c.event_date, EVENTS.c.id))).all()
    print(f"   {'action':22} {'event':18} delivered  payload")
    for row in rows:
        print(
            f"   {row.action_name:22} {(row.event_type or '(none - a sentinel)'):18} "
            f"{row.delivered!s:9}  {row.payload or ''}"
        )
    print()
    print(
        f"   {len(rows)} rows, every one written in the same transaction"
        f" as the change it describes."
    )


def say(what: str) -> None:
    print(f"\n== {what}")


if __name__ == "__main__":
    main()
