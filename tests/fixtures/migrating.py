"""Helpers for the migrator's tests, including migrating from another process.

The lock is between processes, so the tests about several migrators at once use processes:
threads in one interpreter would share a lock file and a GIL, and prove less than they appear to.
`spawn`, not `fork`, so a child starts clean rather than inheriting the parent's connections.
"""

from __future__ import annotations

import hashlib
import multiprocessing
import re
import time
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text

CONTEXT = multiprocessing.get_context("spawn")


def fresh(backend: Any, request: Any, suffix: str = "") -> str:
    """A url for a database nobody else uses."""
    base = re.sub(r"[^a-z0-9]+", "_", request.node.name.lower())[:36].strip("_")
    digest = hashlib.sha1(request.node.nodeid.encode()).hexdigest()[:6]
    return str(backend.url(f"m_{base}_{digest}{suffix}"))


def query(url: str, sql: str) -> list[Any]:
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            return list(result) if result.returns_rows else []
    finally:
        engine.dispose()


def tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def sleep_sql(dialect: str, seconds: float) -> str:
    """A statement that takes about ``seconds`` - SQLite has no sleep, so it counts instead."""
    if dialect == "postgres":
        return f"SELECT pg_sleep({seconds});"
    if dialect in ("mysql", "mariadb"):
        return f"SELECT SLEEP({seconds});"
    rows = int(seconds * 7_500_000)
    return (
        f"WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < {rows}) "
        f"SELECT count(*) FROM c;"
    )


def run(url: str, location: str, results: Any, name: str, go: Any = None) -> None:
    """Migrate one database and report what happened on `results`, whatever happened."""
    from ekbatan.config import ShardingConfig
    from ekbatan.migrations import migrate

    if go is not None:
        go.wait()
    began = time.monotonic()
    try:
        (outcome,) = migrate(ShardingConfig.single(url), Path(location))
        results.put((name, "ok", list(outcome.migrate_result.migrations), time.monotonic() - began))
    except BaseException as error:  # reported, never raised: the parent decides
        results.put((name, "failed", f"{type(error).__name__}: {error}", time.monotonic() - began))


def start(url: str, location: Path, results: Any, name: str, go: Any = None) -> Any:
    process = CONTEXT.Process(target=run, args=(url, str(location), results, name, go))
    process.start()
    return process
