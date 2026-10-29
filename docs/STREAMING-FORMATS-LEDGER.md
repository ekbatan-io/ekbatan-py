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

## Part 2: as robust as Java's readers

The brief (the user, 2026-10-29): "compare it with the java equivalent, i want it to be as robust
as that". Java's readers are the classes generated from the two schemas - `ActionEvent.fromByteBuffer`
and `MyEvent.fromByteBuffer` (Avro 1.12.1), `ActionEvent.parseFrom` and `MyEvent.parseFrom`
(protobuf-java 4.28.3). Proven by twin runs: one probe per format runs Java's generated classes on
every input below and keeps the bytes, and a Python probe reads the same bytes
(`parity-probes/streaming/`).

**As robust** means, for each input:

1. what Java reads, Python reads to the same values;
2. what Java refuses, Python refuses with a `ValueError` - never another exception, a hang, or a
   wrong value;
3. what Java's generated classes pass through as plain strings but Python's typed `ActionEvent`
   cannot hold (an id that is not a UUID, `action_params` that is not a JSON object, a time
   outside Python's `datetime`), Python refuses with a `ValueError` naming the field - a recorded
   difference;
4. what Java offers to read more (a writer's older or newer schema, by
   `getDecoder().addSchema`), Python offers too.

### Avro envelope: `ActionEvent.fromByteBuffer` against `ActionEvent.from_avro`

| # | input | Java | Python | verdict |
| --- | --- | --- | --- | --- |
| AE01 | a message Java's transform wrote, with a model | reads | reads | same values |
| AE02 | the same, with no model | reads | reads | same values |
| AE03 | empty | BadHeaderException | `ValueError`: the message is not in Avro's single-object encoding | both refuse |
| AE04 | one byte | BadHeaderException | `ValueError`: the message is not in Avro's single-object encoding | both refuse |
| AE05 | the marker and half a fingerprint | BadHeaderException | `ValueError`: the message is not in Avro's single-object encoding | both refuse |
| AE06 | the header and no body | AvroRuntimeException | `ValueError`: the message cannot be read as io.ekbatan.events.streaming.actionevent.avro.ActionEvent:... | both refuse |
| AE07 | the body one byte short | AvroRuntimeException | `ValueError`: the message cannot be read as io.ekbatan.events.streaming.actionevent.avro.ActionEvent:... | both refuse |
| AE08 | the body cut in half | AvroRuntimeException | `ValueError`: the message cannot be read as io.ekbatan.events.streaming.actionevent.avro.ActionEvent:... | both refuse |
| AE09 | two bytes left over after the record | reads | reads | same values |
| AE10 | another marker | BadHeaderException | `ValueError`: the message is not in Avro's single-object encoding | both refuse |
| AE11 | another fingerprint | MissingSchemaException | `ValueError`: the message was written with another schema: an unknown fingerprint | both refuse |
| AE12 | the body alone, no header | BadHeaderException | `ValueError`: the message is not in Avro's single-object encoding | both refuse |
| AE13 | a JSON document | BadHeaderException | `ValueError`: the message is not in Avro's single-object encoding | both refuse |
| AE14 | a Protobuf message | BadHeaderException | `ValueError`: the message is not in Avro's single-object encoding | both refuse |
| AE15 | a union branch out of range | ArrayIndexOutOfBoundsException | `ValueError`: the message cannot be read as io.ekbatan.events.streaming.actionevent.avro.ActionEvent:... | both refuse |
| AE16 | a negative string length | AvroRuntimeException | `ValueError`: the message cannot be read as io.ekbatan.events.streaming.actionevent.avro.ActionEvent:... | both refuse |
| AE17 | a string length past the end | AvroRuntimeException | `ValueError`: the message cannot be read as io.ekbatan.events.streaming.actionevent.avro.ActionEvent:... | both refuse |
| AE18 | an id that is not a UUID | reads | `ValueError`: id is not a UUID: 'not-a-uuid' | recorded: Java passes the string through; Python's typed field cannot hold it, and the error names it |
| AE19 | `action_params` that is not JSON | reads | `ValueError`: action_params is not JSON: Expecting value: line 1 column 1 (char 0) | recorded: Java passes the string through; Python's typed field cannot hold it, and the error names it |
| AE20 | `action_params` that is JSON but not an object | reads | `ValueError`: action_params is JSON but not an object: it holds an array | recorded: Java passes the string through; Python's typed field cannot hold it, and the error names it |
| AE21 | a time after year 9999 | reads | `ValueError`: started_date is 253402300800000000 microseconds from the epoch, outside the years a dat... | recorded: Java's `Instant` holds it; a `datetime` cannot, and the error names the field |
| AE22 | a time before 1970 | reads | reads | same values |
| AE23 | no payload, with an event type | reads | reads | same values |
| AE24 | a sentinel: no event type, no payload | reads | reads | same values |
| AE25 | written with only some of the envelope's fields, as the transform allows | MissingSchemaException | `ValueError`: the message was written with another schema: an unknown fingerprint | both refuse |
| AE26 | written with a newer envelope: one more field | MissingSchemaException | `ValueError`: the message was written with another schema: an unknown fingerprint | both refuse |
| AE27 | AE26, with the newer schema registered | reads | reads | same values |

### Avro payload: `MyEvent.fromByteBuffer` against `read_avro_payload`

| # | input | Java | Python | verdict |
| --- | --- | --- | --- | --- |
| AP01 | a payload written as the transform writes one | reads | reads | same values |
| AP02 | the largest decimal the type allows | reads | reads | same values |
| AP13 | a negative decimal | reads | reads | same values |
| AP03 | empty | BadHeaderException | `ValueError`: the payload is not in Avro's single-object encoding | both refuse |
| AP04 | the header and no body | AvroRuntimeException | `ValueError`: the payload cannot be read as com.example.finance.avro.WalletMoneyDepositedEvent: EOFEr... | both refuse |
| AP05 | the body one byte short | AvroRuntimeException | `ValueError`: the payload cannot be read as com.example.finance.avro.WalletMoneyDepositedEvent: EOFEr... | both refuse |
| AP06 | two bytes left over | reads | reads | same values |
| AP07 | another schema with the same name | MissingSchemaException | `ValueError`: the payload was written with another schema: an unknown fingerprint | both refuse |
| AP08 | written with the schema's older version | MissingSchemaException | `ValueError`: the payload was written with another schema: an unknown fingerprint | both refuse |
| AP09 | AP08, with the older schema registered | reads | reads | same values |
| AP10 | written with a newer version, registered | reads | reads | same values |
| AP11 | a decimal with no bytes | NumberFormatException | reads | recorded: fastavro reads no bytes as 0, Java's BigInteger refuses them; no writer of ours sends one |
| AP12 | the envelope, given as a payload | MissingSchemaException | `ValueError`: the payload was written with another schema: an unknown fingerprint, and no writer sche... | both refuse |
| AP14 | written with a version lacking a field the reader has no default for, registered | AvroTypeException | `ValueError`: the payload cannot be read as com.example.finance.avro.WalletMoneyDepositedEvent: Schem... | both refuse |

### Protobuf envelope: `ActionEvent.parseFrom` against `ActionEvent.from_protobuf`

| # | input | Java | Python | verdict |
| --- | --- | --- | --- | --- |
| PE01 | a message Java's transform wrote, with a model | reads | reads | same values |
| PE02 | the same, with no model | reads | reads | same values |
| PE03 | empty | reads | `ValueError`: id is not a UUID: '' | recorded: Java passes the string through; Python's typed field cannot hold it, and the error names it |
| PE04 | one byte short | InvalidProtocolBufferException | `ValueError`: the message is not a protobuf ActionEvent: Error parsing message with type 'io.ekbatan.... | both refuse |
| PE05 | cut in half | InvalidProtocolBufferException | `ValueError`: the message is not a protobuf ActionEvent: Error parsing message with type 'io.ekbatan.... | both refuse |
| PE06 | two zero bytes left over | InvalidProtocolBufferException | `ValueError`: the message is not a protobuf ActionEvent: Error parsing message with type 'io.ekbatan.... | both refuse |
| PE07 | an unknown field, as a newer envelope would carry | reads | reads | same values |
| PE08 | a known field with the wrong wire type | reads | reads | same values |
| PE09 | a string that is not UTF-8 | InvalidProtocolBufferException | `ValueError`: the message is not a protobuf ActionEvent: Error parsing message with type 'io.ekbatan.... | both refuse |
| PE10 | an Avro message | InvalidProtocolBufferException | `ValueError`: the message is not a protobuf ActionEvent: Error parsing message with type 'io.ekbatan.... | both refuse |
| PE11 | a JSON document | InvalidProtocolBufferException | `ValueError`: the message is not a protobuf ActionEvent: Error parsing message with type 'io.ekbatan.... | both refuse |
| PE12 | an id that is not a UUID | reads | `ValueError`: id is not a UUID: 'not-a-uuid' | recorded: Java passes the string through; Python's typed field cannot hold it, and the error names it |
| PE13 | `action_params` that is not JSON | reads | `ValueError`: action_params is not JSON: Expecting value: line 1 column 1 (char 0) | recorded: Java passes the string through; Python's typed field cannot hold it, and the error names it |
| PE14 | no `action_id`, as a descriptor with only some fields writes | reads | `ValueError`: action_id is not a UUID: '' | recorded: Java passes the string through; Python's typed field cannot hold it, and the error names it |
| PE15 | a Timestamp with nanoseconds below a microsecond | reads | `ValueError`: started_date has 789 nanoseconds below a microsecond, which a datetime cannot hold | recorded: Java's `Timestamp` holds it; a `datetime` cannot, and the error names the field |
| PE16 | a Timestamp after year 9999 | reads | `ValueError`: started_date is 253402300800000000 microseconds from the epoch, outside the years a dat... | recorded: Java's `Timestamp` holds it; a `datetime` cannot, and the error names the field |
| PE17 | no payload, with an event type | reads | reads | same values |
| PE18 | a sentinel: no event type | reads | reads | same values |

### Protobuf payload: `MyEvent.parseFrom` against the class's `FromString`

Protobuf's own library on both sides, not ekbatan's code; here to show the two agree.

| # | input | Java | Python | verdict |
| --- | --- | --- | --- | --- |
| PP01 | a payload written as the transform writes one | reads | reads | same values |
| PP02 | one byte short | InvalidProtocolBufferException | `ValueError`: DecodeError: Error parsing message with type 'com.example.finance.proto.WalletMoneyDepo... | both refuse |
| PP03 | an unknown field | reads | reads | same values |

### What the run shows

62 inputs, the same bytes on both sides (`tests/fixtures/streaming/robustness/`, written by Java):

- **21 read to the same values**, including a newer envelope and an older and a newer payload once
  their schemas are given - Python's `writer_schemas`, Java's `getDecoder().addSchema`.
- **30 refused by both**, Python always with a `ValueError`. Before this part, eight of them -
  bytes cut short or corrupt - raised fastavro's `EOFError` or `IndexError`.
- **10 read by Java's generated classes and refused by Python**, each with a `ValueError` naming
  the field: Java hands over the id, `action_params` and the times as they are on the wire, and
  Python's `ActionEvent` holds a UUID, a JSON object and a `datetime`. Before this part, these
  errors did not name the field, a time after year 9999 raised an `OverflowError`, and a Protobuf
  time with nanoseconds below a microsecond was cut to the microsecond without a word.
- **1 read by Python and refused by Java**, AP11: a decimal with no bytes. Java's `BigInteger`
  refuses an empty array; fastavro reads it as zero, and the Avro specification does not say
  which is right. The transform cannot write one - a `BigDecimal`'s unscaled bytes are never
  empty - so it is recorded, not worked around.

Also run: the readers shared by eight threads, 400 rounds, every result the same
(`test_the_readers_can_be_shared_between_threads`); the streaming tests under gevent.

| # | item | status | proof |
| --- | --- | --- | --- |
| R4 | Java's generated classes run on every input, the Java repository left as it was | done | `ParityRobustnessAvroProbeTest` and `ParityRobustnessProtobufProbeTest` in the pipeline modules; `git status` clean after |
| R5 | Python's readers on the same bytes, compared | done | `probe_robustness.py`; the tables above |
| R6 | the comparison kept as tests, red before and green after | done | `tests/unit/test_streaming_robustness.py`: 49 failed, 16 passed before; 66 passed after |
