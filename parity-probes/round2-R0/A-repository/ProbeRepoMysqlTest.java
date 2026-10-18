package io.ekbatan.core.test.repository;

import static io.ekbatan.core.shard.DatabaseRegistry.Builder.databaseRegistry;

import io.ekbatan.core.config.DataSourceConfig;
import io.ekbatan.core.persistence.ConnectionProvider;
import io.ekbatan.core.persistence.TransactionManager;
import io.ekbatan.core.repository.AbstractRepository;
import io.ekbatan.core.shard.DatabaseRegistry;
import io.ekbatan.core.test.model.Dummy;
import io.ekbatan.flyway.FlywayMigrator;
import java.util.UUID;
import org.jooq.SQLDialect;
import org.junit.jupiter.api.Test;
import org.testcontainers.mysql.MySQLContainer;

/** Parity probe (area A). Temporary. */
public class ProbeRepoMysqlTest extends ProbeRepoBase {

    private static final MySQLContainer C = new MySQLContainer("mysql:8.4")
            .withDatabaseName("testdb")
            .withUsername("test")
            .withPassword("test")
            .withEnv("TZ", "UTC");

    private static TransactionManager TM;
    private static DatabaseRegistry REG;
    private static DummyRepository REPO;

    @Test
    void probe() {
        C.start();
        try {
            var cfg = DataSourceConfig.Builder.dataSourceConfig()
                    .jdbcUrl(C.getJdbcUrl())
                    .username(C.getUsername())
                    .password(C.getPassword())
                    .maximumPoolSize(10)
                    .build();
            TM = new TransactionManager(
                    ConnectionProvider.hikariConnectionProvider(cfg),
                    ConnectionProvider.hikariConnectionProvider(cfg),
                    SQLDialect.MYSQL);
            FlywayMigrator.migrate(C.getJdbcUrl(), C.getUsername(), C.getPassword());
            REG = databaseRegistry().withDatabase(TM).build();
            REPO = new DummyRepository(REG);
            runAll();
        } finally {
            C.stop();
        }
    }

    @Override
    protected String d() {
        return "mysql";
    }

    @Override
    protected AbstractRepository<Dummy, ?, ?, UUID> repo() {
        return REPO;
    }

    @Override
    protected DatabaseRegistry registry() {
        return REG;
    }

    @Override
    protected TransactionManager tm() {
        return TM;
    }

    @Override
    protected String setZoneSql() {
        return "SET time_zone = '-05:00'";
    }
}
