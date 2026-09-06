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
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from ..exceptions import SerializationException

__all__ = ["JsonSerializer", "Serializer"]


@runtime_checkable
class Serializer(Protocol):
    """Task data, to bytes and back."""

    def serialize(self, data: Any) -> bytes | None: ...
    def deserialize(self, raw: bytes | None) -> Any: ...


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

    def deserialize(self, raw: bytes | None) -> Any:
        if raw is None:
            return None
        try:
            return json.loads(bytes(raw).decode("utf-8"))
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise SerializationException(f"could not deserialize task data: {exc}") from exc

    def __repr__(self) -> str:
        return "JsonSerializer()"


def _encode(value: Any) -> Any:
    """The scalars JSON has no opinion about, in a form the other port also reads."""
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
