"""Run docs/getting-started.md (and README.md / your-first-action.mdx copies) snippets as written,
borrowing the sqlite example's model, schema and builders for the parts the docs elide."""
import sys, traceback
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ekbatan-py-examples/wallet-service-uv-sqlite/src"))
from wallet_service_sqlite.model import Wallet, WalletState, WalletMoneyDepositedEvent
from wallet_service_sqlite.schema import WALLETS, EVENTS, metadata
from wallet_service_sqlite.repository import WalletRepository as ExampleRepo
from ekbatan import *  # noqa
from ekbatan import ModelRepository, DatabaseRegistry, ShardedId, Action, ActionExecutor, RepositoryRegistry, ActionRegistry
from ekbatan.clock import Clock, SystemClock
from ekbatan.testing import ActionSpec, run_action
ShardingConfig = object
from dataclasses import dataclass

def probe(key, fn):
    try:
        print(f"PROBE {key}: OK {fn()!r}"[:400])
    except Exception as e:
        print(f"PROBE {key}: {type(e).__name__}: {(str(e).splitlines() or [""])[0][:300]}")

# getting-started.md:135-139 / your-first-action.mdx repository, as written
class WalletRepository(ModelRepository[Wallet, ShardedId[Wallet]]):
    def __init__(self, registry: DatabaseRegistry) -> None:
        super().__init__(Wallet, WALLETS, registry)
    def find_all_by_owner(self, owner_id):
        return self.find_all_where(WALLETS.c.owner_id == owner_id)

layout = ShardingConfig.single("sqlite://") if hasattr(ShardingConfig, "single") else None
print("PROBE layout-helper ShardingConfig.single exists:", layout is not None)
from wallet_service_sqlite.app import build
import os, tempfile
d = tempfile.mkdtemp()
svc = build({"WALLET_DATABASE_URL": f"sqlite:///{d}/w.db"})
registry = svc.registry
probe("getting-started:135 repository with no to_row/from_row", lambda: WalletRepository(registry))
wallet_repository = ExampleRepo(registry)
clock = SystemClock()

@dataclass(frozen=True, kw_only=True)
class DepositParams:
    wallet_id: ShardedId[Wallet]
    amount: Decimal

class WalletDepositMoneyAction(Action[DepositParams, Wallet]):
    def __init__(self, wallet_repository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository
    def perform(self, principal, params):
        wallet = self.wallet_repository.get_by_id(params.wallet_id.value)
        return self.plan.update(wallet.deposit(params.amount))

# getting-started.md:172-180 ActionExecutor(...) keyword constructor
def mk_exec():
    return ActionExecutor(
        namespace="com.example.wallets",
        registry=registry,
        repositories=RepositoryRegistry.builder().with_model_repository(wallet_repository).build(),
        actions=ActionRegistry.builder().with_action(WalletDepositMoneyAction(wallet_repository, clock)).build(),
        events=EVENTS,
    )
probe("getting-started:172 ActionExecutor(namespace=,registry=,repositories=,actions=,events=)", mk_exec)

w = Wallet.open(owner_id=uuid4(), currency="EUR", balance=Decimal("100.00"), created_date=clock.now())
from types import SimpleNamespace
class FakeRepo:
    def get(self, _id): return w
probe("getting-started:198 ActionSpec with_principal/assert_emitted(lambda)", lambda: (ActionSpec.of(WalletDepositMoneyAction(FakeRepo(), clock))
    .with_principal("alice")
    .execute(DepositParams(wallet_id=w.id, amount=Decimal("50.00")))
    .assert_updated(Wallet, lambda x: x.balance == Decimal("150.00"))
    .assert_emitted(WalletMoneyDepositedEvent, lambda e: e.amount == Decimal("50.00"))
    .assert_no_additions()) and "passed")
probe("README:236 assert_emitted(WalletMoneyDepositedEvent, 1)", lambda: (ActionSpec.of(WalletDepositMoneyAction(FakeRepo(), clock))
    .execute(DepositParams(wallet_id=w.id, amount=Decimal("50.00")))
    .assert_updated(Wallet, lambda x: x.balance == Decimal("150.00"))
    .assert_emitted(WalletMoneyDepositedEvent, 1)
    .assert_no_additions()) and "passed")
def ra():
    staged = run_action(None, WalletDepositMoneyAction(FakeRepo(), clock), DepositParams(wallet_id=w.id, amount=Decimal("50.00")))
    assert staged.result.balance == Decimal("150.00")
    assert staged.emitted(WalletMoneyDepositedEvent)[0].amount == Decimal("50.00")
    assert staged.updated(Wallet) == [staged.result]
    return "passed"
probe("README:244 run_action", ra)
# README:15 Model[UUID, WalletState] (two type args)
from uuid import UUID
probe("README:15 Model[UUID, WalletState] subscript", lambda: Model[UUID, WalletState])
# README:116 'a change is evolve(..., emit=...)'
probe("README:116 model.evolve(balance=..., emit=event)", lambda: w.evolve(balance=Decimal("1"), emit=WalletMoneyDepositedEvent(model_id=str(w.id), amount=Decimal(1), new_balance=Decimal(1))).events)
svc.close()
staged = run_action(None, WalletDepositMoneyAction(FakeRepo(), clock), DepositParams(wallet_id=w.id, amount=Decimal("50.00")))
u = staged.updated(Wallet)
print("PROBE README:244 updated len:", len(u), "same object as result:", u and u[0] is staged.result, "equal:", u and u[0] == staged.result)
if u:
    import dataclasses
    print("PROBE README:244 differing fields:", [f.name for f in dataclasses.fields(Wallet) if getattr(u[0], f.name) != getattr(staged.result, f.name)])
