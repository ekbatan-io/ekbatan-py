package io.ekbatan.core;

import static io.ekbatan.core.config.DataSourceConfig.Builder.dataSourceConfig;
import static io.ekbatan.core.config.ShardGroupConfig.Builder.shardGroupConfig;
import static io.ekbatan.core.config.ShardMemberConfig.Builder.shardMemberConfig;
import static io.ekbatan.core.config.ShardingConfig.Builder.shardingConfig;
import static io.ekbatan.core.shard.DatabaseRegistry.Builder.databaseRegistry;

import io.ekbatan.core.config.DataSourceConfig;
import io.ekbatan.core.config.PropertyKeyNormalizer;
import io.ekbatan.core.config.ShardingConfig;
import io.ekbatan.core.persistence.ConnectionProvider;
import io.ekbatan.core.persistence.TransactionManager;
import io.ekbatan.core.shard.DatabaseRegistry;
import io.ekbatan.core.shard.EmbeddedBitsShardingStrategy;
import io.ekbatan.core.shard.ShardIdentifier;
import io.ekbatan.core.shard.ShardedUUID;
import io.ekbatan.core.shard.UnknownShardPolicy;
import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Callable;
import org.jooq.SQLDialect;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.dataformat.yaml.YAMLMapper;

/** Parity scan area C, pure logic. Temporary; never committed. */
class ParityCPureProbeTest {

    static final String MARKER = "s3cret-PROBE-marker";
    static final Path DIR = Path.of(
            "/Users/mike/workspace/ekbatan-py/parity-probes/C-transactions-sharding-config");
    static final YAMLMapper YAML = YAMLMapper.builder()
            .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            .build();

    static void p(String key, Object value) {
        System.out.println("PROBE " + key + ": " + value);
    }

    static String sid(ShardIdentifier s) {
        return s == null ? "none" : s.group + ":" + s.member;
    }

    static String err(Callable<?> c) {
        try {
            var r = c.call();
            return "ok";
        } catch (Throwable t) {
            return "error: " + t.getMessage();
        }
    }

    static String hasMarker(String s) {
        return s != null && s.contains(MARKER) ? "yes" : "no";
    }

    static String chain(Throwable t) {
        var sb = new StringBuilder();
        for (var c = t; c != null; c = c.getCause()) {
            sb.append(c).append('\n');
        }
        return sb.toString();
    }

    @Test
    void probe() throws Exception {
        ids();
        shardIdentifiers();
        policy();
        yaml();
        dataSourceConfigs();
        registry();
    }

    // ---------------------------------------------------------------- ids

    void ids() throws Exception {
        int[][] shards = {{0, 0}, {1, 2}, {255, 63}, {128, 32}, {7, 0}, {0, 63}, {255, 0}};
        var lines = new ArrayList<String>();
        for (var s : shards) {
            var shard = ShardIdentifier.of(s[0], s[1]);
            var id = ShardedUUID.generate(shard);
            var u = id.value();
            p("id.mint." + s[0] + "_" + s[1] + ".version", u.version());
            p("id.mint." + s[0] + "_" + s[1] + ".variant", u.variant());
            p("id.mint." + s[0] + "_" + s[1] + ".decoded_self", sid(id.resolveShardIdentifier()));
            lines.add(s[0] + " " + s[1] + " " + u);
        }
        Files.write(DIR.resolve("java-ids.txt"), lines);
        var py = DIR.resolve("py-ids.txt");
        if (Files.exists(py)) {
            var strat = new EmbeddedBitsShardingStrategy();
            for (var line : Files.readAllLines(py)) {
                var parts = line.split(" ");
                var u = UUID.fromString(parts[2]);
                p("id.cross." + parts[0] + "_" + parts[1] + ".decoded_by_other",
                        sid(strat.resolveShardIdentifierById(u).orElse(null)));
            }
        }
        var fixed = UUID.fromString("01890a5d-ac96-774b-bcce-b302099a8057");
        p("id.fixed_v7.shard", sid(new EmbeddedBitsShardingStrategy().resolveShardIdentifierById(fixed).orElse(null)));
        p("id.fixed_v7.instant_ms", ShardedUUID.from(fixed).instant().toEpochMilli());
        var v4 = UUID.fromString("550e8400-e29b-41d4-a716-446655440000");
        p("id.v4.strategy_by_id", sid(new EmbeddedBitsShardingStrategy().resolveShardIdentifierById(v4).orElse(null)));
        p("id.v4.raw_decode", err(() -> ShardedUUID.from(v4).resolveShardIdentifier()) + " "
                + sid(ShardedUUID.from(v4).resolveShardIdentifier()));
        var seen = new HashSet<UUID>();
        for (int i = 0; i < 10000; i++) seen.add(ShardedUUID.generate(ShardIdentifier.of(3, 4)).value());
        p("id.unique_of_10000", seen.size());
        long before = System.currentTimeMillis();
        var t = ShardedUUID.generate(ShardIdentifier.of(1, 1)).instant().toEpochMilli();
        p("id.instant_is_now", Math.abs(t - before) < 1000 ? "yes" : "no");
    }

