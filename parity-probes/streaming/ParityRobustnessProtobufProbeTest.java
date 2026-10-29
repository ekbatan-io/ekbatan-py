package io.ekbatan.test.event_pipeline.protobuf_smt;

import com.example.finance.proto.WalletMoneyDepositedEvent;
import com.google.protobuf.ByteString;
import com.google.protobuf.DescriptorProtos.FileDescriptorSet;
import com.google.protobuf.Timestamp;
import io.ekbatan.events.streaming.actionevent.protobuf.ActionEvent;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/**
 * A parity probe for ekbatan-py, not a test of this repository: the classes protoc generates from
 * ActionEvent.proto and a payload's .proto, run on every input of ekbatan-py's robustness
 * comparison. Each input is kept as a file, so Python reads the very same bytes. Copied into this
 * module, run, and removed again - see ekbatan-py/parity-probes/streaming/README.md.
 */
class ParityRobustnessProtobufProbeTest {

    static final Path OUT = Path.of(System.getenv()
                    .getOrDefault(
                            "EKBATAN_STREAMING_PROBE_OUT",
                            System.getProperty("java.io.tmpdir") + "/ekbatan-streaming-probe"))
            .resolve("robustness-protobuf");

    static final String ID = "0198f4a2-1c3d-7e4f-8a9b-0c1d2e3f4a5b";
    static final String ACTION_ID = "0198f4a2-1c3d-7e4f-8a9b-0c1d2e3f4a5c";
    static final Instant STARTED = Instant.parse("2026-07-31T10:15:30.123456Z");
    static final Instant COMPLETED = Instant.parse("2026-07-31T10:15:31.654321Z");
    static final Instant EVENT = Instant.parse("2026-07-31T10:15:31.500000Z");

    final List<String> lines = new ArrayList<>();

    @Test
    void run_every_case() throws Exception {
        Files.createDirectories(OUT.resolve("cases"));
        Files.write(
                OUT.resolve("payload.desc"),
                FileDescriptorSet.newBuilder()
                        .addFile(WalletMoneyDepositedEvent.getDescriptor().getFile().toProto())
                        .build()
                        .toByteArray());

        // -- the envelope ------------------------------------------------------------------------
        var body = payload().toByteString();
        var valid = row(body).build().toByteArray();
        envelope("PE01", valid);
        envelope("PE02", row(body).clearModelId().clearModelType().build().toByteArray());
        envelope("PE03", new byte[0]);
        envelope("PE04", Arrays.copyOf(valid, valid.length - 1));
        envelope("PE05", Arrays.copyOf(valid, valid.length / 2));
        envelope("PE06", concat(valid, new byte[] {0x00, 0x00}));
        // Field 99, a varint: what a newer envelope's extra field looks like to an older reader.
        envelope("PE07", concat(valid, new byte[] {(byte) 0x98, 0x06, 0x01}));
        // Field 1, the id, as a varint rather than the string it is.
        envelope("PE08", concat(valid, new byte[] {0x08, 0x01}));
        // Field 1 with the bytes C3 28, which are not UTF-8, ahead of everything else.
        envelope("PE09", concat(new byte[] {0x0A, 0x02, (byte) 0xC3, 0x28},
                row(body).clearId().build().toByteArray()));
        envelope("PE10", avroLike());
        envelope("PE11", ("{\"id\":\"" + ID + "\",\"namespace\":\"com.example.finance\"}")
                .getBytes(StandardCharsets.UTF_8));
        envelope("PE12", row(body).setId("not-a-uuid").build().toByteArray());
        envelope("PE13", row(body).setActionParams("not json").build().toByteArray());
        envelope("PE14", row(body).clearActionId().build().toByteArray());
        envelope("PE15", row(body)
                .setStartedDate(Timestamp.newBuilder()
                        .setSeconds(STARTED.getEpochSecond())
                        .setNanos(123456789))
                .build()
                .toByteArray());
        envelope("PE16", row(body)
                .setStartedDate(Timestamp.newBuilder().setSeconds(253402300800L))
                .build()
                .toByteArray());
        envelope("PE17", row(body).clearPayload().build().toByteArray());
        envelope("PE18", row(body).clearEventType().clearPayload().build().toByteArray());

        // -- the payload -------------------------------------------------------------------------
        var validPayload = payload().toByteArray();
        payload("PP01", validPayload);
        payload("PP02", Arrays.copyOf(validPayload, validPayload.length - 1));
        payload("PP03", concat(validPayload, new byte[] {(byte) 0x98, 0x06, 0x01}));

        Files.write(OUT.resolve("java.out"), lines);
    }

    // -- reading, as a consumer does -------------------------------------------------------------

