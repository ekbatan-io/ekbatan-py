"""N6 / migrations: the dialect claims of docs/database/migrations.md, on PostgreSQL 17 and MySQL 8.4.

- PG: a waiting migrator waits past 50 s (the reverted EKB-0313 limit) and logs every 30 s.
- PG: a replica check exists (pg_is_in_recovery) - only read here; a standby is not built.
- MySQL: GET_LOCK named with the database; read_only refused; a half-failed migration recorded as failed.
Containers on host ports 58700/58701, removed at the end. The password is a marker, never printed.
"""
import logging, tempfile, threading, time
from pathlib import Path

import psycopg, pymysql
from testcontainers.postgres import PostgresContainer
from testcontainers.mysql import MySqlContainer

from ekbatan.config import DataSourceConfig, ShardingConfig
from ekbatan.migrations import migrate

PW = "n6-marker-pw"


def probe(k, v): print(f"PROBE {k}: {v}", flush=True)


class Rec(logging.Handler):
    def __init__(self): super().__init__(); self.lines = []
    def emit(self, r): self.lines.append((time.monotonic(), r.levelname, r.getMessage()))


rec = Rec(); lg = logging.getLogger("io.ekbatan.flyway.FlywayMigrator"); lg.addHandler(rec); lg.setLevel(logging.INFO)
root = Path(tempfile.mkdtemp()); d = root / "m"; d.mkdir()
(d / "V1__a.sql").write_text("CREATE TABLE a (x int);")

pg = PostgresContainer("postgres:17", username="postgres", password=PW, dbname="n6").with_bind_ports(5432, 58700)
my = MySqlContainer("mysql:8.4", root_password=PW, dbname="n6").with_bind_ports(3306, 58701)
pg.start(); my.start()
try:
    # ---- PostgreSQL: wait past 50 s --------------------------------------------------------
    holder = psycopg.connect(host="localhost", port=58700, user="postgres", password=PW, dbname="n6", autocommit=True)
    from ekbatan._yoyo import POSTGRES_LOCK_KEY
    holder.execute("SELECT pg_advisory_lock(%s)", (POSTGRES_LOCK_KEY,))
    layout = ShardingConfig.single("postgresql+psycopg://localhost:58700/n6", username="postgres", password=PW)
    out = {}
    def run():
        t = time.monotonic()
        try: out["r"] = migrate(layout, d)[0].migrate_result.migrations
        except Exception as e: out["r"] = f"{type(e).__name__}: {e}"[:200]
        out["t"] = time.monotonic() - t
    th = threading.Thread(target=run); start = time.monotonic(); th.start()
    time.sleep(65)
    probe("pg.still_waiting_at_65s", th.is_alive())
    holder.execute("SELECT pg_advisory_unlock(%s)", (POSTGRES_LOCK_KEY,)); holder.close()
    th.join(30)
    probe("pg.result_after_release", out.get("r"))
    probe("pg.waited_s", round(out.get("t", -1)))
    probe("pg.wait_log", [(round(t - start), lvl, m) for t, lvl, m in rec.lines if "wait" in m])

    # ---- MySQL --------------------------------------------------------------------------------
    rec.lines.clear()
    mlayout = ShardingConfig.single("mysql+pymysql://127.0.0.1:58701/n6", username="root", password=PW)
    admin = pymysql.connect(host="127.0.0.1", port=58701, user="root", password=PW, autocommit=True)
    seen = {}
    def watch():
        for _ in range(200):
            c = admin.cursor(); c.execute("SELECT OBJECT_NAME FROM performance_schema.metadata_locks WHERE OBJECT_TYPE='USER LEVEL LOCK'")
            for (n,) in c.fetchall(): seen[n] = 1
            time.sleep(0.01)
    d2 = root / "m2"; d2.mkdir()
    (d2 / "V1__a.sql").write_text("CREATE TABLE a (x int); DO SLEEP(1);")
    w = threading.Thread(target=watch); w.start(); migrate(mlayout, d2); w.join()
    probe("mysql.lock_names_seen", sorted(seen))
    (d2 / "V2__half.sql").write_text("CREATE TABLE b (x int);\nINSERT INTO no_such VALUES (1);")
    try: migrate(mlayout, d2)
    except Exception as e: probe("mysql.half.error", str(e).splitlines()[0][:400])
    c = admin.cursor(); c.execute("SELECT version, success FROM n6.ekbatan_schema_history ORDER BY installed_rank")
    probe("mysql.half.history", c.fetchall())
    c.execute("SHOW TABLES FROM n6"); probe("mysql.half.tables", sorted(r[0] for r in c.fetchall()))
    (d2 / "V2__half.sql").write_text("CREATE TABLE IF NOT EXISTS b (x int);")
    try: migrate(mlayout, d2); probe("mysql.half.next_run", "ran")
    except Exception as e: probe("mysql.half.next_run", str(e).splitlines()[1][:300])
    from ekbatan.migrations import repair
    repair(mlayout, d2)
    probe("mysql.after_repair", migrate(mlayout, d2)[0].migrate_result.migrations)
    # read_only
    c.execute("SET GLOBAL read_only = 1")
    (d2 / "V3__c.sql").write_text("CREATE TABLE c (x int);")
    try: migrate(mlayout, d2); probe("mysql.read_only", "migrated!")
    except Exception as e: probe("mysql.read_only", f"{type(e).__name__}: {str(e).splitlines()[0][:250]}")
    c.execute("SET GLOBAL read_only = 0")
    probe("password_in_any_log", any(PW in m for _, _, m in rec.lines))
finally:
    pg.stop(); my.stop()
