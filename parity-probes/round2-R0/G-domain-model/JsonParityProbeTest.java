package io.ekbatan.core.domain;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;
import io.ekbatan.core.persistence.jooq.converter.JSONBObjectNodeConverter;
import io.ekbatan.core.shard.ShardIdentifier;
import io.ekbatan.core.shard.ShardedUUID;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Callable;
import org.jooq.JSONB;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * Area G twin probe: JSON of events and params, byte for byte, and JSON input tolerance. Twin:
 * json_probe.py. Path measured is the real one: the application's ObjectMapper (here a default
 * one, as Ekbatan's own tests use) does valueToTree, then JSONBObjectNodeConverter writes the
 * text. Reading back is converter.from + treeToValue, as EventHandlingJob does. Temporary.
 */
class JsonParityProbeTest {

    static final Path DIR = Path.of("/Users/mike/workspace/ekbatan-py/parity-probes/G-domain-model");
    static final ObjectMapper MAPPER = new ObjectMapper();
    static final JSONBObjectNodeConverter CONVERTER = new JSONBObjectNodeConverter();

    static void p(String key, Object value) {
        System.out.println("PROBE " + key + ": " + value);
    }

    static String err(Callable<Object> fn) {
        try {
            return "OK " + fn.call();
        } catch (Throwable e) {
            var m = String.valueOf(e.getMessage()).split("\n")[0];
            if (m.length() > 160) m = m.substring(0, 160);
            return "ERR " + e.getClass().getSimpleName() + ": " + m;
        }
    }

    interface Wallet extends Identifiable<ShardedId<Wallet>> {}

    enum WalletState {
        OPENED,
        CLOSED
    }

    record Money(BigDecimal amount, String currency) {}

    static class WalletNumber extends TypedValue<String> {
        WalletNumber(String v) {
            super(v);
        }
    }

    static final UUID MODEL = UUID.fromString("01941f29-7c7b-7abc-8042-123456789abc");
    static final UUID OWNER = UUID.fromString("f47ac10b-58cc-4372-a567-0e02b2c3d479");

    public static class ProbeEvent extends ModelEvent<Wallet> {
        public final BigDecimal amount;
        public final BigDecimal zero;
        public final BigDecimal sci;
        public final BigDecimal tiny;
        public final BigDecimal huge;
        public final BigDecimal negative;
        public final UUID ownerId;
        public final Instant at;
        public final Instant atMicros;
        public final Instant atMillis;
        public final OffsetDateTime atOffset;
        public final LocalDateTime localAt;
        public final LocalDate day;
        public final LocalTime clock;
        public final WalletState state;
        public final String note;
        public final String missing;
        public final long count;
        public final double ratio;
        public final boolean flag;
        public final List<String> tags;
        public final Map<String, Integer> attrs;
        public final Money nested;
        public final byte[] raw;

        ProbeEvent() {
            super(MODEL.toString(), Wallet.class);
            amount = new BigDecimal("10.50");
            zero = new BigDecimal("0.00");
            sci = new BigDecimal("1E+3");
            tiny = new BigDecimal("0.0000001");
            huge = new BigDecimal("123456789012345678901234567890.123456789");
            negative = new BigDecimal("-0.5");
            ownerId = OWNER;
            at = Instant.parse("2025-01-01T00:00:00Z");
            atMicros = Instant.parse("2025-01-01T00:00:00.123456Z");
            atMillis = Instant.parse("2025-01-01T00:00:00.120Z");
            atOffset = OffsetDateTime.parse("2025-01-01T02:00:00+02:00");
            localAt = LocalDateTime.parse("2025-01-01T10:00:00");
            day = LocalDate.parse("2025-01-31");
            clock = LocalTime.parse("10:15:30");
            state = WalletState.OPENED;
            note = "café € </tag> \"q\" \\ \n \u0001";
            missing = null;
            count = 1L << 60;
            ratio = 0.1;
            flag = true;
            tags = List.of("b", "a");
            var m = new LinkedHashMap<String, Integer>();
            m.put("z", 1);
            m.put("a", 2);
            m.put("10", 3);
            m.put("9", 4);
            attrs = m;
            nested = new Money(new BigDecimal("5.00"), "EUR");
            raw = new byte[] {1, 2, (byte) 255};
        }

