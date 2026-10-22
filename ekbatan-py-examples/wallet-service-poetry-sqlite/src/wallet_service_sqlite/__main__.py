"""Run the service against a throwaway SQLite database and narrate what happens.

make run
"""

from __future__ import annotations

import os
import tempfile
import time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from db_scheduler.clock import SettableClock
from sqlalchemy import select

from ekbatan.clock import SystemClock
from ekbatan.distributedjobs import job_registry

from .actions import (
    CloseWalletAction,
    DepositMoneyAction,
    DepositParams,
    OpenWalletAction,
    OpenWalletParams,
    WalletIdParams,
)
from .app import WalletService, build
from .jobs import PayStipendJob
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
    say("start the jobs")
    # Fan-out, handling and the stipend start polling here, as they would in any service.
    # Nothing below calls them.
    service.start()
    print("   fan-out, handling and the stipend are polling")

    say("open a wallet")
    wallet = service.executor.execute(
        OpenWalletAction,
        OpenWalletParams(owner_id=uuid4(), currency="EUR", opening_balance=Decimal("100.00")),
    )
    print(f"   {wallet.id}  balance {wallet.balance}  on shard {wallet.id.shard}")

    say("deposit into it")
    wallet = service.executor.execute(
        DepositMoneyAction,
        DepositParams(wallet_id=wallet.id, amount=Decimal("25.50"), recipient="ada@example.com"),
    )
    print(f"   balance {wallet.balance}, version {wallet.version}")
    print("   the row and its event were written by one transaction")

    say("let the in-process handler react")
    # Nobody calls the handler: the fan-out job turns the committed event into a delivery and
    # the handling job runs it, each on its own thread, within a second or two.
    deadline = time.monotonic() + 15
    while not service.alerts.all():
        if time.monotonic() > deadline:
            raise RuntimeError("no alert after 15 seconds: fan-out or handling is not running")
        time.sleep(0.1)
    for alert in service.alerts.all():
        print(f"   alert for {alert.recipient}: {alert.message}")
    print("   the handler read the recipient off the action's parameters, not the event")

    say("a day later, the daily stipend runs, as one instance of a cluster would")
    # The stipend ran once at startup, before this wallet existed, and is next due in a day.
    # Rather than wait a day, a scheduler of its own whose clock says a day has passed picks
    # the same row from `scheduled_tasks` and runs it once.
    a_day_later = SettableClock(SystemClock().instant() + timedelta(days=1, seconds=1))
    stipend_only = (
        job_registry()
        .engine(service.registry.default.primary)
        .with_jobs([PayStipendJob(service.executor, service.wallets)])
        .build_manual(a_day_later)
    )
    stipend_only.start()
    stipend_only.scheduler.run_any_due_executions()
    print(f"   balance {service.wallets.get(wallet.id).balance}")

    say("close it")
    service.executor.execute(CloseWalletAction, WalletIdParams(wallet_id=wallet.id))
    service.executor.execute(CloseWalletAction, WalletIdParams(wallet_id=wallet.id))
    print("   closed twice; the second time changed nothing and emitted nothing")

    say("what a consumer reads from the outbox")
    with service.registry.default.connection(readonly=True) as conn:
        rows = conn.execute(select(EVENTS).order_by(EVENTS.c.event_date, EVENTS.c.id)).all()
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
