# Reading the Avro and Protobuf streams in Python - ledger (Dracula 78)

The brief (the user, 2026-10-29, "do it now"): Java ships consumer-side readers for the three
formats its Kafka Connect transforms write - a JSON class, and the Avro and Protobuf schemas with
classes generated from them. Python reads JSON only. Give Python the other two: the same schemas,
one `ActionEvent` with a way in per format, behind optional extras so nobody gets the libraries who
does not need them - `ekbatan[avro]` (fastavro) and `ekbatan[protobuf]` (protobuf). Prove it by
running both ports: Java's own transform writes the messages, Python reads them.

What Java's pipeline puts on Kafka (`ekbatan-events/streaming/README.md` in the Java repository):

- **Avro**, no Schema Registry: the envelope and the payload are each in Avro's single-object
  encoding (a two-byte marker, the schema's 8-byte fingerprint, then the data). Java reads
  `ActionEvent.fromByteBuffer(bytes)`, then `MyEvent.fromByteBuffer(event.getPayload())`.
- **Protobuf**: plain bytes. Java reads `ActionEvent.parseFrom(bytes)`, then
  `MyEvent.parseFrom(event.getPayload())`.

Judgement calls go in `DECISIONS-TO-REVIEW.md` under "Reading the Avro and Protobuf streams
(EKB-0578-)". Status: `todo` / `doing` / `done`. A row is `done` only when a run shows it.

## Java side (a probe run in the Java repository, never committed there)

| # | item | status | proof |
| --- | --- | --- | --- |
| J1 | Avro: Java's `OutboxToAvroTransform` writes messages for known rows, with a payload schema that has a decimal, a timestamp and a union; the bytes and the schema are kept as fixtures | done | `ParityStreamingAvroProbeTest` run in the Java repository (EKB-0081, aee7d3a) wrote `tests/fixtures/streaming/avro/` |
| J2 | Protobuf: Java's `OutboxToProtobufTransform` writes messages the same way, with a payload descriptor; bytes and descriptor kept as fixtures | done | `ParityStreamingProtobufProbeTest`, same run, wrote `tests/fixtures/streaming/protobuf/` |
| J3 | the envelope schemas copied from Java: `ActionEvent.avsc` (source) and the compiled `ActionEvent.desc` (Java's build output), plus `ActionEvent.proto` for reading | done | `cmp` against Java's `ActionEvent.avsc` and `ActionEvent.proto` sources: identical; `test_the_envelope_schemas_are_java_s_byte_for_byte` pins the `.avsc` and `.desc` to the probe's copies |
| J4 | the probe source kept under `parity-probes/streaming/`, and the Java repository left as it was found | done | the two probes and `parity-probes/streaming/README.md`; `git status` in the Java repository clean after the run |

## Python side

| # | item | status | proof |
| --- | --- | --- | --- |
| P1 | optional extras `avro` (fastavro) and `protobuf` (protobuf) | done | `pyproject.toml`; the built wheel's metadata lists `fastavro>=1.9; extra == 'avro'` and `protobuf>=5.26; extra == 'protobuf'` |
| P2 | `ekbatan.streaming` becomes a package carrying the schema files; its imports unchanged | done | the built wheel holds `ekbatan/streaming/ActionEvent.{avsc,desc,proto}`; `test_importing_streaming_loads_neither_library` |
| P3 | `ActionEvent.from_avro(data)`: the envelope, single-object decoding with a fingerprint check; `payload` stays bytes, read by `read_avro_payload(data, schema)` with the same check - Java's two steps (changed from one call, see the decisions) | done | `test_an_avro_message_java_wrote_is_read_whole` and four more |
| P4 | `ActionEvent.from_protobuf(data)`: the envelope parsed with Java's compiled descriptor; `payload` stays bytes, read by the event's own message class's `FromString` | done | `test_a_protobuf_message_java_wrote_is_read_whole` and two more |
| P5 | tests read the Java-written fixtures, and refuse what they must (a foreign fingerprint at either level, bytes in neither format, a missing extra) | done | `tests/unit/test_streaming_formats.py`, 13 tests |
| P6 | docs and website: reading each format in Python; the extras in the install table | done | "Reading Avro and Protobuf" in `docs/events/event-streaming.md` and its website copy; both install tables; the consuming-events page |
| P7 | Dracula 78 matched; decisions to review | done | `DRACULAS.md` row 78, `dracula/B-actions-events.md`; "Reading the Avro and Protobuf streams (EKB-0578-)" |

## Runs

| # | item | status | proof |
| --- | --- | --- | --- |
| R1 | the new tests, red before and green after | done | red: against 883e46a5 the file cannot import `read_avro_payload`; green: 13 passed, and 13 passed under gevent |
| R2 | lint, mypy, docs checks; the SQLite suite | done | `make lint` exit 0; `make test`: unit 2185 passed, SQLite 3325 passed; the website builds and `npm run check` passes |
| R3 | CI on GitHub | todo | not pushed yet |
