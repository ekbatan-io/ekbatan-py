package io.ekbatan.test.event_pipeline.avro_smt;

import com.example.finance.avro.WalletMoneyDepositedEvent;
import io.ekbatan.events.streaming.actionevent.avro.ActionEvent;
import java.io.ByteArrayOutputStream;
import java.math.BigDecimal;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.apache.avro.Conversions;
import org.apache.avro.Schema;
import org.apache.avro.generic.GenericData;
import org.apache.avro.generic.GenericRecord;
import org.apache.avro.io.EncoderFactory;
import org.apache.avro.message.BinaryMessageEncoder;
import org.junit.jupiter.api.Test;

/**
 * A parity probe for ekbatan-py, not a test of this repository: the readers Avro generates from
 * ActionEvent.avsc and a payload schema, run on every input of ekbatan-py's robustness comparison.
 * Each input is kept as a file, so Python reads the very same bytes. Copied into this module, run,
 * and removed again - see ekbatan-py/parity-probes/streaming/README.md.
 */
class ParityRobustnessAvroProbeTest {

    static final Path OUT = Path.of(System.getenv()
                    .getOrDefault(
                            "EKBATAN_STREAMING_PROBE_OUT",
                            System.getProperty("java.io.tmpdir") + "/ekbatan-streaming-probe"))
            .resolve("robustness-avro");

    static final String ID = "0198f4a2-1c3d-7e4f-8a9b-0c1d2e3f4a5b";
    static final String ACTION_ID = "0198f4a2-1c3d-7e4f-8a9b-0c1d2e3f4a5c";
    static final long STARTED = micros(Instant.parse("2026-07-31T10:15:30.123456Z"));
    static final long COMPLETED = micros(Instant.parse("2026-07-31T10:15:31.654321Z"));
    static final long EVENT = micros(Instant.parse("2026-07-31T10:15:31.500000Z"));
    /** 10000-01-01T00:00:00Z: an Instant holds it, a Python datetime does not. */
    static final long YEAR_10000 = 253402300800000000L;

    final List<String> lines = new ArrayList<>();

