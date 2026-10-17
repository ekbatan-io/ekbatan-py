package io.ekbatan.core.domain;

import io.ekbatan.testsupport.time.VirtualClock;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.concurrent.Callable;
import org.junit.jupiter.api.Test;

/** Area G twin probe: clocks. Twin: clock_probe.py. Temporary. */
class ClockParityProbeTest {

    static void p(String key, Object value) {
        System.out.println("PROBE " + key + ": " + fmt(value));
    }

    static String fmt(Object v) {
        if (v instanceof Boolean b) return b ? "True" : "False";
        return String.valueOf(v);
    }

    static String err(Callable<Object> fn) {
        try {
            return "OK " + fmt(fn.call());
        } catch (Throwable e) {
            return "ERR " + e.getClass().getSimpleName() + ": " + e.getMessage();
        }
    }

    static boolean near(Instant a, Instant b) {
        return Math.abs(Duration.between(a, b).toMillis()) < 1000;
    }

    static final Instant T0 = Instant.parse("2025-01-01T00:00:00.123456Z");

    @Test
    void probe() {
        var sys = Clock.systemUTC();
        int maxDigits = 0;
        boolean subMicro = false;
        Instant first = null;
        for (int i = 0; i < 2000; i++) {
            var r = sys.instant();
            if (first == null) first = r;
            int n = r.getNano();
            if (n % 1000 != 0) subMicro = true;
            var s = String.format("%09d", n).replaceAll("0+$", "");
            maxDigits = Math.max(maxDigits, s.length());
        }
        p("system.maxFractionDigits", maxDigits);
        p("system.subMicroNonZero", subMicro);
        p("system.nearWallClock", near(first, Instant.now()));
        p("system.zoneUtc", sys.getZone().equals(ZoneOffset.UTC));

        var v = new VirtualClock();
        var a = v.instant();
        p("virtual.default.realTime", near(a, Instant.now()));
        p("virtual.default.advance", err(() -> {
            v.advance(Duration.ofSeconds(1));
            return null;
        }));
        v.pauseAt(T0);
        p("virtual.pauseAt.read", v.instant());
        p("virtual.pauseAt.twoReadsEqual", v.instant().equals(v.instant()));
        v.advance(Duration.ofMillis(1500));
        p("virtual.advance1500ms", v.instant());
        v.advance(Duration.ofNanos(1000));
        p("virtual.advance1us", v.instant());
        p("virtual.advanceNegative", err(() -> {
            v.advance(Duration.ofSeconds(-10));
            return v.instant();
        }));
        v.pauseAt(OffsetDateTime.parse("2025-01-01T02:00:00+02:00").toInstant());
        p("virtual.pauseAt.offsetInput", v.instant());
        v.resumeFrom(T0);
        p("virtual.resumeFrom.nearAnchor", near(v.instant(), T0));
        p("virtual.resumeFrom.advance", err(() -> {
            v.advance(Duration.ofSeconds(1));
            return null;
        }));
        v.pause();
        var x = v.instant();
        p("virtual.pause.frozen", x.equals(v.instant()));
        v.resume();
        p("virtual.resume.realTime", near(v.instant(), Instant.now()));

        var c = new VirtualClock(Clock.fixed(T0, ZoneOffset.UTC));
        p("virtual.constructedAtInstant.read", c.instant());
        p("virtual.constructedAtInstant.advance", err(() -> {
            c.advance(Duration.ofSeconds(1));
            return c.instant();
        }));
        var n = new VirtualClock(Clock.fixed(Instant.parse("2025-01-01T00:00:00.123456789Z"), ZoneOffset.UTC));
        p("virtual.nanosInstant", n.instant());
        p("toStorage.plus2", "n/a (Instant has no zone)");
    }
}
