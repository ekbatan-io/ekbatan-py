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
import io.ekbatan.core.action.ActionRegistry;
import io.ekbatan.core.action.ExecutionConfiguration;
import io.ekbatan.core.action.RetryConfig;
import io.ekbatan.core.action.persister.event.EventPersister;
import io.ekbatan.core.action.persister.event.single_table_json.SingleTableJsonEventPersister;
import io.ekbatan.core.config.DataSourceConfig;
import io.ekbatan.core.domain.Model;
import io.ekbatan.core.domain.ModelEvent;
import io.ekbatan.core.domain.ModelId;
import io.ekbatan.core.repository.exception.StaleRecordException;
import io.ekbatan.core.shard.CrossShardException;
import io.ekbatan.core.shard.DatabaseRegistry;
import io.ekbatan.core.shard.ShardIdentifier;
import io.ekbatan.flyway.FlywayMigrator;
import io.ekbatan.test.postgres_sharded.wallet.models.Wallet;
import io.ekbatan.test.postgres_sharded.wallet.models.events.WalletCreatedEvent;
import io.ekbatan.test.postgres_sharded.wallet.models.events.WalletMoneyDepositedEvent;
import io.ekbatan.test.postgres_sharded.wallet.repository.WalletRepository;
import io.ekbatan.testsupport.action.ActionSpec;
import java.math.BigDecimal;
import java.security.Principal;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Currency;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.jooq.DSLContext;
import org.junit.jupiter.api.Test;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;
import tools.jackson.databind.ObjectMapper;

/** Area B parity probe: prints PROBE lines, compared against the Python twin. */
@Testcontainers
public class ParityBActionsProbeTest {

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

    static DatabaseRegistry registry;
    static WalletRepository walletRepo;
    static ActionExecutor executor;
    static Clock clock = Clock.systemUTC();
    static final AtomicInteger ATTEMPTS = new AtomicInteger();

    static void p(String key, Object value) {
        System.out.println("PROBE " + key + ": " + value);
    }

    static String sh(ShardIdentifier s) {
        return s.group + ":" + s.member;
    }

    static Wallet newWallet(ShardIdentifier shard) {
        return createWallet(shard, UUID.randomUUID(), Currency.getInstance("EUR"), BigDecimal.TEN, clock.instant())
                .build();
    }

    // ------------------------------------------------------------------ actions

    public record CountryParams(String countryCode) {}

    public record NoParams() {}

    public record IdParams(UUID walletId) {}

    public static class CreateAction extends Action<CountryParams, Wallet> {
        public CreateAction(Clock c) {
            super(c);
        }

        static final AtomicReference<String> SEEN = new AtomicReference<>();

        @Override
        protected Wallet perform(Principal principal, CountryParams params) {
            SEEN.set(principal == null ? "null" : principal.getName());
            var shard =
                    switch (params.countryCode()) {
                        case "MX" -> MEXICO;
                        case "AU" -> AUSTRALIA;
                        default -> GLOBAL;
                    };
            return plan().add(newWallet(shard));
        }
    }

