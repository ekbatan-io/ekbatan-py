"""Run the pool claims in docs/database/sharding.md (DataSourceConfig table and 'The pool runs as
Java's Hikari pool does') and DIVERGENCE.md's pool table, against n6b-pg on 58720."""
import io, logging, time
from sqlalchemy import create_engine, text
from ekbatan.config import DataSourceConfig
from ekbatan.persistence import DataSource

PW = "pw"; MARKER = "N6-SECRET-MARKER-xyzzy"
P = lambda k, v: print(f"PROBE {k}: {v}", flush=True)
buf = io.StringIO(); h = logging.StreamHandler(buf); logging.getLogger().addHandler(h); logging.getLogger().setLevel(logging.DEBUG)
admin = create_engine(f"postgresql+psycopg://postgres:{PW}@127.0.0.1:58720/postgres", pool_size=1)
def count(app):
    with admin.connect() as c:
        return c.execute(text("SELECT count(*) FROM pg_stat_activity WHERE application_name=:a"), {"a": app}).scalar()

d = DataSourceConfig(url="postgresql+psycopg://127.0.0.1:58720/postgres", username="postgres", password=PW)
P("defaults pool_size/max_overflow/pool_timeout/pool_recycle/pool_pre_ping/echo",
  (d.pool_size, d.max_overflow, d.pool_timeout, d.pool_recycle, d.pool_pre_ping, d.echo))

cfg = DataSourceConfig(url="postgresql+psycopg://127.0.0.1:58720/postgres", username="postgres", password=PW,
                       pool_size=3, data_source_properties={"application_name": "n6pool"})
eng = DataSource.from_config(cfg).sqlalchemy_engine; time.sleep(1.5)
P("pool_size=3: open connections 1.5 s after build, no query", count("n6pool"))
with eng.connect() as c:
    c.connection.invalidate()
time.sleep(1.5)
P("after one connection thrown away, open connections", count("n6pool"))
eng.dispose()

bad = DataSourceConfig(url="postgresql+psycopg://127.0.0.1:58720/postgres", username="postgres", password=MARKER, pool_timeout=3)
e2 = DataSource.from_config(bad).sqlalchemy_engine; t0 = time.monotonic()
try:
    with e2.connect(): pass
    P("wrong password", "connected?!")
except Exception as e:
    P("wrong password with pool_timeout=3 fails after", f"{time.monotonic()-t0:.1f}s with {type(e).__name__}: {str(e).splitlines()[0][:110]}")
    P("marker in the error text", MARKER in str(e))
e2.dispose()
P("marker in any log line", MARKER in buf.getvalue())
P("marker in repr(config)", MARKER in repr(bad))
