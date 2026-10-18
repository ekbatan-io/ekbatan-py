"""The JSON of action params and event payloads - the same text Java's Ekbatan writes.

The outbox is read by things that are not this process: a Java service sharing the table, a
Debezium connector, a CDC pipeline. So the text is the one Ekbatan's default Jackson 3 mapper
(``JsonMapper.builder().build()``) writes for the same object, byte for byte, measured against
Jackson in ``parity-probes/G-domain-model/``:

- **keys** are Java's field names: a dataclass field ``new_balance`` is written ``newBalance``
  (:func:`json_name`), in declaration order, as Jackson writes a record or a ``@JsonCreator``
  class. A map keeps its own keys, in insertion order. Nothing is sorted.
- **numbers**: a ``Decimal`` is a JSON number in Java's ``BigDecimal.toString()`` form (``10.50``,
  ``1E+3``); a ``float`` is a number in Java's ``Double.toString()`` form (``0.1``, ``1.0E20``).
- **times**: an aware UTC ``datetime`` is an ``Instant`` (``2025-01-01T00:00:00.120Z``), another
  offset an ``OffsetDateTime`` (``...02:00:00+02:00``), a naive one a ``LocalDateTime``
  (``...10:00:00``); ``date``, ``time`` and ``timedelta`` as ``LocalDate``, ``LocalTime`` and
  ``Duration``.
- **bytes** are base64; **an enum** is its name; **a uuid** its text.
- **a typed value** is Jackson's shape for Java's ``TypedValue``: ``{"value": ...}``, and
  ``{"id": ..., "value": ...}`` for ``Id`` and ``ShardedId``.

Reading parses every JSON number with a fraction as a ``Decimal``, so nothing is lost on the
way in, and :func:`decode` rebuilds each field as its declared type.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import math
import re
import types
import typing
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal
from enum import Enum
from typing import Any

from .errors import ValidationError

__all__ = ["canonical", "decode", "dumps", "json_name", "loads"]


def json_name(name: str) -> str:
    """A Python field name as Java spells the same field: ``new_balance`` -> ``newBalance``.

    Each underscore followed by a letter or digit is dropped and the next character raised.
    Leading and trailing underscores are kept, and a name already in camelCase is unchanged.
    """
    return _CAMEL.sub(lambda m: m.group(1).upper(), name)


_CAMEL = re.compile(r"(?<=[^_])_([0-9A-Za-z])")


#: Keyed by class and never invalidated - a dataclass's fields are fixed at definition.
_FIELDS: dict[type, tuple[tuple[str, str], ...]] = {}


def _json_fields(target: type) -> tuple[tuple[str, str], ...]:
    """``(field name, json key)`` for every field of a dataclass, in declaration order."""
    cached = _FIELDS.get(target)
    if cached is not None:
        return cached
    pairs = tuple((f.name, json_name(f.name)) for f in dataclasses.fields(target))
    seen: dict[str, str] = {}
    for name, key in pairs:
        if key in seen:
            raise ValidationError(
                f"{target.__name__}: fields {seen[key]!r} and {name!r} are both written as "
                f"{key!r} in JSON; rename one"
            )
        seen[key] = name
    _FIELDS[target] = pairs
    return pairs


def canonical(value: Any) -> Any:
    """``value`` as the JSON tree Java would write for it: dicts, lists, text, numbers.

    Decimals stay ``Decimal`` and floats stay ``float`` here; :func:`dumps` writes them as
    numbers. Running it on its own output changes nothing.
    """
    if value is None:
        return None
    if isinstance(value, Enum):
        # By name, as Java's enums write unless they override toString(). Checked first: a
        # StrEnum or an IntEnum is also a str or an int.
        return value.name
    if isinstance(value, bool | str | int):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValidationError(
                f"{value} cannot be written as JSON: Java's BigDecimal has no such value"
            )
        # Java's BigDecimal has no negative zero.
        return value.copy_abs() if value.is_zero() else value
    if isinstance(value, float):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return _datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return _time(value)
    if isinstance(value, timedelta):
        return _duration(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return base64.b64encode(bytes(value)).decode("ascii")
    from .ids import Id, ShardedId, TypedValue

    if isinstance(value, Id | ShardedId):
        # Jackson writes Java's Id and ShardedId through their two getters, getId() and
        # getValue(), which both return the uuid.
        text = str(value.value)
        return {"id": text, "value": text}
    if isinstance(value, TypedValue):
        # Java's TypedValue has one getter, getValue().
        return {"value": canonical(value.value)}
    if isinstance(value, Mapping):
        return {_key(k): canonical(v) for k, v in value.items()}
    if isinstance(value, Sequence | set | frozenset):
        return [canonical(v) for v in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {key: canonical(getattr(value, name)) for name, key in _json_fields(type(value))}
    # Refused rather than written as `str(value)`: for a plain object that is its repr -
    # `<Money object at 0x...>` - and the data would be gone, silently, with an address that
    # changes every run. Java's Jackson writes such an object's fields and reads them back; this
    # port reads back dataclasses only, so a dataclass is the way to the same JSON.
    raise ValidationError(
        f"a {type(value).__qualname__} cannot be written as JSON: make it a dataclass, whose "
        f"fields are written as Java writes an object's, or pass a supported value instead"
    )


def _key(key: Any) -> str:
    """A map key as Jackson writes one: always text."""
    rendered = canonical(key)
    if isinstance(rendered, str):
        return rendered
    if isinstance(rendered, bool | int | Decimal | float):
        return _number(rendered)
    return str(key)


def _fraction(micros: int, *, groups: bool) -> str:
    """The fraction of a second: in groups of three digits (``Instant``, ``LocalTime.toString``)
    or with trailing zeros cut (``DateTimeFormatter.ISO_LOCAL_TIME``)."""
    if micros == 0:
        return ""
    if groups:
        return f".{micros // 1000:03d}" if micros % 1000 == 0 else f".{micros:06d}"
    return "." + f"{micros:06d}".rstrip("0")


def _offset(offset: timedelta) -> str:
    """``ZoneOffset.toString()``: ``Z``, ``+02:00``, or ``+05:30:15``."""
    seconds = int(offset.total_seconds())
    if seconds == 0:
        return "Z"
    sign = "-" if seconds < 0 else "+"
    hours, rest = divmod(abs(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{sign}{hours:02d}:{minutes:02d}" + (f":{secs:02d}" if secs else "")


def _datetime(value: datetime) -> str:
    offset = value.utcoffset()
    clock = f"{value:%H:%M:%S}"
    if offset is None:
        # LocalDateTime, through ISO_LOCAL_DATE_TIME.
        return f"{value.date().isoformat()}T{clock}{_fraction(value.microsecond, groups=False)}"
    if offset == timedelta(0):
        # Instant, through ISO_INSTANT.
        utc = value.astimezone(UTC)
        return f"{utc.date().isoformat()}T{utc:%H:%M:%S}{_fraction(utc.microsecond, groups=True)}Z"
    # OffsetDateTime, through ISO_OFFSET_DATE_TIME.
    return (
        f"{value.date().isoformat()}T{clock}"
        f"{_fraction(value.microsecond, groups=False)}{_offset(offset)}"
    )


def _time(value: time) -> str:
    offset = value.utcoffset()
    if offset is None:
        # LocalTime, through ISO_LOCAL_TIME.
        return f"{value:%H:%M:%S}{_fraction(value.microsecond, groups=False)}"
    # OffsetTime, through its toString(): seconds only when not zero.
    text = f"{value:%H:%M}"
    if value.second or value.microsecond:
        text += f":{value.second:02d}{_fraction(value.microsecond, groups=True)}"
    return text + _offset(offset)


def _duration(value: timedelta) -> str:
    """``Duration.toString()``: ``PT1.5S``, ``PT26H3M``, ``PT-0.5S``."""
    total_micros = (value.days * 86400 + value.seconds) * 1_000_000 + value.microseconds
    seconds, micros = divmod(total_micros, 1_000_000)  # micros >= 0, as Java's nanos are
    nanos = micros * 1000
    if seconds == 0 and nanos == 0:
        return "PT0S"
    effective = seconds + 1 if seconds < 0 and nanos > 0 else seconds
    hours = int(effective / 3600)
    minutes = int((effective - hours * 3600) / 60)
    secs = effective - hours * 3600 - minutes * 60
    text = "PT"
    if hours:
        text += f"{hours}H"
    if minutes:
        text += f"{minutes}M"
    if secs == 0 and nanos == 0 and len(text) > 2:
        return text
    if seconds < 0 and nanos > 0:
        text += "-0" if secs == 0 else str(secs)
    else:
        text += str(secs)
    if nanos > 0:
        digits = str(2_000_000_000 - nanos if seconds < 0 else nanos + 1_000_000_000)
        text += "." + digits[1:].rstrip("0")
    return text + "S"


def _number(value: bool | int | Decimal | float) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        # Python's str() of a Decimal and Java's BigDecimal.toString() are the same algorithm:
        # the "to-scientific-string" of the General Decimal Arithmetic specification.
        return str(value)
    return _java_double(value)


def _java_double(value: float) -> str:
    """Java's ``Double.toString`` (JDK 19 and later): the shortest digits that read back as the
    same double, written plainly from 10^-3 up to 10^7 and as ``d.dddE<n>`` outside it."""
    if math.isnan(value):
        return '"NaN"'
    if math.isinf(value):
        return '"Infinity"' if value > 0 else '"-Infinity"'
    if value == 0:
        return "-0.0" if math.copysign(1.0, value) < 0 else "0.0"
    sign, digit_tuple, exponent = Decimal(repr(value)).as_tuple()
    digits = "".join(map(str, digit_tuple))
    stripped = digits.rstrip("0")
    exponent = int(exponent) + len(digits) - len(stripped)
    digits = stripped
    if len(digits) == 1:
        # Java always gives at least two digits, choosing the two-digit decimal nearest the
        # double - which differs from the one-digit answer only at the bottom of the subnormal
        # range (Double.MIN_VALUE is 4.9E-324, not 5E-324).
        two = Context(prec=2, rounding=ROUND_HALF_EVEN).plus(Decimal(abs(value)))
        _, two_digits, two_exponent = two.as_tuple()
        digits = "".join(map(str, two_digits))
        exponent = int(two_exponent)
        stripped = digits.rstrip("0")
        exponent += len(digits) - len(stripped)
        digits = stripped
    point = exponent + len(digits) - 1  # the power of ten of the first digit
    if 1e-3 <= abs(value) < 1e7:
        if point >= 0:
            whole = digits[: point + 1].ljust(point + 1, "0")
            fraction = digits[point + 1 :] or "0"
        else:
            whole, fraction = "0", "0" * (-point - 1) + digits
        text = f"{whole}.{fraction}"
    else:
        text = f"{digits[0]}.{digits[1:] or '0'}E{point}"
    return ("-" if sign else "") + text


_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}
_NEEDS_ESCAPE = re.compile(r'[\x00-\x1f"\\]')


def _escape(match: re.Match[str]) -> str:
    char = match.group(0)
    return _ESCAPES.get(char) or f"\\u{ord(char):04X}"


def _quote(text: str) -> str:
    """Jackson's string escaping: short forms where JSON has them, ``\\u00XX`` (upper case)
    for the other control characters, everything else as it is."""
    return '"' + _NEEDS_ESCAPE.sub(_escape, text) + '"'


def _write(value: Any, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif isinstance(value, str):
        out.append(_quote(value))
    elif isinstance(value, bool | int | Decimal | float):
        out.append(_number(value))
    elif isinstance(value, dict):
        out.append("{")
        for i, (key, item) in enumerate(value.items()):
            if i:
                out.append(",")
            out.append(_quote(key))
            out.append(":")
            _write(item, out)
        out.append("}")
    elif isinstance(value, list):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _write(item, out)
        out.append("]")
    else:  # pragma: no cover - canonical() leaves nothing else
        raise TypeError(f"not a JSON value: {type(value).__name__}")


def dumps(value: Any) -> str:
    """The JSON text Java's Ekbatan writes for ``value``: no spaces, keys in their own order."""
    out: list[str] = []
    _write(canonical(value), out)
    return "".join(out)


