"""Run the service against a throwaway SQLite database and narrate what happens.

cd ekbatan-py-examples && uv run python -m wallet_service_sqlite
"""

from __future__ import annotations

import tempfile
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
        service = build(f"sqlite:///{Path(directory) / 'wallet.db'}")
        try:
            run(service)
        finally:
            service.close()


def run(service: WalletService) -> None:
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
    service.deliver_pending_events()
    for alert in service.alerts.all():
        print(f"   alert for {alert.recipient}: {alert.message}")
    print("   the handler read the recipient off the action's parameters, not the event")

    say("run the daily stipend, as one instance of a cluster would")
    service.jobs.start()
    # A registry of its own, holding only the stipend. The service registers three jobs and
    # that is what a deployment looks like - but the two event jobs are each one execution
    # that loops for the life of the process, as Java's are, so running one to completion on
    # this thread would never return. Java's own tests never schedule them either; they call
    # `fanoutJob.drainOneRound()`, which is what `deliver_pending_events()` above is.
    stipend_only = (
        job_registry()
        .engine(service.registry.default.primary)
        .with_jobs([PayStipendJob(service.executor, service.wallets)])
        .build_manual(SettableClock(SystemClock().now()))
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
