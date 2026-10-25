"""docs/database/sharding.md claims, on two SQLite files."""
import logging, tempfile, io
from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4, UUID
from sqlalchemy import select, func
from common import probe
from wallet_service_sqlite.model import Wallet, WalletState, WalletCreatedEvent
from wallet_service_sqlite.builders import WalletBuilder
from wallet_service_sqlite.schema import WALLETS, EVENTS, metadata
from wallet_service_sqlite.repository import WalletRepository
from ekbatan import (ShardingConfig, ShardGroupConfig, ShardMemberConfig, DataSourceConfig, DatabaseRegistry,
                     ShardIdentifier, ShardedId, Id, Action, ActionExecutor, ExecutionConfiguration, CrossShardError,
                     ModelRepository, NoShardingStrategy)
from ekbatan.clock import SystemClock

logbuf = io.StringIO()
h = logging.StreamHandler(logbuf); h.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
logging.getLogger().addHandler(h); logging.getLogger().setLevel(logging.INFO)

d = tempfile.mkdtemp()
GLOBAL, MEXICO = ShardIdentifier(0, 0), ShardIdentifier(1, 0)
def layout(policy=None):
    kw = {} if policy is None else {"unknown_shard_policy": policy}
    return ShardingConfig(default_shard=GLOBAL, groups=(
        ShardGroupConfig(group=0, name="global", members=(ShardMemberConfig(member=0, name="global-eu-1", configs={"primaryConfig": DataSourceConfig(url=f"sqlite:///{d}/g.db")}),)),
        ShardGroupConfig(group=1, name="mexico", members=(ShardMemberConfig(member=0, name="mexico-1", configs={"primaryConfig": DataSourceConfig(url=f"sqlite:///{d}/m.db")}),)),
    ), **kw)
reg = DatabaseRegistry.from_config(layout())
for s in reg.shards:
    metadata.create_all(reg.manager(s).primary)
clock = SystemClock()

# sharding.md:162 sharded Wallet.open snippet as written (no with_initial_version, no created_date)
def open_snippet(shard):
    wallet_id = ShardedId[Wallet].generate(shard)
    return (WalletBuilder().id(wallet_id).state(WalletState.OPENED).owner_id(uuid4()).currency("EUR").balance(Decimal(1))
            .with_event(WalletCreatedEvent(model_id=str(wallet_id), owner_id=uuid4(), currency="EUR", opening_balance=Decimal(1))).build())
probe("sharding.md:162 sharded Wallet.open snippet as written", lambda: open_snippet(MEXICO))

repo = WalletRepository(reg)
mk = lambda shard: Wallet.open(owner_id=uuid4(), currency="EUR", balance=Decimal(1), id=ShardedId[Wallet].generate(shard), created_date=clock.now())
g, m = repo.add(mk(GLOBAL)), repo.add(mk(MEXICO))
def placement():
    out = {}
    for s in reg.shards:
        with reg.manager(s).connection() as c:
            out[str(s)] = c.execute(select(func.count()).select_from(WALLETS)).scalar()
    return out
probe("sharding.md:233 add routes by id shard (one row each)", placement)
probe("sharding.md:294 id.shard / minted_at", lambda: (m.id.shard, type(m.id.minted_at).__name__, m.id.minted_at.tzinfo))
probe("sharding.md:239 find_where scatters every shard", lambda: len(repo.find_all_where(WALLETS.c.currency == "EUR")))
probe("sharding.md:240 find_many([]) -> []", lambda: repo.find_all_by_ids([]))
probe("sharding.md:232 repo has delete()", lambda: hasattr(repo, "delete"))
probe("sharding.md:20 str(ShardIdentifier(0,0))", lambda: str(GLOBAL))

# unknown shard: warn and route default
unknown = ShardedId[Wallet].generate(ShardIdentifier(9, 9))
logbuf.truncate(0); logbuf.seek(0)
probe("sharding.md:349 find on unregistered shard falls back", lambda: repo.find_by_id(unknown))
print("PROBE sharding.md:353 warning text:", [l for l in logbuf.getvalue().splitlines() if "not registered" in l])
for pol in ["fail", "FAIL", "useDefaultShard", "use_default_shard", "USE-DEFAULT-SHARD", "", None]:
    def run(pol=pol):
        r2 = DatabaseRegistry.from_config(layout(pol))
        try:
            return WalletRepository(r2).find_by_id(unknown)
        finally:
            r2.close()
    probe(f"sharding.md:376 unknown_shard_policy={pol!r} find(unknown)", run)

