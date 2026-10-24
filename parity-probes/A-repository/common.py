"""Shared setup for the area A probes: a container per dialect, Java's own `dummies` DDL, and
the suite's Wallet model/mapping (the counterpart of Java's Dummy / DummyRepository).

Never prints a password: only PROBE lines and normalised messages are printed.
"""

from __future__ import annotations

import re
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from sqlalchemy import (  # noqa: E402
    CHAR,
    BigInteger,
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    create_engine,
    text,
)

from backends import start  # noqa: E402
from ekbatan.ids import ShardedId  # noqa: E402
from ekbatan.persistence import DatabaseRegistry, TransactionManager, DataSource  # noqa: E402
from ekbatan.schema import json_column, uuid_column  # noqa: E402
from fixtures.domain import Wallet  # noqa: E402
from fixtures.logins import data_source  # noqa: E402

T0 = datetime(2024, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
UUIDP = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

metadata = MetaData()
DUMMIES = Table(
    "dummies",
    metadata,
    Column("id", uuid_column(), primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("state", String(24), nullable=False),
    Column("owner_id", uuid_column(), nullable=False),
    Column("currency", CHAR(3), nullable=False),
    Column("balance", Numeric(19, 4), nullable=False),
    Column("created_date", DateTime, nullable=False),
    Column("updated_date", DateTime, nullable=False),
    Column("reward_points", Integer),
    Column("aliases", json_column()),
)

# Java's V0001__create_junit_dummy_table_<dialect>.sql, verbatim apart from comments.
DDL = {
    "postgres": """CREATE TABLE dummies (
        id UUID PRIMARY KEY, version BIGINT NOT NULL, state VARCHAR(24) NOT NULL,
        owner_id UUID NOT NULL, currency CHAR(3) NOT NULL, balance DECIMAL(19, 4) NOT NULL,
        created_date TIMESTAMP NOT NULL, updated_date TIMESTAMP NOT NULL,
        reward_points INTEGER, aliases JSONB)""",
    "mysql": """CREATE TABLE dummies (
        id CHAR(36) CHARACTER SET ascii NOT NULL, version BIGINT NOT NULL,
        state VARCHAR(24) NOT NULL, owner_id CHAR(36) CHARACTER SET ascii NOT NULL,
        currency CHAR(3) NOT NULL, balance DECIMAL(19, 4) NOT NULL,
        created_date DATETIME(6) NOT NULL, updated_date DATETIME(6) NOT NULL,
        reward_points INT, aliases JSON, PRIMARY KEY (id),
        KEY idx_dummies_owner_id (owner_id))""",
    "mariadb": """CREATE TABLE dummies (
        id UUID PRIMARY KEY, version BIGINT NOT NULL, state VARCHAR(24) NOT NULL,
        owner_id UUID NOT NULL, currency CHAR(3) NOT NULL, balance DECIMAL(19, 4) NOT NULL,
        created_date DATETIME(6) NOT NULL, updated_date DATETIME(6) NOT NULL,
        reward_points INT, aliases JSON)""",
}


class Out:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def p(self, key: str, value: Any) -> None:
        s = str(value).split("\n")[0]
        print(f"PROBE {self.prefix}.{key}: {UUIDP.sub('<uuid>', s)}", flush=True)

    def err(self, key: str, fn: Callable[[], Any]) -> None:
        try:
            v = fn()
            self.p(key, f"no error -> {v}")
        except Exception as e:  # noqa: BLE001
            self.p(f"{key}.err", type(e).__name__)
            self.p(f"{key}.msg", str(e))

    def err_class(self, key: str, fn: Callable[[], Any]) -> None:
        try:
            fn()
            self.p(key, "no error")
        except Exception as e:  # noqa: BLE001
            self.p(f"{key}.err", type(e).__name__)


def jbool(b: bool) -> str:
    return "true" if b else "false"


def ts(dt: datetime) -> str:
    """Instant.toString-like for an aware UTC value; a naive value prints without the Z."""
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")


def fresh(owner: Any = None, id: Any = None) -> Wallet:
    return Wallet.open(
        id=id if id is not None else ShardedId[Wallet].random(),
        owner_id=owner if owner is not None else uuid4(),
        currency="EUR",
        balance=Decimal("10"),
        created_date=T0,
    )


def plain(d: Decimal) -> str:
    n = d.normalize()
    return format(n, "f")


def started(dialect: str):
    backend = start(dialect, Path(tempfile.mkdtemp()))
    return backend


def database(backend: Any, name: str) -> str:
    """A fresh logical database holding Java's dummies table; returns its url."""
    url = backend.url(name)
    eng = create_engine(url)
    try:
        with eng.begin() as c:
            c.execute(text(DDL[backend.dialect]))
    finally:
        eng.dispose()
    return url


def manager(shard: Any, url: str) -> TransactionManager:
    return TransactionManager(shard, DataSource.from_config(data_source(url, pool_size=10)))


def single_registry(url: str) -> DatabaseRegistry:
    from ekbatan.sharding import ShardIdentifier

    return DatabaseRegistry.builder().with_database(manager(ShardIdentifier(0, 0), url)).build()
