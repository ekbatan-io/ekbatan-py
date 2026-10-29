# Streaming probes: Java writes the Avro and Protobuf messages Python reads

`tests/unit/test_streaming_formats.py` reads messages that Java's own Kafka Connect transforms
wrote. These two probes are what wrote them. They are tests of the Java repository's transform
modules, copied in for one run and removed again, and never committed there.

| probe | runs | writes, under `$EKBATAN_STREAMING_PROBE_OUT` |
| --- | --- | --- |
| `ParityStreamingAvroProbeTest.java` | `OutboxToAvroTransform` on two rows | `avro/`: `event.bin`, `no-model.bin`, the payload schema `TestEvent.avsc`, and the envelope schema the module's tests use, `ActionEvent.avsc` |
| `ParityStreamingProtobufProbeTest.java` | `OutboxToProtobufTransform` on the same two rows | `protobuf/`: `event.bin`, `no-model.bin`, the payload descriptor set `TestEvent.desc`, and the envelope's compiled descriptor set from the module's build, `ActionEvent.desc` |

The two rows: one with a model, and one without (`model_id` and `model_type` null), whose Avro
payload takes the union's other branch. The payload has a decimal, a timestamp and a union in
Avro, and a string, a double and a string in Protobuf.

## Running them

From the Java repository's root, with its working tree clean:

```bash
PROBES=/path/to/ekbatan-py/parity-probes/streaming
SMT=ekbatan-events/streaming/debezium-smt
cp $PROBES/ParityStreamingAvroProbeTest.java \
   $SMT/avro/src/test/java/io/ekbatan/events/streaming/debeziumsmt/avro/
cp $PROBES/ParityStreamingProtobufProbeTest.java \
   $SMT/protobuf/src/test/java/io/ekbatan/events/streaming/debeziumsmt/protobuf/

EKBATAN_STREAMING_PROBE_OUT=/tmp/ekbatan-streaming-probe ./gradlew \
  :ekbatan-events-streaming-debezium-smt-avro:test --tests '*ParityStreamingAvroProbeTest' \
  :ekbatan-events-streaming-debezium-smt-protobuf:test --tests '*ParityStreamingProtobufProbeTest'

rm $SMT/avro/src/test/java/io/ekbatan/events/streaming/debeziumsmt/avro/ParityStreamingAvroProbeTest.java \
   $SMT/protobuf/src/test/java/io/ekbatan/events/streaming/debeziumsmt/protobuf/ParityStreamingProtobufProbeTest.java
git status --short   # nothing: the repository is as it was
```

Then copy `/tmp/ekbatan-streaming-probe/avro` and `.../protobuf` over
`tests/fixtures/streaming/avro` and `tests/fixtures/streaming/protobuf` here.

## The robustness comparison

`ParityRobustnessAvroProbeTest.java` and `ParityRobustnessProtobufProbeTest.java` run Java's
generated readers - `ActionEvent.fromByteBuffer`, `ActionEvent.parseFrom`, and the payload classes'
own - on 62 inputs: messages as the transforms write them, and every way one can be cut short,
corrupt, foreign or from another schema version. Each input is kept as a file, with `java.out`,
one line per input. They live in the pipeline test modules, which have the generated classes:

```bash
PIPE=ekbatan-integration-tests/event-pipeline
cp $PROBES/ParityRobustnessAvroProbeTest.java \
   $PIPE/debezium-kafka-avro-smt/src/test/java/io/ekbatan/test/event_pipeline/avro_smt/
cp $PROBES/ParityRobustnessProtobufProbeTest.java \
   $PIPE/debezium-kafka-protobuf-smt/src/test/java/io/ekbatan/test/event_pipeline/protobuf_smt/

EKBATAN_STREAMING_PROBE_OUT=/tmp/ekbatan-streaming-probe ./gradlew \
  :ekbatan-integration-tests-event-pipeline-debezium-kafka-avro-smt:test --tests '*ParityRobustnessAvroProbeTest' \
  :ekbatan-integration-tests-event-pipeline-debezium-kafka-protobuf-smt:test --tests '*ParityRobustnessProtobufProbeTest'

rm $PIPE/debezium-kafka-avro-smt/src/test/java/io/ekbatan/test/event_pipeline/avro_smt/ParityRobustnessAvroProbeTest.java \
   $PIPE/debezium-kafka-protobuf-smt/src/test/java/io/ekbatan/test/event_pipeline/protobuf_smt/ParityRobustnessProtobufProbeTest.java
git status --short   # nothing
```

Then, here, `uv run python parity-probes/streaming/probe_robustness.py /tmp/ekbatan-streaming-probe`
reads the same files with Python's readers and prints the comparison, one table row per input.
Copied into `tests/fixtures/streaming/robustness/`, the inputs and `java.out` are what
`tests/unit/test_streaming_robustness.py` holds Python to on every run.

## When Java's envelope schemas change

The package ships Java's envelope files unchanged, in `src/ekbatan/streaming/`:

| file | Java's |
| --- | --- |
| `ActionEvent.avsc` | `ekbatan-events/streaming/action-event/avro/src/main/avro/ActionEvent.avsc` |
| `ActionEvent.proto` | `ekbatan-events/streaming/action-event/protobuf/src/main/proto/ActionEvent.proto` - for reading; the code loads the `.desc` |
| `ActionEvent.desc` | the protobuf module's compiled descriptor set - the probe's `protobuf/ActionEvent.desc` |

`test_the_envelope_schemas_are_java_s_byte_for_byte` compares the shipped `.avsc` and `.desc`
with the probe's copies. When Java changes either schema, run the probes again, copy the new
fixtures and the three files, and the tests show whether Python still reads what Java writes.
