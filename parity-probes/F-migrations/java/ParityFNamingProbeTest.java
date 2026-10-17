package io.ekbatan.flyway;

import static io.ekbatan.flyway.ParityF.*;

import java.util.List;
import org.junit.jupiter.api.Test;

/** PARITY-PROBE F1: file naming, ordering, what is accepted and refused. */
class ParityFNamingProbeTest {

    static final List<String> FOLDERS = List.of(
            "accept",
            "dup_v1_v1.0",
            "dup_v1_v01",
            "dup_repeatable",
            "lowercase_v",
            "misnamed",
            "repeatable_with_version",
            "undo",
            "conf",
            "callback",
            "post_apply",
            "python_file",
            "non_utf8",
            "long_description",
            "placeholder",
            "placeholder_builtin",
            "hidden",
            "empty",
            "delimiter_mysql");

    @Test
    void go() throws Exception {
        for (var dialect : List.of("pg", "mysql")) {
            for (var folder : FOLDERS) {
                if (folder.equals("delimiter_mysql") && !dialect.equals("mysql")) {
                    continue;
                }
                run(dialect, folder);
            }
        }
    }

    static void run(String dialect, String folder) throws Exception {
        var db = "f1_" + folder.replace('.', '_');
        var key = dialect + "." + folder;
        fresh(dialect, db);
        try (var log = new Captured()) {
            try {
                var r = FlywayMigrator.migrate(cfg(dialect, db), "filesystem:" + SQL + folder);
                probe(key + ".outcome", "OK applied=" + r.migrationsExecuted);
                probe(key + ".versions", r.migrations.stream()
                        .map(m -> m.version.isEmpty() ? "R:" + m.description : m.version)
                        .toList());
            } catch (RuntimeException e) {
                probe(key + ".outcome", "FAIL " + msg(e));
            }
            probe(key + ".warnings", log.warnings().stream()
                    .filter(w -> !w.contains("connection") && !w.contains("Hikari"))
                    .toList());
        }
        probe(key + ".tables", tables(dialect, db));
        if (folder.startsWith("placeholder")) {
            try {
                probe(key + ".t_ph", query(dialect, db, "SELECT v FROM t_ph"));
            } catch (Exception e) {
                probe(key + ".t_ph", "none");
            }
        }
        var h = history(dialect, db);
        probe(key + ".history", h);
    }
}
