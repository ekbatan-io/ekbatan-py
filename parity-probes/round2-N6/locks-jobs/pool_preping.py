"""A backend killed by the server, then one checkout: does pool_pre_ping hand out a live
connection? ekbatan's pool against plain SQLAlchemy QueuePool, n6b-pg on 58720."""
import time
from sqlalchemy import create_engine, text
from ekbatan.config import DataSourceConfig
from ekbatan.persistence import engine_for
PW = "pw"; P = lambda k, v: print(f"PROBE {k}: {v}", flush=True)
admin = create_engine(f"postgresql+psycopg://postgres:{PW}@127.0.0.1:58720/postgres", pool_size=1)
def kill_all(app):
    with admin.connect() as c:
        return c.execute(text("SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE application_name=:a"), {"a": app}).scalar()
def trial(label, e, app):
    cs = [e.connect() for _ in range(3)]                 # make sure three are pooled, as Hikari's fill does
    for c in cs: c.execute(text("select 1"))
    for c in cs: c.close()
    time.sleep(1.5); n = kill_all(app); time.sleep(0.5)
    ok = 0; err = None
    for _ in range(3):
        try:
            with e.connect() as c: c.execute(text("select 1")); ok += 1
        except Exception as ex: err = type(ex).__name__ + ": " + str(ex).splitlines()[0][:80]
    P(f"{label}: killed {n} backends, 3 checkouts ok", f"{ok}/3 {err or ''}")
    e.dispose()
trial("ekbatan engine_for (pool_size=3, pre_ping)", engine_for(DataSourceConfig(url="postgresql+psycopg://127.0.0.1:58720/postgres",
      username="postgres", password=PW, pool_size=3, data_source_properties={"application_name": "n6pp1"})), "n6pp1")
trial("plain create_engine(pool_pre_ping=True)", create_engine(f"postgresql+psycopg://postgres:{PW}@127.0.0.1:58720/postgres",
      pool_size=3, max_overflow=0, pool_pre_ping=True, connect_args={"application_name": "n6pp2"}), "n6pp2")
