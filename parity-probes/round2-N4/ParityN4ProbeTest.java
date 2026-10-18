package io.ekbatan.test.postgres_sharded.wallet.action;

import static io.ekbatan.core.action.ActionExecutor.Builder.actionExecutor;
import static io.ekbatan.core.action.ActionRegistry.Builder.actionRegistry;
import static io.ekbatan.core.action.ExecutionConfiguration.Builder.executionConfiguration;
import static io.ekbatan.core.config.ShardGroupConfig.Builder.shardGroupConfig;
import static io.ekbatan.core.config.ShardMemberConfig.Builder.shardMemberConfig;
import static io.ekbatan.core.config.ShardingConfig.Builder.shardingConfig;
import static io.ekbatan.core.repository.RepositoryRegistry.Builder.repositoryRegistry;
import static io.ekbatan.test.postgres_sharded.wallet.models.Wallet.createWallet;

import io.ekbatan.core.action.Action;
import io.ekbatan.core.action.ActionExecutor;
import io.ekbatan.core.action.ActionPlan;
import io.ekbatan.core.action.RetryConfig;
import io.ekbatan.core.concurrent.LockAcquisitionException;
import io.ekbatan.core.concurrent.PostgresKeyedLockProvider;
import io.ekbatan.core.config.DataSourceConfig;
import io.ekbatan.core.config.ShardingConfig;
import io.ekbatan.core.domain.ShardedId;
import io.ekbatan.core.persistence.ConnectionProvider;
import io.ekbatan.core.repository.exception.StaleRecordException;
import io.ekbatan.core.shard.DatabaseRegistry;
import io.ekbatan.core.shard.ShardIdentifier;
import io.ekbatan.core.shard.ShardedUUID;
import io.ekbatan.core.shard.UnknownShardPolicy;
import io.ekbatan.flyway.FlywayMigrator;
import io.ekbatan.test.postgres_sharded.wallet.models.Wallet;
import io.ekbatan.test.postgres_sharded.wallet.repository.WalletRepository;
import io.ekbatan.testsupport.action.ActionSpec;
import io.ekbatan.testsupport.time.VirtualClock;
import java.math.BigDecimal;
import java.security.Principal;
import java.sql.DriverManager;
import java.time.Clock;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Currency;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;
import tools.jackson.databind.ObjectMapper;

/** Round 2 area N4 parity probe: errors and the testing helper. Prints PROBE lines. */
@Testcontainers
public class ParityN4ProbeTest {

    @Container
    private static final PostgreSQLContainer globalDb = new PostgreSQLContainer("postgres:latest")
            .withDatabaseName("global_db")
            .withUsername("test")
            .withPassword("test")
            .withEnv("TZ", "UTC");

    @Container
    private static final PostgreSQLContainer mexicoDb = new PostgreSQLContainer("postgres:latest")
            .withDatabaseName("mexico_db")
            .withUsername("test")
            .withPassword("test")
            .withEnv("TZ", "UTC");

    static final ShardIdentifier GLOBAL = ShardIdentifier.of(0, 0);
    static final ShardIdentifier MEXICO = ShardIdentifier.of(1, 0);
    static final ShardIdentifier AUSTRALIA = ShardIdentifier.of(2, 0);
    static final String DEAD_URL = "jdbc:postgresql://127.0.0.1:58599/nowhere";
    static final Clock clock = Clock.systemUTC();
    static final AtomicInteger ATTEMPTS = new AtomicInteger();
    static final AtomicReference<ShardedId<Wallet>> FIXED_ID = new AtomicReference<>();
    static final Principal alice = () -> "alice";

    static void p(String key, Object value) {
        System.out.println("PROBE " + key + ": " + value);
    }

    interface Body {
        Object run() throws Exception;
    }

    static Throwable catching(Body b) {
        try {
            b.run();
            return null;
        } catch (Throwable t) {
            return t;
        }
    }

