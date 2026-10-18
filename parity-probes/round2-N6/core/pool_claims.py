"""docs/database/sharding.md DataSourceConfig defaults and the Hikari-like pool (EKB-0305), on PostgreSQL."""
import io, logging, time, dataclasses
from common import probe
from testcontainers.postgres import PostgresContainer
from sqlalchemy import text
from ekbatan import DataSourceConfig, ShardingConfig, ShardGroupConfig, ShardMemberConfig, DatabaseRegistry, ShardIdentifier

probe("sharding.md:85 DataSourceConfig defaults", lambda: {f.name: f.default for f in dataclasses.fields(DataSourceConfig) if f.name != "url"})
buf = io.StringIO(); logging.getLogger().addHandler(logging.StreamHandler(buf)); logging.getLogger().setLevel(logging.DEBUG)
MARK = "N6-Secret-Marker-pw"
pg = PostgresContainer("postgres:17", username="app", password=MARK, dbname="w").with_bind_ports(5432, 58761)
pg.start()
try:
    url = f"postgresql+psycopg://localhost:58761/w"
    def reg_for(pw, size=3, timeout=30.0):
        return DatabaseRegistry.from_config(ShardingConfig(default_shard=ShardIdentifier(0, 0), groups=(
            ShardGroupConfig(group=0, name="g", members=(ShardMemberConfig(member=0, configs={"primaryConfig": DataSourceConfig(url=url, username="app", password=pw, pool_size=size, pool_timeout=timeout)}),)),)))
    r = reg_for(MARK)
    time.sleep(1.5)
    with r.default.connection() as c:
        n = c.execute(text("select count(*) from pg_stat_activity where usename='app'")).scalar()
    probe("sharding.md:105 pool of 3 holds 3 open connections a moment after start (all app sessions incl. this one)", lambda: n)
    r.close()
    bad = reg_for("wrong-" + MARK, size=2, timeout=3.0)
    t0 = time.monotonic()
    def first():
        with bad.default.connection() as c:
            c.execute(text("select 1"))
    probe("sharding.md:108 wrong password: caller fails with driver error", first)
    probe("sharding.md:108 ...only when pool_timeout (3 s) is up: waited s", lambda: round(time.monotonic() - t0, 1))
    bad.close()
    probe("sharding.md:111 password never logged", lambda: MARK in buf.getvalue())
    probe("sharding.md:150 repr of config hides password", lambda: MARK in repr(DataSourceConfig(url=url, username="app", password=MARK, data_source_properties={"x": "y"})))
finally:
    pg.stop()
