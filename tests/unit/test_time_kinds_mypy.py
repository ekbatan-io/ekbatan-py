"""mypy keeps the time kinds apart: the proof that `ekbatan.time`'s labels are worth having.

A small application is written into a temporary directory - an event, a model, and a schema
rendered by schemagen with a zone-less column (an `Instant`), one forced to `LocalDateTime` and
a `TIMESTAMPTZ` (an `OffsetDateTime`) - with one module written right and several wrong, each
in exactly one way:

- an `Instant` passed where a `LocalDateTime` is expected;
- a plain `datetime` passed where an `Instant` is;
- an `OffsetDateTime` passed where an `Instant` is;
- a record's `LocalDateTime` column read into a model's `created_date` (an `Instant`).

The right module also proves that arithmetic keeps the label (`clock.instant() + timedelta`
assigned to an `Instant`) and that the generated records type-check. One `mypy --strict` run:
the right module clean, each wrong one failing on its line.
"""

from __future__ import annotations

import re
import textwrap

import pytest
from sqlalchemy import BIGINT, TIMESTAMP, Column, MetaData, Table
from sqlalchemy.dialects import postgresql

from ekbatan.schemagen import ForcedType, SchemagenConfig, render

MODEL = """
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ekbatan import Model, ModelEvent, ShardedId
from ekbatan.time import Instant, LocalDateTime, OffsetDateTime


class ShiftState(Enum):
    PLANNED = "PLANNED"


@dataclass(frozen=True, kw_only=True)
class Shift(Model[ShardedId["Shift"], ShiftState]):
    opens_at: LocalDateTime
    told_at: OffsetDateTime

    def validate(self) -> None:
        pass


@dataclass(frozen=True, kw_only=True)
class ShiftPlannedEvent(ModelEvent["Shift"]):
    planned_at: Instant
    opens_at: LocalDateTime


def opening(at: LocalDateTime) -> LocalDateTime:
    return at


def planned(at: Instant) -> Instant:
    return at
"""

RIGHT = """
from __future__ import annotations

from datetime import datetime, timedelta

from ekbatan import ShardedId, SystemClock
from ekbatan.time import Instant, LocalDateTime

from .model import Shift, ShiftPlannedEvent, ShiftState, opening, planned
from .schema import ShiftsRecord

clock = SystemClock()
now = clock.instant()
tomorrow: Instant = now + timedelta(days=1)
earlier: Instant = now - timedelta(hours=1)
elapsed: timedelta = tomorrow - now
wall = LocalDateTime(datetime(2026, 3, 1, 9, 0))
event = ShiftPlannedEvent(model_id="s-1", planned_at=tomorrow, opens_at=wall)
planned(now)
opening(wall)


def from_record(record: ShiftsRecord) -> Shift:
    return Shift(
        id=ShardedId[Shift].of(str(record["id"])),
        state=ShiftState.PLANNED,
        version=1,
        created_date=record["created_date"],
        opens_at=record["opens_at"],
        told_at=record["told_at"],
    )
"""

#: (name, the line in RIGHT, what it becomes, the error mypy must give on it)
WRONG = [
    ("instant_as_local", "opening(wall)", "opening(now)", "arg-type"),
    (
        "plain_into_instant",
        "planned(now)",
        "planned(datetime(2026, 3, 1, 9, 0))",
        "arg-type",
    ),
    (
        "offset_as_instant",
        '        created_date=record["created_date"],',
        '        created_date=record["told_at"],',
        "arg-type",
    ),
    (
        "local_record_into_instant",
        '        created_date=record["created_date"],',
        '        created_date=record["opens_at"],',
        "arg-type",
    ),
    (
        "arithmetic_keeps_instant",
        "earlier: Instant = now - timedelta(hours=1)",
        "earlier: LocalDateTime = now - timedelta(hours=1)",
        "assignment",
    ),
]


def _schema() -> str:
    table = Table(
        "shifts",
        MetaData(),
        Column("id", BIGINT(), primary_key=True),
        Column("created_date", TIMESTAMP(), nullable=False),
        Column("opens_at", TIMESTAMP(), nullable=False),
        Column("told_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
    )
    return render(
        [table],
        framework=[],
        migrations_fingerprint="x",
        module=None,
        dialect="postgresql",
        config=SchemagenConfig(
            forced_types=(
                ForcedType(
                    name="LocalDateTime",
                    include_types="TIMESTAMP",
                    include_expression=r"shifts\.opens_at",
                ),
            )
        ),
    )


@pytest.fixture(scope="module")
def checked(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[tuple[int, str]]]:
    from mypy import api

    root = tmp_path_factory.mktemp("time_kinds")
    app = root / "time_kinds_app"
    app.mkdir()
    (app / "__init__.py").write_text("")
    (app / "model.py").write_text(MODEL)
    schema = _schema()
    assert "opens_at: LocalDateTime" in schema and "created_date: Instant" in schema
    assert "told_at: OffsetDateTime" in schema
    (app / "schema.py").write_text(schema)
    files = {"right": RIGHT}
    for name, old, new, _ in WRONG:
        assert RIGHT.count(old) == 1, name
        files[name] = RIGHT.replace(old, new)
    for name, source in files.items():
        (app / f"use_{name}.py").write_text(textwrap.dedent(source))
    stdout, stderr, _ = api.run(
        [
            "--strict",
            "--show-error-codes",
            "--no-error-summary",
            "--cache-dir",
            str(root / ".mypy_cache"),
            "--config-file",
            "",
            str(app),
        ]
    )
    assert not stderr, stderr
    errors: dict[str, list[tuple[int, str]]] = {name: [] for name in files}
    errors["other"] = []
    for line in stdout.splitlines():
        found = re.match(r".*?time_kinds_app[/\\](\w+)\.py:(\d+): error: .*\[([\w-]+)\]$", line)
        if found is None:
            continue
        module, number, code = found.groups()
        name = module.removeprefix("use_") if module.startswith("use_") else "other"
        errors.setdefault(name, []).append((int(number), code))
    errors["_stdout"] = [(0, stdout)]
    return errors


def test_the_records_and_the_right_module_are_clean(
    checked: dict[str, list[tuple[int, str]]],
) -> None:
    assert checked["other"] == [], checked["_stdout"][0][1]
    assert checked["right"] == [], checked["_stdout"][0][1]


@pytest.mark.parametrize(("name", "old", "new", "code"), WRONG, ids=[w[0] for w in WRONG])
def test_each_mix_up_is_a_type_error_on_its_line(
    checked: dict[str, list[tuple[int, str]]], name: str, old: str, new: str, code: str
) -> None:
    source = textwrap.dedent(RIGHT.replace(old, new))
    line = next(i for i, text in enumerate(source.splitlines(), 1) if new.strip() in text)
    assert checked[name], f"mypy accepted {name}:\n{checked['_stdout'][0][1]}"
    assert (line, code) in checked[name], (checked[name], checked["_stdout"][0][1])


def test_a_label_costs_nothing_at_run_time() -> None:
    """`Instant(x)` is `x`: no wrapper, no conversion, no check."""
    from datetime import UTC, datetime

    from ekbatan.time import Instant, LocalDateTime, OffsetDateTime

    aware = datetime(2026, 3, 1, tzinfo=UTC)
    for label in (Instant, LocalDateTime, OffsetDateTime):
        assert label(aware) is aware