def loads(text: str | bytes | None) -> Any:
    """JSON read with every number that has a fraction or exponent as a ``Decimal``.

    A float would turn Java's ``10.50`` into ``10.5`` and cut a long decimal to 17 digits.
    """
    return None if text is None else json.loads(text, parse_float=Decimal)


def decode[T](target: type[T], data: Mapping[str, Any]) -> T:
    """Rebuild a dataclass from its JSON, as Jackson rebuilds a Java object.

    Keys are Java's names (:func:`json_name`); each value is rebuilt as the field's declared type
    - a ``Decimal`` from a number or text, a ``datetime`` from its ISO form with whatever zone
    it was written with (none for a ``LocalDateTime``), ``date``, ``time``, ``timedelta``, bytes
    from base64, an enum from its name, typed ids and values from their JSON shape, nested
    dataclasses, lists and maps.

    Unknown keys are ignored. A missing key leaves the field ``None``, as Jackson leaves a Java
    field null - or takes the field's default, if it declares one. The class's own checks then
    run as for any construction.
    """
    hints = typing.get_type_hints(target)
    values: dict[str, Any] = {}
    init_fields = {f.name: f for f in dataclasses.fields(target) if f.init}  # type: ignore[arg-type]
    for name, key in _json_fields(target):
        declared = init_fields.get(name)
        if declared is None:
            # `model_name` and other init=False fields come from the class, not the JSON.
            continue
        if key in data:
            try:
                values[name] = _revive(data[key], hints.get(name))
            except (ArithmeticError, LookupError, TypeError, ValueError) as error:
                raise ValidationError(
                    f"{target.__name__}.{name}: cannot read {data[key]!r} from JSON key "
                    f"{key!r} as {_type_name(hints.get(name))}: {error}"
                ) from error
        elif (
            declared.default is dataclasses.MISSING
            and declared.default_factory is dataclasses.MISSING
        ):
            values[name] = None
    return target(**values)


