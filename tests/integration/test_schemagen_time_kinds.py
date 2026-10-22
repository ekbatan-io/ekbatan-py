"""Each time column gets its kind in the record, on each database - and the values agree.

One migration per database declares every time column it has. schemagen generates the module
with one rule of the project's own - `reminders.opens` forced to `LocalDateTime` - and then:

- the record says `Instant` for every zone-less timestamp (PostgreSQL `TIMESTAMP`, MySQL and
  MariaDB `DATETIME` *and* `TIMESTAMP`, SQLite `DATETIME`), `LocalDateTime` for the forced
  column, `OffsetDateTime` for PostgreSQL's `TIMESTAMPTZ`, and Python's own `time` and `date`;
- each value read back through the sync driver and the async one is of its kind's sort: naive
  for `Instant` and `LocalDateTime`, aware for `OffsetDateTime`;
- a `TIMESTAMPTZ` keeps the moment, not the offset: a `+03:30` value reads back as the same
  moment at the session's offset (UTC here) - what the schemagen page says;
- the module, and code assigning its record's values to labelled variables, pass
  `mypy --strict`, and assigning the `LocalDateTime` column to an `Instant` does not.

    uv run pytest tests/integration/test_schemagen_time_kinds.py --dialects=all
"""

from __future__ import annotations

import asyncio
import datetime as dt
import typing
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import insert, select

from backends import Backend
from ekbatan.config import DataSourceConfig, split_login
from ekbatan.schemagen import ForcedType, SchemagenConfig, generate
from ekbatan.time import Instant, LocalDateTime, OffsetDateTime
from records import hints as record_hints
from schemagen_support import fresh, load, migrations

NAIVE = dt.datetime(2026, 3, 1, 9, 30, 0, 123456)
TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))

#: Per database: (column, SQL type, the kind its record must say, a value to write).
COLUMNS: dict[str, list[tuple[str, str, Any, Any]]] = {
    "postgres": [
        ("at", "TIMESTAMP", Instant, NAIVE),
        ("opens", "TIMESTAMP", LocalDateTime, NAIVE),
        ("zoned", "TIMESTAMPTZ", OffsetDateTime, NAIVE.replace(tzinfo=TEHRAN)),
        (
            "zoned_3",
            "TIMESTAMPTZ(3)",
            OffsetDateTime,
            NAIVE.replace(microsecond=123000, tzinfo=TEHRAN),
        ),
        ("day", "DATE", dt.date, dt.date(2026, 3, 1)),
        ("clock", "TIME", dt.time, dt.time(9, 30)),
    ],
    "mysql": [
        ("at", "DATETIME(6)", Instant, NAIVE),
        ("opens", "DATETIME(6)", LocalDateTime, NAIVE),
        ("stamped", "TIMESTAMP(6) NULL", Instant, NAIVE),
        ("day", "DATE", dt.date, dt.date(2026, 3, 1)),
        ("clock", "TIME", dt.time, dt.time(9, 30)),
    ],
    "sqlite": [
        ("at", "DATETIME", Instant, NAIVE),
        ("opens", "DATETIME", LocalDateTime, NAIVE),
        ("day", "DATE", dt.date, dt.date(2026, 3, 1)),
        ("clock", "TIME", dt.time, dt.time(9, 30)),
    ],
}
COLUMNS["mariadb"] = COLUMNS["mysql"]

CONFIG = SchemagenConfig(
    forced_types=(
        ForcedType(
            name="LocalDateTime",
            include_types="TIMESTAMP|DATETIME",
            include_expression=r"reminders\.opens",
        ),
    )
)


@pytest.fixture
def generated(backend: Backend, dialect: str, tmp_path: Path) -> tuple[str, Path, Any]:
    columns = ",\n".join(f"    {name} {sql}" for name, sql, _, _ in COLUMNS[dialect])
    sql = f"CREATE TABLE reminders (\n    pk INTEGER NOT NULL PRIMARY KEY,\n{columns}\n);\n"
    url = fresh(backend, "kinds")
    out = tmp_path / "kinds_app" / "schema.py"
    generate(migrations(tmp_path / "migrations", sql), out, url=url, config=CONFIG)
    return url, out, load(out)


def _hints(module: Any) -> dict[str, Any]:
    return record_hints(module.RemindersRecord, vars(module))


def test_each_time_column_gets_its_kind(generated: tuple[str, Path, Any], dialect: str) -> None:
    _, _, module = generated
    hints = _hints(module)
    expected = {name: kind for name, _, kind, _ in COLUMNS[dialect]}
    got = {name: _bare(hints[name]) for name in expected}
    assert got == expected


def _bare(hint: Any) -> Any:
    members = [a for a in typing.get_args(hint) if a is not type(None)]
    return members[0] if members else hint


def _config(url: str) -> DataSourceConfig:
    plain, username, password = split_login(url)
    return DataSourceConfig(url=plain, username=username, password=password)