    @Test
    void run_every_case() throws Exception {
        Files.createDirectories(OUT.resolve("cases"));
        var envelope = parse(Files.readString(Path.of(System.getProperty("smt.action.event.schema"))));
        var payload = parse(Files.readString(
                Path.of(System.getProperty("smt.payload.schemas.dir")).resolve("WalletMoneyDepositedEvent.avsc")));
        keep("envelope.avsc", envelope);
        keep("payload.avsc", payload);

        // -- the envelope ------------------------------------------------------------------------
        var body = payloadBytes(payload, "77.10");
        var valid = encode(envelope, row(body));
        envelope("AE01", valid);
        var noModel = row(body);
        noModel.put("model_id", null);
        noModel.put("model_type", null);
        var validNoModel = encode(envelope, noModel);
        envelope("AE02", validNoModel);
        envelope("AE03", new byte[0]);
        envelope("AE04", new byte[] {(byte) 0xC3});
        envelope("AE05", Arrays.copyOf(valid, 6));
        envelope("AE06", Arrays.copyOf(valid, 10));
        envelope("AE07", Arrays.copyOf(valid, valid.length - 1));
        envelope("AE08", Arrays.copyOf(valid, 10 + (valid.length - 10) / 2));
        envelope("AE09", concat(valid, new byte[] {0x00, 0x01}));
        var otherMarker = valid.clone();
        otherMarker[1] = 0x02;
        envelope("AE10", otherMarker);
        var otherFingerprint = valid.clone();
        otherFingerprint[2] ^= (byte) 0xFF;
        envelope("AE11", otherFingerprint);
        envelope("AE12", Arrays.copyOfRange(valid, 10, valid.length));
        envelope("AE13", ("{\"id\":\"" + ID + "\",\"namespace\":\"com.example.finance\"}")
                .getBytes(StandardCharsets.UTF_8));
        envelope("AE14", protobufLike());
        // model_id's union branch sits right after the seven fields before it; on the no-model
        // row it is 0x00, the null branch. 0x0A is branch 5 of a two-branch union.
        var unionAt = 10 + prefixLength();
        if (validNoModel[unionAt] != 0x00) {
            throw new IllegalStateException("the union branch is not where it was expected");
        }
        var badUnion = validNoModel.clone();
        badUnion[unionAt] = 0x0A;
        envelope("AE15", badUnion);
        // The id's length is the body's first byte: 0x48, 36. 0x01 is -1.
        var negative = valid.clone();
        negative[10] = 0x01;
        envelope("AE16", negative);
        // A length of 1000, then three bytes.
        envelope("AE17", concat(Arrays.copyOf(valid, 10), new byte[] {(byte) 0xD0, 0x0F, 'a', 'b', 'c'}));
        envelope("AE18", encode(envelope, with(row(body), "id", "not-a-uuid")));
        envelope("AE19", encode(envelope, with(row(body), "action_params", "not json")));
        envelope("AE20", encode(envelope, with(row(body), "action_params", "[1,2]")));
        envelope("AE21", encode(envelope, with(row(body), "started_date", YEAR_10000)));
        envelope("AE22", encode(envelope, with(row(body), "started_date", -1L)));
        envelope("AE23", encode(envelope, with(row(body), "payload", null)));
        var sentinel = row(null);
        sentinel.put("event_type", null);
        envelope("AE24", encode(envelope, sentinel));
        // The transform accepts an envelope schema carrying only some of the fields.
        var subset = without(envelope, "action_name", "delivered");
        keep("envelope-subset.avsc", subset);
        var subsetRow = row(body);
        subsetRow.remove("action_name");
        subsetRow.remove("delivered");
        envelope("AE25", encode(subset, subsetRow));
        // A newer envelope: one more field, with a default, as ActionEvent.avsc's rules require.
        var newer = plus(envelope, "trace_id");
        keep("envelope-newer.avsc", newer);
        var newerBytes = encode(newer, with(row(body), "trace_id", "trace-1"));
        envelope("AE26", newerBytes);
        ActionEvent.getDecoder().addSchema(newer);
        envelope("AE27", newerBytes);

        // -- the payload -------------------------------------------------------------------------
        var validPayload = payloadBytes(payload, "77.10");
        payload("AP01", validPayload);
        payload("AP02", payloadBytes(payload, "9999999999.99"));
        payload("AP13", payloadBytes(payload, "-0.01"));
        payload("AP03", new byte[0]);
        payload("AP04", Arrays.copyOf(validPayload, 10));
        payload("AP05", Arrays.copyOf(validPayload, validPayload.length - 1));
        payload("AP06", concat(validPayload, new byte[] {0x00, 0x01}));
        var other = parse("{\"type\":\"record\",\"name\":\"WalletMoneyDepositedEvent\","
                + "\"namespace\":\"com.example.finance.avro\",\"fields\":[{\"name\":\"modelId\",\"type\":\"string\"}]}");
        keep("payload-other.avsc", other);
        var otherRecord = new GenericData.Record(other);
        otherRecord.put("modelId", "wallet-1");
        payload("AP07", encode(other, otherRecord));
        // An older version that had one more field, since removed.
        var older = parse("{\"type\":\"record\",\"name\":\"WalletMoneyDepositedEvent\","
                + "\"namespace\":\"com.example.finance.avro\",\"fields\":["
                + "{\"name\":\"modelId\",\"type\":\"string\"},{\"name\":\"modelName\",\"type\":\"string\"},"
                + "{\"name\":\"amount\",\"type\":{\"type\":\"bytes\",\"logicalType\":\"decimal\",\"precision\":12,\"scale\":2}},"
                + "{\"name\":\"legacyNote\",\"type\":\"string\"}]}");
        keep("payload-older.avsc", older);
        var olderRecord = payloadRecord(older, "77.10");
        olderRecord.put("legacyNote", "old");
        var olderBytes = encode(older, olderRecord, decimals());
        payload("AP08", olderBytes);
        WalletMoneyDepositedEvent.getDecoder().addSchema(older);
        payload("AP09", olderBytes);
        // A newer version: one more field, with a default.
        var newerPayload = parse("{\"type\":\"record\",\"name\":\"WalletMoneyDepositedEvent\","
                + "\"namespace\":\"com.example.finance.avro\",\"fields\":["
                + "{\"name\":\"modelId\",\"type\":\"string\"},{\"name\":\"modelName\",\"type\":\"string\"},"
                + "{\"name\":\"amount\",\"type\":{\"type\":\"bytes\",\"logicalType\":\"decimal\",\"precision\":12,\"scale\":2}},"
                + "{\"name\":\"note\",\"type\":[\"null\",\"string\"],\"default\":null}]}");
        keep("payload-newer.avsc", newerPayload);
        var newerRecord = payloadRecord(newerPayload, "77.10");
        newerRecord.put("note", "new");
        WalletMoneyDepositedEvent.getDecoder().addSchema(newerPayload);
        payload("AP10", encode(newerPayload, newerRecord, decimals()));
        // A version lacking a field this reader needs and has no default for.
        var lacking = parse("{\"type\":\"record\",\"name\":\"WalletMoneyDepositedEvent\","
                + "\"namespace\":\"com.example.finance.avro\",\"fields\":["
                + "{\"name\":\"modelId\",\"type\":\"string\"},"
                + "{\"name\":\"amount\",\"type\":{\"type\":\"bytes\",\"logicalType\":\"decimal\",\"precision\":12,\"scale\":2}}]}");
        keep("payload-lacking.avsc", lacking);
        var lackingRecord = new GenericData.Record(lacking);
        lackingRecord.put("modelId", "wallet-1");
        lackingRecord.put("amount", new BigDecimal("77.10"));
        WalletMoneyDepositedEvent.getDecoder().addSchema(lacking);
        payload("AP14", encode(lacking, lackingRecord, decimals()));
        // A decimal with no bytes at all: GenericData.get() has no decimal conversion, so the
        // ByteBuffer goes out as it is.
        var empty = new GenericData.Record(payload);
        empty.put("modelId", "wallet-1");
        empty.put("modelName", "Wallet");
        empty.put("amount", ByteBuffer.wrap(new byte[0]));
        payload("AP11", encode(payload, empty));
        payload("AP12", valid);

        Files.write(OUT.resolve("java.out"), lines);
    }

