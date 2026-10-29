package io.ekbatan.events.streaming.debeziumsmt.protobuf;

import com.google.protobuf.DescriptorProtos.DescriptorProto;
import com.google.protobuf.DescriptorProtos.FieldDescriptorProto;
import com.google.protobuf.DescriptorProtos.FileDescriptorProto;
import com.google.protobuf.DescriptorProtos.FileDescriptorSet;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.util.Map;
import org.apache.kafka.connect.data.Schema;
import org.apache.kafka.connect.data.SchemaBuilder;
import org.apache.kafka.connect.data.Struct;
import org.apache.kafka.connect.source.SourceRecord;
import org.junit.jupiter.api.Test;

/**
 * A parity probe for ekbatan-py, not a test of this repository: Java's own
 * OutboxToProtobufTransform writes the messages the Python reader is checked against. Copied into
 * this module, run, and removed again - see ekbatan-py/parity-probes/streaming/README.md.
 */
class ParityStreamingProtobufProbeTest {

    static final Instant STARTED = Instant.parse("2026-07-31T10:15:30.123456Z");
    static final Instant COMPLETED = Instant.parse("2026-07-31T10:15:31.654321Z");
    static final Instant EVENT = Instant.parse("2026-07-31T10:15:31.500000Z");

    @Test
    void write_the_fixtures() throws Exception {
        var out = Path.of(System.getenv().getOrDefault(
                        "EKBATAN_STREAMING_PROBE_OUT",
                        System.getProperty("java.io.tmpdir") + "/ekbatan-streaming-probe"))
                .resolve("protobuf");
        Files.createDirectories(out);
        var envelope = Path.of(System.getProperty("ekbatan.actionEventDescriptor"));
        Files.copy(envelope, out.resolve("ActionEvent.desc"), StandardCopyOption.REPLACE_EXISTING);

        // The payload type, declared as protoc would: wallet.proto.TestEvent.
        var payloadFile = FileDescriptorProto.newBuilder()
                .setName("wallet_test_event.proto")
                .setPackage("wallet.proto")
                .setSyntax("proto3")
                .addMessageType(DescriptorProto.newBuilder()
                        .setName("TestEvent")
                        .addField(field("name", 1, FieldDescriptorProto.Type.TYPE_STRING))
                        .addField(field("amount", 2, FieldDescriptorProto.Type.TYPE_DOUBLE))
                        .addField(field("ref", 3, FieldDescriptorProto.Type.TYPE_STRING)))
                .build();
        var payloadDescriptor = out.resolve("TestEvent.desc");
        try (var stream = Files.newOutputStream(payloadDescriptor)) {
            FileDescriptorSet.newBuilder().addFile(payloadFile).build().writeTo(stream);
        }

        var transform = new OutboxToProtobufTransform<SourceRecord>();
        transform.configure(Map.of(
                OutboxToProtobufTransform.ACTION_EVENT_DESCRIPTOR_CONFIG, envelope.toString(),
                OutboxToProtobufTransform.PAYLOAD_DESCRIPTORS_CONFIG,
                "wallet.proto.TestEvent:" + payloadDescriptor));

        write(transform, out.resolve("event.bin"), row(
                "0198f4a2-1c3d-7e4f-8a9b-0c1d2e3f4a5b",
                "0198f4a2-1c3d-7e4f-8a9b-0c1d2e3f4a5c",
                "wallet-1",
                "Wallet",
                "{\"name\":\"Ada\",\"amount\":77.10,\"ref\":\"ORDER-123\"}"));
        write(transform, out.resolve("no-model.bin"), row(
                "0198f4a2-1c3d-7e4f-8a9b-0c1d2e3f4a5d",
                "0198f4a2-1c3d-7e4f-8a9b-0c1d2e3f4a5e",
                null,
                null,
                "{\"name\":\"Bo\",\"amount\":0.5,\"ref\":\"42\"}"));
    }

    static FieldDescriptorProto field(String name, int number, FieldDescriptorProto.Type type) {
        return FieldDescriptorProto.newBuilder()
                .setName(name)
                .setNumber(number)
                .setType(type)
                .setLabel(FieldDescriptorProto.Label.LABEL_OPTIONAL)
                .setJsonName(name)
                .build();
    }

    static void write(OutboxToProtobufTransform<SourceRecord> transform, Path file, Struct row) throws Exception {
        var envelopeSchema = SchemaBuilder.struct()
                .name("debezium.Envelope")
                .field("after", row.schema())
                .field("op", Schema.STRING_SCHEMA)
                .build();
        var envelope = new Struct(envelopeSchema).put("after", row).put("op", "c");
        var record = new SourceRecord(Map.of(), Map.of(), "topic", envelopeSchema, envelope);
        Files.write(file, (byte[]) transform.apply(record).value());
    }

    /** A row as Debezium's PostgreSQL connector sends it: micro timestamps, a boolean flag. */
    static Struct row(String id, String actionId, String modelId, String modelType, String payload) {
        var micros = new SchemaBuilder(Schema.Type.INT64).name("io.debezium.time.MicroTimestamp").build();
        var schema = SchemaBuilder.struct()
                .name("eventlog.events.Value")
                .field("id", Schema.STRING_SCHEMA)
                .field("namespace", Schema.STRING_SCHEMA)
                .field("action_id", Schema.STRING_SCHEMA)
                .field("action_name", Schema.STRING_SCHEMA)
                .field("action_params", Schema.STRING_SCHEMA)
                .field("started_date", micros)
                .field("completion_date", micros)
                .field("model_id", Schema.OPTIONAL_STRING_SCHEMA)
                .field("model_type", Schema.OPTIONAL_STRING_SCHEMA)
                .field("event_type", Schema.OPTIONAL_STRING_SCHEMA)
                .field("payload", Schema.OPTIONAL_STRING_SCHEMA)
                .field("event_date", micros)
                .field("delivered", Schema.BOOLEAN_SCHEMA)
                .build();
        return new Struct(schema)
                .put("id", id)
                .put("namespace", "wallet")
                .put("action_id", actionId)
                .put("action_name", "WalletDepositMoneyAction")
                .put("action_params", "{\"amount\":\"5.00\",\"note\":\"first\"}")
                .put("started_date", micros(STARTED))
                .put("completion_date", micros(COMPLETED))
                .put("model_id", modelId)
                .put("model_type", modelType)
                .put("event_type", "TestEvent")
                .put("payload", payload)
                .put("event_date", micros(EVENT))
                .put("delivered", false);
    }

    static long micros(Instant instant) {
        return instant.getEpochSecond() * 1_000_000L + instant.getNano() / 1_000L;
    }
}
