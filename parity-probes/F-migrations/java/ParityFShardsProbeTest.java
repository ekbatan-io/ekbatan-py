package io.ekbatan.flyway;

import static io.ekbatan.core.config.ShardGroupConfig.Builder.shardGroupConfig;
import static io.ekbatan.core.config.ShardMemberConfig.Builder.shardMemberConfig;
import static io.ekbatan.core.config.ShardingConfig.Builder.shardingConfig;
import static io.ekbatan.flyway.ParityF.*;

import io.ekbatan.core.config.ShardingConfig;
import io.ekbatan.core.shard.ShardIdentifier;
import java.util.List;
import java.util.TreeSet;
import org.junit.jupiter.api.Test;
import org.slf4j.MDC;

/** PARITY-PROBE F4: several shards - order, one failing, the log lines and the MDC keys. */
class ParityFShardsProbeTest {

    static ShardingConfig layout(int[] groups) {
        var b = shardingConfig().defaultShard(ShardIdentifier.of(0, 0));
        String[] names = {"global", "mexico", "brazil"};
        for (int g : groups) {
            b.withGroup(shardGroupConfig()
                    .group(g)
                    .name(names[g])
                    .withMember(shardMemberConfig()
                            .member(0)
                            .primaryConfig(cfg("pg", "f4_s" + g))
                            .build())
                    .build());
        }
        return b.build();
    }

    static void report(String k, Captured log) {
        probe(k + ".ekbatan_lines", log.ekbatan().stream()
                .map(l -> l.replace("io.ekbatan.flyway.FlywayMigrator", "FlywayMigrator"))
                .toList());
        var flywayMdc = new TreeSet<String>();
        long flywayLines = 0;
        for (var l : log.lines) {
            if (l.contains("|org.flywaydb.")) {
                var p = l.split("\\|");
                flywayMdc.add(p[2] + " " + p[3]);
                flywayLines++;
            }
        }
        probe(k + ".flyway_lines_present", flywayLines > 0);
        probe(k + ".flyway_lines_mdc", flywayMdc);
    }

    @Test
    void go() throws Exception {
        for (int g = 0; g < 3; g++) {
            fresh("pg", "f4_s" + g);
        }
        exec("pg", "f4_s1", "CREATE TABLE existing (id INT)");

        probe("targets.config_order", FlywayMigrator.targets(layout(new int[] {0, 1, 2})).stream()
                .map(t -> t.shard().group + ":" + t.shard().member + " " + t.name())
                .toList());
        probe("targets.groups_given_2_0_1", FlywayMigrator.targets(layout(new int[] {2, 0, 1})).stream()
                .map(t -> t.shard().group + ":" + t.shard().member + " " + t.name())
                .toList());

        // one shard failing
        try (var log = new Captured()) {
            try {
                var rs = FlywayMigrator.migrate(layout(new int[] {0, 1, 2}), "filesystem:" + SQL + "quick");
                probe("fail.outcome", "OK " + rs.size());
            } catch (RuntimeException e) {
                probe("fail.outcome", "FAIL " + msg(e));
            }
            report("fail", log);
        }
        probe("fail.mdc_after", MDC.get(FlywayMigrator.MDC_SHARD) + " " + MDC.get(FlywayMigrator.MDC_TARGET));
        for (int g = 0; g < 3; g++) {
            probe("fail.s" + g + ".history", history("pg", "f4_s" + g));
        }

        // fixed, run again
        exec("pg", "f4_s1", "DROP TABLE existing");
        try (var log = new Captured()) {
            var rs = FlywayMigrator.migrate(layout(new int[] {0, 1, 2}), "filesystem:" + SQL + "quick");
            probe("again.results", rs.stream()
                    .map(r -> r.target().name() + " applied=" + r.migrateResult().migrationsExecuted)
                    .toList());
            report("again", log);
        }

        // a single data source, and named targets
        fresh("pg", "f4_single");
        try (var log = new Captured()) {
            FlywayMigrator.migrate(cfg("pg", "f4_single"), "filesystem:" + SQL + "quick");
            report("single", log);
        }
        var t = FlywayMigrator.target(ShardIdentifier.of(3, 1), cfg("pg", "f4_single"));
        probe("target.shard_no_name", t.shard().group + ":" + t.shard().member + " " + t.name());
        var d = FlywayMigrator.target(cfg("pg", "f4_single"));
        probe("target.default", d.shard().group + ":" + d.shard().member + " " + d.name());

        // refusals before anything connects
        try {
            FlywayMigrator.builder().migrate();
            probe("refuse.no_targets", "OK");
        } catch (RuntimeException e) {
            probe("refuse.no_targets", "FAIL " + msg(e));
        }
        try {
            FlywayMigrator.migrate(cfg("pg", "f4_single"), "  ");
            probe("refuse.blank_location", "OK");
        } catch (RuntimeException e) {
            probe("refuse.blank_location", "FAIL " + msg(e));
        }
        try {
            FlywayMigrator.target(ShardIdentifier.of(0, 0), " ", cfg("pg", "f4_single"));
            probe("refuse.blank_target_name", "OK");
        } catch (RuntimeException e) {
            probe("refuse.blank_target_name", "FAIL " + msg(e));
        }
    }
}
