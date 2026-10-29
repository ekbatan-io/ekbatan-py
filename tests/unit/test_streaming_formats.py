"""Reading the Avro and Protobuf messages Java's Kafka Connect transforms write.

The fixtures were written by Java itself: `parity-probes/streaming/` holds the probe that ran
Java's `OutboxToAvroTransform` and `OutboxToProtobufTransform` on two known rows and kept the
bytes they emitted, with the payload schemas they were given. So these read what Java actually
puts on Kafka, not what an encoder of our own agrees with.

Both formats are read in Java's two steps: the envelope, whose payload stays encoded, then the
payload with its event type's own schema. A consumer routes on `event_type` with no schema at all.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from ekbatan.streaming import ActionEvent, read_avro_payload

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "streaming"

#: The probe's rows: three times apart, so no two can be swapped unseen.
STARTED = datetime(2026, 7, 31, 10, 15, 30, 123456)
COMPLETED = datetime(2026, 7, 31, 10, 15, 31, 654321)
EVENT = datetime(2026, 7, 31, 10, 15, 31, 500000)


def an_event(**fields: Any) -> ActionEvent:
    """The first probe row, as every format must read it - payload aside."""
    values: dict[str, Any] = {
        "id": UUID("0198f4a2-1c3d-7e4f-8a9b-0c1d2e3f4a5b"),
        "namespace": "wallet",
        "action_id": UUID("0198f4a2-1c3d-7e4f-8a9b-0c1d2e3f4a5c"),
        "action_name": "WalletDepositMoneyAction",
        "action_params": {"amount": "5.00", "note": "first"},
        "started_date": STARTED,
        "completion_date": COMPLETED,
        "model_id": "wallet-1",
        "model_type": "Wallet",
        "event_type": "TestEvent",
        "event_date": EVENT,
        "delivered": False,
    }
    values.update(fields)
    return ActionEvent(**values)


NO_MODEL = {
    "id": UUID("0198f4a2-1c3d-7e4f-8a9b-0c1d2e3f4a5d"),
    "action_id": UUID("0198f4a2-1c3d-7e4f-8a9b-0c1d2e3f4a5e"),
    "model_id": None,
    "model_type": None,
}


def without_payload(event: ActionEvent) -> ActionEvent:
    return dataclasses.replace(event, payload=None)


# -- Avro --------------------------------------------------------------------------------------


def avro(name: str) -> bytes:
    return (FIXTURES / "avro" / name).read_bytes()


def avro_payload_schema() -> dict[str, Any]:
    schema: dict[str, Any] = json.loads((FIXTURES / "avro" / "TestEvent.avsc").read_text())
    return schema


def test_an_avro_message_java_wrote_is_read_whole() -> None:
    """The envelope needs no payload schema; the payload is read with its own."""
    event = ActionEvent.from_avro(avro("event.bin"))

    assert without_payload(event) == an_event()
    assert isinstance(event.payload, bytes)
    assert read_avro_payload(event.payload, avro_payload_schema()) == {
        "name": "Ada",
        "amount": Decimal("77.10"),
        "at": datetime(2026, 7, 31, 10, 15, 30, 123000, tzinfo=UTC),
        "ref": "ORDER-123",
    }


def test_an_avro_message_with_no_model_and_the_union_s_other_branch() -> None:
    event = ActionEvent.from_avro(avro("no-model.bin"))

    assert without_payload(event) == an_event(**NO_MODEL)
    assert isinstance(event.payload, bytes)
    assert read_avro_payload(event.payload, avro_payload_schema()) == {
        "name": "Bo",
        "amount": Decimal("0.50"),
        "at": datetime(2026, 7, 31, 10, 15, 31, 500000, tzinfo=UTC),
        "ref": 42,
    }


def test_an_avro_payload_schema_with_the_name_but_not_the_fields_is_refused() -> None:
    """The fingerprint is checked before a byte is read, as Java's `fromByteBuffer` checks it:
    a schema that merely shares the name would otherwise misread the payload."""
    event = ActionEvent.from_avro(avro("event.bin"))
    assert isinstance(event.payload, bytes)
    other = avro_payload_schema()
    other["fields"] = [{"name": "name", "type": "string"}]
    with pytest.raises(ValueError, match="the payload was written with another schema"):
        read_avro_payload(event.payload, other)


def test_an_avro_message_written_with_another_envelope_schema_is_refused() -> None:
    data = bytearray(avro("event.bin"))
    data[2] ^= 0xFF  # one byte of the fingerprint
    with pytest.raises(ValueError, match="the message was written with another schema"):
        ActionEvent.from_avro(bytes(data))


def test_bytes_that_are_not_single_object_avro_are_refused() -> None:
    with pytest.raises(ValueError, match="the message is not in Avro's single-object encoding"):
        ActionEvent.from_avro(b"\x00\x01plain avro")
    with pytest.raises(ValueError, match="the payload is not in Avro's single-object encoding"):
        read_avro_payload(b"\x00\x01plain avro", avro_payload_schema())


# -- Protobuf ----------------------------------------------------------------------------------


def protobuf(name: str) -> bytes:
    return (FIXTURES / "protobuf" / name).read_bytes()


def protobuf_payload_type() -> type[Any]:
    """`wallet.proto.TestEvent`, built from the descriptor set the probe gave Java's transform -
    the way a consumer without generated code gets a message class."""
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    files_ = descriptor_pb2.FileDescriptorSet.FromString(protobuf("TestEvent.desc"))
    pool = descriptor_pool.DescriptorPool()
    for file in files_.file:
        pool.Add(file)
    kind: type[Any] = message_factory.GetMessageClass(
        pool.FindMessageTypeByName("wallet.proto.TestEvent")
    )
    return kind


def test_a_protobuf_message_java_wrote_is_read_whole() -> None:
    """The envelope needs no payload class; the payload is read with its own, as Java's
    `MyEvent.parseFrom(event.getPayload())`."""
    test_event = protobuf_payload_type()
    event = ActionEvent.from_protobuf(protobuf("event.bin"))

    assert without_payload(event) == an_event()
    assert test_event.FromString(event.payload) == test_event(
        name="Ada", amount=77.1, ref="ORDER-123"
    )


def test_a_protobuf_message_with_no_model() -> None:
    test_event = protobuf_payload_type()
    event = ActionEvent.from_protobuf(protobuf("no-model.bin"))

    assert without_payload(event) == an_event(**NO_MODEL)
    assert test_event.FromString(event.payload) == test_event(name="Bo", amount=0.5, ref="42")


def test_bytes_that_are_not_a_protobuf_action_event_are_refused() -> None:
    with pytest.raises(ValueError, match="not a protobuf ActionEvent"):
        ActionEvent.from_protobuf(b"\xff\xff\xff\xff")


# -- what ships, and what it needs -------------------------------------------------------------


@pytest.mark.parametrize(
    ("shipped", "java_s"),
    [
        ("ActionEvent.avsc", "avro/ActionEvent.avsc"),
        ("ActionEvent.desc", "protobuf/ActionEvent.desc"),
    ],
)
def test_the_envelope_schemas_are_java_s_byte_for_byte(shipped: str, java_s: str) -> None:
    """Copied from Java's build by the probe; both ports describe one message or neither works."""
    assert (files("ekbatan.streaming") / shipped).read_bytes() == (FIXTURES / java_s).read_bytes()


def test_without_the_avro_extra_it_says_which_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "fastavro", None)
    with pytest.raises(ImportError, match=r"pip install 'ekbatan\[avro\]'"):
        ActionEvent.from_avro(avro("event.bin"))
    with pytest.raises(ImportError, match=r"pip install 'ekbatan\[avro\]'"):
        read_avro_payload(b"", {"type": "record", "name": "Empty", "fields": []})


def test_without_the_protobuf_extra_it_says_which_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "google.protobuf", None)
    with pytest.raises(ImportError, match=r"pip install 'ekbatan\[protobuf\]'"):
        ActionEvent.from_protobuf(protobuf("event.bin"))


def test_importing_streaming_loads_neither_library() -> None:
    """The extras are optional: a consumer of the JSON stream installs neither."""
    probe = (
        "import sys, ekbatan.streaming; "
        "print('fastavro' in sys.modules, 'google.protobuf' in sys.modules)"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert done.stdout.split() == ["False", "False"], done.stdout