def test_values_read_back_are_of_their_kind_s_sort(
    generated: tuple[str, Path, Any], dialect: str
) -> None:
    from ekbatan.aio.persistence import async_engine_for
    from ekbatan.persistence import engine_for

    url, _, module = generated
    table = module.REMINDERS
    row = {name: value for name, _, _, value in COLUMNS[dialect]}
    sync = engine_for(_config(url))
    try:
        with sync.begin() as connection:
            connection.execute(insert(table).values({"pk": 1, **row}))
            through_sync = dict(connection.execute(select(table)).one()._mapping)
    finally:
        sync.dispose()

    async def read() -> dict[str, Any]:
        engine = async_engine_for(_config(url))
        try:
            async with engine.connect() as connection:
                return dict((await connection.execute(select(table))).one()._mapping)
        finally:
            await engine.dispose()

    through_async = asyncio.run(read())
    for mode, back in (("sync", through_sync), ("async", through_async)):
        for name, _, kind, value in COLUMNS[dialect]:
            got = back[name]
            if kind in (Instant, LocalDateTime):
                assert isinstance(got, dt.datetime) and got.tzinfo is None, (mode, name, got)
                assert got == value, (mode, name)
            elif kind is OffsetDateTime:
                assert isinstance(got, dt.datetime) and got.utcoffset() is not None, (mode, name)
                assert got == value, f"{mode} {name}: the same moment"
                assert got.utcoffset() == dt.timedelta(0), (
                    f"{mode} {name}: PostgreSQL keeps the moment, not the +03:30 written; the "
                    f"offset read back is the session's (UTC here)"
                )
            else:
                assert got == value and type(got) is kind, (mode, name, got)


USE = """
from __future__ import annotations

import datetime as dt

from ekbatan.time import Instant, LocalDateTime

from .schema import RemindersRecord


def read(record: RemindersRecord) -> tuple[Instant, LocalDateTime | None, dt.date | None]:
    return record["at"] or Instant(dt.datetime(2026, 1, 1)), record["opens"], record["day"]
"""

WRONG = """
from __future__ import annotations

from ekbatan.time import Instant

from .schema import RemindersRecord


def read(record: RemindersRecord) -> Instant | None:
    return record["opens"]
"""


def test_the_records_type_check_and_hold_the_kinds_apart(
    generated: tuple[str, Path, Any],
) -> None:
    from mypy import api

    _, out, _ = generated
    package = out.parent
    (package / "__init__.py").write_text("")
    (package / "use.py").write_text(USE)
    (package / "wrong.py").write_text(WRONG)
    stdout, stderr, _ = api.run(["--strict", "--no-incremental", "--config-file", "", str(package)])
    assert not stderr, stderr
    errors = [line for line in stdout.splitlines() if ": error:" in line]
    assert all("wrong.py" in line for line in errors), stdout
    assert any("wrong.py:10" in line and "return-value" in line for line in errors), stdout


# -- a time kind the column cannot hold is refused (the time review, T4) -----------------------

#: Per database: a zone-less timestamp, and columns that hold no timestamp at all.
_ZONE_LESS = {"postgres": "TIMESTAMP", "mysql": "DATETIME(6)", "sqlite": "DATETIME"}
_ZONE_LESS["mariadb"] = _ZONE_LESS["mysql"]
_NOT_TIME = ("VARCHAR(20)", "INTEGER", "TEXT", "DATE")


def _refused(backend: Backend, tmp_path: Path, sql_type: str, kind: str) -> str:
    from ekbatan.errors import ValidationError

    config = SchemagenConfig(forced_types=(ForcedType(name=kind, include_expression=r"things\.c"),))
    source = migrations(
        tmp_path / "migrations", f"CREATE TABLE things (id INTEGER PRIMARY KEY, c {sql_type});"
    )
    with pytest.raises(ValidationError) as refused:
        generate(source, tmp_path / "schema.py", url=fresh(backend, "kind"), config=config)
    assert not (tmp_path / "schema.py").exists(), "no module is written"
    return str(refused.value)


def test_offset_date_time_on_a_zone_less_column_is_refused(
    backend: Backend, dialect: str, tmp_path: Path
) -> None:
    """It used to build the column `TIMESTAMP WITH TIME ZONE` and type the record
    `OffsetDateTime`, while the real column reads back naive and keeps no offset."""
    message = _refused(backend, tmp_path, _ZONE_LESS[dialect], "OffsetDateTime")
    assert "things.c" in message and "OffsetDateTime" in message
    assert "has no zone" in message and "Instant" in message and "LocalDateTime" in message


@pytest.mark.parametrize("kind", ["Instant", "LocalDateTime", "OffsetDateTime"])
@pytest.mark.parametrize("sql_type", _NOT_TIME)
def test_a_time_kind_on_a_column_that_holds_no_timestamp_is_refused(
    backend: Backend, tmp_path: Path, sql_type: str, kind: str
) -> None:
    """It used to become `timestamp_column()`, with a note about "fewer than 6 digits"."""
    message = _refused(backend, tmp_path, sql_type, kind)
    assert "things.c" in message and kind in message
    assert "not a timestamp" in message
    assert "fewer than 6 digits" not in message