    static String name(Throwable t) {
        return t == null ? "none" : t.getClass().getSimpleName();
    }

    static String chain(Throwable t) {
        var out = new ArrayList<String>();
        while (t != null) {
            out.add(t.getClass().getSimpleName());
            t = t.getCause();
        }
        return out.isEmpty() ? "none" : String.join(" <- ", out);
    }

    static String oneLine(Throwable t) {
        if (t == null) return "none";
        var m = String.valueOf(t.getMessage()).split("\n")[0];
        return m.length() > 220 ? m.substring(0, 220) : m;
    }

    static void report(String key, Throwable t) {
        p(key + ".type", name(t));
        p(key + ".msg", oneLine(t));
        p(key + ".chain", chain(t));
        p(key + ".caught_by_framework_base", t instanceof RuntimeException ? "RuntimeException" : "no");
    }

    static Wallet newWallet(ShardIdentifier shard) {
        return createWallet(shard, UUID.randomUUID(), Currency.getInstance("EUR"), BigDecimal.TEN, clock.instant())
                .build();
    }

    static long count(PostgreSQLContainer db, String table) throws Exception {
        try (var c = DriverManager.getConnection(db.getJdbcUrl(), "test", "test");
                var rs = c.createStatement().executeQuery("SELECT count(*) FROM " + table)) {
            rs.next();
            return rs.getLong(1);
        }
    }

    static List<String> params(PostgreSQLContainer db) throws Exception {
        var out = new ArrayList<String>();
        try (var c = DriverManager.getConnection(db.getJdbcUrl(), "test", "test");
                var rs = c.createStatement().executeQuery("SELECT action_params::text FROM eventlog.events")) {
            while (rs.next()) out.add(rs.getString(1));
        }
        return out;
    }

    static void truncate() throws Exception {
        for (var db : List.of(globalDb, mexicoDb)) {
            try (var c = DriverManager.getConnection(db.getJdbcUrl(), "test", "test")) {
                c.createStatement().execute("TRUNCATE wallets, eventlog.events");
            }
        }
    }

    // ------------------------------------------------------------------ actions

    public record CountryParams(String countryCode) {}

    public record NoParams() {}

    public static class Opaque {
        private int hidden = 1;
    }

    public record BadParams(Object thing) {}

    public static class Money {
        public final String currency = "EUR";
        public final BigDecimal amount = new BigDecimal("5");
    }

