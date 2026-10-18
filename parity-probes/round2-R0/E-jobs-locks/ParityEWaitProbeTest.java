package io.ekbatan.test.keyed_lock_provider;

import java.time.Duration;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.redisson.Redisson;
import org.redisson.config.Config;

/** Parity scan area E: how long a bounded wait on a held key really takes. Temporary. */
class ParityEWaitProbeTest {

    @Test
    void probe() throws Exception {
        var cfg = new Config();
        cfg.useSingleServer().setAddress("redis://localhost:58179");
        ParityELocksProbeTest.redisson = Redisson.create(cfg);
        try {
            for (var b : List.of("mysql", "mariadb", "redis")) {
                var l = ParityELocksProbeTest.provider(b, 4);
                var k = ParityELocksProbeTest.uk(b);
                var lease = l.acquire(k, Duration.ofMinutes(5));
                for (var w : List.of(250L, 5000L)) {
                    var t = ParityELocksProbeTest.otherTryTimed(l, k, Duration.ofMillis(w));
                    ParityELocksProbeTest.p(b + ".wait_" + w + "ms.got", t.got());
                    ParityELocksProbeTest.p(
                            b + ".wait_" + w + "ms.elapsed_bucket_ms", ParityELocksProbeTest.bucket(t.ms()));
                }
                lease.close();
            }
        } finally {
            ParityELocksProbeTest.redisson.shutdown();
        }
    }
}
