"""Reading what Java's `OutboxToAvroTransform` writes: Avro single-object encoding, twice over.

The envelope and the payload are each a two-byte marker, the 8-byte CRC-64-AVRO fingerprint of
the writer's schema, then the Avro binary. Java's generated `fromByteBuffer` checks the marker and
the fingerprint before it reads a byte of data; this does the same, so a message written with
another schema is refused rather than misread. A message written with another version of the
schema is read once that version is given as a writer schema - Java's `getDecoder().addSchema` -
and Avro's resolution takes it to the reader's. Needs `fastavro`, from the `avro` extra.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterable, Mapping
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


def _key(schema: Mapping[str, Any]) -> str:
    """A schema as text, the same for the same schema however its keys were ordered."""
    return json.dumps(schema, sort_keys=True)


@cache
def _envelope() -> tuple[Any, bytes]:
    """`ActionEvent.avsc`, Java's own copy, parsed - and its fingerprint."""
    return _envelope_schema(_key(json.loads((files(__package__) / "ActionEvent.avsc").read_text())))


@lru_cache(maxsize=256)
def _envelope_schema(text: str) -> tuple[Any, bytes]:
    """An envelope schema, parsed with its times read as the counts they are on the wire.

    fastavro converts a value by its writer's logical type, so a writer schema has the logical
    type taken off too - otherwise a newer envelope's times would come back zoned, and nothing
    here converts one. The fingerprint is the same either way: a logical type is not part of the
    canonical form it is taken from.
    """
    schema = json.loads(text)
    for field in schema["fields"]:
        if field["name"] in _TIMES and isinstance(field["type"], dict):
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


def _read(
    data: bytes,
    reader: tuple[Any, bytes],
    writers: Iterable[tuple[Any, bytes]],
    what: str,
) -> dict[str, Any]:
    fastavro = _fastavro()
    schema, fingerprint = reader
    if data[: len(_MARKER)] != _MARKER or len(data) < _HEADER:
        raise ValueError(
            f"{what} is not in Avro's single-object encoding, which is what Java's "
            f"OutboxToAvroTransform writes: it does not start with C3 01 and a fingerprint"
        )
    written_with = data[len(_MARKER) : _HEADER]
    writer = schema
    if written_with != fingerprint:
        known = {printed: parsed for parsed, printed in writers}
        if written_with not in known:
            given = f", and no writer schema given has it ({len(known)} given)" if known else ""
            raise ValueError(
                f"{what} was written with another schema: fingerprint {written_with.hex()}, where "
                f"this reader's {schema['name']} is {fingerprint.hex()}{given}"
            )
        writer = known[written_with]
    try:
        record: dict[str, Any] = fastavro.schemaless_reader(
            io.BytesIO(data[_HEADER:]), writer, schema
        )
    except Exception as error:
        # Cut short, corrupt, or a writer's version the reader's cannot be resolved from - which
        # fastavro reports as an EOFError, an IndexError or its own error, by where it noticed.
        # The message, not the repr: a UnicodeDecodeError's repr carries the whole input.
        raise ValueError(
            f"{what} cannot be read as {schema['name']}: {type(error).__name__}: {error}"
        ) from error
    return record


def read(data: bytes, writer_schemas: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """The envelope, as the wire has it: ids and `action_params` as strings, times as epoch
    microseconds. Its payload stays the bytes it is: the event's own single-object message."""
    writers = [_envelope_schema(_key(schema)) for schema in writer_schemas]
    return _read(data, _envelope(), writers, "the message")


def read_payload(
    data: bytes, schema: Mapping[str, Any], writer_schemas: Iterable[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    """A payload, read with the schema of its event type."""
    writers = [_payload_schema(_key(writer)) for writer in writer_schemas]
    return _read(data, _payload_schema(_key(schema)), writers, "the payload")