    public static class CreateAction extends Action<CountryParams, Wallet> {
        public CreateAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, CountryParams params) {
            var shard =
                    switch (params.countryCode()) {
                        case "MX" -> MEXICO;
                        case "AU" -> AUSTRALIA;
                        default -> GLOBAL;
                    };
            return plan().add(newWallet(shard));
        }
    }

    public static class TwoShardAction extends Action<NoParams, Void> {
        public TwoShardAction(Clock c) {
            super(c);
        }

        @Override
        protected Void perform(Principal principal, NoParams params) {
            plan().add(newWallet(GLOBAL));
            plan().add(newWallet(MEXICO));
            return null;
        }
    }

    public static class UnregisteredAction extends Action<NoParams, Void> {
        public UnregisteredAction(Clock c) {
            super(c);
        }

        @Override
        protected Void perform(Principal principal, NoParams params) {
            return null;
        }
    }

    public static class FixedIdAction extends Action<NoParams, Wallet> {
        public FixedIdAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, NoParams params) {
            return plan().add(createWallet(
                            GLOBAL, UUID.randomUUID(), Currency.getInstance("EUR"), BigDecimal.TEN, clock.instant())
                    .id(FIXED_ID.get())
                    .build());
        }
    }

    public static class BadParamsAction extends Action<BadParams, Wallet> {
        public BadParamsAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, BadParams params) {
            return plan().add(newWallet(GLOBAL));
        }
    }

    public static class StaleThrowAction extends Action<NoParams, Void> {
        public StaleThrowAction(Clock c) {
            super(c);
        }

        @Override
        protected Void perform(Principal principal, NoParams params) {
            ATTEMPTS.incrementAndGet();
            throw new StaleRecordException("probe stale");
        }
    }

    public static class AssertingAction extends Action<NoParams, Void> {
        public AssertingAction(Clock c) {
            super(c);
        }

        @Override
        protected Void perform(Principal principal, NoParams params) {
            throw new AssertionError("assert inside perform");
        }
    }

    public static class PlanLeakAction extends Action<NoParams, Void> {
        public PlanLeakAction(Clock c) {
            super(c);
        }

        public ActionPlan leak() {
            return plan();
        }

        @Override
        protected Void perform(Principal principal, NoParams params) {
            return null;
        }
    }

    static DataSourceConfig ds(String url) {
        return DataSourceConfig.Builder.dataSourceConfig()
                .jdbcUrl(url)
                .username("test")
                .password("test")
                .maximumPoolSize(5)
                .build();
    }

    static ShardingConfig sharding(Map<ShardIdentifier, String> urls, UnknownShardPolicy policy) {
        var b = shardingConfig().defaultShard(GLOBAL);
        for (var e : urls.entrySet()) {
            b.withGroup(shardGroupConfig()
                    .group(e.getKey().group)
                    .name("group-" + e.getKey().group)
                    .withMember(shardMemberConfig()
                            .member(e.getKey().member)
                            .primaryConfig(ds(e.getValue()))
                            .build())
                    .build());
        }
        if (policy != null) b.unknownShardPolicy(policy);
        return b.build();
    }

    static ActionExecutor executor(DatabaseRegistry registry, WalletRepository repo, List<Action<?, ?>> actions) {
        return actionExecutor()
                .namespace("parity.n4")
                .databaseRegistry(registry)
                .objectMapper(new ObjectMapper())
                .repositoryRegistry(repositoryRegistry()
                        .withModelRepository(Wallet.class, repo)
                        .build())
                .actionRegistry(actionRegistry().withActions(actions).build())
                .build();
    }

    // ------------------------------------------------------------------ the probe

    @Test
    void probe() throws Exception {
        var urls = new java.util.LinkedHashMap<ShardIdentifier, String>();
        urls.put(GLOBAL, globalDb.getJdbcUrl());
        urls.put(MEXICO, mexicoDb.getJdbcUrl());
        var config = sharding(urls, null);
        FlywayMigrator.migrate(config);
        var registry = DatabaseRegistry.fromConfig(config);
        var repo = new WalletRepository(registry);
        var executor = executor(
                registry,
                repo,
                List.of(
                        new CreateAction(clock),
                        new TwoShardAction(clock),
                        new FixedIdAction(clock),
                        new BadParamsAction(clock),
                        new StaleThrowAction(clock)));

        // e1
        var missing = ShardedUUID.generate(GLOBAL).value();
        report("e1.get_missing", catching(() -> repo.getById(missing)));

        // e2
        truncate();
        var w = executor.execute(alice, CreateAction.class, new CountryParams("DE"));
        repo.update(w.deposit(new BigDecimal("1")));
        report("e2.update_stale", catching(() -> repo.update(w.deposit(new BigDecimal("2")))));

        // e3
        report("e3.unregistered", catching(() -> executor.execute(alice, UnregisteredAction.class, new NoParams())));

        // e4
        report("e4.plan_outside", catching(() -> new PlanLeakAction(clock).leak()));

        // e5
        truncate();
        report("e5.cross_shard", catching(() -> executor.execute(alice, TwoShardAction.class, new NoParams())));

        // e6
        var failRegistry = DatabaseRegistry.fromConfig(sharding(urls, UnknownShardPolicy.FAIL));
        var failRepo = new WalletRepository(failRegistry);
        var failExec = executor(failRegistry, failRepo, List.of(new CreateAction(clock)));
        truncate();
        report(
                "e6.fail_policy_execute",
                catching(() -> failExec.execute(alice, CreateAction.class, new CountryParams("AU"))));
        p("e6.wallets_global", count(globalDb, "wallets"));
        failRegistry.close();

        // e9
        truncate();
        FIXED_ID.set(ShardedId.generate(Wallet.class, GLOBAL));
        executor.execute(alice, FixedIdAction.class, new NoParams());
        report("e9.duplicate_key", catching(() -> executor.execute(alice, FixedIdAction.class, new NoParams())));

        // e10
        truncate();
        report(
                "e10.unwritable_params",
                catching(() -> executor.execute(alice, BadParamsAction.class, new BadParams(new Opaque()))));
        p("e10.wallets", count(globalDb, "wallets"));
        p("e10.outbox", count(globalDb, "eventlog.events"));
        p("e10.stored_params", params(globalDb));
        truncate();
        executor.execute(alice, BadParamsAction.class, new BadParams(new Money()));
        p("e10b.plain_class_with_public_fields.stored_params", params(globalDb));

        // e12
        var cfg = executionConfiguration()
                .noRetry()
                .withRetry(RuntimeException.class, new RetryConfig(2, Duration.ofMillis(1)))
                .build();
        ATTEMPTS.set(0);
        var t12 = catching(() -> executor.execute(alice, StaleThrowAction.class, new NoParams(), cfg));
        p("e12.policy_on_base_class.attempts", ATTEMPTS.get());
        p("e12.policy_on_base_class.type", name(t12));

        // e13
        p("e13.stale_no_arg_constructor", name(catching(() -> new StaleRecordException())));

        registry.close();

        // e7
        var deadConfig = sharding(Map.of(GLOBAL, DEAD_URL), null);
        p("e7.unreachable.registry_build", name(catching(() -> { DatabaseRegistry.fromConfig(deadConfig).close(); return null; })));
        var deadRegistry = DatabaseRegistry.fromConfig(deadConfig);
        var deadRepo = new WalletRepository(deadRegistry);
        var started = System.nanoTime();
        var t7 = catching(() -> deadRepo.getById(ShardedUUID.generate(GLOBAL).value()));
        var elapsed = (System.nanoTime() - started) / 1e9;
        report("e7.unreachable.read", t7);
        p("e7.unreachable.is_ConnectivityError", "n/a (no such class)");
        p("e7.unreachable.seconds_to_fail_rounded_to_5", Math.round(elapsed / 5) * 5);
        deadRegistry.close();

        // e8
        var cp = ConnectionProvider.hikariConnectionProvider(ds(DEAD_URL));
        var lock = PostgresKeyedLockProvider.Builder.postgresKeyedLockProvider()
                .connectionProvider(cp)
                .build();
        var t8 = catching(() -> lock.tryAcquire("wallet:1", Duration.ZERO, Duration.ofSeconds(5)));
        report("e8.lock_unreachable", t8);
        p("e8.lock_unreachable.key_field", t8 instanceof LockAcquisitionException l ? l.key : "none");
        cp.close();

        // e11
        report("e11.vclock_advance_running", catching(() -> {
            new VirtualClock().advance(Duration.ofDays(1));
            return null;
        }));

        // t1
        var spec = ActionSpec.of(new AssertingAction(clock));
        var t1 = catching(() -> spec.assertThrows(IllegalStateException.class, new NoParams()));
        p("t1.assert_throws_other_type_action_asserts.type", name(t1));
        p("t1.assert_throws_other_type_action_asserts.msg", oneLine(t1));
        p("t1.assert_throws_other_type_action_asserts.chain", chain(t1));

        // t2
        var t2 = catching(() -> spec.assertThrows(Exception.class, new NoParams()));
        p("t2.assert_throws_broadest_action_asserts", t2 == null ? "passes" : name(t2));

        // t3
        var t3 = catching(() -> ActionSpec.of(new CreateAction(null)).execute(new CountryParams("DE")));
        p("t3.null_clock_action.type", name(t3));
    }
}
