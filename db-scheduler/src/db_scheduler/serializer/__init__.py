"""Turning task data into bytes and back.

A port of `com.github.kagkarlsson.scheduler.serializer`. The default is JSON, because that is
the one format a Java service and a Python service can both read out of the same `task_data`
column.

Java's `JavaSerializer` is deliberately not ported. Native Java serialization has no Python
counterpart, and its Python equivalent - pickle - executes whatever it is handed, which is not a
thing to point at a table several machines write to.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from ..exceptions import SerializationException

__all__ = ["JsonSerializer", "Serializer"]


@runtime_checkable
class Serializer(Protocol):
    """Task data, to bytes and back."""

    def serialize(self, data: Any) -> bytes | None: ...
    def deserialize(self, data_class: type[Any] | None, raw: bytes | None) -> Any: ...


class JsonSerializer:
    """The default. UTF-8 JSON, with the few scalar types a task's data actually carries.

    Keys are sorted and separators are fixed, so the same data serializes to the same bytes on
    both ports - which is what lets a row be compared, or diffed, across a mixed deployment.
    """

    __slots__ = ()

    def serialize(self, data: Any) -> bytes | None:
        if data is None:
            return None
        try:
            return json.dumps(
                data, default=_encode, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise SerializationException(
                f"could not serialize task data of type {type(data).__name__}: {exc}"
            ) from exc

    def deserialize(self, data_class: type[Any] | None, raw: bytes | None) -> Any:
        """Decode, then shape it into `data_class` if one was named.

        Java has to be told the class - `Class<T>` is the only way Jackson can rebuild the
        object. Python does not *need* it to read JSON, so `None` is allowed and gives back
        plain dicts and lists; naming a dataclass gets the dataclass, which is what a task
        declared with one expects to receive.
        """
        if raw is None:
            return None
        try:
            decoded = json.loads(bytes(raw).decode("utf-8"))
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise SerializationException(f"could not deserialize task data: {exc}") from exc

        if decoded is None:
            return None
        if data_class is None:
            # No class named, so the only thing that can be recognised is what tags itself -
            # which is exactly the schedule family, and is what the row needs back.
            return _schedule_from_json(decoded)
        try:
            decoder = _decoder_for(data_class)
            if decoder is not None:
                return decoder(decoded)
            if isinstance(decoded, data_class):
                return decoded
            if is_dataclass(data_class) and isinstance(decoded, dict):
                return data_class(**decoded)
            from_dict = getattr(data_class, "from_dict", None)
            if callable(from_dict):
                return from_dict(decoded)
        except (TypeError, ValueError) as exc:
            raise SerializationException(
                f"could not deserialize task data into {data_class.__name__}: {exc}"
            ) from exc
        raise SerializationException(
            f"could not deserialize task data into {data_class.__name__}: JSON gave a "
            f"{type(decoded).__name__}, and it is neither a dataclass nor has a from_dict"
        )

    def __repr__(self) -> str:
        return "JsonSerializer()"


# ------------------------------------------------------------------ the schedule family
#
# Both Java serializers tag a `Schedule` with `"type"` and then write the object's own fields -
# Gson through a `RuntimeTypeAdapterFactory`, Jackson through `ScheduleMixin`. The tags and the
# field names below are theirs, so a `task_data` blob written by either side means the same
# thing to the other.
#
# This is not decoration: a task with a persistent schedule keeps its cron in `task_data`. A
# schedule that cannot round-trip is a task that cannot reschedule itself.

TYPE_KEY = "type"


def _schedule_to_json(value: Any) -> dict[str, Any] | None:
    from ..task.schedule import CronSchedule, Daily, FixedDelay

    if isinstance(value, CronSchedule):
        return {
            TYPE_KEY: "cron",
            "pattern": value.pattern,
            "zoneId": str(value.zone),
            "cronStyle": value.cron_style.name,
        }
    if isinstance(value, FixedDelay):
        # Milliseconds, as Gson's `DurationAdapter` writes them.
        return {TYPE_KEY: "fixedDelay", "duration": round(value.duration.total_seconds() * 1000)}
    if isinstance(value, Daily):
        return {
            TYPE_KEY: "daily",
            "times": [moment.isoformat() for moment in value.times],
            "zone": str(value.zone),
        }
    return None


def _schedule_from_json(decoded: Any) -> Any:
    """Rebuild a schedule from a tagged object, or hand back what was given.

    Only a dict whose `type` is one of the three tags *and* whose remaining keys are exactly
    that schedule's is revived, so ordinary task data that happens to carry a `type` field is
    left alone.
    """
    from zoneinfo import ZoneInfo

    from ..task.schedule import CronSchedule, CronStyle, Daily, FixedDelay

    if not isinstance(decoded, dict):
        return decoded
    tag = decoded.get(TYPE_KEY)
    keys = set(decoded) - {TYPE_KEY}
    if tag == "cron" and keys == {"pattern", "zoneId", "cronStyle"}:
        return CronSchedule(
            decoded["pattern"], ZoneInfo(decoded["zoneId"]), CronStyle(decoded["cronStyle"])
        )
    if tag == "fixedDelay" and keys == {"duration"}:
        return FixedDelay(timedelta(milliseconds=int(decoded["duration"])))
    if tag == "daily" and keys == {"times", "zone"}:
        return Daily(
            *(time.fromisoformat(moment) for moment in decoded["times"]),
            zone=ZoneInfo(decoded["zone"]),
        )
    return decoded


def _plain_schedule_and_data_to_json(value: Any) -> dict[str, Any]:
    return {"schedule": _encode(value.schedule), "data": value.data}


def _plain_schedule_and_data_from_json(decoded: Any) -> Any:
    from ..task.helper import PlainScheduleAndData

    return PlainScheduleAndData(_schedule_from_json(decoded["schedule"]), decoded.get("data"))


def _persistent_cron_to_json(value: Any) -> dict[str, Any]:
    return {"cronPattern": value.cron_pattern, "zoneId": value.zone_id, "data": value.data}


def _persistent_cron_from_json(decoded: Any) -> Any:
    from zoneinfo import ZoneInfo

    from ..task.schedule import PersistentCronSchedule

    return PersistentCronSchedule(
        decoded["cronPattern"], ZoneInfo(decoded["zoneId"]), decoded.get("data")
    )


def _decoder_for(data_class: type[Any]) -> Any:
    """The reviver for a class the JSON alone cannot identify."""
    from ..task.helper import PlainScheduleAndData
    from ..task.schedule import PersistentCronSchedule, Schedule

    if issubclass(data_class, PlainScheduleAndData):
        return _plain_schedule_and_data_from_json
    if issubclass(data_class, PersistentCronSchedule):
        return _persistent_cron_from_json
    if issubclass(data_class, Schedule):
        return _schedule_from_json
    return None


def _encode(value: Any) -> Any:
    """The scalars JSON has no opinion about, in a form the other port also reads."""
    from ..task.helper import PlainScheduleAndData
    from ..task.schedule import PersistentCronSchedule

    as_schedule = _schedule_to_json(value)
    if as_schedule is not None:
        return as_schedule
    if isinstance(value, PlainScheduleAndData):
        return _plain_schedule_and_data_to_json(value)
    if isinstance(value, PersistentCronSchedule):
        return _persistent_cron_to_json(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        # A string, not a float: a decimal that goes through a float comes back a different
        # number, and task data is frequently money.
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, set | frozenset):
        return sorted(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")