        @JsonCreator
        ProbeEvent(
                @JsonProperty("modelId") String modelId,
                @JsonProperty("modelName") String modelName,
                @JsonProperty("amount") BigDecimal amount,
                @JsonProperty("zero") BigDecimal zero,
                @JsonProperty("sci") BigDecimal sci,
                @JsonProperty("tiny") BigDecimal tiny,
                @JsonProperty("huge") BigDecimal huge,
                @JsonProperty("negative") BigDecimal negative,
                @JsonProperty("ownerId") UUID ownerId,
                @JsonProperty("at") Instant at,
                @JsonProperty("atMicros") Instant atMicros,
                @JsonProperty("atMillis") Instant atMillis,
                @JsonProperty("atOffset") OffsetDateTime atOffset,
                @JsonProperty("localAt") LocalDateTime localAt,
                @JsonProperty("day") LocalDate day,
                @JsonProperty("clock") LocalTime clock,
                @JsonProperty("state") WalletState state,
                @JsonProperty("note") String note,
                @JsonProperty("missing") String missing,
                @JsonProperty("count") long count,
                @JsonProperty("ratio") double ratio,
                @JsonProperty("flag") boolean flag,
                @JsonProperty("tags") List<String> tags,
                @JsonProperty("attrs") Map<String, Integer> attrs,
                @JsonProperty("nested") Money nested,
                @JsonProperty("raw") byte[] raw) {
            super(modelId, Wallet.class);
            this.amount = amount;
            this.zero = zero;
            this.sci = sci;
            this.tiny = tiny;
            this.huge = huge;
            this.negative = negative;
            this.ownerId = ownerId;
            this.at = at;
            this.atMicros = atMicros;
            this.atMillis = atMillis;
            this.atOffset = atOffset;
            this.localAt = localAt;
            this.day = day;
            this.clock = clock;
            this.state = state;
            this.note = note;
            this.missing = missing;
            this.count = count;
            this.ratio = ratio;
            this.flag = flag;
            this.tags = tags;
            this.attrs = attrs;
            this.nested = nested;
            this.raw = raw;
        }
    }

    /** Typed ids and values inside an event: output only. */
    public static class IdsEvent extends ModelEvent<Wallet> {
        public final ShardedId<Wallet> walletId;
        public final WalletNumber walletNumber;
        public final ShardIdentifier shard;

        IdsEvent() {
            super(MODEL.toString(), Wallet.class);
            walletId = ShardedId.of(Wallet.class, ShardedUUID.from(MODEL));
            walletNumber = new WalletNumber("W-1");
            shard = ShardIdentifier.of(1, 2);
        }

        // Ekbatan's events carry a @JsonCreator (modelId, modelName, then the fields), which is
        // what fixes the key order; without one Jackson 3 sorts the keys alphabetically.
        @JsonCreator
        IdsEvent(
                @JsonProperty("modelId") String modelId,
                @JsonProperty("modelName") String modelName,
                @JsonProperty("walletId") ShardedId<Wallet> walletId,
                @JsonProperty("walletNumber") WalletNumber walletNumber,
                @JsonProperty("shard") ShardIdentifier shard) {
            super(modelId, Wallet.class);
            this.walletId = walletId;
            this.walletNumber = walletNumber;
            this.shard = shard;
        }
    }

    public static class SmallEvent extends ModelEvent<Wallet> {
        public final BigDecimal amount;
        public final String note;

        @JsonCreator
        SmallEvent(
                @JsonProperty("modelId") String modelId,
                @JsonProperty("modelName") String modelName,
                @JsonProperty("amount") BigDecimal amount,
                @JsonProperty("note") String note) {
            super(modelId, Wallet.class);
            this.amount = amount;
            this.note = note;
        }
    }

