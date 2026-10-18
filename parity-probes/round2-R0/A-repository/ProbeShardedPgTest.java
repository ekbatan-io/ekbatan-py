package io.ekbatan.core.test.repository;

import static io.ekbatan.core.shard.DatabaseRegistry.Builder.databaseRegistry;
import static io.ekbatan.core.test.generated.jooq.tables.Dummies.DUMMIES;
import static io.ekbatan.core.test.model.DummyBuilder.dummy;
import static io.ekbatan.core.test.model.DummyState.OPENED;
import static java.util.UUID.randomUUID;

import io.ekbatan.core.config.DataSourceConfig;
import io.ekbatan.core.domain.Id;
import io.ekbatan.core.domain.Persistable;
import io.ekbatan.core.persistence.ConnectionProvider;
import io.ekbatan.core.persistence.TransactionManager;
import io.ekbatan.core.repository.ModelRepository;
import io.ekbatan.core.shard.DatabaseRegistry;
import io.ekbatan.core.shard.ShardIdentifier;
import io.ekbatan.core.shard.ShardedUUID;
import io.ekbatan.core.shard.ShardingStrategy;
import io.ekbatan.core.shard.UnknownShardPolicy;
import io.ekbatan.core.test.generated.jooq.tables.Dummies;
import io.ekbatan.core.test.generated.jooq.tables.records.DummiesRecord;
import io.ekbatan.core.test.model.Dummy;
import io.ekbatan.flyway.FlywayMigrator;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Currency;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.function.Consumer;
import java.util.regex.Pattern;
import org.jooq.DSLContext;
import org.jooq.SQLDialect;
import org.jooq.impl.DSL;
import org.jooq.impl.SQLDataType;
import org.junit.jupiter.api.Test;
import org.testcontainers.postgresql.PostgreSQLContainer;

/** Parity probe (area A, sharded routing). Temporary. */
public class ProbeShardedPgTest {

    static final ShardIdentifier A = ShardIdentifier.of(0, 0);
    static final ShardIdentifier B = ShardIdentifier.of(1, 0);
    static final ShardIdentifier U = ShardIdentifier.of(2, 0);
    static final Pattern UUIDP =
            Pattern.compile("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}");
    static final Instant T0 = Instant.parse("2024-01-02T03:04:05.123456Z");

    static final PostgreSQLContainer CA = new PostgreSQLContainer("postgres:17-alpine")
            .withDatabaseName("shard_a")
            .withUsername("test")
            .withPassword("test")
            .withEnv("TZ", "UTC");
    static final PostgreSQLContainer CB = new PostgreSQLContainer("postgres:17-alpine")
            .withDatabaseName("shard_b")
            .withUsername("test")
            .withPassword("test")
            .withEnv("TZ", "UTC");

    DatabaseRegistry reg;

    void p(String k, Object v) {
        var s = String.valueOf(v).split("\n")[0];
        System.out.println("PROBE shard." + k + ": " + UUIDP.matcher(s).replaceAll("<uuid>"));
    }

    void err(String k, Callable<?> r) {
        try {
            p(k, "no error -> " + r.call());
        } catch (Throwable t) {
            Throwable c = t;
            if (c.getClass() == RuntimeException.class && c.getCause() != null) c = c.getCause();
            p(k + ".err", c.getClass().getSimpleName());
            p(k + ".msg", c.getMessage());
        }
    }

    static TransactionManager tm(PostgreSQLContainer c, ShardIdentifier s) {
        var cfg = DataSourceConfig.Builder.dataSourceConfig()
                .jdbcUrl(c.getJdbcUrl())
                .username(c.getUsername())
                .password(c.getPassword())
                .maximumPoolSize(10)
                .build();
        return new TransactionManager(
                ConnectionProvider.hikariConnectionProvider(cfg),
                ConnectionProvider.hikariConnectionProvider(cfg),
                SQLDialect.POSTGRES,
                s);
    }

