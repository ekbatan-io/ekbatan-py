package io.ekbatan.flyway;

import static io.ekbatan.flyway.ParityF.*;

import io.ekbatan.core.config.DataSourceConfig;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;

/** PARITY-PROBE F5: location forms, schemas, and a script migration. PostgreSQL. */
class ParityFLocationsProbeTest {

    static void loc(String key, String... locations) throws Exception {
        var db = "f5_loc";
        fresh("pg", db);
        try (var log = new Captured()) {
            try {
                var r = FlywayMigrator.migrate(cfg("pg", db), locations);
                probe("loc." + key + ".outcome", "OK applied=" + r.migrationsExecuted);
            } catch (RuntimeException e) {
                probe("loc." + key + ".outcome", "FAIL " + msg(e));
            }
            probe("loc." + key + ".warnings", log.warnings());
        }
        probe("loc." + key + ".tables", tables("pg", db));
    }

    static DataSourceConfig withQuery(String db, String query) {
        return DataSourceConfig.Builder.dataSourceConfig()
                .jdbcUrl(url("pg", db) + query)
                .username("postgres")
                .password(PW)
                .build();
    }

    static void schema(String key, boolean precreate) throws Exception {
        var db = "f5_schema";
        fresh("pg", db);
        if (precreate) {
            exec("pg", db, "CREATE SCHEMA app");
        }
        try {
            var r = FlywayMigrator.migrate(withQuery(db, "?currentSchema=app"), "filesystem:" + SQL + "quick");
            probe("schema." + key + ".outcome", "OK applied=" + r.migrationsExecuted);
        } catch (RuntimeException e) {
            probe("schema." + key + ".outcome", "FAIL " + msg(e));
        }
        probe("schema." + key + ".schemas", query("pg", db,
                "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'"
                        + " ORDER BY nspname"));
        probe("schema." + key + ".tables", query("pg", db,
                "SELECT table_schema || '.' || table_name FROM information_schema.tables WHERE table_schema NOT IN"
                        + " ('pg_catalog', 'information_schema') ORDER BY 1"));
        try {
            probe("schema." + key + ".history", query("pg", db,
                    "SELECT installed_rank, version, description, type, script FROM app." + HISTORY
                            + " ORDER BY installed_rank"));
        } catch (Exception e) {
            probe("schema." + key + ".history", "none in app");
        }
    }

    @Test
    void go() throws Exception {
        var quick = SQL + "quick";
        loc("filesystem_prefix", "filesystem:" + quick);
        loc("plain_absolute_path", quick);
        loc("classpath_prefix", "classpath:db/parityF");
        loc("no_location");
        loc("missing_folder", "filesystem:" + SQL + "no_such_folder");
        loc("a_file_not_a_folder", "filesystem:" + quick + "/V1__quick.sql");
        loc("two_locations_same_version", "filesystem:" + quick, "filesystem:" + SQL + "hidden");

        loc("two_locations_same_version_native_form", "filesystem:" + quick, "filesystem:" + SQL + "hidden");

        // A watchdog: after 30 s, a CREATE INDEX CONCURRENTLY still waiting is reported and cancelled.
        var watchdog = new Thread(() -> {
            try {
                Thread.sleep(30_000);
                var stuck = query("pg", "postgres", "SELECT pid || ' ' || wait_event FROM pg_stat_activity WHERE"
                        + " query LIKE 'CREATE INDEX CONCURRENTLY%' AND state = 'active'");
                probe("loc.concurrent_index_no_marker.still_waiting_after_30s", stuck.isEmpty() ? "no"
                        : "yes, on " + stuck.get(0).split(" ")[1]);
                for (var row : stuck) {
                    query("pg", "postgres", "SELECT pg_terminate_backend(" + row.split(" ")[0] + ")");
                }
            } catch (InterruptedException e) {
                // finished first
            } catch (Exception e) {
                probe("loc.concurrent_index_no_marker.watchdog_error", msg(e));
            }
        });
        watchdog.start();
        loc("concurrent_index_no_marker", "filesystem:" + SQL + "concurrent_index_pg");
        watchdog.interrupt();
        watchdog.join();

        schema("missing_schema", false);
        schema("existing_schema", true);

        var marker = Path.of("/private/tmp/claude-501/-Users-mike-workspace-ekbatan/73e3c410-5a5d-4b46-86a8-40a1e2b99616/scratchpad/parityF-script-ran");
        Files.deleteIfExists(marker);
        loc("script_migration", "filesystem:" + SQL + "script_migration");
        probe("loc.script_migration.script_ran", Files.exists(marker));
        Files.deleteIfExists(marker);
    }
}
