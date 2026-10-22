"""A datetime in JSON is written as the kind its field declares (ekbatan.time).

Python has one ``datetime`` for Java's three classes; the declaration says which a Java reader
gets. ``Instant`` - naive UTC digits - is written with ``Z`` exactly as Jackson writes an
``Instant``; ``LocalDateTime`` with no zone; ``OffsetDateTime`` with its offset. A plain
``datetime`` field, a ``datetime`` nothing declares, and a value of the wrong sort for its kind
are refused. Reading back gives each kind's own sort, and a round trip is equal.

The byte-for-byte proof is Java's own output: ``parity-probes/G-domain-model/java_formats.txt.gz``
holds 3000 values each of ``Instant``, ``LocalDateTime`` and ``OffsetDateTime`` written by
Ekbatan's default Jackson 3 mapper (``JsonFormatsParityProbeTest.java``).
"""

from __future__ import annotations

import gzip
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, NewType, TypedDict

import pytest

from ekbatan._json import canonical, canonical_event, decode, dumps, loads
from ekbatan.domain import ModelEvent
from ekbatan.errors import ValidationError
from ekbatan.ids import TypedValue
from ekbatan.time import Instant, LocalDateTime, OffsetDateTime

NAIVE = datetime(2026, 3, 1, 9, 30, 0, 120000)
TEHRAN = timezone(timedelta(hours=3, minutes=30))
AWARE = datetime(2026, 3, 1, 13, 0, 0, 120000, TEHRAN)
FORMATS = Path(__file__).resolve().parents[2] / "parity-probes/G-domain-model/java_formats.txt.gz"
EPOCH = datetime(1970, 1, 1)


# -- Java's own text, byte for byte ----------------------------------------------------------


def _java_rows(kind: str) -> list[tuple[list[int], str]]:
    rows = []
    for line in gzip.decompress(FORMATS.read_bytes()).decode().splitlines():
        found, raw, java = line.split("\t")
        if found == kind:
            rows.append(([int(p) for p in raw.split(",")], java))
    return rows


def test_an_instant_is_written_as_jacksons_instant_for_all_3000_of_javas():
    rows = _java_rows("instant")
    assert len(rows) == 3000
    for (sec, micros), java in rows:
        utc_digits = EPOCH + timedelta(seconds=sec, microseconds=micros)  # naive, UTC
        assert dumps(utc_digits, Instant) == java, (sec, micros)
        assert decode(_Box, {"at": loads(java)}).at == utc_digits


def test_a_local_date_time_is_written_as_jacksons_for_all_3000_of_javas():
    rows = _java_rows("ldt")
    assert len(rows) == 3000
    for (sec, micros), java in rows:
        wall = EPOCH + timedelta(seconds=sec, microseconds=micros)
        assert dumps(wall, LocalDateTime) == java, (sec, micros)
        assert decode(_LocalBox, {"at": loads(java)}).at == wall


def test_an_offset_date_time_is_written_as_jacksons_for_all_of_javas():
    """Every one, those at offset zero included (``...00.4Z``, where an Instant gives
    ``.400Z``) - before the kinds, 16 of 2984 differed because an aware UTC value was taken
    for an Instant."""
    rows = _java_rows("odt")
    assert len(rows) > 2900
    for (sec, micros, offset), java in rows:
        # The moment, shown at Java's offset: the wall digits plus the offset, built directly.
        wall = EPOCH + timedelta(seconds=sec + offset, microseconds=micros)
        value = datetime(
            wall.year, wall.month, wall.day, wall.hour, wall.minute, wall.second,
            wall.microsecond, timezone(timedelta(seconds=offset)),
        )  # fmt: skip
        assert dumps(value, OffsetDateTime) == java, (sec, micros, offset)
        back = decode(_OffsetBox, {"at": loads(java)}).at
        assert back == value and back.utcoffset() == value.utcoffset()


@dataclass(frozen=True)
class _Box:
    at: Instant


@dataclass(frozen=True)
class _LocalBox:
    at: LocalDateTime


@dataclass(frozen=True)
class _OffsetBox:
    at: OffsetDateTime


# -- an event -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    starts: Instant
    opens: LocalDateTime | None = None


class Slot(TypedDict):
    at: Instant
    label: str


