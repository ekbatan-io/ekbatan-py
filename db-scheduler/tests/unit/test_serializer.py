"""`JsonSerializer`, ported from `JacksonSerializerTest.java` / `GsonSerializerTest.java`.

The schedule round-trips are the substance. A task with a persistent schedule keeps its cron in
`task_data`, so a schedule that cannot survive serialization is a task that cannot be
rescheduled - and the tagged shape here (`{"type": "cron", ...}`) is the one both Java
serializers write, so a row is legible to either side.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from db_scheduler.exceptions import SerializationException
from db_scheduler.serializer import JsonSerializer, Serializer
from db_scheduler.task.helper import PlainScheduleAndData
from db_scheduler.task.schedule import (
    CronSchedule,
    CronStyle,
    Daily,
    FixedDelay,
    PersistentCronSchedule,
    Schedules,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def serializer() -> JsonSerializer:
    return JsonSerializer()


def roundtrip(serializer: JsonSerializer, data_class: type | None, value: object) -> object:
    return serializer.deserialize(data_class, serializer.serialize(value))


class TestTheProtocol:
    def test_json_serializer_is_a_serializer(self, serializer: JsonSerializer) -> None:
        assert isinstance(serializer, Serializer)

    def test_none_survives_both_ways(self, serializer: JsonSerializer) -> None:
        assert serializer.serialize(None) is None
        assert serializer.deserialize(None, None) is None

    def test_repr(self, serializer: JsonSerializer) -> None:
        assert repr(serializer) == "JsonSerializer()"


class TestScalars:
    def test_the_plain_json_types(self, serializer: JsonSerializer) -> None:
        for value in [{"n": 1}, [1, 2, 3], "text", 42, 4.5, True]:
            assert roundtrip(serializer, None, value) == value

    def test_an_instant(self, serializer: JsonSerializer) -> None:
        assert roundtrip(serializer, None, {"at": NOW}) == {"at": "2026-01-01T12:00:00+00:00"}

    def test_a_decimal_goes_as_a_string(self, serializer: JsonSerializer) -> None:
        """Money, mostly. Through a float it would come back a different number."""
        assert roundtrip(serializer, None, {"amount": Decimal("0.1")}) == {"amount": "0.1"}

    def test_a_uuid(self, serializer: JsonSerializer) -> None:
        one = UUID("11111111-2222-3333-4444-555555555555")
        assert roundtrip(serializer, None, {"id": one}) == {"id": str(one)}

    def test_the_bytes_are_stable(self, serializer: JsonSerializer) -> None:
        """Same data, same bytes - which is what lets two rows be compared."""
        assert serializer.serialize({"b": 2, "a": 1}) == serializer.serialize({"a": 1, "b": 2})

    def test_something_it_cannot_encode_says_so(self, serializer: JsonSerializer) -> None:
        with pytest.raises(SerializationException):
            serializer.serialize({"f": object()})

    def test_bytes_that_are_not_json_say_so(self, serializer: JsonSerializer) -> None:
        with pytest.raises(SerializationException):
            serializer.deserialize(None, b"not json")


class TestSchedules:
    def test_a_cron_schedule(self, serializer: JsonSerializer) -> None:
        schedule = CronSchedule("* * * * * *")

        assert roundtrip(serializer, None, schedule) == schedule

    def test_a_cron_schedule_keeps_its_style_and_zone(self, serializer: JsonSerializer) -> None:
        schedule = CronSchedule("* * * * *", UTC, CronStyle.UNIX)

        revived = roundtrip(serializer, None, schedule)

        assert isinstance(revived, CronSchedule)
        assert revived.cron_style is CronStyle.UNIX
        assert revived == schedule

    def test_daily(self, serializer: JsonSerializer) -> None:
        daily = Schedules.daily(time(0, 0), zone=UTC)

        revived = roundtrip(serializer, None, daily)

        assert revived == daily
        assert isinstance(revived, Daily)
        assert revived.initial_execution_time(NOW) == daily.initial_execution_time(NOW)

    def test_daily_with_several_times(self, serializer: JsonSerializer) -> None:
        daily = Schedules.daily(time(8, 0), time(13, 30), zone=UTC)

        assert roundtrip(serializer, None, daily) == daily

    def test_fixed_delay(self, serializer: JsonSerializer) -> None:
        delay = Schedules.fixed_delay(timedelta(hours=1))

        revived = roundtrip(serializer, None, delay)

        assert revived == delay
        assert isinstance(revived, FixedDelay)
        assert revived.initial_execution_time(NOW) == delay.initial_execution_time(NOW)

    def test_the_tagged_shape_is_the_one_java_writes(self, serializer: JsonSerializer) -> None:
        """`type` plus the object's own field names - what Gson's RuntimeTypeAdapterFactory and
        Jackson's ScheduleMixin both produce."""
        import json

        raw = serializer.serialize(CronSchedule("0 * * * * ?", UTC, CronStyle.SPRING53))

        assert raw is not None
        assert json.loads(raw) == {
            "type": "cron",
            "pattern": "0 * * * * ?",
            "zoneId": "UTC",
            "cronStyle": "SPRING53",
        }

    def test_fixed_delay_goes_as_milliseconds(self, serializer: JsonSerializer) -> None:
        import json

        raw = serializer.serialize(FixedDelay.of(timedelta(hours=1)))

        assert raw is not None
        assert json.loads(raw) == {"type": "fixedDelay", "duration": 3600000}


class TestScheduleAndData:
    def test_plain_schedule_and_data(self, serializer: JsonSerializer) -> None:
        both = PlainScheduleAndData(Schedules.fixed_delay(timedelta(hours=1)), 50)

        revived = roundtrip(serializer, PlainScheduleAndData, both)

        assert isinstance(revived, PlainScheduleAndData)
        assert revived.schedule.initial_execution_time(NOW) == both.schedule.initial_execution_time(
            NOW
        )
        assert revived.data == 50
        assert revived == both

    def test_plain_schedule_and_data_without_data(self, serializer: JsonSerializer) -> None:
        both = PlainScheduleAndData(CronSchedule("* * * * * *"))

        assert roundtrip(serializer, PlainScheduleAndData, both) == both

    def test_a_persistent_cron_schedule(self, serializer: JsonSerializer) -> None:
        stored = PersistentCronSchedule("0 0 12 * * ?", UTC, {"n": 1})

        revived = roundtrip(serializer, PersistentCronSchedule, stored)

        assert isinstance(revived, PersistentCronSchedule)
        assert revived.schedule == stored.schedule
        assert revived.data == {"n": 1}


@dataclass
class Order:
    customer: str
    amount: int


class TestDataClasses:
    def test_a_dataclass_comes_back_as_itself(self, serializer: JsonSerializer) -> None:
        assert roundtrip(serializer, Order, Order("ada", 3)) == Order("ada", 3)

    def test_without_a_data_class_it_stays_a_dict(self, serializer: JsonSerializer) -> None:
        assert roundtrip(serializer, None, Order("ada", 3)) == {"customer": "ada", "amount": 3}

    def test_a_data_class_the_json_does_not_fit_says_so(
        self, serializer: JsonSerializer
    ) -> None:
        with pytest.raises(SerializationException, match="Order"):
            serializer.deserialize(Order, serializer.serialize({"nope": 1}))

    def test_a_data_class_that_json_already_satisfies_is_left_alone(
        self, serializer: JsonSerializer
    ) -> None:
        assert serializer.deserialize(dict, serializer.serialize({"n": 1})) == {"n": 1}
