"""Reading what Java's `OutboxToProtobufTransform` writes: plain protobuf bytes.

The envelope's message type comes from `ActionEvent.desc` - Java's own compiled descriptor set,
built with `--include_imports`, so `google/protobuf/timestamp.proto` travels with it - loaded into
a pool of its own. No code is generated, and the schema is Java's byte for byte. Needs
`protobuf`, from the `protobuf` extra.
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from typing import Any

#: The envelope's full name, from `ActionEvent.proto`'s package.
_ENVELOPE = "io.ekbatan.events.streaming.actionevent.protobuf.ActionEvent"

#: Present only when set: proto3 `optional` fields, which a sentinel row leaves out.
_OPTIONAL = ("model_id", "model_type", "event_type", "payload")


def _protobuf() -> None:
    """Say which extra is missing, rather than leave an ImportError naming `google`."""
    try:
        from google.protobuf import message  # noqa: F401
    except ImportError:
        raise ImportError(
            "reading the Protobuf stream needs protobuf: install ekbatan with its protobuf extra, "
            "pip install 'ekbatan[protobuf]'"
        ) from None


@cache
def _envelope() -> Any:
    """The `ActionEvent` message class, built from Java's descriptor set."""
    _protobuf()
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    descriptors = descriptor_pb2.FileDescriptorSet.FromString(
        (files(__package__) / "ActionEvent.desc").read_bytes()
    )
    pool = descriptor_pool.DescriptorPool()
    for file in descriptors.file:
        pool.Add(file)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName(_ENVELOPE))


def _micros(field: str, stamp: Any) -> int:
    """A Timestamp as a count of microseconds since the epoch, as Avro's are.

    The transform writes whole microseconds - a timestamp column holds no finer - so nanoseconds
    below one are not this writer's; a `datetime` cannot hold them, and dropping them would hand
    back another time. Read as the fields they are, not through `ToMicroseconds`, which refuses
    a time outside years 1 to 9999 without saying which field held it.
    """
    if stamp.nanos % 1000:
        raise ValueError(
            f"{field} has {stamp.nanos % 1000} nanoseconds below a microsecond, which a datetime "
            f"cannot hold"
        )
    micros: int = stamp.seconds * 1_000_000 + stamp.nanos // 1000
    return micros


def read(data: bytes) -> dict[str, Any]:
    """The envelope, as the wire has it: ids and `action_params` as strings, times as epoch
    microseconds. Its payload stays the bytes it is: the event's own protobuf message."""
    _protobuf()
    from google.protobuf.message import DecodeError

    try:
        message = _envelope().FromString(data)
    except DecodeError as error:
        raise ValueError(f"the message is not a protobuf ActionEvent: {error}") from None

    row: dict[str, Any] = {
        "id": message.id,
        "namespace": message.namespace,
        "action_id": message.action_id,
        "action_name": message.action_name,
        "action_params": message.action_params,
        "started_date": _micros("started_date", message.started_date),
        "completion_date": _micros("completion_date", message.completion_date),
        "event_date": _micros("event_date", message.event_date),
        "delivered": message.delivered,
    }
    for name in _OPTIONAL:
        row[name] = getattr(message, name) if message.HasField(name) else None
    return row
