package io.ekbatan.test.keyed_lock_provider;

import static io.ekbatan.core.concurrent.MariaDBKeyedLockProvider.Builder.mariaDBKeyedLockProvider;
import static io.ekbatan.core.concurrent.MySQLKeyedLockProvider.Builder.mySQLKeyedLockProvider;
import static io.ekbatan.core.concurrent.PostgresKeyedLockProvider.Builder.postgresKeyedLockProvider;
import static io.ekbatan.core.config.DataSourceConfig.Builder.dataSourceConfig;
import static io.ekbatan.core.persistence.ConnectionProvider.hikariConnectionProvider;
import static io.ekbatan.keyedlock.redis.RedisKeyedLockProvider.Builder.redisKeyedLockProvider;

import io.ekbatan.core.concurrent.KeyedLockProvider;
import io.ekbatan.core.concurrent.KeyedLockProvider.Lease;
import io.ekbatan.core.concurrent.LockAcquisitionException;
import io.ekbatan.core.persistence.ConnectionProvider;
import java.sql.Connection;
import java.sql.DriverManager;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.redisson.Redisson;
import org.redisson.api.RType;
import org.redisson.api.RedissonClient;
import org.redisson.config.Config;

/** Parity scan area E: keyed locks. Prints PROBE lines; asserts nothing. Temporary. */
class ParityELocksProbeTest {

    static final String PG_URL = "jdbc:postgresql://localhost:56432/testdb";
    static final String MY_URL = "jdbc:mysql://localhost:56306/testdb?allowPublicKeyRetrieval=true&useSSL=false";
    static final String MA_URL = "jdbc:mariadb://localhost:56307/testdb";
    static final String PW = System.getenv().getOrDefault("PARITY_PW", "test");
    static final Duration FIVE_MIN = Duration.ofMinutes(5);

    static RedissonClient redisson;

    static void p(String k, Object v) {
        System.out.println("PROBE " + k + ": " + v);
    }

    static long bucket(long ms) {
        return Math.round(ms / 250.0) * 250;
    }

    static String uk(String b) {
        return "parity:" + b + ":" + UUID.randomUUID();
    }

    static String url(String b) {
        return switch (b) {
            case "pg" -> PG_URL;
            case "mysql" -> MY_URL;
            case "mariadb" -> MA_URL;
            default -> null;
        };
    }

    static ConnectionProvider pool(String url, int size) {
        return hikariConnectionProvider(dataSourceConfig()
                .jdbcUrl(url)
                .username("test")
                .password(PW)
                .maximumPoolSize(size)
                .build());
    }

    static KeyedLockProvider provider(String b, int poolSize) {
        return switch (b) {
            case "pg" -> postgresKeyedLockProvider().connectionProvider(pool(PG_URL, poolSize)).build();
            case "mysql" -> mySQLKeyedLockProvider().connectionProvider(pool(MY_URL, poolSize)).build();
            case "mariadb" -> mariaDBKeyedLockProvider().connectionProvider(pool(MA_URL, poolSize)).build();
            default -> redisKeyedLockProvider().redissonClient(redisson).build();
        };
    }

    static Connection admin(String b) throws Exception {
        return switch (b) {
            case "pg" -> DriverManager.getConnection(PG_URL, "test", PW);
            case "mysql" -> DriverManager.getConnection(MY_URL, "root", PW);
            default -> DriverManager.getConnection(MA_URL, "root", PW);
        };
    }

    static List<String> heldNames(String b) throws Exception {
        var out = new ArrayList<String>();
        if (b.equals("redis")) {
            for (var key : redisson.getKeys().getKeysByPattern("ekbatan-lock:*")) {
                var type = redisson.getKeys().getType(key);
                var t = type == RType.MAP ? "hash" : type == RType.OBJECT ? "string" : String.valueOf(type);
                out.add(key + "|" + t);
            }
            out.sort(null);
            return out;
        }
        var sql = switch (b) {
            case "pg" ->
                "SELECT ((classid::bigint << 32) | objid::bigint)::text FROM pg_locks WHERE locktype = 'advisory'"
                        + " AND granted ORDER BY 1";
            case "mysql" ->
                "SELECT OBJECT_NAME FROM performance_schema.metadata_locks WHERE OBJECT_TYPE = 'USER LEVEL LOCK'"
                        + " ORDER BY 1";
            default ->
                "SELECT TABLE_SCHEMA FROM information_schema.METADATA_LOCK_INFO WHERE LOCK_TYPE = 'User lock'"
                        + " ORDER BY 1";
        };
        try (var c = admin(b);
                var st = c.createStatement();
                var rs = st.executeQuery(sql)) {
            while (rs.next()) {
                out.add(rs.getString(1));
            }
        }
        return out;
    }

