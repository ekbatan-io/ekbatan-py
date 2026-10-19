"""Every layer that holds a time keeps it exactly as given - one check per layer.

A naive value and an aware non-UTC value go in; the very same object (or, for text, the same
digits and offset) comes out. A conversion anywhere - a zone stamped on, an offset moved to UTC,
a zone stripped - fails here, naming the layer. The database side is
`tests/integration/test_timestamps.py` and `test_time_as_is_events.py`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import pytest

from ekbatan._json import decode, dumps
from ekbatan.action.outbox import EventEntity
from ekbatan.events.notification import EventNotification, EventNotificationState
from ekbatan.repository import _from_column, _reader_for, _to_column, _writer_for
from ekbatan.schema import timestamp_column
from ekbatan.streaming import ActionEvent

NAIVE = datetime(2026, 1, 1, 10, 0, 0, 123456)
TEHRAN = datetime(2026, 1, 1, 13, 30, 0, 654321, tzinfo=timezone(timedelta(hours=3, minutes=30)))
TIMES = pytest.mark.parametrize("at", [NAIVE, TEHRAN], ids=["naive", "plus-03-30"])


@TIMES
def test_the_by_name_mapping_hands_a_time_on_as_it_is(at):
    """`columns_by_field_name` / `model_by_field_name`: declared, optional and untyped fields."""
    for hint in (datetime, datetime | None):
        assert _writer_for(hint)(at) is at
        assert _reader_for(hint)(at) is at
    assert _to_column(at) is at, "an undeclared field"
    assert _from_column(at, datetime) is at


def test_the_timestamp_column_converts_nothing():
    """A column type only: it adds no bind or result step of its own to the dialect's."""
    from sqlalchemy import DateTime
    from sqlalchemy.dialects import mysql, postgresql, sqlite
    from sqlalchemy.types import TypeDecorator

    column = timestamp_column()
    assert type(column).process_bind_param is TypeDecorator.process_bind_param
    assert type(column).process_result_value is TypeDecorator.process_result_value
    for dialect in (postgresql.dialect(), mysql.dialect(), sqlite.dialect()):
        plain = DateTime().dialect_impl(dialect)
        ours = column.dialect_impl(dialect)
        bind_mine, bind_plain = ours.bind_processor(dialect), plain.bind_processor(dialect)
        sent = bind_plain(NAIVE) if bind_plain else NAIVE
        assert (bind_mine(NAIVE) if bind_mine else NAIVE) == sent, dialect.name
        read_mine = ours.result_processor(dialect, None)
        read_plain = plain.result_processor(dialect, None)
        expected = read_plain(sent) if read_plain else sent
        assert (read_mine(sent) if read_mine else sent) == expected == NAIVE, dialect.name
    mysql_type = column.load_dialect_impl(mysql.dialect())
    assert getattr(mysql_type, "fsp", None) == 6, "microseconds on MySQL and MariaDB"


def _entity(at: datetime) -> EventEntity:
    return EventEntity.create_event_entity(
        id=uuid.uuid4(),
        namespace="n",
        action_id=uuid.uuid4(),
        action_name="A",
        action_params={},
        started_date=at,
        completion_date=at,
        model_id="m",
        model_type="M",
        event_type="E",
        payload={},
        event_date=at,
    ).build()


@TIMES
def test_an_outbox_row_keeps_its_times_as_given(at):
    entity = _entity(at)
    assert entity.started_date is at and entity.event_date is at
    row = entity.to_row()
    assert row["started_date"] is at and row["completion_date"] is at and row["event_date"] is at
    back = EventEntity.from_row(type("Row", (), row)())
    assert back.started_date is at


@TIMES
def test_a_notification_keeps_its_times_as_given(at):
    notification = (
        EventNotification.builder()
        .id(uuid.uuid4())
        .event_id(uuid.uuid4())
        .handler_name("audit")
        .state(EventNotificationState.PENDING)
        .attempts(0)
        .next_retry_at(at)
        .created_date(at)
        .updated_date(at)
        .namespace("n")
        .action_id(uuid.uuid4())
        .action_name("A")
        .action_params({})
        .started_date(at)
        .completion_date(at)
        .event_type("E")
        .event_date(at)
        .build()
    )
    row = notification.to_row()
    for name in ("next_retry_at", "created_date", "updated_date", "started_date", "event_date"):
        assert getattr(notification, name) is at, name
        assert row[name] is at, name


@TIMES
def test_a_cdc_row_keeps_its_times_as_given(at):
    row = {
        "id": uuid.uuid4(),
        "namespace": "n",
        "action_id": uuid.uuid4(),
        "action_name": "A",
        "action_params": {},
        "started_date": at,
        "completion_date": at,
        "event_date": at,
    }
    event = ActionEvent.from_message(row)
    assert event.started_date is at and event.completion_date is at and event.event_date is at


@dataclass(frozen=True)
class Stamped:
    at: datetime


def test_event_json_writes_a_time_as_it_is():
    """No offset moved, none added, none dropped: the digits and the offset given are the text."""
    assert dumps(Stamped(NAIVE)) == '{"at":"2026-01-01T10:00:00.123456"}'
    assert dumps(Stamped(TEHRAN)) == '{"at":"2026-01-01T13:30:00.654321+03:30"}'
    utc = NAIVE.replace(tzinfo=UTC)
    assert dumps(Stamped(utc)) == '{"at":"2026-01-01T10:00:00.123456Z"}'
    assert decode(Stamped, {"at": "2026-01-01T10:00:00.123456"}).at == NAIVE
    assert decode(Stamped, {"at": "2026-01-01T13:30:00.654321+03:30"}).at.utcoffset() == (
        timedelta(hours=3, minutes=30)
    )
