"""Reading what Java's `OutboxToAvroTransform` writes: Avro single-object encoding, twice over.

The envelope and the payload are each a two-byte marker, the 8-byte CRC-64-AVRO fingerprint of
the writer's schema, then the Avro binary. Java's generated `fromByteBuffer` checks the marker and
the fingerprint before it reads a byte of data; this does the same, so a message written with
another schema is refused rather than misread. Needs `fastavro`, from the `avro` extra.
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from functools import cache, lru_cache
from importlib.resources import files
from typing import Any

#: Avro's single-object marker, `C3 01`, ahead of the fingerprint.
_MARKER = b"\xc3\x01"
_HEADER = len(_MARKER) + 8

#: The envelope's `timestamp-micros` fields: on the wire, a count of microseconds since the epoch.
_TIMES = ("started_date", "completion_date", "event_date")


def _fastavro() -> Any:
    try:
        import fastavro
    except ImportError:
        raise ImportError(
            "reading the Avro stream needs fastavro: install ekbatan with its avro extra, "
            "pip install 'ekbatan[avro]'"
        ) from None
    return fastavro


@cache
def _envelope() -> tuple[Any, bytes]:
    """`ActionEvent.avsc`, Java's own copy, parsed - and its fingerprint.

    The times are read as the counts they are on the wire, not as fastavro's zoned datetimes, so
    nothing here converts one. The fingerprint is the same either way: a logical type is not part
    of the canonical form it is taken from.
    """
    schema = json.loads((files(__package__) / "ActionEvent.avsc").read_text())
    for field in schema["fields"]:
        if field["name"] in _TIMES:
            field["type"] = field["type"]["type"]
    parsed = _fastavro().parse_schema(schema)
    return parsed, _fingerprint(parsed)


@lru_cache(maxsize=256)
def _payload_schema(text: str) -> tuple[Any, bytes]:
    """A payload schema parsed, and its fingerprint: once per schema, not once per message."""
    schema = _fastavro().parse_schema(json.loads(text))
    return schema, _fingerprint(schema)


def _fingerprint(parsed: Any) -> bytes:
    from fastavro.schema import fingerprint, to_parsing_canonical_form

    # The order the bytes sit in the header, measured against Java's own messages.
    return bytes.fromhex(fingerprint(to_parsing_canonical_form(parsed), "CRC-64-AVRO"))


def _read(data: bytes, schema: Any, fingerprint: bytes, what: str) -> dict[str, Any]:
    if data[: len(_MARKER)] != _MARKER or len(data) < _HEADER:
        raise ValueError(
            f"{what} is not in Avro's single-object encoding, which is what Java's "
            f"OutboxToAvroTransform writes: it does not start with C3 01"
        )
    written_with = data[len(_MARKER) : _HEADER]
    if written_with != fingerprint:
        raise ValueError(
            f"{what} was written with another schema: fingerprint {written_with.hex()}, where "
            f"this reader's {schema['name']} is {fingerprint.hex()}"
        )
    body = io.BytesIO(data[_HEADER:])
    record: dict[str, Any] = _fastavro().schemaless_reader(body, schema, schema)
    return record


def read(data: bytes) -> dict[str, Any]:
    """The envelope, as the wire has it: ids and `action_params` as strings, times as epoch
    microseconds. Its payload stays the bytes it is: the event's own single-object message."""
    envelope, fingerprint = _envelope()
    return _read(data, envelope, fingerprint, "the message")


def read_payload(data: bytes, schema: Mapping[str, Any]) -> dict[str, Any]:
    """A payload, read with the schema of its event type."""
    parsed, fingerprint = _payload_schema(json.dumps(schema, sort_keys=True))
    return _read(data, parsed, fingerprint, "the payload")