    // ---------------------------------------------------------------- shard identifier

    void shardIdentifiers() {
        p("sid.of_256_0", err(() -> ShardIdentifier.of(256, 0)));
        p("sid.of_0_64", err(() -> ShardIdentifier.of(0, 64)));
        p("sid.of_-1_0", err(() -> ShardIdentifier.of(-1, 0)));
        p("sid.of_255_63", err(() -> ShardIdentifier.of(255, 63)));
        p("sid.str_1_2", ShardIdentifier.of(1, 2).toString());
        p("sid.equal", ShardIdentifier.of(1, 2).equals(ShardIdentifier.of(1, 2)));
        p("sid.hash_equal", ShardIdentifier.of(1, 2).hashCode() == ShardIdentifier.of(1, 2).hashCode());
        p("sid.default", sid(ShardIdentifier.DEFAULT));
    }

    // ---------------------------------------------------------------- policy

    static String minimalYaml(String extra) {
        return extra + """
                defaultShard:
                  group: 0
                  member: 0
                groups:
                  - group: 0
                    name: global
                    members:
                      - member: 0
                        configs:
                          primaryConfig:
                            jdbcUrl: jdbc:postgresql://primary:5432/db
                            username: app
                """;
    }

    static String policyName(UnknownShardPolicy policy) {
        return policy == null ? "null" : policy.name().toLowerCase();
    }

    void policy() {
        Map<String, String> spellings = new LinkedHashMap<>();
        spellings.put("fail", "fail");
        spellings.put("FAIL", "FAIL");
        spellings.put("Fail", "Fail");
        spellings.put("useDefaultShard", "useDefaultShard");
        spellings.put("use_default_shard", "use_default_shard");
        spellings.put("USE-DEFAULT-SHARD", "USE-DEFAULT-SHARD");
        spellings.put("use-default-shard", "use-default-shard");
        spellings.put("UseDefaultShard", "UseDefaultShard");
        spellings.put("padded_fail", "' fail '");
        spellings.put("empty_string", "''");
        spellings.put("null_value", "");
        spellings.put("shrug", "shrug");
        for (var e : spellings.entrySet()) {
            String r;
            try {
                var cfg = YAML.readValue(minimalYaml("unknownShardPolicy: " + e.getValue() + "\n"), ShardingConfig.class);
                r = policyName(cfg.unknownShardPolicy);
            } catch (Exception ex) {
                r = "error";
            }
            p("policy.yaml." + e.getKey(), r);
        }
        try {
            p("policy.yaml.omitted", policyName(YAML.readValue(minimalYaml(""), ShardingConfig.class).unknownShardPolicy));
        } catch (Exception ex) {
            p("policy.yaml.omitted", "error");
        }
        p("policy.yaml.shrug.msg_names_both_values", err(() -> YAML.readValue(minimalYaml("unknownShardPolicy: shrug\n"), ShardingConfig.class))
                .contains("fail") ? "yes" : "no");
        // programmatic
        var member = shardMemberConfig().member(0).primaryConfig(dataSourceConfig().jdbcUrl("jdbc:postgresql://h/db").build()).build();
        var group = shardGroupConfig().group(0).name("g").withMember(member).build();
        p("policy.config_builder_null", policyName(shardingConfig().defaultShard(ShardIdentifier.DEFAULT).withGroup(group)
                .unknownShardPolicy(null).build().unknownShardPolicy));
        p("policy.config_builder_omitted", policyName(shardingConfig().defaultShard(ShardIdentifier.DEFAULT).withGroup(group)
                .build().unknownShardPolicy));
    }

    // ---------------------------------------------------------------- yaml

