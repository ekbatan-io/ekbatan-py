"""docs/database/repositories.md inherited surface claims, on SQLite."""
from decimal import Decimal
from uuid import uuid4
from common import probe, service
from wallet_service_sqlite.model import Wallet, WalletState
from wallet_service_sqlite.schema import WALLETS
from ekbatan.clock import SystemClock
from ekbatan import ShardedId

svc = service(); repo = svc.wallet_repository; clock = SystemClock()
mk = lambda: Wallet.open(owner_id=uuid4(), currency="EUR", balance=Decimal(10), created_date=clock.now())
a = mk()
r = repo.add(a)
probe("repositories.md:71 add -> M 'stamped' (same values as passed?)", lambda: {"equal": r == a, "same": r is a, "version": r.version})
u = repo.update(r.copy().balance(Decimal(20)).build())
probe("repositories.md:73 update -> version already moved", lambda: (r.version, u.version, repo.get(a.id).version))
probe("find missing -> None", lambda: repo.find(ShardedId[Wallet].random()))
probe("get missing raises EntityNotFoundError", lambda: repo.get(ShardedId[Wallet].random()))
b = repo.add(mk())
probe("find_many", lambda: len(repo.find_many([a.id, b.id])))
probe("all()", lambda: len(repo.all()))
probe("find_one_where", lambda: repo.find_one_where(WALLETS.c.id == b.id.value).id == b.id)
probe("find_where AND", lambda: len(repo.find_where(WALLETS.c.state == "OPENED", WALLETS.c.balance < 15)))
probe("count/exists/exists_where", lambda: (repo.count(), repo.exists(a.id), repo.exists_where(WALLETS.c.balance > 100)))
repo.update(repo.get(b.id).delete())
probe("soft-deleted filtered on reads (find, all, count, find_where)", lambda: (repo.find(b.id), len(repo.all()), repo.count(), len(repo.find_where(WALLETS.c.id == b.id.value))))
probe("stale update raises StaleRecordError", lambda: repo.update(r.copy().balance(Decimal(1)).build()))
probe("add_no_result/update_no_result exist", lambda: (hasattr(repo, "add_no_result"), hasattr(repo, "update_no_result"), hasattr(repo, "add_all_no_result"), hasattr(repo, "update_all_no_result")))
probe("no offset pagination: all(offset=...)", lambda: repo.all(offset=1))
probe("0304 add_all(None)", lambda: repo.add_all(None))
probe("0304 add(None)", lambda: repo.add(None))
svc.close()
