"""N6 / migrations: the snippets of docs/database/migrations.md run as written (paths adapted)."""
import tempfile, sqlite3, os
from pathlib import Path

def probe(k, v): print(f"PROBE {k}: {v}", flush=True)
def err(fn):
    try: r = fn(); return f"ok {r!r}"[:200]
    except Exception as e: return f"{type(e).__name__}: {e}"[:250]

root = Path(tempfile.mkdtemp()); os.chdir(root)

# "Writing the first migration" - verbatim
from sqlalchemy import MetaData
from ekbatan.migrations import emit_ddl
from ekbatan.schema import notifications_table, outbox_table, scheduled_tasks_table
metadata = MetaData()
outbox_table(metadata)
notifications_table(metadata)
scheduled_tasks_table(metadata)
ddl = emit_ddl(metadata, "postgresql")
probe("emit_ddl_tables", [l.split()[2] for l in ddl.splitlines() if l.startswith("CREATE TABLE")])
probe("emit_ddl_stable", ddl == emit_ddl(metadata, "postgresql"))

# "Or, for a test"
from ekbatan.config import ShardingConfig
from ekbatan.persistence import DatabaseRegistry
from ekbatan.migrations import migrate_registry, migrate, info
layout = ShardingConfig.single(f"sqlite:///{root/'t.db'}")
registry = DatabaseRegistry.from_config(layout)
probe("migrate_registry", err(lambda: migrate_registry(registry, metadata)))
c = sqlite3.connect(root / "t.db"); probe("migrate_registry_tables", sorted(r[0] for r in c.execute("select name from sqlite_master where type='table'"))); c.close()

# no location
probe("no_location", err(lambda: migrate(layout)))
probe("missing_dir", err(lambda: migrate(layout, "nope")))

# history column types + installed_on UTC text
Path("migrations").mkdir(); Path("migrations/V1__a.sql").write_text("CREATE TABLE a (x int);")
single_database = ShardingConfig.single(f"sqlite:///{root/'h.db'}")
migrate(single_database, "migrations")
c = sqlite3.connect(root / "h.db")
probe("installed_on", c.execute("select installed_on, typeof(installed_on) from ekbatan_schema_history").fetchone()); c.close()

# info snippet verbatim, and info takes no lock / changes nothing
Path("migrations/V2__b.sql").write_text("CREATE TABLE b (x int);")
(shard,) = info(single_database, "migrations")
for m in shard.migrations:
    print("   ", m.category, m.version, m.description, m.state.value)
import fcntl
h = open(root / "h.db-ekbatan-migrate.lock", "a+"); fcntl.flock(h.fileno(), fcntl.LOCK_EX)
import threading
res = {}
t = threading.Thread(target=lambda: res.setdefault("r", info(single_database, "migrations")[0].schema_version)); t.start(); t.join(5)
probe("info_while_lock_held_returns", (not t.is_alive(), res.get("r")))
fcntl.flock(h.fileno(), fcntl.LOCK_UN); h.close()
c = sqlite3.connect(root / "h.db"); probe("info_changed_nothing", c.execute("select count(*) from ekbatan_schema_history").fetchone()[0]); c.close()