    static void killHolders(String b) throws Exception {
        try (var c = admin(b)) {
            if (b.equals("pg")) {
                try (var st = c.createStatement()) {
                    st.execute("SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype = 'advisory'"
                            + " AND pid <> pg_backend_pid()");
                }
                return;
            }
            for (var name : heldNames(b)) {
                long id;
                try (var ps = c.prepareStatement("SELECT IS_USED_LOCK(?)")) {
                    ps.setString(1, name);
                    try (var rs = ps.executeQuery()) {
                        rs.next();
                        id = rs.getLong(1);
                    }
                }
                try (var st = c.createStatement()) {
                    st.execute("KILL " + id);
                }
            }
        }
    }

    static boolean otherCanAcquire(KeyedLockProvider l, String key) throws Exception {
        var got = new AtomicBoolean();
        var err = new AtomicReference<Throwable>();
        var t = new Thread(() -> {
            try {
                var lease = l.tryAcquire(key, Duration.ZERO, FIVE_MIN);
                if (lease.isPresent()) {
                    got.set(true);
                    lease.get().close();
                }
            } catch (Throwable e) {
                err.set(e);
            }
        });
        t.start();
        t.join();
        if (err.get() != null) {
            return false;
        }
        return got.get();
    }

    record Timed(boolean got, long ms) {}

    static Timed otherTryTimed(KeyedLockProvider l, String key, Duration wait) throws Exception {
        var got = new AtomicBoolean();
        var ms = new AtomicLong();
        var t = new Thread(() -> {
            try {
                var t0 = System.nanoTime();
                var lease = l.tryAcquire(key, wait, FIVE_MIN);
                ms.set((System.nanoTime() - t0) / 1_000_000);
                if (lease.isPresent()) {
                    got.set(true);
                    lease.get().close();
                }
            } catch (Throwable e) {
                ms.set(-1);
            }
        });
        t.start();
        t.join();
        return new Timed(got.get(), ms.get());
    }

    static String refused(Runnable r) {
        try {
            r.run();
            return "accepted";
        } catch (RuntimeException e) {
            return "refused";
        }
    }

    static String typeOf(Runnable r) {
        try {
            r.run();
            return "none";
        } catch (RuntimeException e) {
            return e.getClass().getSimpleName();
        }
    }

    interface Body {
        void run() throws Exception;
    }

    static Runnable unchecked(Body b) {
        return () -> {
            try {
                b.run();
            } catch (RuntimeException e) {
                throw e;
            } catch (Exception e) {
                throw new RuntimeException(e);
            }
        };
    }

    @Test
    void probe() throws Exception {
        var cfg = new Config();
        cfg.useSingleServer().setAddress("redis://localhost:56379");
        redisson = Redisson.create(cfg);
        redisson.getKeys().deleteByPattern("ekbatan-lock:*");
        try {
            for (var b : List.of("pg", "mysql", "mariadb", "redis")) {
                runBackend(b);
            }
            pgOnly();
        } finally {
            redisson.shutdown();
        }
    }

