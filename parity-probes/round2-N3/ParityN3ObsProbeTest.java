package io.ekbatan.test.postgres_sharded.wallet.action;

import static io.ekbatan.core.action.ActionExecutor.Builder.actionExecutor;
import static io.ekbatan.core.action.ActionRegistry.Builder.actionRegistry;
import static io.ekbatan.core.action.ExecutionConfiguration.Builder.executionConfiguration;
import static io.ekbatan.core.concurrent.PostgresKeyedLockProvider.Builder.postgresKeyedLockProvider;
import static io.ekbatan.core.config.ShardGroupConfig.Builder.shardGroupConfig;
import static io.ekbatan.core.config.ShardMemberConfig.Builder.shardMemberConfig;
import static io.ekbatan.core.config.ShardingConfig.Builder.shardingConfig;
import static io.ekbatan.core.persistence.ConnectionProvider.hikariConnectionProvider;
import static io.ekbatan.core.repository.RepositoryRegistry.Builder.repositoryRegistry;
import static io.ekbatan.test.postgres_sharded.wallet.models.Wallet.createWallet;

import io.ekbatan.core.action.Action;
import io.ekbatan.core.action.ActionExecutor;
import io.ekbatan.core.config.DataSourceConfig;
import io.ekbatan.core.repository.exception.StaleRecordException;
import io.ekbatan.core.shard.DatabaseRegistry;
import io.ekbatan.core.shard.ShardIdentifier;
import io.ekbatan.flyway.FlywayMigrator;
import io.ekbatan.test.postgres_sharded.wallet.models.Wallet;
import io.ekbatan.test.postgres_sharded.wallet.repository.WalletRepository;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.sdk.OpenTelemetrySdk;
import io.opentelemetry.sdk.testing.exporter.InMemoryMetricReader;
import io.opentelemetry.sdk.testing.exporter.InMemorySpanExporter;
import io.opentelemetry.sdk.metrics.SdkMeterProvider;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.data.SpanData;
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.math.BigDecimal;
import java.security.Principal;
import java.sql.DriverManager;
import java.text.MessageFormat;
import java.time.Clock;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Currency;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.logging.Handler;
import java.util.logging.Level;
import java.util.logging.LogManager;
import java.util.logging.LogRecord;
import java.util.logging.Logger;
import org.junit.jupiter.api.Test;
import org.testcontainers.postgresql.PostgreSQLContainer;
import tools.jackson.databind.ObjectMapper;

/** Parity round 2, area N3 (observability): logs and spans. Temporary; never committed. */
public class ParityN3ObsProbeTest {

    static final String MARK = "n3MarkerPw7731q";
    static final InMemorySpanExporter SPANS = InMemorySpanExporter.create();
    static final InMemoryMetricReader METRICS = InMemoryMetricReader.create();
    static volatile String SCEN = "setup";
    static final List<String[]> RECORDS = new CopyOnWriteArrayList<>();
    static final List<String> ALL_TEXT = new CopyOnWriteArrayList<>();

    static {
        OpenTelemetrySdk.builder()
                .setTracerProvider(SdkTracerProvider.builder()
                        .addSpanProcessor(SimpleSpanProcessor.create(SPANS))
                        .build())
                .setMeterProvider(
                        SdkMeterProvider.builder().registerMetricReader(METRICS).build())
                .buildAndRegisterGlobal();
        var root = LogManager.getLogManager().getLogger("");
        for (var h : root.getHandlers()) root.removeHandler(h);
        root.setLevel(Level.ALL);
        root.addHandler(new Handler() {
            @Override
            public void publish(LogRecord r) {
                String msg;
                try {
                    msg = r.getParameters() == null || r.getParameters().length == 0
                            ? r.getMessage()
                            : MessageFormat.format(r.getMessage().replace("'", "''"), r.getParameters());
                } catch (Exception e) {
                    msg = r.getMessage();
                }
                var t = r.getThrown();
                var sw = new StringWriter();
                if (t != null) t.printStackTrace(new PrintWriter(sw));
                ALL_TEXT.add(SCEN + " " + r.getLoggerName() + " " + msg + " " + sw);
                if (r.getLoggerName() != null && r.getLoggerName().startsWith("io.ekbatan")) {
                    RECORDS.add(new String[] {
                        SCEN,
                        level(r.getLevel()),
                        r.getLoggerName(),
                        msg,
                        t == null ? "-" : t.getClass().getSimpleName()
                    });
                } else if (r.getLevel().intValue() >= Level.INFO.intValue()) {
                    var name = r.getLoggerName() == null ? "?" : r.getLoggerName();
                    OTHER.merge(SCEN + " " + level(r.getLevel()) + " " + topOf(name), 1, Integer::sum);
                }
            }

            @Override
            public void flush() {}

            @Override
            public void close() {}
        });
        Logger.getLogger("io.ekbatan").setLevel(Level.ALL);
    }