    long countOn(ShardIdentifier s, UUID id) {
        return reg.primary
                .get(s)
                .fetchCount(DSL.table(DSL.name("dummies")), DSL.field(DSL.name("id"), SQLDataType.UUID).eq(id));
    }

    static Dummy on(ShardIdentifier s, UUID owner) {
        var id = Id.of(Dummy.class, ShardedUUID.generate(s).value());
        return dummy().id(id)
                .state(OPENED)
                .ownerId(owner)
                .currency(Currency.getInstance("EUR"))
                .balance(BigDecimal.TEN)
                .createdDate(T0)
                .withInitialVersion()
                .build();
    }

    static Dummy on(ShardIdentifier s) {
        return on(s, randomUUID());
    }

    static UUID id(Dummy d) {
        return d.id.getValue();
    }

    /** Routes every model to B by its content, and cannot route by id. */
    static final class NonIdStrategy implements ShardingStrategy<UUID> {
        public boolean usesShardAwareId() {
            return false;
        }

        public Optional<ShardIdentifier> resolveShardIdentifierById(UUID id) {
            return Optional.empty();
        }

        public Optional<ShardIdentifier> resolveShardIdentifier(Persistable<?> p) {
            return Optional.of(B);
        }
    }

    static final class NonIdRepo extends ModelRepository<Dummy, DummiesRecord, Dummies, UUID> {
        final ShardedDummyRepository mapper;

        NonIdRepo(DatabaseRegistry r) {
            super(Dummy.class, DUMMIES, DUMMIES.ID, r, new NonIdStrategy());
            mapper = new ShardedDummyRepository(r);
        }

        public Dummy fromRecord(DummiesRecord rec) {
            return mapper.fromRecord(rec);
        }

        public DummiesRecord toRecord(Dummy m) {
            return mapper.toRecord(m);
        }
    }

