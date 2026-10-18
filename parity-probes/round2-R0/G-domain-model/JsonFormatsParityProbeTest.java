package io.ekbatan.core.domain;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.*;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.Random;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

/**
 * Group J twin probe (EKB-0290): many values of each kind written by Ekbatan's default Jackson 3
 * mapper, with the inputs, so json_formats_probe.py can write the same values and compare the
 * text byte for byte, and read Java's text back. Temporary.
 */
class JsonFormatsParityProbeTest {

    static final Path DIR = Path.of("/Users/mike/workspace/ekbatan-py/parity-probes/G-domain-model");
    static final JsonMapper M = JsonMapper.builder().build();

    @Test
    void probe() throws Exception {
        var r = new Random(4242);
        var lines = new ArrayList<String>();
        for (int i = 0; i < 20000; i++) {
            double d = switch (i % 4) {
                case 0 -> Double.longBitsToDouble(r.nextLong());
                case 1 -> r.nextDouble() * Math.pow(10, r.nextInt(30) - 15);
                case 2 -> (double) Math.round(r.nextDouble() * 1e6) / Math.pow(10, r.nextInt(8));
                default -> Double.longBitsToDouble(r.nextLong() & 0x000FFFFFFFFFFFFFL);
            };
            if (Double.isNaN(d) || Double.isInfinite(d)) continue;
            lines.add("dbl\t" + Long.toHexString(Double.doubleToRawLongBits(d)) + "\t" + M.writeValueAsString(d));
        }
        for (double d : new double[] {Double.MIN_VALUE, Double.MAX_VALUE, Double.MIN_NORMAL, 1e7, 1e-3, 9.999999999999999e6, 1e23, 2e-323, -0.0, 0.0}) {
            lines.add("dbl\t" + Long.toHexString(Double.doubleToRawLongBits(d)) + "\t" + M.writeValueAsString(d));
        }
        for (int i = 0; i < 5000; i++) {
            var unscaled = new BigInteger(r.nextInt(120) + 1, r).multiply(BigInteger.valueOf(r.nextBoolean() ? 1 : -1));
            int scale = r.nextInt(40) - 20;
            var bd = new BigDecimal(unscaled, scale);
            lines.add("bd\t" + unscaled + "," + scale + "\t" + M.writeValueAsString(bd));
        }
        long lo = Instant.parse("0001-01-01T00:00:00Z").getEpochSecond();
        long hi = Instant.parse("9999-12-31T23:59:59Z").getEpochSecond();
        for (int i = 0; i < 3000; i++) {
            long sec = lo + (long) (r.nextDouble() * (hi - lo));
            int micros = switch (i % 4) {
                case 0 -> 0;
                case 1 -> r.nextInt(1000) * 1000;
                case 2 -> r.nextInt(1_000_000);
                default -> r.nextInt(10) * 100_000;
            };
            int offset = (r.nextInt(36) - 18) * 3600 + (i % 7 == 0 ? r.nextInt(4) * 15 * 60 : 0) + (i % 50 == 0 ? 15 : 0);
            var inst = Instant.ofEpochSecond(sec, micros * 1000L);
            lines.add("instant\t" + sec + "," + micros + "\t" + M.writeValueAsString(inst));
            var ldt = LocalDateTime.ofEpochSecond(sec, micros * 1000, ZoneOffset.UTC);
            lines.add("ldt\t" + sec + "," + micros + "\t" + M.writeValueAsString(ldt));
            var zo = ZoneOffset.ofTotalSeconds(offset);
            var odt = OffsetDateTime.ofInstant(inst, zo);
            if (odt.getYear() >= 1 && odt.getYear() <= 9999) {
                lines.add("odt\t" + sec + "," + micros + "," + offset + "\t" + M.writeValueAsString(odt));
            }
            long nod = (sec % 86400 + 86400) % 86400 * 1_000_000_000L + micros * 1000L;
            lines.add("lt\t" + nod + "\t" + M.writeValueAsString(LocalTime.ofNanoOfDay(nod)));
            lines.add("ot\t" + nod + "," + offset + "\t" + M.writeValueAsString(OffsetTime.of(LocalTime.ofNanoOfDay(nod), zo)));
            lines.add("ld\t" + Math.floorDiv(sec, 86400) + "\t" + M.writeValueAsString(LocalDate.ofEpochDay(Math.floorDiv(sec, 86400))));
            long dsec = (long) ((r.nextDouble() - 0.5) * (i % 3 == 0 ? 200 : 400000));
            int dmic = i % 5 == 0 ? 0 : r.nextInt(1_000_000);
            lines.add("dur\t" + dsec + "," + dmic + "\t" + M.writeValueAsString(Duration.ofSeconds(dsec, dmic * 1000L)));
        }
        for (int i = 0; i < 2000; i++) {
            var sb = new StringBuilder();
            var cps = new ArrayList<String>();
            int n = r.nextInt(12);
            for (int k = 0; k < n; k++) {
                int cp = switch (r.nextInt(4)) {
                    case 0 -> r.nextInt(0x80);
                    case 1 -> r.nextInt(0x20);
                    case 2 -> 0x80 + r.nextInt(0xD000);
                    default -> 0x10000 + r.nextInt(0x10000);
                };
                sb.appendCodePoint(cp);
                cps.add(Integer.toString(cp));
            }
            lines.add("str\t" + String.join(",", cps) + "\t" + M.writeValueAsString(sb.toString()));
            var bytes = new byte[r.nextInt(40)];
            r.nextBytes(bytes);
            lines.add("bytes\t" + HexFormat.of().formatHex(bytes) + "\t" + M.writeValueAsString(bytes));
        }
        Files.write(DIR.resolve("java_formats.txt"), lines);
        System.out.println("PROBE formats.written: " + lines.size());
    }
}