    static final String FULL = """
            defaultShard:
              group: 0
              member: 0
            groups:
              - group: 0
                name: global
                members:
                  - member: 0
                    name: global-eu-1
                    configs:
                      primaryConfig:
                        jdbcUrl: jdbc:postgresql://primary-eu-1:5432/db
                        username: app
                        maximumPoolSize: 20
                        dataSourceProperties:
                          ApplicationName: orders-service
                          ha.enableJMX: true
                          connectTimeout: 10
                      secondaryConfig:
                        jdbcUrl: jdbc:postgresql://replica-eu-1:5432/db
                        username: app
                      lockConfig:
                        jdbcUrl: jdbc:postgresql://locks-eu-1:5432/db
                        username: app
              - group: 1
                name: mexico
                members:
                  - member: 3
                    configs:
                      primaryConfig:
                        jdbcUrl: jdbc:postgresql://primary-mx-1:5432/db
            """;

    ShardingConfig load(String yaml) throws Exception {
        return YAML.readValue(yaml, ShardingConfig.class);
    }

    String loadResult(String yaml) {
        try {
            var c = load(yaml);
            return "ok";
        } catch (Exception e) {
            return "error";
        }
    }

    String loadMessage(String yaml) {
        try {
            load(yaml);
            return "ok";
        } catch (Exception e) {
            return e.getMessage().lines().findFirst().orElse("");
        }
    }

    void yaml() throws Exception {
        var c = load(FULL);
        p("yaml.default_shard", sid(c.defaultShard));
        p("yaml.groups", c.groups.size());
        p("yaml.group1.number", c.groups.get(1).group);
        p("yaml.group0.name", c.groups.get(0).name);
        p("yaml.member0.name", c.groups.get(0).members.get(0).name.orElse("none"));
        p("yaml.member_mx.name", c.groups.get(1).members.get(0).name.orElse("none"));
        p("yaml.member_mx.number", c.groups.get(1).members.get(0).member);
        var primary = c.groups.get(0).members.get(0).primaryConfig();
        p("yaml.primary.max_pool", primary.maximumPoolSize);
        p("yaml.secondary.present", c.groups.get(0).members.get(0).secondaryConfig().isPresent());
        p("yaml.secondary.max_pool_default", c.groups.get(0).members.get(0).secondaryConfig().get().maximumPoolSize);
        p("yaml.lock_config.present", c.groups.get(0).members.get(0).configFor("lockConfig").isPresent());
        p("yaml.mx.secondary.present", c.groups.get(1).members.get(0).secondaryConfig().isPresent());
        p("yaml.mx.username", c.groups.get(1).members.get(0).primaryConfig().username.orElse("none"));
        Object v = primary.dataSourceProperties.get("connectTimeout");
        p("yaml.dsp.connectTimeout", v + " (" + (v instanceof String ? "text" : "number") + ")");
        Object b = primary.dataSourceProperties.get("ha.enableJMX");
        p("yaml.dsp.ha.enableJMX", b + " (" + (b instanceof String ? "text" : "bool") + ")");
        p("yaml.dsp.names_kept", primary.dataSourceProperties.keySet());

        p("yaml.group_number_missing", loadResult(FULL.replace("  - group: 1\n    name: mexico", "  - name: mexico")));
        try {
            var g = load(FULL.replace("  - group: 1\n    name: mexico", "  - name: mexico"));
            p("yaml.group_number_missing.value", g.groups.get(1).group);
        } catch (Exception e) {
            p("yaml.group_number_missing.value", "error");
        }
        p("yaml.member_number_missing", loadResult(FULL.replace("      - member: 3\n        configs:", "      - configs:")));
        p("yaml.unknown_key", loadResult(FULL.replace("    members:\n      - member: 3", "    memmbers:\n      - member: 3")));
        p("yaml.unknown_key.names_key", loadMessage(FULL.replace("    members:\n      - member: 3", "    memmbers:\n      - member: 3")).contains("memmbers") ? "yes" : "no");
        p("yaml.no_primary", loadMessage("""
                defaultShard: {group: 0, member: 0}
                groups:
                  - group: 0
                    name: g
                    members:
                      - member: 0
                        configs:
                          secondaryConfig:
                            jdbcUrl: jdbc:postgresql://h/db
                """).contains("configs.primaryConfig is required") ? "error: configs.primaryConfig is required" : "other");
        p("yaml.no_groups", loadMessage("defaultShard: {group: 0, member: 0}\ngroups: []\n").contains("at least one group is required") ? "error: at least one group is required" : "other");
        p("yaml.no_default_shard", loadResult(minimalYaml("").replace("defaultShard:\n  group: 0\n  member: 0\n", "")));
        p("yaml.group_no_name", loadResult(minimalYaml("").replace("    name: global\n", "")));
        p("yaml.default_names_nothing", loadResult(minimalYaml("").replace("defaultShard:\n  group: 0\n  member: 0", "defaultShard:\n  group: 9\n  member: 9")));
        p("yaml.duplicate_shard", loadResult(minimalYaml("") + """
                  - group: 0
                    name: again
                    members:
                      - member: 0
                        configs:
                          primaryConfig:
                            jdbcUrl: jdbc:postgresql://other:5432/db
                """));
        p("yaml.mixed_dialects", loadResult(minimalYaml("") + """
                  - group: 1
                    name: my
                    members:
                      - member: 0
                        configs:
                          primaryConfig:
                            jdbcUrl: jdbc:mysql://other:3306/db
                """));
        p("yaml.kebab_keys.core_binding", loadResult(minimalYaml("").replace("defaultShard", "default-shard")));
        p("yaml.kebab_keys.di_normalizer", PropertyKeyNormalizer.kebabToCamel("default-shard.group") + " "
                + PropertyKeyNormalizer.kebabToCamel("groups[0].members[0].configs.primary-config.maximum-pool-size"));
        p("yaml.bad_pool_value.marker_in_error", hasMarker(loadMessage(minimalYaml("").replace("username: app", "username: app\n            maximumPoolSize: " + MARKER))));
        p("yaml.password_as_map.marker_in_error", hasMarker(loadMessage(minimalYaml("").replace("username: app", "username: app\n            password: {x: " + MARKER + "}"))));
        p("yaml.password_as_list.marker_in_error", hasMarker(loadMessage(minimalYaml("").replace("username: app", "username: app\n            password: [" + MARKER + "]"))));
        p("yaml.password_ok.result", loadResult(minimalYaml("").replace("username: app", "username: app\n            password: " + MARKER)));
    }