    public record DepositParams(UUID walletId, BigDecimal amount, Instant requestedAt, String memo) {}

    static String write(Object value) {
        ObjectNode tree = MAPPER.valueToTree(value);
        return CONVERTER.to(tree).data();
    }

    static String dec(BigDecimal d) {
        return d == null ? "None" : d.toPlainString() + " (scale " + d.scale() + ")";
    }

    static String describe(ProbeEvent e) {
        var out = new ArrayList<String>();
        out.add("modelId=" + e.modelId);
        out.add("modelName=" + e.modelName);
        out.add("amount=" + dec(e.amount));
        out.add("zero=" + dec(e.zero));
        out.add("sci=" + dec(e.sci));
        out.add("tiny=" + dec(e.tiny));
        out.add("huge=" + dec(e.huge));
        out.add("negative=" + dec(e.negative));
        out.add("ownerId=" + e.ownerId);
        out.add("at=" + e.at);
        out.add("atMicros=" + e.atMicros);
        out.add("atMillis=" + e.atMillis);
        out.add("atOffset=" + (e.atOffset == null ? "None" : e.atOffset.toInstant()));
        out.add("localAt=" + e.localAt);
        out.add("day=" + e.day);
        out.add("clock=" + e.clock);
        out.add("state=" + e.state);
        out.add("note=" + (e.note == null ? "None" : e.note.equals(new ProbeEvent().note) ? "same" : "changed"));
        out.add("missing=" + (e.missing == null ? "None" : e.missing));
        out.add("count=" + e.count);
        out.add("ratio=" + e.ratio);
        out.add("flag=" + (e.flag ? "True" : "False"));
        out.add("tags=" + e.tags);
        out.add("attrs=" + e.attrs);
        out.add("nested=" + (e.nested == null ? "None" : dec(e.nested.amount()) + " " + e.nested.currency()));
        out.add("raw=" + (e.raw == null ? "None" : HexFormat.of().formatHex(e.raw)));
        return String.join(" | ", out);
    }

    static ProbeEvent readBack(String text) {
        ObjectNode node = CONVERTER.from(JSONB.valueOf(text));
        return MAPPER.treeToValue(node, ProbeEvent.class);
    }

    static SmallEvent readSmall(String text) {
        ObjectNode node = CONVERTER.from(JSONB.valueOf(text));
        return MAPPER.treeToValue(node, SmallEvent.class);
    }

    static String small(SmallEvent e) {
        return "modelId=" + e.modelId + " amount=" + dec(e.amount) + " note="
                + (e.note == null ? "None (None)" : e.note + " (text)");
    }

