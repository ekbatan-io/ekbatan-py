package io.ekbatan.flyway;

import static io.ekbatan.flyway.ParityF.*;

import java.util.ArrayList;
import java.util.List;
import java.util.TreeSet;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import org.junit.jupiter.api.Test;

/** PARITY-PROBE F3: the lock while migrating, two migrators at once, a lock held by someone else. */
class ParityFLockProbeTest {

    static final long HOLD_SECONDS = 70;

    @Test
    void go() throws Exception {
        for (var dialect : List.of("pg", "mysql")) {
            var key = concurrent(dialect);
            held(dialect, key);
        }
    }

    static List<String> sampleLocks(String dialect, String db) throws Exception {
        if (dialect.equals("pg")) {
            return query(dialect, "postgres",
                    "SELECT locktype || '|' || classid || '|' || objid || '|' || objsubid || '|' || mode || '|' || granted"
                            + " FROM pg_locks WHERE locktype = 'advisory' AND database = (SELECT oid FROM pg_database"
                            + " WHERE datname = '" + db + "')");
        }
        return query(dialect, "",
                "SELECT CONCAT(OBJECT_NAME, '|', LOCK_TYPE, '|', LOCK_STATUS) FROM performance_schema.metadata_locks"
                        + " WHERE OBJECT_TYPE = 'USER LEVEL LOCK'");
    }

    /** Returns the lock a migrator was seen holding, to hold it from outside in {@link #held}. */
    static String concurrent(String dialect) throws Exception {
        var db = "f3_lock";
        var k = dialect + ".concurrent";
        fresh(dialect, db);
        var outcomes = new CopyOnWriteArrayList<String>();
        var start = new CountDownLatch(1);
        List<Thread> threads = new ArrayList<>();
        for (int i = 0; i < 2; i++) {
            var t = new Thread(() -> {
                try {
                    start.await();
                    var r = FlywayMigrator.migrate(cfg(dialect, db), "filesystem:" + SQL + "lock_" + dialect);
                    outcomes.add("OK applied=" + r.migrationsExecuted);
                } catch (Exception e) {
                    outcomes.add("FAIL " + msg(e));
                }
            });
            t.start();
            threads.add(t);
        }
        long t0 = System.nanoTime();
        start.countDown();
        var seen = new TreeSet<String>();
        for (int i = 0; i < 12; i++) {
            Thread.sleep(500);
            seen.addAll(sampleLocks(dialect, db));
        }
        for (var t : threads) {
            t.join();
        }
        long secs = Math.round((System.nanoTime() - t0) / 1e9);
        var sorted = new ArrayList<>(outcomes);
        java.util.Collections.sort(sorted);
        probe(k + ".outcomes", sorted);
        probe(k + ".tables", tables(dialect, db));
        probe(k + ".history", history(dialect, db));
        probe(k + ".locks_seen", seen);
        probe(k + ".locks_left_after", sampleLocks(dialect, db));
        return seen.isEmpty() ? null : seen.first();
    }

    static void held(String dialect, String lock) throws Exception {
        var db = "f3_lock";
        var k = dialect + ".held";
        fresh(dialect, db);
        if (lock == null) {
            probe(k + ".outcome", "no lock was seen to hold");
            return;
        }
        var holderReady = new CountDownLatch(1);
        var holder = new Thread(() -> {
            try (var c = connect(dialect, dialect.equals("pg") ? db : db);
                    var s = c.createStatement()) {
                if (dialect.equals("pg")) {
                    var p = lock.split("\\|");
                    long key = (Long.parseLong(p[1]) << 32) | (Long.parseLong(p[2]) & 0xffffffffL);
                    s.execute("SELECT pg_advisory_lock(" + key + ")");
                } else {
                    var name = lock.split("\\|")[0];
                    s.execute("SELECT GET_LOCK('" + name + "', 0)");
                }
                holderReady.countDown();
                Thread.sleep(HOLD_SECONDS * 1000);
            } catch (Exception e) {
                probe(k + ".holder_error", msg(e));
                holderReady.countDown();
            }
        });
        holder.start();
        holderReady.await();
        long t0 = System.nanoTime();
        try {
            var r = FlywayMigrator.migrate(cfg(dialect, db), "filesystem:" + SQL + "quick");
            probe(k + ".outcome", "OK applied=" + r.migrationsExecuted);
        } catch (Exception e) {
            probe(k + ".outcome", "FAIL " + msg(e));
        }
        long secs = (System.nanoTime() - t0) / 1_000_000_000L;
        probe(k + ".seconds_rounded_to_10", Math.round(secs / 10.0) * 10);
        holder.join();
        probe(k + ".history", history(dialect, db));
    }
}
