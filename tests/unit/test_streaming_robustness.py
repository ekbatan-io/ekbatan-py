"""Python's readers held to Java's, input by input.

`tests/fixtures/streaming/robustness/` holds the inputs of the comparison and, in each `java.out`,
what Java's generated classes did with them: `ActionEvent.fromByteBuffer` and the payload class's
`fromByteBuffer` for Avro, `ActionEvent.parseFrom` and the payload class's `parseFrom` for
Protobuf. `parity-probes/streaming/` wrote both. Every input is a case here, held to three rules:

- what Java reads, Python reads, to the same values;
- what Java refuses, Python refuses, with a `ValueError`;
- what Java's generated classes hand over as a plain string but a typed `ActionEvent` cannot hold
  - an id that is not a UUID, `action_params` that is not a JSON object, a time a `datetime`
  cannot hold - Python refuses with a `ValueError` naming the field.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ekbatan.streaming import ActionEvent, read_avro_payload

ROBUSTNESS = Path(__file__).resolve().parents[1] / "fixtures" / "streaming" / "robustness"
AVRO = ROBUSTNESS / "avro"
PROTOBUF = ROBUSTNESS / "protobuf"

#: Read by Java's generated classes, refused by Python, and the field each refusal names.
PYTHON_REFUSES = {
    "AE18": "id",  # not a UUID
    "AE19": "action_params",  # not JSON
    "AE20": "action_params",  # JSON, but an array
    "AE21": "started_date",  # after year 9999
    "PE03": "id",  # an empty message: every field at its default, so the id is ""
    "PE12": "id",
    "PE13": "action_params",
    "PE14": "action_id",  # "", written with a descriptor that lacks the field
    "PE15": "started_date",  # nanoseconds below a microsecond
    "PE16": "started_date",  # after year 9999
}

#: Refused by Java, read by Python: a decimal with no bytes. Java's BigInteger refuses an empty
#: array; fastavro reads it as zero. The transform cannot write one - a BigDecimal's unscaled
#: bytes are never empty - so this records the difference rather than working around fastavro.
PYTHON_READS = {"AP11": {"modelId": "wallet-1", "modelName": "Wallet", "amount": "0.00"}}

#: The writer schemas Java's decoders held for each case, added in the order its probe ran.
_ALL_THREE = ["payload-older.avsc", "payload-newer.avsc", "payload-lacking.avsc"]
WRITERS = {
    "AE27": ["envelope-newer.avsc"],
    "AP09": ["payload-older.avsc"],
    "AP10": ["payload-older.avsc", "payload-newer.avsc"],
    "AP14": _ALL_THREE,
    "AP11": _ALL_THREE,
    "AP12": _ALL_THREE,
}

EPOCH = datetime(1970, 1, 1)


def java(folder: Path) -> dict[str, tuple[str, str]]:
    outcomes = {}
    for line in (folder / "java.out").read_text().splitlines():
        case, status, detail = line.split("\t", 2)
        outcomes[case] = (status, detail)
    return outcomes


JAVA_AVRO = java(AVRO)
JAVA_PROTOBUF = java(PROTOBUF)


def schema(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((AVRO / name).read_text())
    return loaded


def envelope(event: ActionEvent) -> dict[str, Any]:
    """The event as Java's probe printed one: ids as text, times as microseconds since the
    epoch, the payload's bytes as hex."""

    def micros(value: datetime) -> str:
        return str((value - EPOCH) // timedelta(microseconds=1))

    payload = event.payload
    return {
        "id": str(event.id),
        "namespace": event.namespace,
        "action_id": str(event.action_id),
        "action_name": event.action_name,
        "action_params": json.dumps(event.action_params, separators=(",", ":")),
        "started_date": micros(event.started_date),
        "completion_date": micros(event.completion_date),
        "model_id": event.model_id,
        "model_type": event.model_type,
        "event_type": event.event_type,
        "payload": payload.hex() if isinstance(payload, bytes) else payload,
        "event_date": micros(event.event_date),
        "delivered": event.delivered,
    }


def read_avro(case: str) -> dict[str, Any]:
    data = (AVRO / "cases" / f"{case}.bin").read_bytes()
    writers = [schema(name) for name in WRITERS.get(case, [])]
    if case.startswith("AE"):
        return envelope(ActionEvent.from_avro(data, writer_schemas=writers))
    record = read_avro_payload(data, schema("payload.avsc"), writer_schemas=writers)
    return {
        "modelId": record["modelId"],
        "modelName": record["modelName"],
        "amount": format(record["amount"], "f"),
    }


def deposited() -> Any:
    """`com.example.finance.proto.WalletMoneyDepositedEvent`, from the descriptor Java kept."""
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    files = descriptor_pb2.FileDescriptorSet.FromString((PROTOBUF / "payload.desc").read_bytes())
    pool = descriptor_pool.DescriptorPool()
    for file in files.file:
        pool.Add(file)
    return message_factory.GetMessageClass(
        pool.FindMessageTypeByName("com.example.finance.proto.WalletMoneyDepositedEvent")
    )


def read_protobuf(case: str) -> dict[str, Any]:
    data = (PROTOBUF / "cases" / f"{case}.bin").read_bytes()
    if case.startswith("PE"):
        return envelope(ActionEvent.from_protobuf(data))
    message = deposited().FromString(data)
    return {"modelId": message.modelId, "modelName": message.modelName, "amount": message.amount}


def held_to_java(case: str, outcome: tuple[str, str], read: Callable[[str], Any]) -> None:
    status, detail = outcome
    if case in PYTHON_REFUSES:
        assert status == "OK", f"Java no longer reads {case}: {detail}"
        with pytest.raises(ValueError, match=rf"\b{PYTHON_REFUSES[case]} "):
            read(case)
    elif case in PYTHON_READS:
        assert status == "ERR", f"Java now reads {case}"
        assert read(case) == PYTHON_READS[case]
    elif status == "OK":
        assert read(case) == json.loads(detail)
    else:
        with pytest.raises(ValueError):
            read(case)


@pytest.mark.parametrize("case", sorted(JAVA_AVRO))
def test_avro_as_java_reads_it(case: str) -> None:
    held_to_java(case, JAVA_AVRO[case], read_avro)


@pytest.mark.parametrize("case", sorted(c for c in JAVA_PROTOBUF if c.startswith("PE")))
def test_protobuf_as_java_reads_it(case: str) -> None:
    held_to_java(case, JAVA_PROTOBUF[case], read_protobuf)


@pytest.mark.parametrize("case", sorted(c for c in JAVA_PROTOBUF if c.startswith("PP")))
def test_a_protobuf_payload_as_java_reads_it(case: str) -> None:
    """The payload class is the consumer's own, so its errors are protobuf's: a `DecodeError`,
    as Java's are protobuf-java's `InvalidProtocolBufferException`."""
    from google.protobuf.message import DecodeError

    status, detail = JAVA_PROTOBUF[case]
    if status == "OK":
        assert read_protobuf(case) == json.loads(detail)
    else:
        with pytest.raises(DecodeError):
            read_protobuf(case)


# -- what the table does not show --------------------------------------------------------------


def test_every_input_of_the_comparison_is_a_case() -> None:
    """One `java.out` line per case file, and the other way round: no input left unchecked."""
    for folder, outcomes in ((AVRO, JAVA_AVRO), (PROTOBUF, JAVA_PROTOBUF)):
        assert sorted(p.stem for p in (folder / "cases").glob("*.bin")) == sorted(outcomes)


def test_writer_schemas_that_do_not_match_change_nothing() -> None:
    """The schemas a consumer holds are given once, for every message: a message written with the
    reader's own schema is read with it whatever else is given."""
    data = (AVRO / "cases" / "AP01.bin").read_bytes()
    writers = [schema(name) for name in _ALL_THREE]

    assert read_avro_payload(data, schema("payload.avsc"), writer_schemas=writers) == (
        read_avro_payload(data, schema("payload.avsc"))
    )


def test_a_refused_json_column_is_not_repeated_in_the_error() -> None:
    """`action_params` can hold anything an action was given, a password included; the error says
    what is wrong with it and never what it holds."""
    row = {
        "id": "0198f4a2-1c3d-7e4f-8a9b-0c1d2e3f4a5b",
        "namespace": "wallet",
        "action_id": "0198f4a2-1c3d-7e4f-8a9b-0c1d2e3f4a5c",
        "action_name": "SignInAction",
        "action_params": "password=s3cret-value",
        "started_date": 0,
        "completion_date": 0,
        "event_date": 0,
    }
    with pytest.raises(ValueError, match="action_params ") as refused:
        ActionEvent.from_message(
            row, json_columns_are_strings=True, timestamps_are_epoch_micros=True
        )
    assert "s3cret" not in str(refused.value)

    row["action_params"] = '["s3cret-value"]'
    with pytest.raises(ValueError, match="action_params ") as refused:
        ActionEvent.from_message(
            row, json_columns_are_strings=True, timestamps_are_epoch_micros=True
        )
    assert "s3cret" not in str(refused.value)


def test_the_readers_can_be_shared_between_threads() -> None:
    """Java's generated decoders are shared by every thread of a consumer; so are these - one
    cached schema per version, read by all."""
    from concurrent.futures import ThreadPoolExecutor

    cases = [("AE01", read_avro), ("AE27", read_avro), ("AP10", read_avro)]
    cases.append(("PE01", read_protobuf))
    expected = [read(case) for case, read in cases]

    def read_all(_: int) -> list[dict[str, Any]]:
        return [read(case) for case, read in cases]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(read_all, range(400)))
    assert all(result == expected for result in results)