# sharding.md:272 unsharded repo dbs() visits only default
class Plain(WalletRepository):
    def __init__(self, registry):
        ModelRepository.__init__(self, Wallet, WALLETS, registry, sharding=NoShardingStrategy())
probe("sharding.md:272 unsharded repo dbs() shards", lambda: [str(s) for s, c in Plain(reg).dbs()] if True else None)

# sharding.md:316-335 strategy that cannot resolve from an id: construction vs first call
class NoIdStrategy:
    uses_shard_aware_id = False
    def shard_for_id(self, key): return None
def no_id():
    class R(WalletRepository):
        def __init__(self, registry):
            ModelRepository.__init__(self, Wallet, WALLETS, registry, sharding=NoIdStrategy())
    r = R(reg)
    return ("constructed", r.find_by_id(g.id))
probe("sharding.md:316 vs :333 non-id strategy: refused at construction or first id call?", no_id)

# cross-shard
@dataclass(frozen=True, kw_only=True)
class TwoParams:
    a: ShardedId[Wallet]
    b: ShardedId[Wallet]
class TouchBoth(Action[TwoParams, None]):
    def __init__(self, wallet_repository, clock):
        super().__init__(clock); self.wallet_repository = wallet_repository
    def perform(self, principal, params):
        self.plan.update(self.wallet_repository.get_by_id(params.a).deposit(Decimal(1)))
        self.plan.update(self.wallet_repository.get_by_id(params.b).deposit(Decimal(1)))
ex = ActionExecutor(namespace="com.example.n6", registry=reg, repositories=[repo], actions=[TouchBoth(repo, clock)], events=EVENTS)
probe("sharding.md:389 cross-shard refused, naming two shards", lambda: ex.execute(TouchBoth, TwoParams(a=g.id, b=m.id)))
logbuf.truncate(0); logbuf.seek(0)
ex.execute(TouchBoth, TwoParams(a=g.id, b=m.id), config=ExecutionConfiguration(allow_cross_shard=True))
def outbox_each():
    out = {}
    for s in reg.shards:
        with reg.manager(s).connection() as c:
            out[str(s)] = [{"id": str(r.id), "action_id": str(r.action_id), "model_id": r.model_id, "event_type": r.event_type} for r in c.execute(select(EVENTS).where(EVENTS.c.action_name == "TouchBoth"))]
    return out
probe("sharding.md:398 cross-shard: outbox row on every shard, same id", outbox_each)
print("PROBE action.mdx:186 cross-shard log lines:", [l for l in logbuf.getvalue().splitlines() if "shard" in l.lower()][:5])

# partial failure: break mexico by dropping its wallets table after staging
class BreakSecond(TouchBoth):
    def perform(self, principal, params):
        super().perform(principal, params)
        with reg.manager(MEXICO).connection() as c:
            c.exec_driver_sql("ALTER TABLE wallets RENAME TO wallets_gone")
ex2 = ActionExecutor(namespace="com.example.n6", registry=reg, repositories=[repo], actions=[BreakSecond(repo, clock)], events=EVENTS)
logbuf.truncate(0); logbuf.seek(0)
probe("partial cross-shard failure raises", lambda: ex2.execute(BreakSecond, TwoParams(a=g.id, b=m.id), config=ExecutionConfiguration.no_retry(allow_cross_shard=True)))
print("PROBE action.mdx:187 partial-failure log levels:", [l[:200] for l in logbuf.getvalue().splitlines() if "shard" in l.lower()][:6])

# ids sort as Java's UUID.compareTo (signed halves)
lo, hi = Id[Wallet](UUID("10000000-0000-4000-8000-000000000000")), Id[Wallet](UUID("90000000-0000-4000-8000-000000000000"))
probe("sharding.md:299 8-f id sorts before 0-7 id", lambda: sorted([lo, hi])[0] == hi)
reg.close()
