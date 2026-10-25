"""docs/database/transaction-manager.md and docs/concepts/actions.md claims, run on SQLite."""
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select, update, text
from common import probe, service
from wallet_service_sqlite.actions import WalletCreateAction, WalletDepositMoneyAction, OpenWalletParams, DepositParams
from wallet_service_sqlite.schema import WALLETS, EVENTS
from ekbatan import DatabaseRegistry, ExecutionConfiguration, DEFAULT_RETRIES

svc = service()
ex, wallet_repository, reg = svc.executor, svc.wallet_repository, svc.registry
tm = reg.default
w = ex.execute(None, WalletCreateAction, OpenWalletParams(owner_id=uuid4(), currency="EUR", opening_balance=Decimal("100")))

# actions.md:53 "plan.update returns the row ... stamped, version already moved"
def stamped():
    w2 = wallet_repository.get_by_id(w.id.value)
    from ekbatan.action.plan import ActionPlan
    from ekbatan.clock import SystemClock
    p = ActionPlan(SystemClock())
    d = w2.deposit(Decimal(1))
    r = p.update(d)
    return {"version_in": d.version, "version_out": r.version, "updated_date_changed": r.updated_date != d.updated_date}
probe("actions.md:53 plan.update stamps updated_date / moves version", stamped)

# actions.md:160 default one retry after 100 ms
probe("actions.md:160 DEFAULT_RETRIES", lambda: DEFAULT_RETRIES)

# tm.md:63-80 nested block
def nested():
    out = {}
    with tm.transaction() as outer:
        outer.execute(update(WALLETS).where(WALLETS.c.id == w.id.value).values(currency="USD"))
        try:
            with tm.transaction() as inner:
                out["inner_is_new_connection"] = inner is not outer
                out["inner_sees_outer_write"] = inner.execute(select(WALLETS.c.currency).where(WALLETS.c.id == w.id.value)).scalar()
                out["tx_db_inside_inner_is_inner"] = wallet_repository.tx_db() is inner.connection
                inner.execute(update(WALLETS).where(WALLETS.c.id == w.id.value).values(owner_id=uuid4()))
        except Exception as e:
            out["inner_write_after_outer"] = f"{type(e).__name__}: {str(e).splitlines()[0][:80]}"
        out["tx_db_after_inner_is_outer"] = wallet_repository.tx_db() is outer.connection
    return out
probe("tm.md:69-80 nested block (sqlite)", nested)

# tm.md:82 action inside caller tx commits on its own
def action_in_tx():
    class Boom(Exception): pass
    try:
        with tm.transaction():
            ex.execute(None, WalletDepositMoneyAction, DepositParams(wallet_id=w.id, amount=Decimal("5"), recipient="x"))
            raise Boom()
    except Boom:
        pass
    return str(wallet_repository.get_by_id(w.id.value).balance)
probe("tm.md:82 action inside rolled-back block stays committed (balance 105?)", action_in_tx)

# tm.md:120 a second manager for the same shard sees no transaction
def second_manager():
    from wallet_service_sqlite.app import load_ekbatan_config, CONFIG_FILE
    reg2 = DatabaseRegistry.from_config(load_ekbatan_config(CONFIG_FILE, environ={"WALLET_DATABASE_URL": str(tm.primary.url)}).sharding)
    with tm.transaction():
        return reg2.default.current()
probe("tm.md:120 second manager same shard current() inside first's block", second_manager)

# tm.md:96 inherited reads see committed state, not the block's own write
def inherited_read():
    with tm.transaction() as tx:
        tx.execute(update(WALLETS).where(WALLETS.c.id == w.id.value).values(currency="GBP"))
        got = wallet_repository.get_by_id(w.id.value).currency
        tx.rollback() if False else None
        raise_after = got
    wallets_after = wallet_repository.get_by_id(w.id.value).currency
    return {"inside_inherited_get": raise_after, "after_commit": wallets_after}
probe("tm.md:96 inherited get inside block (committed state?)", inherited_read)

# tm.md:156 tm.connection() inside a block joins it; outside AUTOCOMMIT
def conn_join():
    with tm.transaction() as tx:
        with tm.connection() as c:
            a = c is tx.connection
        with tm.connection(readonly=True) as c:
            b = c is tx.connection
    with tm.connection() as c:
        c.execute(update(WALLETS).where(WALLETS.c.id == w.id.value).values(currency="CHF"))
    return {"connection_joins": a, "readonly_joins": b, "autocommit_write_kept": wallet_repository.get_by_id(w.id.value).currency}
probe("tm.md:156-160 tm.connection join / autocommit", conn_join)

# repositories.md: db() opens its own connection inside a block
def db_inside():
    with tm.transaction() as tx:
        with wallet_repository.db() as c:
            return {"db_is_tx": c is tx.connection}
probe("repositories.md: db() inside a block opens its own", db_inside)
svc.close()