    // ---------------------------------------------------------------- data source config

    static DataSourceConfig.Builder pg() {
        return dataSourceConfig().jdbcUrl("jdbc:postgresql://h:5432/db");
    }

    void dataSourceConfigs() {
        var d = pg().build();
        p("dsc.default.max_connections", d.maximumPoolSize);
        p("dsc.default.username", d.username.orElse("none"));
        p("dsc.default.password", d.password.isPresent() ? "set" : "none");
        p("dsc.pool_0", err(() -> pg().maximumPoolSize(0).build()));
        p("dsc.pool_-1", err(() -> pg().maximumPoolSize(-1).build()));
        p("dsc.url_blank", err(() -> dataSourceConfig().jdbcUrl("  ").build()));
        p("dsc.url_missing", err(() -> dataSourceConfig().build()));
        p("dsc.username_blank", err(() -> pg().username("  ").build()));
        p("dsc.username_empty", err(() -> pg().username("").build()));
        p("dsc.password_empty", err(() -> pg().username("u").password("").build()));
        p("dsc.password_only", err(() -> pg().password("x").build()));
        p("dsc.dialect.postgres", pg().build().dialect);
        p("dsc.dialect.mysql", dataSourceConfig().jdbcUrl("jdbc:mysql://h:3306/db").build().dialect);
        p("dsc.dialect.mariadb", dataSourceConfig().jdbcUrl("jdbc:mariadb://h:3306/db").build().dialect);
        p("dsc.dialect.oracle", err(() -> dataSourceConfig().jdbcUrl("jdbc:oracle:thin:@h:1521:x").build()));
        p("dsc.dialect.sqlite", err(() -> dataSourceConfig().jdbcUrl("jdbc:sqlite:x.db").build()));
        p("dsc.dialect.garbage", err(() -> dataSourceConfig().jdbcUrl("nonsense").build()));
        p("dsc.dsp.user", err(() -> pg().dataSourceProperties(Map.of("user", "x")).build()));
        p("dsc.dsp.PASSWORD", err(() -> pg().dataSourceProperties(Map.of("PASSWORD", "x")).build()));
        p("dsc.dsp.passwd", err(() -> pg().dataSourceProperties(Map.of("passwd", "x")).build()));
        var nullValue = new LinkedHashMap<String, Object>();
        nullValue.put("ssl", null);
        p("dsc.dsp.null_value", err(() -> pg().dataSourceProperties(nullValue).build()));
        p("dsc.dsp.nested", err(() -> pg().dataSourceProperties(Map.of("ssl", Map.of("ca", "x"))).build()));
        p("dsc.dsp.list", err(() -> pg().dataSourceProperties(Map.of("ssl", List.of("x"))).build()));
        p("dsc.dsp.blank_name", err(() -> pg().dataSourceProperties(Map.of(" ", "x")).build()));
        Object n = pg().dataSourceProperties(Map.of("connectTimeout", 10)).build().dataSourceProperties.get("connectTimeout");
        p("dsc.dsp.number_kept_as", n instanceof String ? "text" : "number");
        // credentials in the url
        p("dsc.url_query_password", err(() -> dataSourceConfig().jdbcUrl("jdbc:postgresql://h/db?password=" + MARKER).build()).startsWith("error") ? "error" : "ok");
        p("dsc.url_query_password.marker_in_error", hasMarker(err(() -> dataSourceConfig().jdbcUrl("jdbc:postgresql://h/db?password=" + MARKER).build())));
        p("dsc.url_userinfo_password", err(() -> dataSourceConfig().jdbcUrl("jdbc:postgresql://u:" + MARKER + "@h/db").build()).startsWith("error") ? "error" : "ok");
        p("dsc.url_userinfo_password.marker_in_error", hasMarker(err(() -> dataSourceConfig().jdbcUrl("jdbc:postgresql://u:" + MARKER + "@h/db").build())));
        p("dsc.url_query_user", err(() -> dataSourceConfig().jdbcUrl("jdbc:postgresql://h/db?user=bob").build()).startsWith("error") ? "error" : "ok");
        p("dsc.url_query_sslpassword", err(() -> dataSourceConfig().jdbcUrl("jdbc:postgresql://h/db?sslpassword=" + MARKER).build()).startsWith("error") ? "error" : "ok");
        p("dsc.url_query_sslpassword.marker_in_error", hasMarker(err(() -> dataSourceConfig().jdbcUrl("jdbc:postgresql://h/db?sslpassword=" + MARKER).build())));
        p("dsc.dsp.password.marker_in_error", hasMarker(err(() -> pg().dataSourceProperties(Map.of("password", MARKER)).build())));
        // printed forms
        var secret = pg().username("u").password(MARKER).dataSourceProperties(Map.of("sslpassword", MARKER)).build();
        p("dsc.printed.marker", hasMarker(String.valueOf(secret)));
        var member = shardMemberConfig().member(0).primaryConfig(secret).build();
        var group = shardGroupConfig().group(0).name("g").withMember(member).build();
        var cfg = shardingConfig().defaultShard(ShardIdentifier.DEFAULT).withGroup(group).build();
        p("dsc.member_printed.marker", hasMarker(String.valueOf(member)));
        p("dsc.layout_printed.marker", hasMarker(String.valueOf(cfg)));
    }