    @Test
    void probe() throws Exception {
        var event = new ProbeEvent();
        var text = write(event);
        p("json.event", text);
        p("json.event.pythonDefaultNames", "n/a (Java field names are the keys)");
        ObjectNode tree = MAPPER.valueToTree(event);
        var keys = new ArrayList<String>();
        tree.propertyNames().forEach(keys::add);
        p("json.event.keyOrder", keys);
        for (var k : keys) p("json.field." + k, CONVERTER.to((ObjectNode) MAPPER.createObjectNode().set("v", tree.get(k))).data());
        Files.writeString(DIR.resolve("java_event.json"), text);

        p("json.idsEvent", write(new IdsEvent()));

        var params = new DepositParams(
                MODEL, new BigDecimal("10.50"), Instant.parse("2025-01-01T00:00:00.120Z"), null);
        var ptext = write(params);
        p("json.params", ptext);
        Files.writeString(DIR.resolve("java_params.json"), ptext);

        // own round trip
        p("json.ownRoundTrip", err(() -> describe(readBack(text))));
        // the other port's text
        var py = DIR.resolve("python_event.json");
        if (Files.exists(py)) {
            var pyText = Files.readString(py);
            p("json.readOtherPort", err(() -> describe(readBack(pyText))));
            ObjectNode n = CONVERTER.from(JSONB.valueOf(pyText));
            n.remove("raw");
            p("json.readOtherPort.withoutRaw", err(() -> describe(MAPPER.treeToValue(n, ProbeEvent.class))));
        } else {
            p("json.readOtherPort", "python_event.json missing");
        }
        var pySnake = DIR.resolve("python_event_snake.json");
        if (Files.exists(pySnake)) {
            var pyText = Files.readString(pySnake);
            p("json.readOtherPortDefaultNames", err(() -> describe(readBack(pyText))));
        } else {
            p("json.readOtherPortDefaultNames", "python_event_snake.json missing");
        }

        // Java's own loss, and the setting that would stop it (EKB-0291, "Noticed in the Java")
        var bigMapper = tools.jackson.databind.json.JsonMapper.builder()
                .enable(tools.jackson.databind.DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS)
                .build();
        p("json.ownRoundTrip.withBigDecimalForFloats", err(() -> describe(
                MAPPER.treeToValue((ObjectNode) bigMapper.readTree(text), ProbeEvent.class))));

        // input tolerance
        p("in.exact", err(() -> small(readSmall("{\"modelId\":\"m\",\"modelName\":\"Wallet\",\"amount\":\"1.50\",\"note\":\"n\"}"))));
        p("in.unknownField", err(() -> small(readSmall("{\"modelId\":\"m\",\"modelName\":\"Wallet\",\"amount\":\"1.50\",\"note\":\"n\",\"extra\":1}"))));
        p("in.missingFields", err(() -> small(readSmall("{\"modelId\":\"m\",\"modelName\":\"Wallet\"}"))));
        p("in.missingModelId", err(() -> small(readSmall("{\"modelName\":\"Wallet\",\"amount\":\"1.50\",\"note\":\"n\"}"))));
        p("in.missingModelName", err(() -> small(readSmall("{\"modelId\":\"m\",\"amount\":\"1.50\",\"note\":\"n\"}"))));
        p("in.otherModelName", err(() -> readSmall("{\"modelId\":\"m\",\"modelName\":\"Other\",\"amount\":\"1.50\",\"note\":\"n\"}").modelName));
        p("in.amountNumber", err(() -> small(readSmall("{\"modelId\":\"m\",\"modelName\":\"Wallet\",\"amount\":1.50,\"note\":\"n\"}"))));
        p("in.amountHugeNumber", err(() -> small(readSmall("{\"modelId\":\"m\",\"modelName\":\"Wallet\",\"amount\":123456789012345678901234567890.123456789,\"note\":\"n\"}"))));
        p("in.amountInt", err(() -> small(readSmall("{\"modelId\":\"m\",\"modelName\":\"Wallet\",\"amount\":7,\"note\":\"n\"}"))));
        p("in.amountBadString", err(() -> small(readSmall("{\"modelId\":\"m\",\"modelName\":\"Wallet\",\"amount\":\"abc\",\"note\":\"n\"}"))));
        p("in.noteNumber", err(() -> small(readSmall("{\"modelId\":\"m\",\"modelName\":\"Wallet\",\"amount\":\"1\",\"note\":5}"))));
        p("in.nullAmount", err(() -> small(readSmall("{\"modelId\":\"m\",\"modelName\":\"Wallet\",\"amount\":null,\"note\":\"n\"}"))));
        p("in.duplicateKey", err(() -> small(readSmall("{\"modelId\":\"m\",\"modelName\":\"Wallet\",\"amount\":\"1\",\"amount\":\"2\",\"note\":\"n\"}"))));
        p("in.snakeCaseKeys", err(() -> small(readSmall("{\"model_id\":\"m\",\"modelId\":\"m\",\"modelName\":\"Wallet\",\"amount\":\"1\",\"new_note\":\"n\"}"))));
    }
}
