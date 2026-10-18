"""After a database restart, how many requests fail? (Round 2, N6 'needs you' 1.)"""
import subprocess, time
from sqlalchemy import text
from testcontainers.postgres import PostgresContainer
from ekbatan import DataSourceConfig, DatabaseRegistry, ShardIdentifier, ShardingConfig

MARK = "N6-Secret-Marker-pw"
pg = PostgresContainer("postgres:17", username="app", password=MARK, dbname="w").with_bind_ports(5432, 58763)
pg.start()
try:
    url = "postgresql+psycopg://localhost:58763/w"
    layout = ShardingConfig.single(url, username="app", password=MARK, pool_size=3)
    with DatabaseRegistry.from_config(layout) as registry:
        with registry.default.connection() as conn:
            conn.execute(text("select 1"))
        time.sleep(1)
        subprocess.run(["docker", "restart", pg.get_wrapped_container().id], check=True, capture_output=True)
        time.sleep(3)
        failed = 0
        for _ in range(9):
            try:
                with registry.default.connection() as conn:
                    conn.execute(text("select 1"))
            except Exception:
                failed += 1
        print("PROBE requests failed after restart:", failed, "of 9")
finally:
    pg.stop()
