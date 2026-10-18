"""website reference/action.mdx, action-plan.mdx, action-executor.mdx claims, run on SQLite."""
import logging
from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select, func
from common import probe, service
from wallet_service_sqlite.actions import OpenWalletAction, DepositMoneyAction, OpenWalletParams, DepositParams
from wallet_service_sqlite.schema import WALLETS, EVENTS
from wallet_service_sqlite.model import Wallet
from ekbatan import Action, ActionExecutor, ActionRegistry, RepositoryRegistry
from ekbatan.action.plan import ActionPlan
from ekbatan.clock import SystemClock

svc = service()
ex, wallets, reg = svc.executor, svc.wallets, svc.registry
clock = SystemClock()
w = ex.execute(OpenWalletAction, OpenWalletParams(owner_id=uuid4(), currency="EUR", opening_balance=Decimal("100")))

probe("action.mdx:57 execute(an instance) 'no registry is needed'",
      lambda: ex.execute(DepositMoneyAction(wallets, clock), DepositParams(wallet_id=w.id, amount=Decimal(1), recipient="x")))

def add_returns():
    p = ActionPlan(clock)
    n = Wallet.open(owner_id=uuid4(), currency="EUR", balance=Decimal(0), created_date=clock.now())
    r = p.add(n)
    return {"same_object": r is n}
probe("action-plan.mdx:52 add returns the stamped row, not the object you passed", add_returns)

def changes_view():
    p = ActionPlan(clock)
    p.add(Wallet.open(owner_id=uuid4(), currency="EUR", balance=Decimal(0), created_date=clock.now()))
    ch = p.changes()
    try:
        ch.clear(); return "mutable!"
    except Exception as e:
        return f"read-only ({type(e).__name__})"
probe("action-plan.mdx changes() is a read-only view", changes_view)

class NoopAction(Action[None, str]):
    def perform(self, principal, params):
        return "nothing"
class ScalarAction(Action[str, str]):
    def perform(self, principal, params):
        return params
class WhoAction(Action[None, str]):
    def perform(self, principal, params):
        return principal
ex2 = ActionExecutor(namespace="com.example.n6", registry=reg,
                     repositories=RepositoryRegistry.builder().with_model_repository(wallets).build(),
                     actions=ActionRegistry.builder().with_action(NoopAction(clock)).with_action(ScalarAction(clock)).with_action(WhoAction(clock)).build(),
                     events=EVENTS)
def sentinel():
    ex2.execute(NoopAction, None)
    with reg.default.connection() as c:
        rows = c.execute(select(EVENTS.c.action_name, EVENTS.c.event_type, EVENTS.c.action_params).where(EVENTS.c.action_name.like("%Noop%"))).all()
    return [tuple(r) for r in rows]
probe("action-plan.mdx:96 no change still writes one sentinel row, event_type null", sentinel)
probe("action.mdx:46 bare scalar params refused", lambda: ex2.execute(ScalarAction, "hello"))
probe("action.mdx:117 principal kwarg passed to perform", lambda: ex2.execute(WhoAction, None, principal="ada"))
probe("action.mdx:121 events table has no principal column", lambda: [c.name for c in EVENTS.c if "princ" in c.name])
probe("action-executor.mdx:118 hyphen in namespace refused", lambda: ActionExecutor(namespace="my-service", registry=reg, repositories=[wallets]))
probe("action-executor.mdx:65 registry=None refused", lambda: ActionExecutor(namespace="a.b", registry=None, repositories=[wallets]))
probe("action-executor.mdx:60 repositories=None refused", lambda: ActionExecutor(namespace="a.b", registry=reg, repositories=None))
def no_events_kw():
    e = ActionExecutor(namespace="com.example.noevents", registry=reg, repositories=[wallets],
                       actions=[OpenWalletAction(clock)])
    e.execute(OpenWalletAction, OpenWalletParams(owner_id=uuid4(), currency="EUR", opening_balance=Decimal("1")))
    with reg.default.connection() as c:
        return c.execute(select(func.count()).select_from(EVENTS).where(EVENTS.c.namespace == "com.example.noevents")).scalar()
probe("action-executor.mdx:58 events left out -> default persister writes framework events table", no_events_kw)
def unknown_repo():
    e = ActionExecutor(namespace="com.example.x", registry=reg, repositories=[svc.alerts], actions=[OpenWalletAction(clock)])
    e.execute(OpenWalletAction, OpenWalletParams(owner_id=uuid4(), currency="EUR", opening_balance=Decimal("1")))
probe("action-executor.mdx:57 a staged type with no repository: names type and lists registered", unknown_repo)
svc.close()