    // ---------------------------------------------------------------- registry

    static TransactionManager tm(int g, int m) {
        var cfg = dataSourceConfig().jdbcUrl("jdbc:postgresql://127.0.0.1:1/x").username("u").build();
        var cp = ConnectionProvider.hikariConnectionProvider(cfg);
        return new TransactionManager(cp, cp, SQLDialect.POSTGRES, ShardIdentifier.of(g, m));
    }

    static String captureErr(Runnable r) {
        var old = System.err;
        var buf = new ByteArrayOutputStream();
        System.setErr(new PrintStream(buf, true));
        try {
            r.run();
        } finally {
            System.setErr(old);
        }
        return buf.toString();
    }

    void registry() {
        p("reg.empty", err(() -> databaseRegistry().build()));
        var a = tm(0, 0);
        var b = tm(1, 0);
        p("reg.two_no_default", err(() -> databaseRegistry().withDatabase(a).withDatabase(b).build()));
        p("reg.one_no_default.default", sid(databaseRegistry().withDatabase(a).build().defaultShard));
        p("reg.null_policy", policyName(databaseRegistry().withDatabase(a).withUnknownShardPolicy(null).build().unknownShardPolicy));
        var reg = databaseRegistry().withDefaultDatabase(a).withDatabase(b).build();
        p("reg.default", sid(reg.defaultShard));
        p("reg.manager_unknown", err(() -> reg.transactionManager(ShardIdentifier.of(9, 9))));
        p("reg.manager_known", sid(reg.transactionManager(ShardIdentifier.of(1, 0)).shardIdentifier));
        p("reg.effective.registered", sid(reg.effectiveShard(ShardIdentifier.of(1, 0))));
        var holder = new String[1];
        var log = captureErr(() -> holder[0] = sid(reg.effectiveShard(ShardIdentifier.of(5, 5))));
        p("reg.effective.unknown_default_policy", holder[0]);
        p("reg.effective.unknown.warn_lines", log.lines().filter(l -> l.contains("is not registered")).count());
        p("reg.effective.unknown.warn_text", log.lines().filter(l -> l.contains("is not registered")).findFirst().orElse("none").replaceAll("^.*?(WARN)", "$1"));
        var log2 = captureErr(() -> {
            reg.effectiveShard(ShardIdentifier.of(5, 5));
            reg.effectiveShard(ShardIdentifier.of(5, 5));
        });
        p("reg.effective.unknown.warn_every_call", log2.lines().filter(l -> l.contains("is not registered")).count());
        var log3 = captureErr(() -> holder[0] = err(() -> sid(reg.effectiveShard(null))) + " -> "
                + (holder[0] = sidOrErr(() -> reg.effectiveShard(null))));
        p("reg.effective.null_default_policy", sidOrErr(() -> reg.effectiveShard(null)));
        p("reg.effective.null.warn_lines", log3.lines().filter(l -> l.contains("is not registered")).count());
        var failing = databaseRegistry().withDefaultDatabase(a).withDatabase(b)
                .withUnknownShardPolicy(UnknownShardPolicy.FAIL).build();
        p("reg.effective.unknown_fail_policy", err(() -> failing.effectiveShard(ShardIdentifier.of(5, 5))));
        p("reg.effective.null_fail_policy", sidOrErr(() -> failing.effectiveShard(null)));
        p("reg.effective.registered_fail_policy", sid(failing.effectiveShard(ShardIdentifier.of(1, 0))));
        p("reg.shards_order", reg.allTransactionManagers().stream().map(t -> sid(t.shardIdentifier)).toList());

        // fromConfig
        var pgCfg = dataSourceConfig().jdbcUrl("jdbc:postgresql://127.0.0.1:1/x").username("u").build();
        var replicaCfg = dataSourceConfig().jdbcUrl("jdbc:postgresql://127.0.0.1:2/x").username("u").build();
        var noReplica = shardMemberConfig().member(0).primaryConfig(pgCfg).build();
        var withReplica = shardMemberConfig().member(1).primaryConfig(pgCfg).secondaryConfig(replicaCfg).build();
        var layout = shardingConfig().defaultShard(ShardIdentifier.DEFAULT)
                .withGroup(shardGroupConfig().group(0).name("g").withMember(noReplica).withMember(withReplica).build())
                .build();
        var fromCfg = DatabaseRegistry.fromConfig(layout);
        var t0 = fromCfg.transactionManager(ShardIdentifier.of(0, 0));
        var t1 = fromCfg.transactionManager(ShardIdentifier.of(0, 1));
        p("reg.from_config.no_replica_shares_pool", t0.secondaryConnectionProvider == t0.primaryConnectionProvider);
        p("reg.from_config.replica_own_pool", t1.secondaryConnectionProvider != t1.primaryConnectionProvider);
        p("reg.from_config.policy_default", policyName(fromCfg.unknownShardPolicy));
        var badDefault = shardingConfig().defaultShard(ShardIdentifier.of(9, 9))
                .withGroup(shardGroupConfig().group(0).name("g").withMember(noReplica).build()).build();
        p("reg.from_config.default_names_nothing", err(() -> DatabaseRegistry.fromConfig(badDefault)));
        var dup = shardingConfig().defaultShard(ShardIdentifier.DEFAULT)
                .withGroup(shardGroupConfig().group(0).name("g").withMember(noReplica).build())
                .withGroup(shardGroupConfig().group(0).name("again").withMember(noReplica).build())
                .build();
        p("reg.from_config.duplicate_shard", err(() -> {
            var r = DatabaseRegistry.fromConfig(dup);
            var n = r.allTransactionManagers().size();
            r.close();
            return n;
        }));
        try {
            var r = DatabaseRegistry.fromConfig(dup);
            p("reg.from_config.duplicate_shard.registered", r.allTransactionManagers().size());
            r.close();
        } catch (Exception e) {
            p("reg.from_config.duplicate_shard.registered", "error");
        }
        p("reg.close_twice", err(() -> {
            fromCfg.close();
            fromCfg.close();
            return null;
        }));
        reg.close();
        failing.close();
    }

    static String sidOrErr(Callable<ShardIdentifier> c) {
        try {
            return sid(c.call());
        } catch (Throwable t) {
            return "error: " + t.getMessage();
        }
    }
}
