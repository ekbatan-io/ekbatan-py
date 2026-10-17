"""PARITY-PROBE F helpers. Mirrors ParityF.java: same databases, same keys, same row format.

The containers are started by hand (see README in F-migrations.md): PostgreSQL 17 on 55432,
MySQL 9.4.0 on 53306, MariaDB 11.4 on 53307, all with a throwaway password that is never printed.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import psycopg
import pymysql

from ekbatan.config import DataSourceConfig

SQL = Path(__file__).resolve().parent / "sql"
PW = "parity-pw-F"
HISTORY = "ekbatan_schema_history"

PORTS = {"pg": 55432, "mysql": 53306, "maria": 53307}


def probe(key: str, value: object) -> None:
    if isinstance(value, (list, tuple)):
        value = "[" + ", ".join(str(v) for v in value) + "]"
    print(f"PROBE {key}: {value}", flush=True)


def user(dialect: str) -> str:
    return "postgres" if dialect == "pg" else "root"


def url(dialect: str, db: str, query: str = "") -> str:
    scheme = {"pg": "postgresql+psycopg", "mysql": "mysql+pymysql", "maria": "mariadb+pymysql"}[
        dialect
    ]
    return f"{scheme}://localhost:{PORTS[dialect]}/{db}{query}"


def cfg(dialect: str, db: str, query: str = "") -> DataSourceConfig:
    return DataSourceConfig(url=url(dialect, db, query), username=user(dialect), password=PW)


def connect(dialect: str, db: str | None):
    if dialect == "pg":
        return psycopg.connect(
            host="localhost",
            port=PORTS[dialect],
            user="postgres",
            password=PW,
            dbname=db or "postgres",
            autocommit=True,
        )
    return pymysql.connect(
        host="127.0.0.1",
        port=PORTS[dialect],
        user="root",
        password=PW,
        database=db or None,
        autocommit=True,
    )


def fresh(dialect: str, db: str) -> None:
    c = connect(dialect, None)
    try:
        cur = c.cursor()
        if dialect == "pg":
            cur.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
        else:
            cur.execute(f"DROP DATABASE IF EXISTS {db}")
        cur.execute(f"CREATE DATABASE {db}")
    finally:
        c.close()


def execute(dialect: str, db: str, sql: str) -> None:
    c = connect(dialect, db)
    try:
        c.cursor().execute(sql)
    finally:
        c.close()


def _s(v: object) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def query(dialect: str, db: str, sql: str) -> list[str]:
    c = connect(dialect, db)
    try:
        cur = c.cursor()
        cur.execute(sql)
        return ["|".join(_s(v) for v in row) for row in cur.fetchall()]
    finally:
        c.close()


def _schema(dialect: str) -> str:
    return "current_schema()" if dialect == "pg" else "DATABASE()"


def tables(dialect: str, db: str) -> list[str]:
    return query(
        dialect,
        db,
        "SELECT table_name FROM information_schema.tables WHERE table_schema = "
        f"{_schema(dialect)} AND table_name NOT LIKE '%schema_history' ORDER BY table_name",
    )


def has_history(dialect: str, db: str, table: str = HISTORY) -> bool:
    return bool(
        query(
            dialect,
            db,
            "SELECT 1 FROM information_schema.tables WHERE table_schema = "
            f"{_schema(dialect)} AND table_name = '{table}'",
        )
    )


def history(dialect: str, db: str, table: str = HISTORY) -> list[str]:
    if not has_history(dialect, db, table):
        return ["<no history table>"]
    return query(
        dialect,
        db,
        "SELECT installed_rank, version, description, type, script, checksum, installed_by, "
        "CASE WHEN execution_time >= 0 THEN 'time>=0' ELSE 'time<0' END, "
        "CASE WHEN success THEN 'true' ELSE 'false' END "
        f"FROM {table} ORDER BY installed_rank",
    )


def columns(dialect: str, db: str, table: str = HISTORY) -> str:
    return ", ".join(
        query(
            dialect,
            db,
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE "
            f"table_schema = {_schema(dialect)} AND table_name = '{table}' "
            "ORDER BY ordinal_position",
        )
    )


def msg(e: BaseException) -> str:
    m = re.sub(r"\s+", " ", str(e)).strip()
    if len(m) > 400:
        m = m[:400] + "..."
    return f"{type(e).__name__}: {m}"


class Captured(logging.Handler):
    """Every record from the migrator's logger, with the MDC attributes."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)
        text = re.sub(r"\s+", " ", record.getMessage())
        self.lines.append(
            f"{record.levelname}|{record.name}|{getattr(record, 'ekbatanShard', None)}|"
            f"{getattr(record, 'ekbatanTarget', None)}|{text}"
        )

    def __enter__(self) -> Captured:
        root = logging.getLogger("io.ekbatan")
        root.setLevel(logging.DEBUG)
        root.addHandler(self)
        return self

    def __exit__(self, *exc: object) -> None:
        logging.getLogger("io.ekbatan").removeHandler(self)

    def warnings(self) -> list[str]:
        return [
            line.rsplit("|", 1)[1]
            for line in self.lines
            if line.startswith(("WARNING|", "ERROR|", "CRITICAL|"))
        ]