    void envelope(String id, byte[] bytes) throws Exception {
        Files.write(OUT.resolve("cases").resolve(id + ".bin"), bytes);
        try {
            var event = ActionEvent.parseFrom(bytes);
            var m = new LinkedHashMap<String, Object>();
            m.put("id", event.getId());
            m.put("namespace", event.getNamespace());
            m.put("action_id", event.getActionId());
            m.put("action_name", event.getActionName());
            m.put("action_params", event.getActionParams());
            m.put("started_date", time(event.getStartedDate()));
            m.put("completion_date", time(event.getCompletionDate()));
            m.put("model_id", event.hasModelId() ? event.getModelId() : null);
            m.put("model_type", event.hasModelType() ? event.getModelType() : null);
            m.put("event_type", event.hasEventType() ? event.getEventType() : null);
            m.put("payload", event.hasPayload() ? HexFormat.of().formatHex(event.getPayload().toByteArray()) : null);
            m.put("event_date", time(event.getEventDate()));
            m.put("delivered", event.getDelivered());
            lines.add(id + "\tOK\t" + json(m));
        } catch (Throwable e) {
            lines.add(id + "\tERR\t" + describe(e));
        }
    }

    void payload(String id, byte[] bytes) throws Exception {
        Files.write(OUT.resolve("cases").resolve(id + ".bin"), bytes);
        try {
            var event = WalletMoneyDepositedEvent.parseFrom(bytes);
            var m = new LinkedHashMap<String, Object>();
            m.put("modelId", event.getModelId());
            m.put("modelName", event.getModelName());
            m.put("amount", event.getAmount());
            lines.add(id + "\tOK\t" + json(m));
        } catch (Throwable e) {
            lines.add(id + "\tERR\t" + describe(e));
        }
    }

    // -- writing, as the transform does ----------------------------------------------------------

    static WalletMoneyDepositedEvent payload() {
        return WalletMoneyDepositedEvent.newBuilder()
                .setModelId("wallet-1")
                .setModelName("Wallet")
                .setAmount("77.10")
                .build();
    }

    static ActionEvent.Builder row(ByteString payload) {
        return ActionEvent.newBuilder()
                .setId(ID)
                .setNamespace("com.example.finance")
                .setActionId(ACTION_ID)
                .setActionName("WalletDepositMoneyAction")
                .setActionParams("{\"amount\":\"77.10\",\"note\":\"first\"}")
                .setStartedDate(timestamp(STARTED))
                .setCompletionDate(timestamp(COMPLETED))
                .setModelId("wallet-1")
                .setModelType("Wallet")
                .setEventType("WalletMoneyDepositedEvent")
                .setPayload(payload)
                .setEventDate(timestamp(EVENT))
                .setDelivered(false);
    }

    static Timestamp timestamp(Instant instant) {
        return Timestamp.newBuilder()
                .setSeconds(instant.getEpochSecond())
                .setNanos(instant.getNano())
                .build();
    }

    /** The shape of an Avro single-object message: C3 01, an 8-byte fingerprint, a body. */
    static byte[] avroLike() {
        var out = new ByteArrayOutputStream();
        out.writeBytes(new byte[] {(byte) 0xC3, 0x01, 1, 2, 3, 4, 5, 6, 7, 8, 0x48});
        out.writeBytes(ID.getBytes(StandardCharsets.UTF_8));
        return out.toByteArray();
    }

    // -- small helpers ---------------------------------------------------------------------------

    /** Microseconds since the epoch, and any nanoseconds left over below a microsecond. */
    static String time(Timestamp t) {
        var micros = t.getSeconds() * 1_000_000L + t.getNanos() / 1000;
        var below = t.getNanos() % 1000;
        return below == 0 ? String.valueOf(micros) : micros + "+" + below + "ns";
    }

    static byte[] concat(byte[] a, byte[] b) {
        var out = Arrays.copyOf(a, a.length + b.length);
        System.arraycopy(b, 0, out, a.length, b.length);
        return out;
    }

    static String describe(Throwable e) {
        var message = String.valueOf(e.getMessage()).replace('\n', ' ').replace('\r', ' ');
        return e.getClass().getSimpleName() + ": " + message;
    }

    static String json(Map<String, Object> m) {
        var out = new StringBuilder("{");
        for (var entry : m.entrySet()) {
            if (out.length() > 1) {
                out.append(',');
            }
            out.append(quote(entry.getKey())).append(':');
            var value = entry.getValue();
            if (value == null) {
                out.append("null");
            } else if (value instanceof Boolean) {
                out.append(value);
            } else {
                out.append(quote(value.toString()));
            }
        }
        return out.append('}').toString();
    }

    static String quote(String s) {
        var out = new StringBuilder("\"");
        for (var c : s.toCharArray()) {
            if (c == '"' || c == '\\') {
                out.append('\\').append(c);
            } else if (c < 0x20) {
                out.append(String.format("\\u%04x", (int) c));
            } else {
                out.append(c);
            }
        }
        return out.append('"').toString();
    }
}