    @Test
    void probe() {
        CA.start();
        CB.start();
        try {
            var tmA = tm(CA, A);
            var tmB = tm(CB, B);
            FlywayMigrator.migrate(CA.getJdbcUrl(), CA.getUsername(), CA.getPassword());
            FlywayMigrator.migrate(CB.getJdbcUrl(), CB.getUsername(), CB.getPassword());
            reg = databaseRegistry().withDefaultDatabase(tmA).withDatabase(tmB).build();
            var s = new ShardedDummyRepository(reg);
            var n = new DummyRepository(reg);

            var dB = on(B);
            s.add(dB);
            p("add_B.on_B", countOn(B, id(dB)));
            p("add_B.on_A", countOn(A, id(dB)));
            p("findById_B", s.findById(id(dB)).isPresent());
            p("existsById_B", s.existsById(id(dB)));
            var dU = on(U);
            s.add(dU);
            p("add_unregistered.on_A", countOn(A, id(dU)));
            p("findById_unregistered", s.findById(id(dU)).isPresent());

            var dA = on(A);
            var dB2 = on(B);
            err("addAll_cross.A_then_B", () -> s.addAll(List.of(dA, dB2)));
            err("addAll_cross.B_then_A", () -> s.addAll(List.of(dB2, dA)));
            p("addAll_cross.nothing_written", countOn(A, id(dA)) + countOn(B, id(dB2)));
            err("addAllNoResult_cross", () -> {
                s.addAllNoResult(List.of(dA, dB2));
                return null;
            });
            err("addAll_registered_and_unregistered", () -> s.addAll(List.of(on(B), on(U))).size());
            err("addAll_default_and_unregistered", () -> s.addAll(List.of(on(A), on(U))).size());
            s.add(dA);
            s.add(dB2);
            err("updateAll_cross", () -> s.updateAll(List.of(
                    s.getById(id(dA)).deposit(BigDecimal.ONE),
                    s.getById(id(dB2)).deposit(BigDecimal.ONE))));
            var dB3 = s.add(on(B));
            p("update_on_B.version", s.update(s.getById(id(dB3)).deposit(BigDecimal.ONE)).version);

            var total = s.count();
            p("count_across", total);
            p("findAll_across", s.findAll().size());
            var ownerB = randomUUID();
            var dB4 = s.add(on(B, ownerB));
            var ownerCond = DSL.field(DSL.name("owner_id"), SQLDataType.UUID).eq(ownerB);
            p("findOneWhere_across", s.findOneWhere(ownerCond).isPresent());
            p("existsWhere_across", s.existsWhere(ownerCond));
            p("countWhere_across", s.countWhere(ownerCond));
            p("findAllWhere_across", s.findAllWhere(ownerCond).size());
            p("findAllByIds_across", s.findAllByIds(List.of(id(dB), id(dA), id(dU), ShardedUUID.generate(B).value()))
                    .size());

            // same owner on both shards: findOneWhere across shards with one match per shard
            var ownerBoth = randomUUID();
            s.add(on(A, ownerBoth));
            s.add(on(B, ownerBoth));
            err("findOneWhere_one_match_per_shard", () -> s.findOneWhere(
                            DSL.field(DSL.name("owner_id"), SQLDataType.UUID).eq(ownerBoth))
                    .isPresent());

            // unsharded repository on a sharded registry
            var aRows = reg.primary.get(A).fetchCount(DSL.table(DSL.name("dummies")));
            p("unsharded.count_equals_A_rows", n.count() == aRows);
            p("unsharded.findAll_equals_A_rows", n.findAll().size() == aRows);
            var dB5 = on(B);
            n.add(dB5);
            p("unsharded.add_B_id.on_A", countOn(A, id(dB5)));
            p("unsharded.add_B_id.on_B", countOn(B, id(dB5)));
            p("unsharded.findById_row_on_B", n.findById(id(dB)).isPresent());
            err("unsharded.addAll_mixed_ids", () -> n.addAll(List.of(on(A), on(B))).size());

            // strategy that cannot route by id
            var x = new NonIdRepo(reg);
            err("nonid.findById", () -> x.findById(randomUUID()));
            err("nonid.existsById", () -> x.existsById(randomUUID()));
            err("nonid.findAllByIds", () -> x.findAllByIds(List.of(randomUUID())));
            err("nonid.getById", () -> x.getById(randomUUID()));
            var dX = on(A);
            x.add(dX);
            p("nonid.add_routes_by_model.on_B", countOn(B, id(dX)));
            p("nonid.findAll_scatters", x.findAll().size() == s.findAll().size());

            // a write for shard B while a transaction is open on A, then A rolls back
            var dB6 = on(B);
            try {
                tmA.inTransaction((Consumer<DSLContext>) ctx -> {
                    s.add(dB6);
                    throw new IllegalStateException("boom");
                });
            } catch (RuntimeException e) {
                p("tx_on_A.outer_exception", e.getClass().getSimpleName());
            }
            p("tx_on_A.write_to_B_survives_A_rollback", countOn(B, id(dB6)));

            // a write for shard A inside a transaction on A uses that transaction
            var dA7 = on(A);
            try {
                tmA.inTransaction((Consumer<DSLContext>) ctx -> {
                    s.add(dA7);
                    throw new IllegalStateException("boom");
                });
            } catch (RuntimeException e) {
                // expected
            }
            p("tx_on_A.write_to_A_rolled_back", countOn(A, id(dA7)) == 0);

            // unknown shard policy FAIL
            var regFail = databaseRegistry()
                    .withDefaultDatabase(tmA)
                    .withDatabase(tmB)
                    .withUnknownShardPolicy(UnknownShardPolicy.FAIL)
                    .build();
            var sf = new ShardedDummyRepository(regFail);
            err("fail_policy.add_unregistered", () -> sf.add(on(U)));
            err("fail_policy.findById_unregistered", () -> sf.findById(ShardedUUID.generate(U).value()));
            err("fail_policy.findAllByIds_unregistered", () -> sf.findAllByIds(List.of(ShardedUUID.generate(U).value())));
        } finally {
            CA.stop();
            CB.stop();
        }
    }
}