    void runBackend(String b) throws Exception {
        var l = provider(b, 16);

        // 1. lock key -> backend name/number
        try (var lease = l.acquire("wallet:abc", Duration.ofSeconds(30))) {
            p(b + ".name.wallet_abc", heldNames(b));
            if (b.equals("redis")) {
                p(b + ".ttl_s.wallet_abc", Math.round(redisson.getBucket("ekbatan-lock:wallet:abc").remainTimeToLive() / 1000.0));
            }
        }
        try (var lease = l.acquire("café:1", Duration.ofSeconds(30))) {
            p(b + ".name.unicode_cafe", heldNames(b));
        }
        p(b + ".name.after_release", heldNames(b));

        // 2. re-entry and inner close three times
        {
            var k = uk(b);
            var outer = l.acquire(k, FIVE_MIN);
            var t0 = System.nanoTime();
            var inner = l.acquire(k, FIVE_MIN);
            p(b + ".reenter.fast", (System.nanoTime() - t0) / 1_000_000 < 50);
            p(b + ".reenter.other_thread_can_acquire", otherCanAcquire(l, k));
            inner.close();
            inner.close();
            inner.close();
            p(b + ".inner_close_x3.outer_held", outer.isHeld());
            p(b + ".inner_close_x3.inner_held", inner.isHeld());
            p(b + ".inner_close_x3.other_can_acquire", otherCanAcquire(l, k));
            outer.close();
            p(b + ".inner_close_x3.after_outer_close.other_can_acquire", otherCanAcquire(l, k));
        }

        // 3. inner lease reports after its own close
        {
            var k = uk(b);
            var outer = l.acquire(k, FIVE_MIN);
            var inner = l.acquire(k, FIVE_MIN);
            inner.close();
            p(b + ".inner_close_once.inner_held", inner.isHeld());
            p(b + ".inner_close_once.outer_held", outer.isHeld());
            p(b + ".inner_close_once.other_can_acquire", otherCanAcquire(l, k));
            outer.close();
        }

        // 4. outer closed first
        {
            var k = uk(b);
            var outer = l.acquire(k, FIVE_MIN);
            var inner = l.acquire(k, FIVE_MIN);
            outer.close();
            p(b + ".outer_first.outer_held", outer.isHeld());
            p(b + ".outer_first.inner_held", inner.isHeld());
            p(b + ".outer_first.other_can_acquire", otherCanAcquire(l, k));
            inner.close();
            p(b + ".outer_first.after_inner_close.other_can_acquire", otherCanAcquire(l, k));
        }

        // 5. try_acquire re-entry with zero wait
        {
            var k = uk(b);
            var outer = l.acquire(k, FIVE_MIN);
            var re = l.tryAcquire(k, Duration.ZERO, FIVE_MIN);
            p(b + ".try_reenter_zero_wait.present", re.isPresent());
            re.ifPresent(Lease::close);
            outer.close();
        }

        // 6. close twice
        {
            var k = uk(b);
            var lease = l.acquire(k, FIVE_MIN);
            var r = refused(unchecked(() -> {
                lease.close();
                lease.close();
            }));
            p(b + ".close_twice", r.equals("accepted") ? "no-error" : "error");
            p(b + ".close_twice.other_can_acquire", otherCanAcquire(l, k));
        }

        // 7. try_acquire timing against a held key
        {
            var k = uk(b);
            var lease = l.acquire(k, FIVE_MIN);
            for (var w : List.of(0L, 500L, 1000L, 1500L)) {
                var t = otherTryTimed(l, k, Duration.ofMillis(w));
                p(b + ".try_held.wait_" + w + "ms.got", t.got());
                p(b + ".try_held.wait_" + w + "ms.elapsed_bucket_ms", bucket(t.ms()));
            }
            lease.close();
        }

        // 8. try_acquire succeeds when the holder releases in time
        {
            var k = uk(b);
            var lease = l.acquire(k, FIVE_MIN);
            var result = new AtomicReference<Timed>();
            var t = new Thread(unchecked(() -> result.set(otherTryTimed(l, k, Duration.ofSeconds(5)))));
            t.start();
            Thread.sleep(300);
            lease.close();
            t.join();
            p(b + ".try_released_in_time.got", result.get().got());
            p(b + ".try_released_in_time.under_1500ms", result.get().ms() < 1500);
        }

        // 9. blocking acquire waits, then gets it
        {
            var k = uk(b);
            var lease = l.acquire(k, FIVE_MIN);
            var got = new AtomicBoolean();
            var t = new Thread(unchecked(() -> {
                try (var w = l.acquire(k, FIVE_MIN)) {
                    got.set(true);
                }
            }));
            t.start();
            Thread.sleep(500);
            p(b + ".blocking_acquire.got_while_held", got.get());
            lease.close();
            t.join(3000);
            p(b + ".blocking_acquire.got_after_release", got.get());
        }

        // 10. max hold expiry
        {
            var k = uk(b);
            var lease = l.acquire(k, Duration.ofMillis(500));
            Thread.sleep(1500);
            p(b + ".expiry.held", lease.isHeld());
            p(b + ".expiry.other_can_acquire", otherCanAcquire(l, k));
            p(b + ".expiry.close_after", refused(lease::close).equals("accepted") ? "no-error" : "error");
        }

        // 11. re-entry after expiry takes a fresh lock
        {
            var k = uk(b);
            var lease = l.acquire(k, Duration.ofMillis(300));
            Thread.sleep(900);
            var again = l.acquire(k, FIVE_MIN);
            p(b + ".reenter_after_expiry.held", again.isHeld());
            p(b + ".reenter_after_expiry.other_can_acquire", otherCanAcquire(l, k));
            again.close();
            lease.close();
        }

        // 12. inner max hold ignored
        {
            var k = uk(b);
            var outer = l.acquire(k, FIVE_MIN);
            var inner = l.acquire(k, Duration.ofMillis(300));
            Thread.sleep(900);
            p(b + ".inner_max_hold_ignored.outer_held", outer.isHeld());
            p(b + ".inner_max_hold_ignored.inner_held", inner.isHeld());
            p(b + ".inner_max_hold_ignored.other_can_acquire", otherCanAcquire(l, k));
            if (b.equals("redis")) {
                p(b + ".inner_max_hold_ignored.ttl_over_60s",
                        redisson.getBucket("ekbatan-lock:" + k).remainTimeToLive() > 60_000);
            }
            inner.close();
            outer.close();
        }

        // 13. outer max hold governs the inner lease
        {
            var k = uk(b);
            var outer = l.acquire(k, Duration.ofMillis(500));
            var inner = l.acquire(k, FIVE_MIN);
            Thread.sleep(1300);
            p(b + ".outer_max_hold_governs.inner_held", inner.isHeld());
            p(b + ".outer_max_hold_governs.other_can_acquire", otherCanAcquire(l, k));
            inner.close();
            outer.close();
        }

        // 14. a lease closed from another thread
        {
            var k = uk(b);
            var ref = new AtomicReference<Lease>();
            var t = new Thread(unchecked(() -> ref.set(l.acquire(k, FIVE_MIN))));
            t.start();
            t.join();
            var r = refused(ref.get()::close);
            p(b + ".close_from_other_thread", r.equals("accepted") ? "no-error" : "error");
            p(b + ".close_from_other_thread.held", ref.get().isHeld());
            p(b + ".close_from_other_thread.other_can_acquire", otherCanAcquire(l, k));
        }

        // 15. provider close with a lease open
        {
            var k = uk(b);
            var p2 = provider(b, 4);
            var lease = p2.acquire(k, FIVE_MIN);
            p2.close();
            p(b + ".provider_close.lease_held", lease.isHeld());
            p(b + ".provider_close.other_can_acquire", otherCanAcquire(l, k));
            lease.close();
        }

        // 16. argument checks
        p(b + ".validate.blank_key", refused(unchecked(() -> l.acquire("  ", FIVE_MIN))));
        p(b + ".validate.empty_key", refused(unchecked(() -> l.acquire("", FIVE_MIN))));
        p(b + ".validate.zero_max_hold", refused(unchecked(() -> l.acquire("k", Duration.ZERO))));
        p(b + ".validate.negative_max_hold", refused(unchecked(() -> l.acquire("k", Duration.ofMillis(-1)))));
        p(b + ".validate.negative_max_wait",
                refused(unchecked(() -> l.tryAcquire("k", Duration.ofMillis(-1), FIVE_MIN))));
        p(b + ".validate.try_zero_max_hold", refused(unchecked(() -> l.tryAcquire("k", Duration.ZERO, Duration.ZERO))));
        p(b + ".validate.type_blank_key", typeOf(unchecked(() -> l.acquire("  ", FIVE_MIN))));

        // 17. lock held across a connection drop (SQL) / key deleted under the holder (Redis)
        if (!b.equals("redis")) {
            var k = uk(b);
            var lease = l.acquire(k, FIVE_MIN);
            killHolders(b);
            Thread.sleep(500);
            p(b + ".conn_drop.other_can_acquire", otherCanAcquire(l, k));
            p(b + ".conn_drop.lease_held", lease.isHeld());
            p(b + ".conn_drop.close", refused(lease::close).equals("accepted") ? "no-error" : "error");
            var k2 = uk(b);
            var after = l.tryAcquire(k2, Duration.ZERO, FIVE_MIN);
            p(b + ".conn_drop.provider_still_works", after.isPresent());
            after.ifPresent(Lease::close);
        } else {
            var k = uk(b);
            var lease = l.acquire(k, FIVE_MIN);
            redisson.getKeys().delete("ekbatan-lock:" + k);
            var other = new AtomicReference<Optional<Lease>>();
            var t = new Thread(unchecked(() -> other.set(l.tryAcquire(k, Duration.ZERO, FIVE_MIN))));
            t.start();
            t.join();
            p(b + ".conn_drop.other_can_acquire", other.get().isPresent());
            p(b + ".conn_drop.lease_held", lease.isHeld());
            p(b + ".conn_drop.close", refused(lease::close).equals("accepted") ? "no-error" : "error");
            p(b + ".conn_drop.new_owner_key_survives_old_close",
                    redisson.getKeys().countExists("ekbatan-lock:" + k) == 1);
            var t2 = new Thread(() -> other.get().ifPresent(Lease::close));
            t2.start();
            t2.join();
        }

        // 18. interrupting a waiting acquire
        {
            var k = uk(b);
            var lease = l.acquire(k, FIVE_MIN);
            var outcome = new AtomicReference<String>("still-waiting");
            var t = new Thread(() -> {
                try (var w = l.acquire(k, FIVE_MIN)) {
                    outcome.set("acquired");
                } catch (InterruptedException e) {
                    outcome.set("InterruptedException");
                } catch (Throwable e) {
                    outcome.set(e.getClass().getSimpleName());
                }
            });
            t.start();
            Thread.sleep(300);
            t.interrupt();
            t.join(7000);
            p(b + ".interrupt_waiting_acquire", outcome.get());
            lease.close();
            t.join();
        }

        System.out.println("PROBE-DONE " + b);
    }

