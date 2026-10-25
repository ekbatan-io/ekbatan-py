"""your-first-action.mdx step 7 and docs/concepts/outbox.md: what a deposit writes to events."""
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select
from common import probe, service
from wallet_service_sqlite.actions import WalletCreateAction, WalletDepositMoneyAction, OpenWalletParams, DepositParams
from wallet_service_sqlite.schema import EVENTS
svc = service()
w = svc.executor.execute(WalletCreateAction, OpenWalletParams(owner_id=uuid4(), currency="EUR", opening_balance=Decimal("100")))
svc.executor.execute(WalletDepositMoneyAction, DepositParams(wallet_id=w.id, amount=Decimal("10.00"), recipient="ada@example.com"))
with svc.registry.default.connection() as c:
    for r in c.execute(select(EVENTS).order_by(EVENTS.c.event_date)).mappings():
        print("PROBE row:", {k: r[k] for k in ("action_name", "model_type", "event_type", "payload", "action_params", "namespace")})
probe("wallets version after deposit", lambda: svc.wallet_repository.get_by_id(w.id.value).version)
svc.close()
