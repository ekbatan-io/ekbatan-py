package io.ekbatan.test.keyed_lock_provider;

import static io.ekbatan.core.config.DataSourceConfig.Builder.dataSourceConfig;
import static io.ekbatan.core.persistence.ConnectionProvider.hikariConnectionProvider;
import static io.ekbatan.distributedjobs.JobRegistry.jobRegistry;

import com.github.kagkarlsson.scheduler.task.ExecutionContext;
import com.github.kagkarlsson.scheduler.task.schedule.FixedDelay;
import com.github.kagkarlsson.scheduler.task.schedule.Schedule;
import io.ekbatan.core.concurrent.KeyedLockProvider;
import io.ekbatan.core.concurrent.KeyedLockProvider.Lease;
import io.ekbatan.distributedjobs.DistributedJob;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.redisson.Redisson;
import org.redisson.config.Config;

/** Parity scan area E: a Java holder against a Python waiter, and the reverse. Temporary. */
class ParityECrossPortTest {

    static final Path DIR = Path.of(System.getProperty("parity.dir",
            "/private/tmp/claude-501/-Users-mike-workspace-ekbatan/73e3c410-5a5d-4b46-86a8-40a1e2b99616/scratchpad/xport"));
    static final List<String> KEYS = List.of("xport:wallet:abc", "xport:café");
    static final Duration FIVE_MIN = Duration.ofMinutes(5);

    static void p(String k, Object v) {
        System.out.println("PROBE " + k + ": " + v);
    }

    static void mark(String name) throws Exception {
        Files.createDirectories(DIR);
        Files.writeString(DIR.resolve(name), "x");
    }

    static void await(String name) throws Exception {
        var deadline = System.currentTimeMillis() + 240_000;
        while (!Files.exists(DIR.resolve(name))) {
            if (System.currentTimeMillis() > deadline) {
                throw new IllegalStateException("timed out waiting for " + name);
            }
            Thread.sleep(50);
        }
    }

    static Map<String, KeyedLockProvider> providers() {
        var cfg = new Config();
        cfg.useSingleServer().setAddress("redis://localhost:56379");
        ParityELocksProbeTest.redisson = Redisson.create(cfg);
        var m = new LinkedHashMap<String, KeyedLockProvider>();
        for (var b : List.of("pg", "mysql", "mariadb", "redis")) {
            m.put(b, ParityELocksProbeTest.provider(b, 4));
        }
        return m;
    }

    @Test
    void javaHolds() throws Exception {
        var ps = providers();
        var leases = new ArrayList<Lease>();
        for (var e : ps.entrySet()) {
            for (var k : KEYS) {
                leases.add(e.getValue().acquire(k, FIVE_MIN));
            }
        }
        mark("java-holding");
        await("python-tried");
        Thread.sleep(1000);
        for (var l : leases) {
            l.close();
        }
        mark("java-released");
        p("xport.java_holds.released", true);
        // Redis only: Python waits on this key, and only Redisson's unlock message wakes it in time.
        var wake = ps.get("redis").acquire("xport:wake", FIVE_MIN);
        mark("java-holding-wake");
        await("python-waiting-wake");
        Thread.sleep(1000);
        wake.close();
        ParityELocksProbeTest.redisson.shutdown();
    }

    @Test
    void javaTries() throws Exception {
        var ps = providers();
        await("python-holding");
        for (var e : ps.entrySet()) {
            for (var k : KEYS) {
                var tag = "xport.python_holds." + e.getKey() + "." + (k.contains("wallet") ? "wallet" : "unicode");
                try {
                    var got = e.getValue().tryAcquire(k, Duration.ZERO, FIVE_MIN);
                    p(tag + ".java_try_zero_got", got.isPresent());
                    got.ifPresent(Lease::close);
                } catch (Exception ex) {
                    p(tag + ".java_try_zero_got", "raised " + ex.getClass().getSimpleName());
                    System.out.println("DETAIL " + ex);
                }
            }
        }
        mark("java-tried");
        for (var e : ps.entrySet()) {
            var k = KEYS.get(0);
            var tag = "xport.python_holds." + e.getKey() + ".wallet";
            var t0 = System.nanoTime();
            try {
                var got = e.getValue().tryAcquire(k, Duration.ofSeconds(15), FIVE_MIN);
                p(tag + ".java_gets_it_after_python_releases", got.isPresent());
                got.ifPresent(Lease::close);
            } catch (Exception ex) {
                p(tag + ".java_gets_it_after_python_releases", "raised " + ex.getClass().getSimpleName());
            }
        }
        // Redis only: Java is already waiting when Python releases, with a 5-minute TTL on the key.
        await("python-holding-wake");
        mark("java-waiting-wake");
        var w0 = System.nanoTime();
        try {
            var got = ps.get("redis").tryAcquire("xport:wake", Duration.ofSeconds(15), FIVE_MIN);
            p("xport.python_holds.redis.wake.java_got", got.isPresent());
            p("xport.python_holds.redis.wake.java_woken_under_3s", System.nanoTime() - w0 < 3_000_000_000L);
            got.ifPresent(Lease::close);
        } catch (Exception ex) {
            p("xport.python_holds.redis.wake.java_got", "raised " + ex.getClass().getSimpleName());
            System.out.println("DETAIL " + ex);
        }
        ParityELocksProbeTest.redisson.shutdown();
    }

    @Test
    void sharedJobTable() throws Exception {
        var cp = hikariConnectionProvider(dataSourceConfig()
                .jdbcUrl(ParityELocksProbeTest.PG_URL)
                .username("test")
                .password(ParityELocksProbeTest.PW)
                .maximumPoolSize(6)
                .build());
        var job = new DistributedJob() {
            @Override
            public String name() {
                return "shared-job";
            }

            @Override
            public Schedule schedule() {
                return FixedDelay.ofMillis(300);
            }

            @Override
            public void execute(ExecutionContext ctx) {
                var c = cp.acquire();
                try (var st = c.createStatement()) {
                    st.execute("insert into job_runs (port, started_at) values ('java', clock_timestamp())");
                    Thread.sleep(200);
                    st.execute("update job_runs set ended_at = clock_timestamp() where id = (select max(id)"
                            + " from job_runs where port = 'java')");
                } catch (Exception e) {
                    throw new RuntimeException(e);
                } finally {
                    cp.release(c);
                }
            }
        };
        await("python-jobs-started");
        var reg = jobRegistry()
                .connectionProvider(cp)
                .registerShutdownHook(false)
                .pollInterval(Duration.ofMillis(100))
                .withJob(job)
                .build();
        reg.start();
        mark("java-jobs-started");
        Thread.sleep(10_000);
        reg.stop();
        mark("java-jobs-stopped");
        p("xport.shared_job.java_stopped", true);
    }
}
