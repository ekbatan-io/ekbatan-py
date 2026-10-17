package io.ekbatan.flyway;

import static io.ekbatan.flyway.ParityF.*;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * PARITY-PROBE F6: one database, both ports in turn. Each method is one step; cross.sh runs them
 * in order with the Python steps (f6_cross.py) between them.
 */
class ParityFCrossProbeTest {

    static final List<String> DIALECTS = List.of("pg", "mysql");
    static final Path START = Path.of(
            "/private/tmp/claude-501/-Users-mike-workspace-ekbatan/73e3c410-5a5d-4b46-86a8-40a1e2b99616/scratchpad/parityF-start");

    static void migrate(String k, String dialect, String db, String folder) throws Exception {
        try {
            var r = FlywayMigrator.migrate(cfg(dialect, db), "filesystem:" + SQL + folder);
            probe(k + ".outcome", "OK applied=" + r.migrationsExecuted + " initial=" + r.initialSchemaVersion);
        } catch (RuntimeException e) {
            probe(k + ".outcome", "FAIL " + msg(e));
        }
        probe(k + ".tables", tables(dialect, db));
        probe(k + ".flyway_history", history(dialect, db));
        probe(k + ".ekbatan_history", history(dialect, db, "ekbatan_schema_history"));
    }

    @Test
    void a1_java_first() throws Exception {
        for (var d : DIALECTS) {
            fresh(d, "f6_a");
            migrate("cross." + d + ".a1_java_migrates_base", d, "f6_a", "life/base");
            probe("cross." + d + ".a1_installed_on_sample", query(d, "f6_a",
                    "SELECT installed_on FROM " + HISTORY + " WHERE installed_rank = 1"));
        }
    }

    @Test
    void a4_java_continues_after_python_baselined() throws Exception {
        for (var d : DIALECTS) {
            migrate("cross." + d + ".a4_java_migrates_base_plus_v3", d, "f6_a", "life/fixed");
        }
    }

    @Test
    void b2_java_after_python() throws Exception {
        for (var d : DIALECTS) {
            migrate("cross." + d + ".b2_java_migrates_base_plus_v3", d, "f6_b", "life/fixed");
        }
    }

    @Test
    void c1_java_concurrent() throws Exception {
        long at = Long.parseLong(Files.readString(START).trim());
        for (var d : DIALECTS) {
            while (System.currentTimeMillis() < at) {
                Thread.sleep(20);
            }
            try {
                var r = FlywayMigrator.migrate(cfg(d, "f6_c"), "filesystem:" + SQL + "lock_" + d);
                probe("cross." + d + ".c1_java_outcome", "OK applied=" + r.migrationsExecuted);
            } catch (RuntimeException e) {
                probe("cross." + d + ".c1_java_outcome", "FAIL " + msg(e));
            }
            at += 15_000; // the second dialect starts 15 s later, in both ports
        }
    }
}
