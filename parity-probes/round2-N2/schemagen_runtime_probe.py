"""Area N2: what the column types schemagen chose do at run time, for the columns where they
differ from the type jOOQ codegen chose (see jooq-*.columns.txt). Python only - the Java side is
the generated jOOQ type (OffsetDateTime, String, native UUID), whose behaviour is jOOQ's.

Columns are declared exactly as schemagen emitted them in py-schema/<dialect>/schema.py.
Fixed ports 58301 (PostgreSQL) and 58302 (MariaDB). Containers are removed at the end.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, MetaData, Table, create_engine, select, text
from testcontainers.mysql import MySqlContainer
from testcontainers.postgres import PostgresContainer

from ekbatan.schema import timestamp_column, uuid_column


def p(key: str, value: object) -> None:
    print(f"PROBE {key}: {value}")


def attempt(fn):
    try:
        return f"OK {fn()}"
    except Exception as e:  # noqa: BLE001
        return f"ERR {type(e).__name__}: {str(e).splitlines()[0][:160]}"


def postgres() -> None:
    pg = PostgresContainer("postgres:17-alpine", driver="psycopg").with_bind_ports(5432, 58301)
    pg.start()
    try:
        url = pg.get_connection_url()
        md = MetaData()
        t = Table(
            "probe_items",
            md,
            Column("id", uuid_column(), primary_key=True),
            Column("ext_id", uuid_column()),  # VARCHAR(36) in the migration; jOOQ: String
            Column("zoned_ts", timestamp_column()),  # TIMESTAMPTZ; jOOQ: OffsetDateTime
        )
        setup = create_engine(url)
        with setup.begin() as c:
            c.execute(text("CREATE TABLE probe_items (id UUID PRIMARY KEY, ext_id VARCHAR(36), zoned_ts TIMESTAMPTZ)"))
        setup.dispose()

        instant = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
        for zone in ("UTC", "Asia/Tehran"):
            engine = create_engine(url, connect_args={"options": f"-c TimeZone={zone}"})
            rid = uuid.uuid4()
            with engine.begin() as c:
                c.execute(t.insert().values(id=rid, zoned_ts=instant))
                back = c.execute(select(t.c.zoned_ts).where(t.c.id == rid)).scalar_one()
                stored = c.execute(
                    text("SELECT to_char(zoned_ts AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') FROM probe_items WHERE id = :i"),
                    {"i": rid},
                ).scalar_one()
            p(f"pg.timestamptz.session_{zone}.wrote", instant.isoformat())
            p(f"pg.timestamptz.session_{zone}.storedUtc", stored)
            p(f"pg.timestamptz.session_{zone}.readBack", back.isoformat())
            p(f"pg.timestamptz.session_{zone}.equal", back == instant)
            engine.dispose()

        engine = create_engine(url)
        with engine.begin() as c:
            c.execute(text("INSERT INTO probe_items (id, ext_id) VALUES (gen_random_uuid(), 'not-a-uuid-but-36-characters-long!!!')"))
        with engine.connect() as c:
            p("pg.varchar36Id.readNonUuidText", attempt(lambda: c.execute(select(t.c.ext_id).where(t.c.ext_id.is_not(None))).scalar_one()))
        with engine.connect() as c:
            u = uuid.uuid4()
            p("pg.varchar36Id.whereEqualsUuid", attempt(lambda: c.execute(select(t.c.id).where(t.c.ext_id == u)).all()))
        with engine.begin() as c:
            u = uuid.uuid4()
            p("pg.varchar36Id.insertUuid", attempt(lambda: c.execute(t.insert().values(id=uuid.uuid4(), ext_id=u)).rowcount))
        engine.dispose()
    finally:
        pg.stop()


def mariadb() -> None:
    db = MySqlContainer("mariadb:11.8", dialect="pymysql", username="root").with_bind_ports(3306, 58302)
    db.start()
    try:
        _, _, rest = db.get_connection_url().partition("://")
        url = f"mariadb+pymysql://{rest}"
        md = MetaData()
        t = Table("probe_items", md, Column("id", uuid_column(), primary_key=True), Column("owner_id", uuid_column()))
        engine = create_engine(url)
        with engine.begin() as c:
            c.execute(text("CREATE TABLE probe_items (id UUID PRIMARY KEY, owner_id UUID)"))
        rid, owner = uuid.uuid4(), uuid.uuid4()
        with engine.begin() as c:
            p("mariadb.nativeUuid.insert", attempt(lambda: c.execute(t.insert().values(id=rid, owner_id=owner)).rowcount))
        with engine.connect() as c:
            row = c.execute(select(t).where(t.c.owner_id == owner)).one()
            p("mariadb.nativeUuid.whereEquals.found", row.id == rid)
            p("mariadb.nativeUuid.readType", type(row.id).__name__)
            p("mariadb.nativeUuid.rawText", c.execute(text("SELECT CAST(id AS CHAR) FROM probe_items")).scalar_one() == str(rid))
        engine.dispose()
    finally:
        db.stop()


if __name__ == "__main__":
    postgres()
    mariadb()
