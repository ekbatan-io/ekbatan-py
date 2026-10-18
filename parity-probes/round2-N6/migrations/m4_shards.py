"""N6 / migrations: the two-shard failure log of docs/database/migrations.md, and shards= refusing a typo."""
import logging, tempfile
from pathlib import Path
from ekbatan.config import DataSourceConfig, ShardingConfig, ShardGroupConfig, ShardMemberConfig
from ekbatan.sharding import ShardIdentifier
from ekbatan.migrations import migrate

def probe(k, v): print(f"PROBE {k}: {v}", flush=True)
root = Path(tempfile.mkdtemp())
GLOBAL, MEXICO = ShardIdentifier(0, 0), ShardIdentifier(1, 0)
def layout(mexico_url):
    return ShardingConfig(default_shard=GLOBAL, groups=(
        ShardGroupConfig(group=0, name="global", members=(ShardMemberConfig(member=0, configs={"primaryConfig": DataSourceConfig(url=f"sqlite:///{root/'g.db'}")}),)),
        ShardGroupConfig(group=1, name="mexico", members=(ShardMemberConfig(member=0, configs={"primaryConfig": DataSourceConfig(url=mexico_url)}),)),
    ))
d = root / "m"; d.mkdir(); (d / "V1__a.sql").write_text("CREATE TABLE a (x int);")
(root / "mx.db").write_bytes(b"")
import sqlite3; c = sqlite3.connect(root / "mx.db"); c.execute("create table other (x int)"); c.commit(); c.close()
lines = []
class H(logging.Handler):
    def emit(self, r): lines.append(f"{r.levelname} {r.name} {r.getMessage()}")
lg = logging.getLogger("io.ekbatan.flyway.FlywayMigrator"); lg.addHandler(H()); lg.setLevel(logging.INFO)
try: migrate(layout(f"sqlite:///{root/'mx.db'}"), d)
except Exception as e: probe("error_last_line", str(e).splitlines()[-1][:300])
for l in lines:
    if l.startswith("ERROR"): probe("log", l)
try: migrate(layout(f"sqlite:///{root/'mx2.db'}"), d, shards=[ShardIdentifier(9, 9)])
except Exception as e: probe("unknown_shard", f"{type(e).__name__}: {e}")