    // -- reading, as a consumer does -------------------------------------------------------------

    void envelope(String id, byte[] bytes) throws Exception {
        Files.write(OUT.resolve("cases").resolve(id + ".bin"), bytes);
        try {
            var event = ActionEvent.fromByteBuffer(ByteBuffer.wrap(bytes));
            var m = new LinkedHashMap<String, Object>();
            m.put("id", event.getId());
            m.put("namespace", event.getNamespace());
            m.put("action_id", event.getActionId());
            m.put("action_name", event.getActionName());
            m.put("action_params", event.getActionParams());
            m.put("started_date", String.valueOf(micros(event.getStartedDate())));
            m.put("completion_date", String.valueOf(micros(event.getCompletionDate())));
            m.put("model_id", event.getModelId());
            m.put("model_type", event.getModelType());
            m.put("event_type", event.getEventType());
            m.put("payload", event.getPayload() == null ? null : hex(event.getPayload()));
            m.put("event_date", String.valueOf(micros(event.getEventDate())));
            m.put("delivered", event.getDelivered());
            lines.add(id + "\tOK\t" + json(m));
        } catch (Throwable e) {
            lines.add(id + "\tERR\t" + describe(e));
        }
    }

    void payload(String id, byte[] bytes) throws Exception {
        Files.write(OUT.resolve("cases").resolve(id + ".bin"), bytes);
        try {
            var event = WalletMoneyDepositedEvent.fromByteBuffer(ByteBuffer.wrap(bytes));
            var m = new LinkedHashMap<String, Object>();
            m.put("modelId", event.getModelId());
            m.put("modelName", event.getModelName());
            m.put("amount", event.getAmount().toPlainString());
            lines.add(id + "\tOK\t" + json(m));
        } catch (Throwable e) {
            lines.add(id + "\tERR\t" + describe(e));
        }
    }

    // -- writing, as the transform does: BinaryMessageEncoder over GenericData --------------------

    static byte[] encode(Schema schema, GenericRecord record) throws Exception {
        return encode(schema, record, GenericData.get());
    }

    static byte[] encode(Schema schema, GenericRecord record, GenericData model) throws Exception {
        var buffer = new BinaryMessageEncoder<GenericRecord>(model, schema).encode(record);
        var bytes = new byte[buffer.remaining()];
        buffer.get(bytes);
        return bytes;
    }

    static GenericData decimals() {
        var model = new GenericData();
        model.addLogicalTypeConversion(new Conversions.DecimalConversion());
        return model;
    }

    static byte[] payloadBytes(Schema schema, String amount) throws Exception {
        return encode(schema, payloadRecord(schema, amount), decimals());
    }