    public static class DepositAction extends Action<IdParams, Wallet> {
        public DepositAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, IdParams params) {
            var w = walletRepo.getById(params.walletId());
            return plan().update(w.deposit(new BigDecimal("5")));
        }
    }

    public static class CreateAndDepositAction extends Action<NoParams, Wallet> {
        public CreateAndDepositAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, NoParams params) {
            return plan().add(newWallet(GLOBAL).deposit(new BigDecimal("5")));
        }
    }

    public static class TouchAction extends Action<IdParams, Wallet> {
        public TouchAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, IdParams params) {
            var w = walletRepo.getById(params.walletId());
            return plan().update(w.copy().build());
        }
    }

    public static class NothingAction extends Action<NoParams, Object> {
        public NothingAction(Clock c) {
            super(c);
        }

        @Override
        protected Object perform(Principal principal, NoParams params) {
            return null;
        }
    }

    static final AtomicReference<Exception> THROWN = new AtomicReference<>();

    public static class ThrowAction extends Action<String, Wallet> {
        public ThrowAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, String kind) throws Exception {
            ATTEMPTS.incrementAndGet();
            plan().add(newWallet(GLOBAL));
            Exception e =
                    switch (kind) {
                        case "runtime" -> new RuntimeException("boom");
                        case "stale" -> new StaleRecordException("stale");
                        case "probe" -> new ProbeEx("probe");
                        case "probesub" -> new ProbeSubEx("probesub");
                        case "wrapped" -> new RuntimeException("wrapped", new StaleRecordException("inner"));
                        case "illegalarg" -> new IllegalArgumentException("bad");
                        default -> throw new IllegalStateException(kind);
                    };
            THROWN.set(e);
            throw e;
        }
    }

    public static class ProbeEx extends RuntimeException {
        public ProbeEx(String m) {
            super(m);
        }
    }

    public static class ProbeSubEx extends ProbeEx {
        public ProbeSubEx(String m) {
            super(m);
        }
    }

    public static class StaleUpdateAction extends Action<IdParams, Wallet> {
        public StaleUpdateAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, IdParams params) {
            ATTEMPTS.incrementAndGet();
            var w = walletRepo.getById(params.walletId());
            return plan().update(w.copy().version(999L).build());
        }
    }

    static final List<UUID> STAGED_IDS = new CopyOnWriteArrayList<>();

    public static class FreshPlanAction extends Action<NoParams, Wallet> {
        public FreshPlanAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, NoParams params) {
            int n = ATTEMPTS.incrementAndGet();
            var w = plan().add(newWallet(GLOBAL));
            STAGED_IDS.add(w.id.getValue());
            if (n == 1) throw new StaleRecordException("first attempt");
            return w;
        }
    }

    public static class TwoShardAction extends Action<CountryParams, Wallet> {
        public TwoShardAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, CountryParams p) { var order = p.countryCode();
            if (order.equals("mx-first")) {
                plan().add(newWallet(MEXICO));
                return plan().add(newWallet(GLOBAL));
            }
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
            ATTEMPTS.incrementAndGet();
            var g = plan().add(newWallet(GLOBAL));
            var mx = walletRepo.getById(params.walletId());
            plan().update(mx.copy().version(999L).build());
            return g;
        }
    }

    public static class NullParamsAction extends Action<NoParams, Wallet> {
        public NullParamsAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, NoParams params) {
            return plan().add(newWallet(GLOBAL));
        }
    }

    public static class ScalarParamsAction extends Action<String, Wallet> {
        public ScalarParamsAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, String params) {
            return plan().add(newWallet(GLOBAL));
        }
    }

    public static class DoubleStageAction extends Action<String, Wallet> {
        public DoubleStageAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, String kind) {
            ATTEMPTS.incrementAndGet();
            var w = newWallet(GLOBAL);
            switch (kind) {
                case "add-add" -> {
                    plan().add(w);
                    plan().add(w);
                }
                case "add-update" -> {
                    plan().add(w);
                    plan().update(w);
                }
                case "update-update" -> {
                    plan().update(w);
                    plan().update(w);
                }
                default -> throw new IllegalStateException(kind);
            }
            return w;
        }
    }

    public static class OuterA {
        public static class Dup extends ModelEvent<Wallet> {
            public Dup(ModelId<UUID> id) {
                super(id.getId().toString(), Wallet.class);
            }
        }
    }

    public static class OuterB {
        public static class UnregisteredAction extends Action<NoParams, Object> {
            public UnregisteredAction(Clock c) {
                super(c);
            }

            @Override
            protected Object perform(Principal principal, NoParams params) {
                return null;
            }
        }

        public static class Dup extends ModelEvent<Wallet> {
            public Dup(ModelId<UUID> id) {
                super(id.getId().toString(), Wallet.class);
            }
        }
    }

    public static class DupEventAction extends Action<CountryParams, Wallet> {
        public DupEventAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, CountryParams p) { var which = p.countryCode();
            var w = newWallet(GLOBAL);
            ModelEvent<Wallet> e = which.equals("a") ? new OuterA.Dup(w.id) : new OuterB.Dup(w.id);
            return plan().add(w.copy().withEvent(e).build());
        }
    }

    public static class ReadOwnAction extends Action<NoParams, String> {
        public ReadOwnAction(Clock c) {
            super(c);
        }

        @Override
        protected String perform(Principal principal, NoParams params) {
            var w = plan().add(newWallet(GLOBAL));
            return String.valueOf(walletRepo.findById(w.id.getValue()).isPresent());
        }
    }

    static final AtomicReference<UUID> INNER_ID = new AtomicReference<>();

    public static class OuterAction extends Action<NoParams, Wallet> {
        public OuterAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, NoParams params) throws Exception {
            var mine = plan().add(newWallet(GLOBAL));
            var inner = executor.execute(principal, CreateAction.class, new CountryParams("DE"));
            INNER_ID.set(inner.id.getValue());
            p("s29.outer_plan_still_has_its_add", plan().additions(Wallet.class).size());
            throw new RuntimeException("outer fails after inner " + mine.id.getValue());
        }
    }

    public static class PlanProbeAction extends Action<NoParams, String> {
        public PlanProbeAction(Clock c) {
            super(c);
        }

        public ActionPlan exposePlan() {
            return plan();
        }

        @Override
        protected String perform(Principal principal, NoParams params) throws Exception {
            var out = new AtomicReference<String>("none");
            var t = new Thread(() -> {
                try {
                    plan();
                    out.set("ok");
                } catch (Exception e) {
                    out.set(e.getClass().getSimpleName());
                }
            });
            t.start();
            t.join();
            return out.get();
        }
    }

    static CountDownLatch latch;

    public static class LatchDepositAction extends Action<IdParams, Wallet> {
        public LatchDepositAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, IdParams params) throws Exception {
            ATTEMPTS.incrementAndGet();
            var w = walletRepo.getById(params.walletId());
            latch.countDown();
            latch.await(10, TimeUnit.SECONDS);
            return plan().update(w.deposit(BigDecimal.ONE));
        }
    }

    public static class ReAddAction extends Action<IdParams, Wallet> {
        public ReAddAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, IdParams params) {
            ATTEMPTS.incrementAndGet();
            var w = walletRepo.getById(params.walletId());
            return plan().add(w.copy().version(1L).build());
        }
    }

    /** Wraps the default persister; records shard order and what is visible mid-transaction. */
    static class SpyPersister implements EventPersister {
        final EventPersister inner;
        final List<String> order = new CopyOnWriteArrayList<>();
        volatile boolean fail = false;
        volatile String duringOther = "?";
        volatile String duringTx = "?";

        SpyPersister(EventPersister inner) {
            this.inner = inner;
        }

        @Override
        public void persistActionEvents(
                String namespace,
                String actionName,
                Instant startedDate,
                Instant completionDate,
                Object actionParams,
                Collection<ModelEvent<?>> modelEvents,
                ShardIdentifier shard,
                UUID actionEventId) {
            order.add(sh(shard));
            duringOther = String.valueOf(count(registry.primary.get(shard), "wallets"));
            duringTx = String.valueOf(count(
                    registry.transactionManager(shard)
                            .currentTransactionDbContext()
                            .orElseThrow(),
                    "wallets"));
            inner.persistActionEvents(
                    namespace,
                    actionName,
                    startedDate,
                    completionDate,
                    actionParams,
                    modelEvents,
                    shard,
                    actionEventId);
            if (fail) throw new IllegalStateException("persister fails");
        }
    }

    // ------------------------------------------------------------------ helpers

    static long count(DSLContext db, String table) {
        return db.fetchOne("select count(*) from " + table).get(0, Long.class);
    }

    static long wallets(ShardIdentifier s) {
        return count(registry.primary.get(s), "wallets");
    }

    static long outbox(ShardIdentifier s) {
        return count(registry.primary.get(s), "eventlog.events");
    }

    static void truncate() {
        for (var s : List.of(GLOBAL, MEXICO)) {
            registry.primary.get(s).execute("truncate table wallets, eventlog.events");
        }
    }

    static List<org.jooq.Record> rows(ShardIdentifier s) {
        return new ArrayList<>(registry.primary
                .get(s)
                .fetch("select id::text as id, namespace, action_id::text as action_id, action_name,"
                        + " action_params::text as params, started_date::text as started,"
                        + " completion_date::text as completed, model_id, model_type, event_type,"
                        + " case when payload is null then null when jsonb_typeof(payload) = 'object' then (payload - 'modelId' - 'ownerId' - 'owner_id')::text else 'JSON ' || jsonb_typeof(payload) end as payload, (payload is null) as payload_sql_null, event_date::text as evdate,"
                        + " delivered, substring(id::text, 15, 1) as idv, substring(action_id::text, 15, 1) as aidv,"
                        + " (started_date <= completion_date) as started_le,"
                        + " (event_date = completion_date) as ev_eq,"
                        + " abs(extract(epoch from (started_date - (now() at time zone 'utc')))) < 30 as utc_close"
                        + " from eventlog.events order by ctid"));
    }

    static void describeOne(String prefix, org.jooq.Record r) {
        p(prefix + ".namespace", r.get("namespace"));
        p(prefix + ".action_name", r.get("action_name"));
        p(prefix + ".action_params", r.get("params"));
        p(prefix + ".model_type", r.get("model_type"));
        p(prefix + ".event_type", r.get("event_type"));
        p(prefix + ".payload_minus_ids", r.get("payload"));
        p(prefix + ".delivered", r.get("delivered"));
        p(prefix + ".id_uuid_version", r.get("idv"));
        p(prefix + ".action_id_uuid_version", r.get("aidv"));
        p(prefix + ".started_le_completion", r.get("started_le"));
        p(prefix + ".event_date_eq_completion", r.get("ev_eq"));
        p(prefix + ".started_is_utc_now", r.get("utc_close"));
        p(prefix + ".model_id_null", r.get("model_id") == null);
        p(prefix + ".payload_sql_null", r.get("payload_sql_null"));
    }

    static String err(Throwable t) {
        return t == null ? "none" : t.getClass().getSimpleName();
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

    static Principal alice = () -> "alice";

    // ------------------------------------------------------------------ the probe

    @Test
    void probe() throws Exception {
        var g = DataSourceConfig.Builder.dataSourceConfig()
                .jdbcUrl(globalDb.getJdbcUrl())
                .username(globalDb.getUsername())
                .password(globalDb.getPassword())
                .maximumPoolSize(10)
                .build();
        var m = DataSourceConfig.Builder.dataSourceConfig()
                .jdbcUrl(mexicoDb.getJdbcUrl())
                .username(mexicoDb.getUsername())
                .password(mexicoDb.getPassword())
                .maximumPoolSize(10)
                .build();
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
        FlywayMigrator.migrate(sharding);
        registry = DatabaseRegistry.fromConfig(sharding);
        walletRepo = new WalletRepository(registry);
        var repos = repositoryRegistry().withModelRepository(Wallet.class, walletRepo).build();

        var actions = actionRegistry()
                .withActions(List.of(
                        new CreateAction(clock),
                        new DepositAction(clock),
                        new CreateAndDepositAction(clock),
                        new TouchAction(clock),
                        new NothingAction(clock),
                        new ThrowAction(clock),
                        new StaleUpdateAction(clock),
                        new FreshPlanAction(clock),
                        new TwoShardAction(clock),
                        new PartialAction(clock),
                        new NullParamsAction(clock),
                        new ScalarParamsAction(clock),
                        new DoubleStageAction(clock),
                        new DupEventAction(clock),
                        new ReadOwnAction(clock),
                        new OuterAction(clock),
                        new PlanProbeAction(clock),
                        new LatchDepositAction(clock),
                        new ReAddAction(clock),
                        new UpdateThenAddAction(clock),
                        new ThreeAddsAction(clock)))
                .build();
        executor = actionExecutor()
                .namespace("parity.actions")
                .databaseRegistry(registry)
                .objectMapper(new ObjectMapper())
                .repositoryRegistry(repos)
                .actionRegistry(actions)
                .build();
        var noRetry = executionConfiguration().noRetry().build();
        var cross = executionConfiguration().allowCrossShard(true).build();

        // S1 add
        truncate();
        var created = executor.execute(alice, CreateAction.class, new CountryParams("DE"));
        p("s1.result_version", created.version);
        p("s1.result_events_attached", created.events.size());
        p("s1.wallets_global", wallets(GLOBAL));
        p("s1.wallets_mexico", wallets(MEXICO));
        p("s1.outbox_global", outbox(GLOBAL));
        p("s1.outbox_mexico", outbox(MEXICO));
        var r1 = rows(GLOBAL).getFirst();
        describeOne("s1.row", r1);
        p("s1.row.model_id_is_wallet_id", created.id.getValue().toString().equals(r1.get("model_id")));
        p("s1.principal_seen", CreateAction.SEEN.get());
        p(
                "s1.db_version",
                registry.primary
                        .get(GLOBAL)
                        .fetchOne("select version from wallets")
                        .get(0));
        executor.execute(null, CreateAction.class, new CountryParams("DE"));
        p("s40.null_principal_seen", CreateAction.SEEN.get());
        p(
                "s40.outbox_has_principal_column",
                registry.primary
                        .get(GLOBAL)
                        .fetchOne("select count(*) from information_schema.columns where table_schema='eventlog'"
                                + " and table_name='events' and column_name like '%principal%'")
                        .get(0));

        // S2 update
        truncate();
        var w2 = executor.execute(alice, CreateAction.class, new CountryParams("DE"));
        var beforeUpdated = registry.primary
                .get(GLOBAL)
                .fetchOne("select updated_date::text from wallets")
                .get(0);
        registry.primary.get(GLOBAL).execute("truncate table eventlog.events");
        var dep = executor.execute(alice, DepositAction.class, new IdParams(w2.id.getValue()));
        p("s2.result_version", dep.version);
        var wr = registry.primary
                .get(GLOBAL)
                .fetchOne("select version, balance::text, updated_date::text from wallets");
        p("s2.db_version", wr.get(0));
        p("s2.db_balance", wr.get(1));
        p("s2.updated_date_unchanged", beforeUpdated.equals(wr.get(2)));
        var r2 = rows(GLOBAL);
        p("s2.outbox_rows", r2.size());
        p("s2.event_type", r2.getFirst().get("event_type"));
        p("s2.payload_minus_ids", r2.getFirst().get("payload"));
        p("s2.action_params", r2.getFirst().get("params"));

        // S3 create + deposit composed before staging
        truncate();
        executor.execute(alice, CreateAndDepositAction.class, new NoParams());
        var r3 = rows(GLOBAL);
        var types3 = new ArrayList<String>();
        for (var r : r3) types3.add(String.valueOf(r.get("event_type")));
        p("s3.event_types_in_insert_order", types3);
        p(
                "s3.db_version",
                registry.primary.get(GLOBAL).fetchOne("select version from wallets").get(0));
        p("s3.same_action_id", r3.stream().map(r -> r.get("action_id")).distinct().count() == 1);

        // S4 update with no new event
        truncate();
        var w4 = executor.execute(alice, CreateAction.class, new CountryParams("DE"));
        registry.primary.get(GLOBAL).execute("truncate table eventlog.events");
        var t4 = executor.execute(alice, TouchAction.class, new IdParams(w4.id.getValue()));
        var r4 = rows(GLOBAL);
        p("s4.outbox_rows", r4.size());
        p("s4.event_type", r4.getFirst().get("event_type"));
        p("s4.payload", r4.getFirst().get("payload"));
        p("s4.payload_sql_null", r4.getFirst().get("payload_sql_null"));
        p("s4.model_type", r4.getFirst().get("model_type"));
        p("s4.result_version", t4.version);
        p(
                "s4.db_version",
                registry.primary.get(GLOBAL).fetchOne("select version from wallets").get(0));

        // S5 empty plan
        truncate();
        var res5 = executor.execute(alice, NothingAction.class, new NoParams());
        p("s5.result", res5);
        p("s5.outbox_global", outbox(GLOBAL));
        p("s5.outbox_mexico", outbox(MEXICO));
        describeOne("s5.row", rows(GLOBAL).getFirst());

        // S6 perform throws
        truncate();
        ATTEMPTS.set(0);
        var e6 = catching(() -> executor.execute(alice, ThrowAction.class, "runtime"));
        p("s6.caller_exception", err(e6));
        p("s6.same_instance", e6 == THROWN.get());
        p("s6.attempts", ATTEMPTS.get());
        p("s6.wallets", wallets(GLOBAL));
        p("s6.outbox", outbox(GLOBAL));

        // S7 perform throws StaleRecord directly
        ATTEMPTS.set(0);
        var e7 = catching(() -> executor.execute(alice, ThrowAction.class, "stale"));
        p("s7.caller_exception", err(e7));
        p("s7.attempts", ATTEMPTS.get());

        // S8 optimistic-lock conflict in persist, default retry
        truncate();
        var w8 = executor.execute(alice, CreateAction.class, new CountryParams("DE"));
        registry.primary.get(GLOBAL).execute("truncate table eventlog.events");
        ATTEMPTS.set(0);
        long t0 = System.nanoTime();
        var e8 = catching(() -> executor.execute(alice, StaleUpdateAction.class, new IdParams(w8.id.getValue())));
        long ms = (System.nanoTime() - t0) / 1_000_000;
        p("s8.caller_exception", err(e8));
        p("s8.attempts", ATTEMPTS.get());
        p("s8.elapsed_ge_100ms", ms >= 100);
        p("s8.elapsed_lt_1000ms", ms < 1000);
        p("s8.outbox", outbox(GLOBAL));
        p(
                "s8.db_version",
                registry.primary.get(GLOBAL).fetchOne("select version from wallets").get(0));

        // S9 noRetry
        ATTEMPTS.set(0);
        var e9 = catching(
                () -> executor.execute(alice, StaleUpdateAction.class, new IdParams(w8.id.getValue()), noRetry));
        p("s9.caller_exception", err(e9));
        p("s9.attempts", ATTEMPTS.get());

        // S10 withRetry replaces the default for the same class
        ATTEMPTS.set(0);
        var three = executionConfiguration()
                .withRetry(StaleRecordException.class, new RetryConfig(3, java.time.Duration.ZERO))
                .build();
        catching(() -> executor.execute(alice, StaleUpdateAction.class, new IdParams(w8.id.getValue()), three));
        p("s10.attempts", ATTEMPTS.get());

        // S11 exact class match
        var probeCfg = executionConfiguration()
                .withRetry(ProbeEx.class, new RetryConfig(2, java.time.Duration.ZERO))
                .build();
        ATTEMPTS.set(0);
        catching(() -> executor.execute(alice, ThrowAction.class, "probe", probeCfg));
        p("s11.exact_attempts", ATTEMPTS.get());
        ATTEMPTS.set(0);
        catching(() -> executor.execute(alice, ThrowAction.class, "probesub", probeCfg));
        p("s11.subclass_attempts", ATTEMPTS.get());
        ATTEMPTS.set(0);
        catching(() -> executor.execute(alice, ThrowAction.class, "stale", probeCfg));
        p("s11.default_dropped_when_other_added", ATTEMPTS.get());

        // S12 cause chain
        ATTEMPTS.set(0);
        var e12 = catching(() -> executor.execute(alice, ThrowAction.class, "wrapped"));
        p("s12.attempts", ATTEMPTS.get());
        p("s12.caller_exception", err(e12));

        // S13 fresh plan per retry
        truncate();
        ATTEMPTS.set(0);
        STAGED_IDS.clear();
        var w13 = executor.execute(alice, FreshPlanAction.class, new NoParams());
        p("s13.attempts", ATTEMPTS.get());
        p("s13.wallets", wallets(GLOBAL));
        p("s13.result_is_second_attempt", w13.id.getValue().equals(STAGED_IDS.get(1)));
        p("s13.outbox", outbox(GLOBAL));

        // S14 cross-shard rejected
        truncate();
        var e14 = catching(() -> executor.execute(alice, TwoShardAction.class, new CountryParams("mx-first")));
        p("s14.caller_exception", err(e14));
        if (e14 instanceof CrossShardException c) {
            p("s14.active_shard", sh(c.activeShard));
            p("s14.requested_shard", sh(c.requestedShard));
        }
        p("s14.wallets_global", wallets(GLOBAL));
        p("s14.wallets_mexico", wallets(MEXICO));
        p("s14.outbox_global", outbox(GLOBAL));
        p("s14.outbox_mexico", outbox(MEXICO));

        // S15 cross-shard allowed, with a spy persister to see commit order
        truncate();
        var spy = new SpyPersister(new SingleTableJsonEventPersister(registry, new ObjectMapper()));
        var spyExec = actionExecutor()
                .namespace("parity.actions")
                .databaseRegistry(registry)
                .objectMapper(new ObjectMapper())
                .repositoryRegistry(repos)
                .actionRegistry(actions)
                .eventPersister(spy)
                .build();
        spyExec.execute(alice, TwoShardAction.class, new CountryParams("mx-first"), cross);
        p("s15.commit_order", spy.order);
        p("s15.wallets_global", wallets(GLOBAL));
        p("s15.wallets_mexico", wallets(MEXICO));
        p("s15.outbox_global", outbox(GLOBAL));
        p("s15.outbox_mexico", outbox(MEXICO));
        var g15 = rows(GLOBAL).getFirst();
        var m15 = rows(MEXICO).getFirst();
        p("s15.same_action_id", g15.get("action_id").equals(m15.get("action_id")));
        p("s15.same_started_date", g15.get("started").equals(m15.get("started")));
        var gw = registry.primary.get(GLOBAL).fetchOne("select id::text from wallets").get(0);
        var mw = registry.primary.get(MEXICO).fetchOne("select id::text from wallets").get(0);
        p("s15.global_row_is_global_wallet", gw.equals(g15.get("model_id")));
        p("s15.mexico_row_is_mexico_wallet", mw.equals(m15.get("model_id")));

        // S16 partial commit, no retry
        truncate();
        var mx16 = executor.execute(alice, CreateAction.class, new CountryParams("MX"));
        truncate();
        registry.primary
                .get(MEXICO)
                .execute(
                        "insert into wallets values (?::uuid, 1, 'OPENED', gen_random_uuid(), 'MXN', 1, now(), now())",
                        mx16.id.getValue().toString());
        ATTEMPTS.set(0);
        var noRetryCross =
                executionConfiguration().allowCrossShard(true).noRetry().build();
        var e16 = catching(
                () -> executor.execute(alice, PartialAction.class, new IdParams(mx16.id.getValue()), noRetryCross));
        p("s16.caller_exception", err(e16));
        p("s16.attempts", ATTEMPTS.get());
        p("s16.wallets_global", wallets(GLOBAL));
        p("s16.wallets_mexico", wallets(MEXICO));
        p("s16.outbox_global", outbox(GLOBAL));
        p("s16.outbox_mexico", outbox(MEXICO));

        // S17 partial commit, default retry on
        truncate();
        registry.primary
                .get(MEXICO)
                .execute(
                        "insert into wallets values (?::uuid, 1, 'OPENED', gen_random_uuid(), 'MXN', 1, now(), now())",
                        mx16.id.getValue().toString());
        ATTEMPTS.set(0);
        var e17 = catching(
                () -> executor.execute(alice, PartialAction.class, new IdParams(mx16.id.getValue()), cross));
        p("s17.caller_exception", err(e17));
        p("s17.attempts", ATTEMPTS.get());
        p("s17.wallets_global", wallets(GLOBAL));
        p("s17.outbox_global", outbox(GLOBAL));

        // S18 unknown shard falls back to default
        truncate();
        executor.execute(alice, CreateAction.class, new CountryParams("AU"));
        p("s18.wallets_global", wallets(GLOBAL));
        p("s18.outbox_global", outbox(GLOBAL));
        p("s18.outbox_mexico", outbox(MEXICO));

        // S19 unregistered action
        var e19 = catching(() -> executor.execute(alice, OuterB.UnregisteredAction.class, new NoParams()));
        p("s19.caller_exception", err(e19));

        // S20 null params
        truncate();
        var e20 = catching(() -> executor.execute(alice, NullParamsAction.class, null));
        p("s20.caller_exception", err(e20));
        p("s20.wallets", wallets(GLOBAL));
        p("s20.outbox", outbox(GLOBAL));

        // S21 scalar params
        truncate();
        var e21 = catching(() -> executor.execute(alice, ScalarParamsAction.class, "MX"));
        p("s21.caller_exception", err(e21));
        p("s21.wallets", wallets(GLOBAL));

        // S23-25 staging one row twice
        for (var kind : List.of("add-add", "add-update", "update-update")) {
            truncate();
            ATTEMPTS.set(0);
            var e = catching(() -> executor.execute(alice, DoubleStageAction.class, kind));
            p("s23." + kind + ".caller_exception", err(e));
            p("s23." + kind + ".attempts", ATTEMPTS.get());
            p("s23." + kind + ".wallets", wallets(GLOBAL));
        }

        // S26 event simple-name collision
        truncate();
        var e26a = catching(() -> executor.execute(alice, DupEventAction.class, new CountryParams("a")));
        var e26b = catching(() -> executor.execute(alice, DupEventAction.class, new CountryParams("b")));
        p("s26.first", err(e26a));
        p("s26.second", err(e26b));
        p("s26.wallets", wallets(GLOBAL));
        var e26c = catching(() -> executor.execute(alice, DupEventAction.class, new CountryParams("a")));
        p("s26.first_again", err(e26c));

        // S27 transaction visibility
        truncate();
        spy.order.clear();
        spyExec.execute(alice, CreateAction.class, new CountryParams("DE"));
        p("s27.during_other_connection_sees_wallet", spy.duringOther);
        p("s27.during_in_transaction_sees_wallet", spy.duringTx);
        p("s27.after_other_connection_sees_wallet", wallets(GLOBAL));
        truncate();
        spy.fail = true;
        var e27 = catching(() -> spyExec.execute(alice, CreateAction.class, new CountryParams("DE")));
        spy.fail = false;
        p("s27.persister_failure_exception", err(e27));
        p("s27.persister_failure_wallets", wallets(GLOBAL));
        p("s27.persister_failure_outbox", outbox(GLOBAL));

        // S28 a read inside perform does not see the staged add
        truncate();
        p("s28.read_sees_staged", executor.execute(alice, ReadOwnAction.class, new NoParams()));

        // S29 nested execute
        truncate();
        var e29 = catching(() -> executor.execute(alice, OuterAction.class, new NoParams()));
        p("s29.outer_exception", err(e29));
        p("s29.wallets", wallets(GLOBAL));
        p(
                "s29.inner_wallet_committed",
                walletRepo.findById(INNER_ID.get()).isPresent());
        p("s29.outbox_rows", outbox(GLOBAL));

        // S30 plan() outside execution and on another thread
        var probeAction = new PlanProbeAction(clock);
        p("s30.plan_outside_execute", err(catching(probeAction::exposePlan)));
        p("s30.plan_on_new_thread", executor.execute(alice, PlanProbeAction.class, new NoParams()));

        // S31 concurrency: two deposits on one wallet
        truncate();
        var w31 = executor.execute(alice, CreateAction.class, new CountryParams("DE"));
        latch = new CountDownLatch(2);
        ATTEMPTS.set(0);
        var errs = new CopyOnWriteArrayList<String>();
        var threads = new ArrayList<Thread>();
        for (int i = 0; i < 2; i++) {
            var t = new Thread(() -> {
                try {
                    executor.execute(alice, LatchDepositAction.class, new IdParams(w31.id.getValue()));
                } catch (Exception e) {
                    errs.add(err(e));
                }
            });
            threads.add(t);
            t.start();
        }
        for (var t : threads) t.join();
        p("s31.errors", errs);
        p("s31.attempts", ATTEMPTS.get());
        p(
                "s31.final_balance",
                registry.primary
                        .get(GLOBAL)
                        .fetchOne("select balance::text from wallets")
                        .get(0));
        p(
                "s31.final_version",
                registry.primary.get(GLOBAL).fetchOne("select version from wallets").get(0));

        // S32 fixed clock
        truncate();
        var fixed = Clock.fixed(Instant.parse("2026-01-01T10:00:00.123456Z"), ZoneOffset.UTC);
        var fixedExec = actionExecutor()
                .namespace("parity.actions")
                .databaseRegistry(registry)
                .objectMapper(new ObjectMapper())
                .repositoryRegistry(repos)
                .actionRegistry(actionRegistry()
                        .withAction(NothingAction.class, new NothingAction(fixed))
                        .build())
                .clock(fixed)
                .build();
        fixedExec.execute(alice, NothingAction.class, new NoParams());
        var r32 = rows(GLOBAL).getFirst();
        p("s32.started_date", r32.get("started"));
        p("s32.completion_date", r32.get("completed"));
        p("s32.event_date", r32.get("evdate"));
        var e32 = catching(() -> actionExecutor()
                .namespace("parity.actions")
                .databaseRegistry(registry)
                .objectMapper(new ObjectMapper())
                .repositoryRegistry(repos)
                .actionRegistry(actionRegistry()
                        .withAction(NothingAction.class, new NothingAction(Clock.systemUTC()))
                        .build())
                .clock(fixed)
                .build());
        p("s32.action_clock_differs_from_executor_clock", err(e32));

        // S33 duplicate primary key
        truncate();
        var w33 = executor.execute(alice, CreateAction.class, new CountryParams("DE"));
        ATTEMPTS.set(0);
        var e33 = catching(() -> executor.execute(alice, ReAddAction.class, new IdParams(w33.id.getValue())));
        p("s33.caller_exception", err(e33));
        p("s33.attempts", ATTEMPTS.get());

        // S34 namespace validation
        var candidates = new String[] {null, "", " ", "my-service", "a.b_c", "_x.y1", "1abc", "a..b", "a.", "abc\n", "caf\u00e9"
        };
        int i = 0;
        for (var ns : candidates) {
            var e = catching(() -> actionExecutor()
                    .namespace(ns)
                    .databaseRegistry(registry)
                    .objectMapper(new ObjectMapper())
                    .repositoryRegistry(repos)
                    .actionRegistry(actions)
                    .build());
            p("s34.ns" + (i++), e == null ? "accepted" : "refused");
        }

        // S35 RetryConfig validation
        p("s35.negative_max_retries", err(catching(() -> new RetryConfig(-1, java.time.Duration.ZERO))));
        p("s35.negative_delay", err(catching(() -> new RetryConfig(1, java.time.Duration.ofMillis(-1)))));
        p("s35.zero_retries", err(catching(() -> new RetryConfig(0, java.time.Duration.ZERO))));

        // S36 two instances of one action type
        truncate();
        var e36 = catching(() -> {
            var reg = actionRegistry()
                    .withAction(CreateAction.class, new CreateAction(clock))
                    .withAction(CreateAction.class, new CreateAction(clock))
                    .build();
            return reg;
        });
        p("s36.duplicate_registration", err(e36));

        // S37 executor builder without an event persister
        var e37 = catching(() -> actionExecutor()
                .namespace("parity.actions")
                .databaseRegistry(registry)
                .objectMapper(new ObjectMapper())
                .repositoryRegistry(repos)
                .actionRegistry(actions)
                .build());
        p("s37.no_event_persister", err(e37));

        // S38 no repository for a staged type
        var e38a = catching(() -> actionExecutor()
                .namespace("parity.actions")
                .databaseRegistry(registry)
                .objectMapper(new ObjectMapper())
                .repositoryRegistry(repositoryRegistry().build())
                .actionRegistry(actions)
                .build());
        p("s38.empty_repository_registry", err(e38a));
        var emptyRepoExec = actionExecutor()
                .namespace("parity.actions")
                .databaseRegistry(registry)
                .objectMapper(new ObjectMapper())
                .repositoryRegistry(repositoryRegistry().build())
                .actionRegistry(actions)
                .build();
        truncate();
        var e38 = catching(() -> emptyRepoExec.execute(alice, CreateAction.class, new CountryParams("DE")));
        p("s38.unknown_type_exception", err(e38));
        p("s38.outbox", outbox(GLOBAL));

        // S42 grouping order: update staged before add
        truncate();
        var w42 = executor.execute(alice, CreateAction.class, new CountryParams("DE"));
        registry.primary.get(GLOBAL).execute("truncate table eventlog.events");
        executor.execute(alice, UpdateThenAddAction.class, new IdParams(w42.id.getValue()));
        var types42 = new ArrayList<String>();
        for (var r : rows(GLOBAL)) types42.add(String.valueOf(r.get("event_type")));
        p("s42.event_types_in_insert_order", types42);
        truncate();
        var ids = executor.execute(alice, ThreeAddsAction.class, new NoParams());
        var got = new ArrayList<String>();
        for (var r : rows(GLOBAL)) got.add(String.valueOf(r.get("model_id")));
        p("s42.three_adds_insert_order_is_staging_order", got.equals(ids));

        // S43 execute inside a transaction the caller opened, which then rolls back
        truncate();
        var e43 = catching(() -> {
            registry.transactionManager(GLOBAL).inTransactionChecked((DSLContext tx) -> {
                executor.execute(alice, CreateAction.class, new CountryParams("DE"));
                throw new RuntimeException("caller rolls back");
            });
            return null;
        });
        p("s43.caller_exception", err(e43));
        p("s43.wallets_after_outer_rollback", wallets(GLOBAL));
        p("s43.outbox_after_outer_rollback", outbox(GLOBAL));

        // S39 ActionSpec
        truncate();
        var spec = ActionSpec.of(new CreateAction(clock)).withPrincipal(alice);
        var a = spec.execute(new CountryParams("DE"));
        p("s39.pass_chain", err(catching(() -> a.assertAdded(Wallet.class, 1)
                .assertEmitted(WalletCreatedEvent.class)
                .assertNoUpdates()
                .assertTotalAdditions(1)
                .assertNotEmitted(WalletMoneyDepositedEvent.class))));
        p("s39.added_count_wrong", msg(catching(() -> a.assertAdded(Wallet.class, 2))));
        p("s39.emitted_missing", msg(catching(() -> a.assertEmitted(WalletMoneyDepositedEvent.class))));
        p("s39.updated_one_missing", msg(catching(() -> a.assertUpdated(Wallet.class, w -> {}))));
        p("s39.no_changes", msg(catching(a::assertNoChanges)));
        p("s39.total_additions_wrong", msg(catching(() -> a.assertTotalAdditions(3))));
        p("s39.emitted_base_class_count", msg(catching(() -> a.assertEmitted(ModelEvent.class, 1))));
        p("s39.added_base_class", msg(catching(() -> a.assertAdded(Model.class, 1))));
        p("s39.no_events", msg(catching(a::assertNoEvents)));
        p("s39.not_emitted_present", msg(catching(() -> a.assertNotEmitted(WalletCreatedEvent.class))));
        p("s39.verifier_failure", err(catching(() -> a.assertAdded(Wallet.class, w -> {
            throw new AssertionError("verifier says no");
        }))));
        p("s39.result_verifier_gets_result", err(catching(() -> a.assertResult(w -> {
            if (w.version != 1L) throw new AssertionError("bad");
        }))));
        p("s39.nothing_persisted", wallets(GLOBAL));
        p("s39.principal_seen", CreateAction.SEEN.get());
        var throwSpec = ActionSpec.of(new ThrowAction(clock));
        p(
                "s39.throws_subclass_ok",
                err(catching(() -> throwSpec.assertThrows(RuntimeException.class, "illegalarg"))));
        p(
                "s39.throws_wrong_type",
                msg(catching(() -> throwSpec.assertThrows(IllegalStateException.class, "illegalarg"))));
        p(
                "s39.throws_nothing",
                msg(catching(() -> ActionSpec.of(new CreateAction(clock))
                        .assertThrows(RuntimeException.class, new CountryParams("DE")))));
        p(
                "s39.throws_verifier_runs",
                err(catching(() -> throwSpec.assertThrows(IllegalArgumentException.class, "illegalarg", e -> {
                    throw new AssertionError("verifier ran");
                }))));
        p("s39.plan_outside", err(catching(() -> new PlanProbeAction(clock).exposePlan())));

        registry.close();
    }

    static String msg(Throwable t) {
        if (t == null) return "passed";
        return t.getClass().getSimpleName() + " / " + t.getMessage();
    }

    public static class UpdateThenAddAction extends Action<IdParams, Wallet> {
        public UpdateThenAddAction(Clock c) {
            super(c);
        }

        @Override
        protected Wallet perform(Principal principal, IdParams params) {
            var w = walletRepo.getById(params.walletId());
            plan().update(w.deposit(BigDecimal.ONE));
            return plan().add(newWallet(GLOBAL));
        }
    }

    public static class ThreeAddsAction extends Action<NoParams, List<String>> {
        public ThreeAddsAction(Clock c) {
            super(c);
        }

        @Override
        protected List<String> perform(Principal principal, NoParams params) {
            var out = new ArrayList<String>();
            for (int i = 0; i < 3; i++) {
                out.add(plan().add(newWallet(GLOBAL)).id.getValue().toString());
            }
            return out;
        }
    }
}