def _type_name(hint: Any) -> str:
    return getattr(hint, "__name__", None) or str(hint)


def _revive(value: Any, hint: Any) -> Any:
    if value is None or hint is None or hint is Any:
        return value

    # `Decimal | None` and friends: the interesting type is the one that is not None.
    if typing.get_origin(hint) in (typing.Union, types.UnionType):
        candidates = [a for a in typing.get_args(hint) if a is not type(None)]
        if len(candidates) != 1:
            return value
        hint = candidates[0]

    origin = typing.get_origin(hint) or hint
    if origin is str:
        # Jackson reads a number or a boolean into a String field as its text.
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value) if isinstance(value, int | Decimal) else value
    if origin is Decimal:
        if isinstance(value, Decimal):
            return value
        if isinstance(value, int | str) and not isinstance(value, bool):
            return Decimal(value)
        return Decimal(str(value))
    if origin is float:
        return float(value) if isinstance(value, int | Decimal | str) else value
    if origin is uuid.UUID:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    if origin is datetime:
        return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if origin is date:
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    if origin is time:
        return value if isinstance(value, time) else time.fromisoformat(str(value))
    if origin is timedelta:
        return value if isinstance(value, timedelta) else _parse_duration(str(value))
    if origin is bytes:
        return value if isinstance(value, bytes) else base64.b64decode(str(value))
    if isinstance(origin, type):
        from .ids import Id, ShardedId, TypedValue

        if issubclass(origin, TypedValue):
            if isinstance(value, origin):
                return value
            inner = value
            if isinstance(value, Mapping):
                inner = value.get("value", value.get("id"))
            if issubclass(origin, Id | ShardedId):
                inner = inner if isinstance(inner, uuid.UUID) else uuid.UUID(str(inner))
            return origin(inner)
        if issubclass(origin, Enum):
            return value if isinstance(value, origin) else origin[str(value)]
        if dataclasses.is_dataclass(origin) and isinstance(value, Mapping):
            return decode(origin, value)
    args = typing.get_args(hint)
    if origin in (list, tuple, set, frozenset) and isinstance(value, list):
        if origin is tuple and args and (len(args) != 2 or args[1] is not Ellipsis):
            # tuple[A, B]: one hint per position.
            items = [_revive(v, a) for v, a in zip(value, args, strict=False)]
        else:
            items = [_revive(v, args[0] if args else None) for v in value]
        return items if origin is list else origin(items)
    if origin in (dict, Mapping) and isinstance(value, Mapping) and len(args) == 2:
        return {k: _revive(v, args[1]) for k, v in value.items()}
    return value


_DURATION = re.compile(
    r"([-+]?)P(?:([-+]?[0-9]+)D)?"
    r"(?:T(?:([-+]?[0-9]+)H)?(?:([-+]?[0-9]+)M)?(?:([-+]?)([0-9]+)(?:[.,]([0-9]{0,9}))?S)?)?",
    re.IGNORECASE,
)


def _parse_duration(text: str) -> timedelta:
    """``Duration.parse``: ``PnDTnHnMn.nS``, each part optional and signed."""
    match = _DURATION.fullmatch(text)
    if (
        match is None
        or match.group(2, 3, 4, 6) == (None, None, None, None)
        or text.upper().endswith("T")
    ):
        raise ValidationError(f"{text!r} is not an ISO-8601 duration")
    negate, days, hours, minutes, sec_sign, secs, fraction = match.groups()
    seconds = timedelta(
        seconds=int(secs or 0), microseconds=int((fraction or "").ljust(6, "0")[:6])
    )
    if sec_sign == "-":
        seconds = -seconds
    total = (
        timedelta(days=int(days or 0), hours=int(hours or 0), minutes=int(minutes or 0)) + seconds
    )
    return -total if negate == "-" else total
