"""Count PostgreSQL sessions from outside the pool: before, and 1.5 s after a pool_size=3 registry is built."""
import time, psycopg
from testcontainers.postgres import PostgresContainer
from ekbatan import DataSourceConfig, ShardingConfig, ShardGroupConfig, ShardMemberConfig, DatabaseRegistry, ShardIdentifier
MARK = "N6-Secret-Marker-pw"
pg = PostgresContainer("postgres:17", username="app", password=MARK, dbname="w").with_bind_ports(5432, 58762)
pg.start()
try:
    side = psycopg.connect(host="localhost", port=58762, user="app", password=MARK, dbname="w", autocommit=True)
    q = "select count(*) from pg_stat_activity where usename='app' and pid <> pg_backend_pid()"
    print("PROBE sessions before:", side.execute(q).fetchone()[0])
    r = DatabaseRegistry.from_config(ShardingConfig(default_shard=ShardIdentifier(0, 0), groups=(
        ShardGroupConfig(group=0, name="g", members=(ShardMemberConfig(member=0, configs={"primaryConfig": DataSourceConfig(url="postgresql+psycopg://localhost:58762/w", username="app", password=MARK, pool_size=3)}),)),)))
    time.sleep(1.5)
    print("PROBE sessions 1.5 s after pool_size=3 built, no query:", side.execute(q).fetchone()[0])
    r.close(); time.sleep(0.5)
    print("PROBE sessions after close:", side.execute(q).fetchone()[0])
finally:
    pg.stop()
