"""Creates area N5's tables on the three databases from Java's own migration files.

PostgreSQL gets two databases, `testdb` (shard 0) and `shard1` (shard 1), for the sharded flows.
Lock waits are bounded at 4 s on every database so a self-deadlock ends instead of hanging:
PostgreSQL waits for ever by default, MySQL and MariaDB for 50 s.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import MetaData, create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import MARKER, PORTS, SCHEMES, scheduled_tasks_table  # noqa: E402

JAVA = Path(
    "/private/tmp/claude-501/-Users-mike-workspace-ekbatan/73e3c410-5a5d-4b46-86a8-40a1e2b99616/scratchpad/r2java-N5"
    "/ekbatan-integration-tests/local-event-handler"
)


def statements(path: Path) -> list[str]:
    return [s.strip() for s in path.read_text().split(";") if s.strip()]


def engine(dialect: str, db: str, user: str = "test"):
    return create_engine(
        f"{SCHEMES[dialect]}://{user}:{MARKER}@localhost:{PORTS[dialect]}/{db}", isolation_level="AUTOCOMMIT"
    )


def migrate(dialect: str, db: str) -> None:
    folder = JAVA / ("pg" if dialect == "pg" else dialect) / "src/test/resources/db/migration"
    e = engine(dialect, db)
    with e.connect() as c:
        if dialect == "pg":
            c.execute(text("drop schema if exists eventlog cascade"))
        else:
            c.execute(text("drop database if exists eventlog"))
        for t in ("widgets", "notes", "audit_entries", "side_rows", "scheduled_tasks"):
            c.execute(text(f"drop table if exists {t}"))
        for f in sorted(folder.glob("V*.sql")):
            for s in statements(f):
                c.execute(text(s))
        c.execute(text("create table side_rows (id int primary key, note varchar(64))"))
    scheduled_tasks_table(md := MetaData()).create(e)
    _ = md
    e.dispose()
    print(f"migrated {dialect}/{db}")


def main() -> None:
    pg = engine("pg", "postgres")
    with pg.connect() as c:
        for db in ("shard1",):
            c.execute(text(f"drop database if exists {db} with (force)"))
            c.execute(text(f"create database {db}"))
        c.execute(text("alter role test set lock_timeout = '4s'"))
    pg.dispose()
    for d in ("mysql", "mariadb"):
        root = engine(d, "testdb", "root")
        with root.connect() as c:
            c.execute(text("grant all privileges on *.* to 'test'@'%'"))
            c.execute(text("flush privileges"))
            c.execute(text("set global innodb_lock_wait_timeout = 4"))
            c.execute(text("set global max_connections = 400"))
        root.dispose()
    migrate("pg", "testdb")
    migrate("pg", "shard1")
    migrate("mysql", "testdb")
    migrate("mariadb", "testdb")


if __name__ == "__main__":
    main()
