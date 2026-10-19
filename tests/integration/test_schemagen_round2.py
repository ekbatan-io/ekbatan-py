"""Round 2's two schemagen bugs, each with a test that failed on the generator before this one.

These use only `generate(migrations, out, url=...)`, which the old generator had too, so they
were run against it (`git show d9bfb35:src/ekbatan/schemagen.py` put back in place): 7 of the 8
fail there - the N2 #4 ones as a module that does not import (on SQLite the common types
imported already, so that one passes on both), the N2 #2 ones as a uuid column that will not
hold or find a plain text id.

    uv run pytest tests/integration/test_schemagen_round2.py --dialects=all
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select

from backends import Backend
from ekbatan.schemagen import generate
from schemagen_support import engine, fresh, load, migrations


def _family(dialect: str) -> str:
    return "mysql" if dialect == "mariadb" else dialect


def _an_id(dialect: str) -> Any:
    return uuid.uuid4() if dialect != "sqlite" else uuid.uuid4().hex


def _now() -> datetime:
    return datetime(2026, 1, 2, 3, 4, 5)


COMMON = {
    "postgres": """
CREATE TYPE status AS ENUM ('NEW', 'DONE');
CREATE TABLE common (id UUID PRIMARY KEY, flag BOOLEAN NOT NULL, status status NOT NULL,
    raw BYTEA, note TEXT, tags TEXT[], when_at TIMESTAMP NOT NULL);
""",
    "mysql": """
CREATE TABLE common (id CHAR(36) PRIMARY KEY, flag BOOLEAN NOT NULL,
    status ENUM('NEW', 'DONE') NOT NULL, raw BLOB, small TINYBLOB, note LONGTEXT, tiny TINYINT,
    when_at DATETIME(6) NOT NULL);
""",
    "sqlite": """
CREATE TABLE common (id CHAR(32) PRIMARY KEY, flag BOOLEAN NOT NULL, raw BLOB, note TEXT,
    when_at DATETIME NOT NULL);
""",
}


def test_round2_n2_4_a_schema_with_common_types_imports(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """N2 #4: a BOOLEAN on MySQL or MariaDB (TINYINT), an ENUM, BYTEA, LONGTEXT, TINYBLOB, an
    array: the old generator imported each from `sqlalchemy`, which has none of them, or wrote
    `ARRAY()` without its item type - and the application could not import its own schema."""
    source = migrations(tmp_path / "migrations", COMMON[_family(dialect)])
    out = tmp_path / "app" / "schema.py"
    url = fresh(backend)

    generate(source, out, url=url)
    module = load(out)

    db = engine(url)
    try:
        with db.begin() as connection:
            row: dict[str, Any] = {
                "id": _an_id(dialect),
                "flag": True,
                "raw": b"\x00\xff",
                "note": "n",
                "when_at": _now(),
            }
            if dialect != "sqlite":
                row["status"] = "DONE"
            if dialect == "postgres":
                row["tags"] = ["a", "b"]
            connection.execute(insert(module.COMMON).values(row))
            back = connection.execute(select(module.COMMON)).mappings().one()
    finally:
        db.dispose()
    assert back["flag"] is True
    assert back["raw"] == b"\x00\xff"
    if dialect == "postgres":
        assert back["tags"] == ["a", "b"]


def test_round2_n2_2_a_36_character_text_id_stays_text(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """N2 #2: a `VARCHAR(36)` named `*_id` was turned into a uuid column on every database, and
    on PostgreSQL `WHERE ext_id = 'abc'` then failed ("operator does not exist: character
    varying = uuid"). Java forces only `CHAR(36)`, only on MySQL; so does this."""
    family = _family(dialect)
    key = {"postgres": "UUID", "mysql": "CHAR(36)", "sqlite": "CHAR(32)"}[family]
    source = migrations(
        tmp_path / "migrations",
        f"CREATE TABLE refs (id {key} PRIMARY KEY, ext_id VARCHAR(36) NOT NULL);",
    )
    out = tmp_path / "app" / "schema.py"
    url = fresh(backend)

    generate(source, out, url=url)
    module = load(out)

    db = engine(url)
    try:
        with db.begin() as connection:
            connection.execute(
                insert(module.REFS).values(id=_an_id(dialect), ext_id="ORDER-2026-0001")
            )
            found = connection.execute(
                select(module.REFS.c.ext_id).where(module.REFS.c.ext_id == "ORDER-2026-0001")
            ).scalar_one()
    finally:
        db.dispose()
    assert found == "ORDER-2026-0001"