    void pgOnly() throws Exception {
        // lock_timeout left on a pooled connection after a timed-out bounded wait
        var holderLocks = provider("pg", 4);
        var pool1 = pool(PG_URL, 1);
        var p1 = postgresKeyedLockProvider().connectionProvider(pool1).build();
        var k = uk("pg");
        var lease = holderLocks.acquire(k, FIVE_MIN);
        var res = new AtomicReference<Optional<Lease>>();
        var t = new Thread(unchecked(() -> res.set(p1.tryAcquire(k, Duration.ofMillis(200), FIVE_MIN))));
        t.start();
        t.join();
        p("pg.lock_timeout_after_timed_out_try.got", res.get().isPresent());
        var c = pool1.acquire();
        try (var st = c.createStatement();
                var rs = st.executeQuery("SHOW lock_timeout")) {
            rs.next();
            p("pg.lock_timeout_after_timed_out_try.next_borrower_sees", rs.getString(1));
        }
        pool1.release(c);
        lease.close();
        var ok = p1.tryAcquire(k, Duration.ofMillis(200), FIVE_MIN);
        p("pg.lock_timeout_after_acquired_try.got", ok.isPresent());
        ok.ifPresent(Lease::close);
        c = pool1.acquire();
        try (var st = c.createStatement();
                var rs = st.executeQuery("SHOW lock_timeout")) {
            rs.next();
            p("pg.lock_timeout_after_acquired_try.next_borrower_sees", rs.getString(1));
        }
        pool1.release(c);

        // backend unreachable
        var dead = postgresKeyedLockProvider()
                .connectionProvider(pool("jdbc:postgresql://localhost:1/none", 1))
                .build();
        try {
            dead.tryAcquire("k", Duration.ZERO, FIVE_MIN);
            p("pg.backend_down.raises", "nothing");
        } catch (RuntimeException e) {
            p("pg.backend_down.is_lock_acquisition_error", e instanceof LockAcquisitionException);
            p("pg.backend_down.type", e.getClass().getSimpleName());
        }
        System.out.println("PROBE-DONE pgOnly");
    }
}