    static final AtomicInteger SPAN_HITS = new AtomicInteger();
    static final Map<String, Integer> OTHER = new java.util.concurrent.ConcurrentHashMap<>();

    static String topOf(String name) {
        var parts = name.split("\\.");
        return parts.length >= 3 ? parts[0] + "." + parts[1] + "." + parts[2] : name;
    }

    static String level(Level l) {
        if (l == Level.SEVERE) return "ERROR";
        if (l == Level.WARNING) return "WARN";
        if (l == Level.INFO) return "INFO";
        if (l == Level.FINE) return "DEBUG";
        return "TRACE";
    }

    static final ShardIdentifier GLOBAL = ShardIdentifier.of(0, 0);
    static final ShardIdentifier MEXICO = ShardIdentifier.of(1, 0);
    static final ShardIdentifier AUSTRALIA = ShardIdentifier.of(2, 0);
    static DatabaseRegistry registry;
    static WalletRepository walletRepo;
    static ActionExecutor executor;
    static final Clock clock = Clock.systemUTC();
    static final AtomicInteger ATTEMPTS = new AtomicInteger();

    static void p(String k, Object v) {
        System.out.println("PROBE " + k + ": " + v);
    }

    static Wallet newWallet(ShardIdentifier shard) {
        return createWallet(shard, UUID.randomUUID(), Currency.getInstance("EUR"), BigDecimal.TEN, clock.instant())
                .build();
    }

    public record CountryParams(String countryCode) {}

    public record IdParams(UUID walletId) {}

    public record NoParams() {}

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