@dataclass(frozen=True, eq=False)
class Deadline(TypedValue[Instant]):
    pass


#: A label of the user's own, on top of Instant - still an Instant.
DueAt = NewType("DueAt", Instant)


@dataclass(frozen=True, kw_only=True)
class ShiftPlannedEvent(ModelEvent["Shift"]):
    planned_at: Instant
    opens_at: LocalDateTime
    told_at: OffsetDateTime
    ends_at: Instant | None = None
    day: date = date(2026, 3, 1)
    clock: time = time(9, 30)
    windows: list[Window] = field(default_factory=list)
    reminders: tuple[Instant, ...] = ()
    pair: tuple[Instant, LocalDateTime] | None = None
    by_name: dict[str, Instant] = field(default_factory=dict)
    seq: Sequence[OffsetDateTime] = ()
    slot: Slot | None = None
    deadline: Deadline | None = None
    due: DueAt | None = None
    noted: Annotated[Instant, "meta"] | None = None


def _event(**overrides: Any) -> ShiftPlannedEvent:
    values: dict[str, Any] = {
        "model_id": "s-1",
        "planned_at": Instant(NAIVE),
        "opens_at": LocalDateTime(NAIVE),
        "told_at": OffsetDateTime(AWARE),
    }
    values.update(overrides)
    return ShiftPlannedEvent(**values)


def test_each_kind_in_an_event_writes_as_declared():
    tree = canonical_event(_event())
    assert tree["plannedAt"] == "2026-03-01T09:30:00.120Z"
    assert tree["opensAt"] == "2026-03-01T09:30:00.12"
    assert tree["toldAt"] == "2026-03-01T13:00:00.12+03:30"
    assert tree["endsAt"] is None
    assert tree["day"] == "2026-03-01" and tree["clock"] == "09:30:00", "date and time unchanged"


def test_kinds_reach_inside_nested_dataclasses_lists_tuples_dicts_and_optionals():
    tree = canonical_event(
        _event(
            ends_at=Instant(NAIVE),
            windows=[Window(Instant(NAIVE), LocalDateTime(NAIVE)), Window(Instant(NAIVE))],
            reminders=(Instant(NAIVE), Instant(NAIVE)),
            pair=(Instant(NAIVE), LocalDateTime(NAIVE)),
            by_name={"first": Instant(NAIVE)},
            seq=[OffsetDateTime(AWARE)],
            slot={"at": Instant(NAIVE), "label": "x"},
            deadline=Deadline(Instant(NAIVE)),
            due=DueAt(Instant(NAIVE)),
            noted=Instant(NAIVE),
        )
    )
    z, local, offset = "2026-03-01T09:30:00.120Z", "2026-03-01T09:30:00.12", "+03:30"
    assert tree["endsAt"] == z
    assert tree["windows"] == [{"starts": z, "opens": local}, {"starts": z, "opens": None}]
    assert tree["reminders"] == [z, z]
    assert tree["pair"] == [z, local]
    assert tree["byName"] == {"first": z}
    assert tree["seq"][0].endswith(offset)
    assert tree["slot"] == {"at": z, "label": "x"}
    assert tree["deadline"] == {"value": z}
    assert tree["due"] == z
    assert tree["noted"] == z


def test_a_round_trip_is_equal_and_gives_each_kind_its_own_sort():
    event = _event(
        ends_at=Instant(NAIVE),
        windows=[Window(Instant(NAIVE), LocalDateTime(NAIVE))],
        reminders=(Instant(NAIVE),),
        by_name={"a": Instant(NAIVE)},
        seq=[OffsetDateTime(AWARE)],
        due=DueAt(Instant(NAIVE)),
    )
    back = decode(ShiftPlannedEvent, loads(dumps(canonical_event(event))))
    assert back == event
    assert back.planned_at.tzinfo is None, "an Instant reads back naive: UTC digits"
    assert back.opens_at.tzinfo is None
    assert back.told_at.utcoffset() == timedelta(hours=3, minutes=30), "the offset kept"
    assert back.windows[0].starts.tzinfo is None
    assert back.reminders == (Instant(NAIVE),)
    assert back.due == NAIVE


# -- refusals -----------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class PlainTimeEvent(ModelEvent["Shift"]):
    happened_at: datetime


