"""Does the pool refill after a connection is thrown away (sharding.md: 'and refills when a
connection is thrown away')? Three ways to throw one away, against n6b-pg on 58720."""
import time
from sqlalchemy import create_engine, text
from ekbatan.config import DataSourceConfig
from ekbatan.persistence import engine_for
PW = "pw"; P = lambda k, v: print(f"PROBE {k}: {v}", flush=True)
admin = create_engine(f"postgresql+psycopg://postgres:{PW}@127.0.0.1:58720/postgres", pool_size=1)
def count(app):
    with admin.connect() as c:
        return c.execute(text("SELECT count(*) FROM pg_stat_activity WHERE application_name=:a"), {"a": app}).scalar()
def eng(app):
    e = engine_for(DataSourceConfig(url="postgresql+psycopg://127.0.0.1:58720/postgres", username="postgres", password=PW,
                                    pool_size=3, data_source_properties={"application_name": app}))
    time.sleep(1.5); return e
e = eng("n6r1")
with e.connect() as c: c.invalidate()
time.sleep(1.0); a = count("n6r1"); time.sleep(5); P("Connection.invalidate(): open after 1 s / 6 s", (a, count("n6r1")))
e.dispose()
e = eng("n6r2")
with admin.connect() as c:
    c.execute(text("SELECT pg_terminate_backend(pid) FROM (SELECT pid FROM pg_stat_activity WHERE application_name='n6r2' LIMIT 1) s"))
time.sleep(6); P("server killed one backend: open after 6 s, no checkout", count("n6r2"))
with e.connect() as c: c.execute(text("select 1"))
time.sleep(1); P("... after one checkout (pre_ping)", count("n6r2"))
e.dispose()
