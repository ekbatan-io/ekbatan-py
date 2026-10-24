"""scheduled-tasks.md: 'Either DDL works with either port' (Java's timestamptz table, a non-UTC
session), the DDL this port emits, and the pool-size warning. n6b-pg on 58720."""
import io, logging, time
from datetime import UTC, datetime, timedelta
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.dialects import postgresql, mysql
from db_scheduler.task.schedule import FixedDelay
from ekbatan import DataSource
from ekbatan.config import DataSourceConfig
from ekbatan.distributedjobs import DistributedJob, job_registry
from ekbatan.schema import scheduled_tasks_table
PW = "pw"; P = lambda k, v: print(f"PROBE {k}: {v}", flush=True)
buf = io.StringIO(); logging.getLogger().addHandler(logging.StreamHandler(buf)); logging.getLogger().setLevel(logging.INFO)

md = MetaData(); t = scheduled_tasks_table(md)
for d in (postgresql.dialect(), mysql.dialect()):
    ddl = str(CreateTable(t).compile(dialect=d))
    P(f"{d.name} execution_time column", [l.strip() for l in ddl.splitlines() if "execution_time" in l][0])
    P(f"{d.name} indexes", [str(CreateIndex(i).compile(dialect=d)) for i in sorted(t.indexes, key=lambda i: i.name)])

JAVA_DDL = """CREATE TABLE scheduled_tasks (task_name TEXT NOT NULL, task_instance TEXT NOT NULL, task_data BYTEA,
 execution_time TIMESTAMP WITH TIME ZONE NOT NULL, picked BOOLEAN NOT NULL, picked_by TEXT,
 last_success TIMESTAMP WITH TIME ZONE, last_failure TIMESTAMP WITH TIME ZONE, consecutive_failures INT,
 last_heartbeat TIMESTAMP WITH TIME ZONE, version BIGINT NOT NULL, priority SMALLINT,
 PRIMARY KEY (task_name, task_instance))"""
eng = create_engine(f"postgresql+psycopg://postgres:{PW}@127.0.0.1:58720/postgres", pool_size=8, max_overflow=0,
                    connect_args={"options": "-c timezone=America/New_York"})
SOURCE = DataSource.from_config(DataSourceConfig(url="postgresql+psycopg://127.0.0.1:58720/postgres", username="postgres", password=PW,
                    pool_size=8, data_source_properties={"options": "-c timezone=America/New_York"}))
with eng.begin() as c:
    c.execute(text("DROP TABLE IF EXISTS scheduled_tasks")); c.execute(text(JAVA_DDL))
ran = []
class Tick(DistributedJob):
    def name(self): return "tick"
    def schedule(self): return FixedDelay(timedelta(hours=1))
    def execute(self, ctx): ran.append(datetime.now(UTC))
reg = job_registry().data_source(SOURCE).with_jobs([Tick()]).poll_interval(timedelta(milliseconds=300)).shutdown_max_wait(timedelta(seconds=2)).build()
before = datetime.now(UTC); reg.start(); time.sleep(3); reg.stop()
with eng.connect() as c:
    et = c.execute(text("SELECT execution_time FROM scheduled_tasks WHERE task_name='tick'")).scalar()
    tz = c.execute(text("SHOW timezone")).scalar()
P("session timezone", tz)
P("job ran right away on Java's timestamptz table", len(ran) == 1)
P("next execution_time - (run + 1 h), seconds", round((et - (ran[0] + timedelta(hours=1))).total_seconds(), 1) if ran else None)

# pool-size warning: workers 3 with a 3-connection pool, and with 4
for size in (3, 4, 6):
    buf.truncate(0); buf.seek(0)
    e = DataSource.from_config(DataSourceConfig(url="postgresql+psycopg://127.0.0.1:58720/postgres", username="postgres", password=PW, pool_size=size))
    job_registry().data_source(e).with_jobs([Tick()]).workers(3).build()
    w = [l for l in buf.getvalue().splitlines() if "connection pool holds" in l]
    P(f"workers=3 pool={size} warning", (w[0][w[0].find('Give'):w[0].find('Give')+50]) if w else "none")
    e.close()
with eng.begin() as c: c.execute(text("DROP TABLE scheduled_tasks"))
