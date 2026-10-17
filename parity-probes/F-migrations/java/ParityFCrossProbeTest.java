package io.ekbatan.flyway;

import static io.ekbatan.flyway.ParityF.*;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.Test;

/**
 * PARITY-PROBE F6: one database, both ports in turn. Each method is one step; cross.sh runs them
 * in order with the Python steps (f6_cross.py) between them. Rewritten for parity fix M: Java
 * migrates V1-V2, Python V3, Java V4, and the reverse; both validate; both migrate again.
 */
class ParityFCrossProbeTest {

    static final List<String> DIALECTS = List.of("pg", "mysql", "maria");
    static final Path START = Path.of(
            "/private/tmp/claude-501/-Users-mike-workspace-ekbatan/73e3c410-5a5d-4b46-86a8-40a1e2b99616/scratchpad/parityF-start");

    static void show(String k, String dialect, String db) throws Exception {
        probe(k + ".tables", tables(dialect, db));
        probe(k + ".history", history(dialect, db));
    }

    static void migrate(String k, String dialect, String db, String folder) throws Exception {
        try {
            var r = FlywayMigrator.migrate(cfg(dialect, db), "filesystem:" + SQL + folder);
            probe(k + ".outcome", "OK applied=" + r.migrationsExecuted + " initial=" + r.initialSchemaVersion
                    + " target=" + r.targetSchemaVersion);
        } catch (RuntimeException e) {
            probe(k + ".outcome", "FAIL " + msg(e));
        }
        show(k, dialect, db);
    }

    static void validate(String k, String dialect, String db, String folder) {
        try {
            Flyway.configure()
                    .dataSource(url(dialect, db), user(dialect), PW)
                    .locations("filesystem:" + SQL + folder)
                    .load()
                    .validate();
            probe(k + ".validate", "OK");
        } catch (RuntimeException e) {
            probe(k + ".validate", "FAIL " + msg(e));
        }
    }

    static void atStart(long offsetMillis) throws Exception {
        long at = Long.parseLong(Files.readString(START).trim()) + offsetMillis;
        while (System.currentTimeMillis() < at) {
            Thread.sleep(20);
        }
    }

    @Test
    void a1_java_migrates_v1_v2() throws Exception {
        for (var d : DIALECTS) {
            fresh(d, "f6_a");
            migrate("cross." + d + ".a1_java_migrates_v1_v2", d, "f6_a", "turns/a");
        }
    }

    @Test
    void a3_java_migrates_v4() throws Exception {
        for (var d : DIALECTS) {
            migrate("cross." + d + ".a3_java_migrates_v4", d, "f6_a", "turns/c");
        }
    }

    @Test
    void a5_java_validates_and_migrates_again() throws Exception {
        for (var d : DIALECTS) {
            validate("cross." + d + ".a5_java", d, "f6_a", "turns/c");
            migrate("cross." + d + ".a5_java_migrates_again", d, "f6_a", "turns/c");
        }
    }

    @Test
    void b2_java_migrates_v3() throws Exception {
        for (var d : DIALECTS) {
            migrate("cross." + d + ".b2_java_migrates_v3", d, "f6_b", "turns/b");
        }
    }

    @Test
    void b4_java_validates_and_migrates_again() throws Exception {
        for (var d : DIALECTS) {
            validate("cross." + d + ".b4_java", d, "f6_b", "turns/c");
            migrate("cross." + d + ".b4_java_migrates_again", d, "f6_b", "turns/c");
        }
    }

    @Test
    void c1_java_concurrent() throws Exception {
        for (var d : DIALECTS) {
            atStart(switch (d) {
                case "pg" -> 0;
                case "mysql" -> 15_000;
                default -> 30_000;
            });
            long began = System.currentTimeMillis();
            var folder = d.equals("pg") ? "lock_pg" : "lock_mysql";
            try {
                var r = FlywayMigrator.migrate(cfg(d, "f6_c"), "filesystem:" + SQL + folder);
                probe("cross." + d + ".c1_java_outcome", "OK applied=" + r.migrationsExecuted);
            } catch (RuntimeException e) {
                probe("cross." + d + ".c1_java_outcome", "FAIL " + msg(e));
            }
            probe("cross." + d + ".c1_java_took_s", Math.round((System.currentTimeMillis() - began) / 1000.0));
        }
    }

    @Test
    void e1_java_waits_while_python_builds_a_concurrent_index() throws Exception {
        atStart(1_000); // a second after Python, so Python holds the lock
        long began = System.currentTimeMillis();
        try {
            var r = FlywayMigrator.migrate(cfg("pg", "f6_e"), "filesystem:" + SQL + "cic_pg");
            probe("cross.pg.e1_java_outcome", "OK applied=" + r.migrationsExecuted);
        } catch (RuntimeException e) {
            probe("cross.pg.e1_java_outcome", "FAIL " + msg(e));
        }
        probe("cross.pg.e1_java_took_under_30s", System.currentTimeMillis() - began < 30_000);
    }
}