def test_a_plain_datetime_field_is_refused_with_the_fix_in_the_message():
    with pytest.raises(ValidationError) as raised:
        canonical_event(PlainTimeEvent(model_id="s-1", happened_at=NAIVE))
    assert str(raised.value) == (
        "event PlainTimeEvent field happened_at is a plain datetime; declare it Instant, "
        "LocalDateTime or OffsetDateTime (ekbatan.time)"
    )


def test_a_plain_datetime_in_a_params_dataclass_is_refused():
    @dataclass(frozen=True)
    class BookParams:
        at: datetime

    with pytest.raises(ValidationError, match="dataclass BookParams field at is a plain datetime"):
        canonical(BookParams(NAIVE), where="action params")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"planned_at": AWARE}, "field planned_at is declared Instant but holds an aware"),
        ({"opens_at": AWARE}, "field opens_at is declared LocalDateTime but holds an aware"),
        ({"told_at": NAIVE}, "field told_at is declared OffsetDateTime but holds a naive"),
        ({"windows": [Window(AWARE)]}, "dataclass Window field starts is declared Instant"),
        ({"reminders": (NAIVE, AWARE)}, r"field reminders\[1\] is declared Instant"),
    ],
)
def test_a_value_of_the_wrong_sort_for_its_kind_is_refused(overrides, message):
    with pytest.raises(ValidationError, match=message):
        canonical_event(_event(**overrides))


def test_a_datetime_nothing_declares_is_refused():
    """A ``dict[str, Any]`` value, or a bare one: nothing says which kind."""
    with pytest.raises(ValidationError, match=r"action params\['at'\] is a datetime nothing"):
        canonical({"at": NAIVE}, where="action params")
    with pytest.raises(ValidationError, match="nothing declares a kind"):
        dumps(NAIVE)


def test_a_payload_key_no_field_gives_has_no_kind():
    @dataclass(frozen=True, kw_only=True)
    class CustomPayloadEvent(ModelEvent["Shift"]):
        def payload(self) -> dict[str, Any]:
            return {"when": NAIVE}

    with pytest.raises(ValidationError, match="event CustomPayloadEvent payload key 'when'"):
        canonical_event(CustomPayloadEvent(model_id="s-1"))


@pytest.mark.parametrize(
    ("box", "text", "message"),
    [
        (_Box, "2026-03-01T09:30:00", "ending in Z"),
        (_Box, "2026-03-01T09:30:00+02:00", "ending in Z"),
        (_LocalBox, "2026-03-01T09:30:00Z", "has no zone attached"),
        (_OffsetBox, "2026-03-01T09:30:00", "with its offset; this has none"),
    ],
)
def test_reading_text_of_another_sort_than_the_kind_is_refused(box, text, message):
    with pytest.raises(ValidationError, match=message):
        decode(box, {"at": text})


def test_reading_an_instant_takes_javas_z_and_up_to_nine_fraction_digits():
    assert decode(_Box, {"at": "2026-03-01T09:30:00Z"}).at == datetime(2026, 3, 1, 9, 30)
    nanos = decode(_Box, {"at": "2026-03-01T09:30:00.123456789Z"}).at
    assert nanos == datetime(2026, 3, 1, 9, 30, 0, 123456) and nanos.tzinfo is None


def test_kinds_and_their_mapping_hints_are_mapped_through_unions_of_one():
    """``Mapping`` hints and ``X | None`` reach the kind, as list and dict do."""

    @dataclass(frozen=True)
    class Times:
        by_day: Mapping[str, Instant]
        maybe: LocalDateTime | None

    assert canonical(Times({"mon": Instant(NAIVE)}, None)) == {
        "byDay": {"mon": "2026-03-01T09:30:00.120Z"},
        "maybe": None,
    }


def test_an_aware_utc_value_is_not_taken_for_an_instant_any_more():
    """The old rule wrote an aware UTC value as an Instant by looking at it. Now only the
    declaration decides: aware UTC in an OffsetDateTime field is Java's OffsetDateTime text."""
    assert dumps(datetime(2026, 3, 1, 9, 30, tzinfo=UTC), OffsetDateTime) == (
        '"2026-03-01T09:30:00Z"'
    )
    with pytest.raises(ValidationError, match="declared Instant but holds an aware"):
        dumps(datetime(2026, 3, 1, 9, 30, tzinfo=UTC), Instant)