    public static class FailAction extends Action<NoParams, Wallet> {
        public FailAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, NoParams params) {
            plan().add(newWallet(GLOBAL));
            throw new IllegalStateException("perform boom");
        }
    }

    public static class FreshPlanAction extends Action<NoParams, Wallet> {
        public FreshPlanAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, NoParams params) {
            int n = ATTEMPTS.incrementAndGet();
            var w = plan().add(newWallet(GLOBAL));
            if (n == 1) throw new StaleRecordException("first attempt");
            return w;
        }
    }

    public static class StaleUpdateAction extends Action<IdParams, Wallet> {
        public StaleUpdateAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, IdParams params) {
            var w = walletRepo.getById(params.walletId());
            return plan().update(w.copy().version(999L).build());
        }
    }

    public static class TwoShardAction extends Action<NoParams, Wallet> {
        public TwoShardAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, NoParams p) {
            var g = plan().add(newWallet(GLOBAL));
            plan().add(newWallet(MEXICO));
            return g;
        }
    }

    public static class PartialAction extends Action<IdParams, Wallet> {
        public PartialAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, IdParams params) {
            var g = plan().add(newWallet(GLOBAL));
            var mx = walletRepo.getById(params.walletId());
            plan().update(mx.copy().version(999L).build());
            return g;
        }
    }

    interface Body {
        void run() throws Exception;
    }

    static void scenario(String name, Body body) throws Exception {
        SPANS.reset();
        SCEN = name;
        try {
            body.run();
            p(name + ".outcome", "ok");
        } catch (Throwable t) {
            p(name + ".outcome", t.getClass().getSimpleName());
        }
        Thread.sleep(50);
        dumpSpans(name);
        dumpLogs(name);
    }

    static void dumpLogs(String name) {
        int i = 0;
        for (var r : RECORDS) {
            if (!r[0].equals(name)) continue;
            p(name + ".log." + (i++), r[1] + " " + r[2] + " | " + r[3].replace("\n", "\\n") + " | exc=" + r[4]);
        }
    }

    static void dumpSpans(String name) {
        var spans = SPANS.getFinishedSpanItems();
        var byId = new HashMap<String, String>();
        for (var s : spans) byId.put(s.getSpanId(), s.getName());
        int i = 0;
        for (var s : spans) {
            p(name + ".span." + (i++), describe(s, byId));
            var full = new StringBuilder(s.toString());
            if (full.indexOf(MARK) >= 0) SPAN_HITS.incrementAndGet();
        }
    }

    static String describe(SpanData s, Map<String, String> byId) {
        var attrs = new TreeMap<String, String>();
        s.getAttributes().forEach((k, v) -> attrs.put(k.getKey(), String.valueOf(v)));
        var events = new ArrayList<String>();
        for (var e : s.getEvents()) {
            var ea = new TreeMap<String, String>();
            e.getAttributes().forEach((k, v) -> {
                if (!k.getKey().equals("exception.stacktrace")) ea.put(k.getKey(), String.valueOf(v));
            });
            events.add(e.getName() + ea);
        }
        var parent = s.getParentSpanContext().isValid()
                ? byId.getOrDefault(s.getParentSpanId(), "outside")
                : "root";
        return s.getName() + " parent=" + parent + " kind=" + s.getKind() + " scope="
                + s.getInstrumentationScopeInfo().getName() + " status=" + s.getStatus().getStatusCode() + ":"
                + s.getStatus().getDescription() + " attrs=" + attrs + " events=" + events;
    }

    static DataSourceConfig ds(PostgreSQLContainer db, String dbName) {
        return DataSourceConfig.Builder.dataSourceConfig()
                .jdbcUrl(db.getJdbcUrl().replace("/global_db", "/" + dbName))
                .username(db.getUsername())
                .password(db.getPassword())
                .maximumPoolSize(6)
                .build();
    }

    @Test
    void probe() throws Exception {
        var db = new PostgreSQLContainer("postgres:17-alpine")
                .withDatabaseName("global_db")
                .withUsername("probe")
                .withPassword(MARK)
                .withEnv("TZ", "UTC");
        db.start();
        try {
            run(db);
        } finally {
            db.stop();
        }
    }

    static void run(PostgreSQLContainer db) throws Exception {
        try (var c = DriverManager.getConnection(db.getJdbcUrl(), db.getUsername(), db.getPassword());
                var st = c.createStatement()) {
            st.execute("CREATE DATABASE mexico_db");
        }
        var g = ds(db, "global_db");
        var m = ds(db, "mexico_db");
        var sharding = shardingConfig()
                .defaultShard(GLOBAL)
                .withGroup(shardGroupConfig()
                        .group(0)
                        .name("global")
                        .withMember(shardMemberConfig().member(0).primaryConfig(g).build())
                        .build())
                .withGroup(shardGroupConfig()
                        .group(1)
                        .name("mexico")
                        .withMember(shardMemberConfig().member(0).primaryConfig(m).build())
                        .build())
                .build();
        scenario("m1_migrate", () -> FlywayMigrator.migrate(sharding));
        scenario("r1_registry", () -> {
            registry = DatabaseRegistry.fromConfig(sharding);
        });
        walletRepo = new WalletRepository(registry);
        var repos = repositoryRegistry().withModelRepository(Wallet.class, walletRepo).build();
        var actions = actionRegistry()
                .withActions(List.of(
                        new CreateAction(clock),
                        new FailAction(clock),
                        new FreshPlanAction(clock),
                        new StaleUpdateAction(clock),
                        new TwoShardAction(clock),
                        new PartialAction(clock)))
                .build();
        executor = actionExecutor()
                .namespace("parity.n3")
                .databaseRegistry(registry)
                .objectMapper(new ObjectMapper())
                .repositoryRegistry(repos)
                .actionRegistry(actions)
                .clock(clock)
                .build();
        Principal alice = () -> "alice";
        var noRetry = executionConfiguration().noRetry().build();
        var cross = executionConfiguration().allowCrossShard(true).build();
        var noRetryCross = executionConfiguration().allowCrossShard(true).noRetry().build();
        var oneRetry = executionConfiguration()
                .withRetry(StaleRecordException.class, new io.ekbatan.core.action.RetryConfig(1, Duration.ZERO))
                .build();

        var created = new Wallet[1];
        scenario("a1_success", () -> created[0] = executor.execute(alice, CreateAction.class, new CountryParams("DE")));
        scenario("a1b_no_principal", () -> executor.execute(null, CreateAction.class, new CountryParams("DE")));
        scenario("a2_retry_then_success", () -> {
            ATTEMPTS.set(0);
            executor.execute(alice, FreshPlanAction.class, new NoParams());
        });
        scenario("a3_perform_fails", () -> executor.execute(alice, FailAction.class, new NoParams()));
        scenario(
                "a4_persist_fails_no_retry",
                () -> executor.execute(alice, StaleUpdateAction.class, new IdParams(created[0].id.getValue()), noRetry));
        scenario(
                "a5_retries_exhausted",
                () -> executor.execute(alice, StaleUpdateAction.class, new IdParams(created[0].id.getValue()), oneRetry));
        scenario("a6_cross_shard_allowed", () -> executor.execute(alice, TwoShardAction.class, new NoParams(), cross));
        scenario("a7_cross_shard_rejected", () -> executor.execute(alice, TwoShardAction.class, new NoParams()));
        var mx = executor.execute(alice, CreateAction.class, new CountryParams("MX"));
        scenario(
                "a8_partial_commit",
                () -> executor.execute(alice, PartialAction.class, new IdParams(mx.id.getValue()), noRetryCross));
        scenario("a9_unknown_shard", () -> executor.execute(alice, CreateAction.class, new CountryParams("AU")));
        scenario("a10_repo_add_all", () -> walletRepo.addAll(List.of(newWallet(GLOBAL), newWallet(GLOBAL))));
        scenario("a11_repo_add_all_no_result", () -> walletRepo.addAllNoResult(List.of(newWallet(GLOBAL), newWallet(GLOBAL))));

        // Locks (PostgreSQL advisory)
        var lockCp = hikariConnectionProvider(ds(db, "global_db"));
        var locks = postgresKeyedLockProvider().connectionProvider(lockCp).build();
        var otherLocks = postgresKeyedLockProvider()
                .connectionProvider(hikariConnectionProvider(ds(db, "global_db")))
                .build();
        scenario("l1_wait_times_out", () -> {
            var held = locks.acquire("order-1", Duration.ofMinutes(5));
            var t = new Thread(() -> {
                try {
                    p("l1_wait_times_out.got", otherLocks.tryAcquire("order-1", Duration.ofMillis(200), Duration.ofMinutes(5)).isPresent());
                } catch (Exception e) {
                    p("l1_wait_times_out.error", e.getClass().getSimpleName());
                }
            });
            t.start();
            t.join();
            held.close();
        });
        scenario("l2_hold_limit_expires", () -> {
            locks.acquire("order-2", Duration.ofMillis(300));
            Thread.sleep(900);
        });
        scenario("l3_release_fails", () -> {
            var lease = locks.acquire("order-3", Duration.ofMinutes(5));
            try (var c = DriverManager.getConnection(db.getJdbcUrl(), db.getUsername(), db.getPassword());
                    var st = c.createStatement()) {
                st.execute("SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype = 'advisory'");
            }
            Thread.sleep(200);
            lease.close();
        });

        // Secrets
        var hits = 0;
        for (var s : ALL_TEXT) if (s.contains(MARK)) {
            hits++;
            var parts = s.split(" ");
            p("secret.hit." + hits, parts[0] + " " + parts[1]);
        }
        p("secret.log_records_scanned", ALL_TEXT.size() > 100);
        p("secret.log_records_with_marker", hits);
        p("secret.spans_with_marker", SPAN_HITS.get());
        for (var e : new TreeMap<>(OTHER).entrySet()) p("library_info_plus." + e.getKey(), e.getValue());
    }
}
