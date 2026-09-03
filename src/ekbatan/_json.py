"""One rendering of a value, so that two languages agree about a row.

The outbox is read by things that are not this process: a Debezium connector, a CDC pipeline,
a service written in another language. Every type here has more than one plausible rendering
and the wrong choice is only discovered downstream, so the choices are made once, here.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any

__all__ = ["canonical", "dumps", "loads"]


def canonical(value: Any) -> Any:
    """A value in the form the outbox stores it in."""
    if value is None or isinstance(value, bool | str | int):
        return value
    if isinstance(value, Decimal):
        # A string, at the scale it was written. Money is the whole point, and no two
        # languages render a binary float alike - or, for a value like 0.1, correctly.
        return format(value, "f")
    if isinstance(value, float):
        # Reached only if an application put one here. Rendered as a decimal string so it at
        # least crosses the boundary unambiguously, but a money column should never see one.
        return repr(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        from .clock import to_storage

        return to_storage(value).isoformat(timespec="microseconds") + "Z"
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, Enum):
        # By name. An ordinal changes meaning when someone inserts a member in the middle, and
        # nothing about that change looks dangerous at the time.
        return value.name
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(k): canonical(v) for k, v in value.items()}
    if isinstance(value, Sequence | set | frozenset):
        return [canonical(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        import dataclasses

        return {f.name: canonical(getattr(value, f.name)) for f in dataclasses.fields(value)}
    return str(value)


def dumps(value: Any) -> str:
    """Canonical JSON: keys sorted, no insignificant space.

    Sorted because no two JSON writers agree on key order and nothing downstream may depend on
    it - and because two ports producing byte-identical output for the same event is a property
    worth being able to assert.
    """
    return json.dumps(canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def loads(text: str | bytes | None) -> Any:
    return None if text is None else json.loads(text)
