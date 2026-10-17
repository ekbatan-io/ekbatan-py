package io.ekbatan.flyway;

import static io.ekbatan.flyway.ParityF.*;

import java.util.List;
import org.junit.jupiter.api.Test;

/** PARITY-PROBE F2: the history table, re-runs, edits, failures, baseline - per dialect. */
class ParityFLifecycleProbeTest {

    @Test
    void go() throws Exception {
        for (var dialect : List.of("pg", "mysql", "maria")) {
            life(dialect);
            future(dialect);
            nonEmpty(dialect);
            baseline(dialect);
        }
    }

    static void step(String dialect, String db, String key, String folder) throws Exception {
        try (var log = new Captured()) {
            try {
                var r = FlywayMigrator.migrate(cfg(dialect, db), "filesystem:" + SQL + "life/" + folder);
                probe(key + ".outcome", "OK applied=" + r.migrationsExecuted + " initial=" + r.initialSchemaVersion
                        + " target=" + r.targetSchemaVersion);
                probe(key + ".result_warnings", r.warnings);
            } catch (RuntimeException e) {
                probe(key + ".outcome", "FAIL " + msg(e));
            }
            probe(key + ".log_warnings", log.warnings());
        }
        probe(key + ".tables", tables(dialect, db));
        probe(key + ".history", history(dialect, db));
    }

    static void life(String dialect) throws Exception {
        var db = "f2_life";
        var k = dialect + ".life.";
        fresh(dialect, db);
        step(dialect, db, k + "a_first", "base");
        probe(k + "a_first.history_tables", query(dialect, db,
                "SELECT table_name FROM information_schema.tables WHERE table_name LIKE '%schema_history'"
                        + " AND table_schema = " + (dialect.equals("pg") ? "current_schema()" : "DATABASE()")));
        probe(k + "a_first.columns", columns(dialect, db));
        probe(k + "a_first.indexes", dialect.equals("pg")
                ? query(dialect, db, "SELECT indexname || ' ' || indexdef FROM pg_indexes WHERE tablename = '"
                        + HISTORY + "' ORDER BY indexname")
                : query(dialect, db, "SELECT index_name, column_name FROM information_schema.statistics WHERE"
                        + " table_schema = DATABASE() AND table_name = '" + HISTORY + "' ORDER BY index_name"));
        probe(k + "a_first.installed_on_type", query(dialect, db,
                "SELECT data_type FROM information_schema.columns WHERE table_name = '" + HISTORY
                        + "' AND column_name = 'installed_on' AND table_schema = "
                        + (dialect.equals("pg") ? "current_schema()" : "DATABASE()")));
        step(dialect, db, k + "b_again", "base");
        step(dialect, db, k + "c_edited", "edited_v1");
        step(dialect, db, k + "d_crlf", "crlf_v1");
        step(dialect, db, k + "e_joined", "joined_v1");
        step(dialect, db, k + "f_renamed", "renamed_v1");
        step(dialect, db, k + "g_respelled", "respelled_v1");
        step(dialect, db, k + "h_out_of_order", "out_of_order");
        step(dialect, db, k + "i_missing", "missing_v2");
        step(dialect, db, k + "j_repeatable_changed", "repeatable_changed");
        step(dialect, db, k + "k_failing", "failing");
        step(dialect, db, k + "l_fixed", "fixed");
        step(dialect, db, k + "m_fixed_again", "fixed");
    }

    static void future(String dialect) throws Exception {
        var db = "f2_future";
        fresh(dialect, db);
        step(dialect, db, dialect + ".future.a_newer_release", "future");
        step(dialect, db, dialect + ".future.b_older_release", "base");
    }

    static void nonEmpty(String dialect) throws Exception {
        var db = "f2_nonempty";
        fresh(dialect, db);
        exec(dialect, db, "CREATE TABLE existing (id INT)");
        step(dialect, db, dialect + ".nonempty", "base");
    }

    static void baseline(String dialect) throws Exception {
        var db = "f2_baseline";
        var k = dialect + ".baseline";
        fresh(dialect, db);
        exec(dialect, db, "CREATE TABLE life_a (id INT PRIMARY KEY, name VARCHAR(20))");
        try {
            var r = FlywayMigrator.builder()
                    .withDataSource(cfg(dialect, db))
                    .locations("filesystem:" + SQL + "life/base")
                    .customize(c -> c.baselineOnMigrate(true).baselineVersion("1"))
                    .migrate()
                    .get(0)
                    .migrateResult();
            probe(k + ".outcome", "OK applied=" + r.migrationsExecuted + " initial=" + r.initialSchemaVersion
                    + " target=" + r.targetSchemaVersion);
        } catch (RuntimeException e) {
            probe(k + ".outcome", "FAIL " + msg(e));
        }
        probe(k + ".tables", tables(dialect, db));
        probe(k + ".history", history(dialect, db));
    }
}
