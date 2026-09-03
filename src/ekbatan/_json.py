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

__all__ = ["canonical", "decode", "dumps", "loads"]


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
    from .ids import TypedValue

    if isinstance(value, TypedValue):
        # The primitive inside, not the wrapper. An Id[Wallet] has to reach the outbox as the
        # uuid string a consumer can join on - rendering it as {"value": "..."} would make
        # every payload this port writes unreadable to every other.
        return canonical(value.value)
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


def decode[T](target: type[T], data: Mapping[str, Any]) -> T:
    """Rebuild a dataclass from what :func:`dumps` wrote.

    The inverse of :func:`canonical`, driven by the target's own annotations - a field typed
    ``Decimal`` is rebuilt from its string, a ``datetime`` from its ISO form, an enum from its
    name. Without this a handler would receive the JSON and have to re-parse its own event,
    which is a mapping layer per handler and a place for each of them to get it wrong.
    """
    import dataclasses
    import typing

    hints = typing.get_type_hints(target)
    fields = {f.name for f in dataclasses.fields(target)}  # type: ignore[arg-type]
    return target(
        **{name: _revive(value, hints.get(name)) for name, value in data.items() if name in fields}
    )


def _revive(value: Any, hint: Any) -> Any:
    import dataclasses
    import typing

    if value is None or hint is None:
        return value

    # `Decimal | None` and friends: the interesting type is the one that is not None.
    if typing.get_origin(hint) in (typing.Union, __import__("types").UnionType):
        candidates = [a for a in typing.get_args(hint) if a is not type(None)]
        hint = candidates[0] if len(candidates) == 1 else None
        if hint is None:
            return value

    if hint is Decimal:
        return Decimal(str(value))
    if hint is uuid.UUID:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    if hint is datetime:
        if isinstance(value, datetime):
            return value
        text = str(value)
        return datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    if isinstance(hint, type):
        if issubclass(hint, Enum):
            return hint[str(value)]
        if dataclasses.is_dataclass(hint) and isinstance(value, Mapping):
            return decode(hint, value)
    origin = typing.get_origin(hint)
    if origin in (list, tuple, set, frozenset) and isinstance(value, list):
        (inner,) = typing.get_args(hint)[:1] or (None,)
        items = [_revive(v, inner) for v in value]
        return origin(items) if origin is not list else items
    return value