    static GenericData.Record payloadRecord(Schema schema, String amount) {
        var record = new GenericData.Record(schema);
        record.put("modelId", "wallet-1");
        record.put("modelName", "Wallet");
        record.put("amount", new BigDecimal(amount));
        return record;
    }

    static Map<String, Object> row(byte[] payload) {
        var m = new LinkedHashMap<String, Object>();
        m.put("id", ID);
        m.put("namespace", "com.example.finance");
        m.put("action_id", ACTION_ID);
        m.put("action_name", "WalletDepositMoneyAction");
        m.put("action_params", "{\"amount\":\"77.10\",\"note\":\"first\"}");
        m.put("started_date", STARTED);
        m.put("completion_date", COMPLETED);
        m.put("model_id", "wallet-1");
        m.put("model_type", "Wallet");
        m.put("event_type", "WalletMoneyDepositedEvent");
        m.put("payload", payload == null ? null : ByteBuffer.wrap(payload));
        m.put("event_date", EVENT);
        m.put("delivered", false);
        return m;
    }

    static Map<String, Object> with(Map<String, Object> row, String field, Object value) {
        row.put(field, value);
        return row;
    }

    static byte[] encode(Schema schema, Map<String, Object> row) throws Exception {
        var record = new GenericData.Record(schema);
        row.forEach(record::put);
        return encode(schema, record);
    }

    /** The bytes of the seven fields ahead of model_id, as the binary encoding writes them. */
    static int prefixLength() throws Exception {
        var out = new ByteArrayOutputStream();
        var encoder = EncoderFactory.get().binaryEncoder(out, null);
        encoder.writeString(ID);
        encoder.writeString("com.example.finance");
        encoder.writeString(ACTION_ID);
        encoder.writeString("WalletDepositMoneyAction");
        encoder.writeString("{\"amount\":\"77.10\",\"note\":\"first\"}");
        encoder.writeLong(STARTED);
        encoder.writeLong(COMPLETED);
        encoder.flush();
        return out.size();
    }

    /** The start of a protobuf ActionEvent: field 1, the id, and field 2, the namespace. */
    static byte[] protobufLike() {
        var out = new ByteArrayOutputStream();
        out.write(0x0A);
        out.write(ID.length());
        out.writeBytes(ID.getBytes(StandardCharsets.UTF_8));
        out.write(0x12);
        out.write("com.example.finance".length());
        out.writeBytes("com.example.finance".getBytes(StandardCharsets.UTF_8));
        return out.toByteArray();
    }

    static Schema without(Schema schema, String... dropped) {
        var fields = new ArrayList<Schema.Field>();
        for (var field : schema.getFields()) {
            if (!Arrays.asList(dropped).contains(field.name())) {
                fields.add(new Schema.Field(field, field.schema()));
            }
        }
        return Schema.createRecord(schema.getName(), schema.getDoc(), schema.getNamespace(), false, fields);
    }

    static Schema plus(Schema schema, String name) {
        var fields = new ArrayList<Schema.Field>();
        for (var field : schema.getFields()) {
            fields.add(new Schema.Field(field, field.schema()));
        }
        var optional = Schema.createUnion(Schema.create(Schema.Type.NULL), Schema.create(Schema.Type.STRING));
        fields.add(new Schema.Field(name, optional, null, Schema.Field.NULL_DEFAULT_VALUE));
        return Schema.createRecord(schema.getName(), schema.getDoc(), schema.getNamespace(), false, fields);
    }

    // -- small helpers ---------------------------------------------------------------------------

    static Schema parse(String text) {
        return new Schema.Parser().parse(text);
    }

    static void keep(String name, Schema schema) throws Exception {
        Files.writeString(OUT.resolve(name), schema.toString(true));
    }

    static long micros(Instant instant) {
        return ChronoUnit.MICROS.between(Instant.EPOCH, instant);
    }

    static byte[] concat(byte[] a, byte[] b) {
        var out = Arrays.copyOf(a, a.length + b.length);
        System.arraycopy(b, 0, out, a.length, b.length);
        return out;
    }

    static String hex(ByteBuffer buffer) {
        var copy = buffer.duplicate();
        var bytes = new byte[copy.remaining()];
        copy.get(bytes);
        return HexFormat.of().formatHex(bytes);
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
