"""Python's readers on the inputs Java's robustness probes kept, compared with what Java did.

    uv run python parity-probes/streaming/probe_robustness.py /tmp/ekbatan-streaming-probe

Reads `robustness-avro/` and `robustness-protobuf/` under the given folder - the case files and
`java.out` the two Java probes wrote - writes `python.out` beside each `java.out`, and prints one
table row per case: what Java did, what Python did, and the verdict. A line is `ID<TAB>OK<TAB>json`
when the input was read, `ID<TAB>ERR<TAB>Type: message` when it was refused - a `ValueError` in
Python - and, in Python only, `ID<TAB>RAISE<TAB>Type: message` for any other exception.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ekbatan.streaming import ActionEvent, read_avro_payload

EPOCH = datetime(1970, 1, 1)


def micros(value: datetime) -> str:
    return str((value - EPOCH) // timedelta(microseconds=1))


def compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def envelope(event: ActionEvent) -> dict[str, Any]:
    payload = event.payload
    return {
        "id": str(event.id),
        "namespace": event.namespace,
        "action_id": str(event.action_id),
        "action_name": event.action_name,
        "action_params": compact(event.action_params),
        "started_date": micros(event.started_date),
        "completion_date": micros(event.completion_date),
        "model_id": event.model_id,
        "model_type": event.model_type,
        "event_type": event.event_type,
        "payload": payload.hex() if isinstance(payload, bytes) else payload,
        "event_date": micros(event.event_date),
        "delivered": event.delivered,
    }


def outcome(read: Callable[[], dict[str, Any]]) -> str:
    try:
        return "OK\t" + compact(read())
    except ValueError as error:
        return f"ERR\t{type(error).__name__}: {error}".replace("\n", " ")
    except Exception as error:  # what must never happen: anything but a ValueError
        return f"RAISE\t{type(error).__name__}: {error}".replace("\n", " ")


def avro(folder: Path) -> list[str]:
    cases = folder / "cases"

    def schema(name: str) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads((folder / name).read_text())
        return loaded

    payload_schema = schema("payload.avsc")
    # The schemas Java's decoder holds when it reads each case: it keeps every one added to it,
    # and adds them in the order its probe runs - AP09, AP10, AP14, then AP11 and AP12.
    older, newer = schema("payload-older.avsc"), schema("payload-newer.avsc")
    all_three = [older, newer, schema("payload-lacking.avsc")]
    registered = {"AP09": [older], "AP10": [older, newer]}
    registered |= {"AP14": all_three, "AP11": all_three, "AP12": all_three}
    lines = []
    for path in sorted(cases.glob("*.bin")):
        case, data = path.stem, path.read_bytes()
        if case.startswith("AE"):
            writers = [schema("envelope-newer.avsc")] if case == "AE27" else []

            def read_envelope(data: bytes = data, writers: list[Any] = writers) -> dict[str, Any]:
                if writers:
                    return envelope(ActionEvent.from_avro(data, writer_schemas=writers))
                return envelope(ActionEvent.from_avro(data))

            lines.append(f"{case}\t{outcome(read_envelope)}")
        else:
            writers = registered.get(case, [])

            def read_payload(data: bytes = data, writers: list[Any] = writers) -> dict[str, Any]:
                record = (
                    read_avro_payload(data, payload_schema, writer_schemas=writers)
                    if writers
                    else read_avro_payload(data, payload_schema)
                )
                return {
                    "modelId": record["modelId"],
                    "modelName": record["modelName"],
                    "amount": format(record["amount"], "f"),
                }

            lines.append(f"{case}\t{outcome(read_payload)}")
    return lines


def protobuf(folder: Path) -> list[str]:
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    files = descriptor_pb2.FileDescriptorSet.FromString((folder / "payload.desc").read_bytes())
    pool = descriptor_pool.DescriptorPool()
    for file in files.file:
        pool.Add(file)
    deposited = message_factory.GetMessageClass(
        pool.FindMessageTypeByName("com.example.finance.proto.WalletMoneyDepositedEvent")
    )

    lines = []
    for path in sorted((folder / "cases").glob("*.bin")):
        case, data = path.stem, path.read_bytes()
        if case.startswith("PE"):

            def read_envelope(data: bytes = data) -> dict[str, Any]:
                return envelope(ActionEvent.from_protobuf(data))

            lines.append(f"{case}\t{outcome(read_envelope)}")
        else:

            def read_payload(data: bytes = data) -> dict[str, Any]:
                try:
                    message = deposited.FromString(data)
                except Exception as error:  # protobuf's own DecodeError, as Java's
                    raise ValueError(f"{type(error).__name__}: {error}") from None
                return {
                    "modelId": message.modelId,
                    "modelName": message.modelName,
                    "amount": message.amount,
                }

            lines.append(f"{case}\t{outcome(read_payload)}")
    return lines


def verdict(java: tuple[str, str], python: tuple[str, str]) -> str:
    (j, jv), (p, pv) = java, python
    if p == "RAISE":
        return "**PROBLEM**: Python raises something other than a ValueError"
    if j == "OK" and p == "OK":
        return "same" if json.loads(jv) == json.loads(pv) else "**PROBLEM**: different values"
    if j == "ERR" and p == "ERR":
        return "both refuse"
    if j == "OK":
        return "Python refuses what Java reads"
    return "**PROBLEM**: Python reads what Java refuses"


def compare(folder: Path, python_lines: list[str]) -> None:
    (folder / "python.out").write_text("\n".join(python_lines) + "\n")

    def parse(lines: list[str]) -> dict[str, tuple[str, str]]:
        return {
            line.split("\t")[0]: (line.split("\t")[1], line.split("\t", 2)[2])
            for line in lines
            if line
        }

    java = parse((folder / "java.out").read_text().splitlines())
    python = parse(python_lines)
    for case in sorted(java):
        (j, jv), (p, pv) = java[case], python[case]
        shown_j = "reads" if j == "OK" else jv
        shown_p = "reads" if p == "OK" else pv
        print(f"| {case} | {shown_j} | {shown_p} | {verdict(java[case], python[case])} |")


def main() -> None:
    root = Path(sys.argv[1])
    compare(root / "robustness-avro", avro(root / "robustness-avro"))
    compare(root / "robustness-protobuf", protobuf(root / "robustness-protobuf"))


if __name__ == "__main__":
    main()
