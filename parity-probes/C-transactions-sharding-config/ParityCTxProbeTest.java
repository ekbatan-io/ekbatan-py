package io.ekbatan.test.postgres_sharded;

import static io.ekbatan.core.config.DataSourceConfig.Builder.dataSourceConfig;
import static io.ekbatan.core.config.ShardGroupConfig.Builder.shardGroupConfig;
import static io.ekbatan.core.config.ShardMemberConfig.Builder.shardMemberConfig;
import static io.ekbatan.core.config.ShardingConfig.Builder.shardingConfig;
import static io.ekbatan.core.shard.DatabaseRegistry.Builder.databaseRegistry;

import io.ekbatan.core.config.DataSourceConfig;
import io.ekbatan.core.persistence.ConnectionProvider;
import io.ekbatan.core.persistence.TransactionManager;
import io.ekbatan.core.shard.DatabaseRegistry;
import io.ekbatan.core.shard.ShardIdentifier;
import io.ekbatan.core.shard.ShardedUUID;
import io.ekbatan.test.postgres_sharded.wallet.repository.WalletRepository;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;
import java.util.logging.Handler;
import java.util.logging.Level;
import java.util.logging.LogRecord;
import java.util.logging.Logger;
import org.jooq.DSLContext;
import org.jooq.SQLDialect;
import org.junit.jupiter.api.Test;

/** Parity scan area C, against a real PostgreSQL. Temporary; never committed. */
class ParityCTxProbeTest {

    static final String MARKER = "s3cret-PROBE-marker";
    static int port;
    static final List<String> LOGS = Collections.synchronizedList(new ArrayList<>());
    static final List<String> ALL = Collections.synchronizedList(new ArrayList<>());

    static void p(String key, Object value) {
        System.out.println("PROBE " + key + ": " + value);
    }

    static String yn(boolean b) {
        return b ? "yes" : "no";
    }

    static String url(String db) {
        return "jdbc:postgresql://localhost:" + port + "/" + db;
    }

    static DataSourceConfig cfg(String db, int pool, String appName) {
        return dataSourceConfig()
                .jdbcUrl(url(db))
                .username("probe")
                .password(MARKER)
                .maximumPoolSize(pool)
                .dataSourceProperties(Map.of("ApplicationName", appName))
                .build();
    }

    static TransactionManager tm(String db, int pool, String appName) {
        var cp = ConnectionProvider.hikariConnectionProvider(cfg(db, pool, appName));
        return new TransactionManager(cp, cp, SQLDialect.POSTGRES, ShardIdentifier.DEFAULT);
    }

    static Connection side() throws SQLException {
        return DriverManager.getConnection(url("testdb"), "probe", MARKER);
    }

    static long count(String sql) throws SQLException {
        try (var c = side();
                var rs = c.createStatement().executeQuery(sql)) {
            rs.next();
            return rs.getLong(1);
        }
    }

    static long rows(int id) throws SQLException {
        return count("select count(*) from tx_probe where id = " + id);
    }

    static int pid(DSLContext dsl) {
        return ((Number) dsl.fetchValue("select pg_backend_pid()")).intValue();
    }

    static String db(DSLContext dsl) {
        return String.valueOf(dsl.fetchValue("select current_database()"));
    }

    /** Exposes the protected accessors. */
    static class Repo extends WalletRepository {
        Repo(DatabaseRegistry r) {
            super(r);
        }

        DSLContext dbAt(ShardIdentifier s) {
            return db(s);
        }

        DSLContext readonlyAt(ShardIdentifier s) {
            return readonlyDb(s);
        }

        Object txAt(ShardIdentifier s) {
            return txDb(s);
        }

        DSLContext dbById(UUID id) {
            return db(id);
        }

        DSLContext dbDefault() {
            return db();
        }

        DSLContext readonlyDefault() {
            return readonlyDb();
        }

        DSLContext txElse() {
            return txDbElseDb();
        }

        Object txDefault() {
            return txDb();
        }
    }

    static void tx(TransactionManager tm, java.util.function.Consumer<DSLContext> block) {
        tm.inTransaction(block);
    }

    static long logs(String needle) {
        synchronized (LOGS) {
            return LOGS.stream().filter(l -> l.contains(needle)).count();
        }
    }

    @Test
    void probe() throws Exception {
        port = Integer.parseInt(Files.readString(Path.of(
                        "/private/tmp/claude-501/-Users-mike-workspace-ekbatan/73e3c410-5a5d-4b46-86a8-40a1e2b99616/scratchpad/pgport"))
                .trim());
        var root = Logger.getLogger("");
        root.setLevel(Level.ALL);
        for (var h : root.getHandlers()) h.setLevel(Level.ALL);
        Logger.getLogger("com.zaxxer").setLevel(Level.ALL);
        root.addHandler(new Handler() {
            @Override
            public void publish(LogRecord r) {
                var line = r.getLevel() + " " + r.getLoggerName() + " - " + r.getMessage()
                        + (r.getParameters() == null ? "" : " " + java.util.Arrays.toString(r.getParameters()))
                        + (r.getThrown() == null ? "" : " " + chain(r.getThrown()));
                LOGS.add(line);
                ALL.add(line);
            }

            @Override
            public void flush() {}

            @Override
            public void close() {}
        });
        try (var c = side()) {
            c.createStatement().execute("drop table if exists tx_probe");
            c.createStatement().execute("create table tx_probe (id int primary key, note text)");
            c.createStatement().execute("drop table if exists tx_deferred");
            c.createStatement()
                    .execute("create table tx_deferred (id int, constraint u unique (id) deferrable initially deferred)");
            c.createStatement().execute("insert into tx_probe values (500, 'locked-row')");
        }
        transactions();
        nesting();
        failures();
        replica();
        accessors();
        pools();
        redaction();
    }

    static String chain(Throwable t) {
        var sb = new StringBuilder();
        for (var c = t; c != null; c = c.getCause()) sb.append(c).append(" | ");
        return sb.toString();
    }

    // ------------------------------------------------------------ transactions

    void transactions() throws Exception {
        var tm = tm("testdb", 5, "parity-c");
        var registry = databaseRegistry().withDatabase(tm).build();
        var plain = registry.primary.get(ShardIdentifier.DEFAULT);

        var during = new long[1];
        tx(tm, dsl -> {
            dsl.execute("insert into tx_probe values (1, 'commit')");
            try {
                during[0] = rows(1);
            } catch (SQLException e) {
                throw new RuntimeException(e);
            }
        });
        p("tx.uncommitted.visible_to_other_connection", yn(during[0] == 1));
        p("tx.commit.visible_after", yn(rows(1) == 1));

        var boom = new RuntimeException("boom");
        try {
            tx(tm, dsl -> {
                dsl.execute("insert into tx_probe values (2, 'rollback')");
                throw boom;
            });
        } catch (RuntimeException e) {
            p("tx.rollback.runtime.propagated_same_object", yn(e == boom));
        }
        p("tx.rollback.runtime.row_kept", yn(rows(2) == 1));
        try {
            tm.inTransactionChecked((TransactionManager.CheckedConsumer<DSLContext>) dsl -> {
                dsl.execute("insert into tx_probe values (4, 'rollback-checked-form')");
                throw boom;
            });
        } catch (Exception e) {
            p("tx.rollback.runtime.checked_form_same_object", yn(e == boom));
        }
        p("tx.rollback.runtime.checked_form_row_kept", yn(rows(4) == 1));

        var error = new AssertionError("fatal");
        try {
            tx(tm, dsl -> {
                dsl.execute("insert into tx_probe values (3, 'error')");
                throw error;
            });
        } catch (Throwable e) {
            p("tx.rollback.base_error.propagated_same_object", yn(e == error));
        }
        p("tx.rollback.base_error.row_kept", yn(rows(3) == 1));

        var checked = new java.io.IOException("checked");
        try {
            tx(tm, dsl -> {
                sneaky(checked);
            });
        } catch (Exception e) {
            p("tx.checked.unchecked_form", e == checked ? "unchanged" : "wrapped in " + e.getClass().getSimpleName());
        }
        try {
            tm.inTransactionChecked((TransactionManager.CheckedConsumer<DSLContext>) dsl -> {
                throw checked;
            });
        } catch (Exception e) {
            p("tx.checked.checked_form", e == checked ? "unchanged" : "wrapped");
        }

        p("tx.current.outside", tm.currentTransactionDbContext().isPresent() ? "present" : "empty");
        var inside = new String[3];
        tx(tm, dsl -> {
            inside[0] = tm.currentTransactionDbContext().isPresent() ? "present" : "empty";
            inside[1] = yn(tm.currentTransactionDbContext().get() == dsl);
            var t = new Thread(() ->
                    inside[2] = tm.currentTransactionDbContext().isPresent() ? "present" : "empty");
            t.start();
            try {
                t.join();
            } catch (InterruptedException e) {
                throw new RuntimeException(e);
            }
        });
        p("tx.current.inside", inside[0]);
        p("tx.current.is_block_context", inside[1]);
        p("tx.current.other_thread_inside", inside[2]);
        p("tx.current.after", tm.currentTransactionDbContext().isPresent() ? "present" : "empty");

        // autocommit outside a transaction
        plain.execute("insert into tx_probe values (10, 'autocommit')");
        p("tx.autocommit.outside_visible_immediately", yn(rows(10) == 1));
        try {
            plain.execute("insert into tx_probe values (11, 'first')");
            plain.execute("insert into tx_probe values (11, 'duplicate')");
        } catch (Exception e) {
            // expected
        }
        p("tx.autocommit.first_statement_kept_after_second_fails", yn(rows(11) == 1));

        // the rollback log line
        LOGS.clear();
        try {
            tx(tm, dsl -> {
                throw new IllegalStateException("x");
            });
        } catch (RuntimeException e) {
            // expected
        }
        p("tx.log.rollback_warn_lines", logs("rolled back"));
        p("tx.log.rollback_warn_text", LOGS.stream().filter(l -> l.contains("rolled back")).findFirst().orElse("none"));
        registry.close();
    }

    @SuppressWarnings("unchecked")
    static <E extends Throwable> void sneaky(Throwable t) throws E {
        throw (E) t;
    }

    // ------------------------------------------------------------ nesting

    void nesting() throws Exception {
        var tm = tm("testdb", 5, "parity-c");
        var res = new String[6];
        tx(tm, outer -> {
            outer.execute("insert into tx_probe values (20, 'outer')");
            var outerPid = pid(outer);
            tx(tm, inner -> {
                res[0] = yn(pid(inner) == outerPid);
                res[1] = yn(((Number) inner.fetchValue("select count(*) from tx_probe where id = 20")).intValue() == 1);
            });
            res[2] = yn(pid(tm.currentTransactionDbContext().get()) == outerPid);
        });
        p("tx.nested.same_connection", res[0]);
        p("tx.nested.inner_sees_outer_uncommitted", res[1]);
        p("tx.nested.current_after_inner_is_outer", res[2]);

        try {
            tx(tm, outer -> {
                outer.execute("insert into tx_probe values (21, 'outer')");
                tx(tm, inner -> {
                    inner.execute("insert into tx_probe values (22, 'inner')");
                });
                throw new RuntimeException("outer fails");
            });
        } catch (RuntimeException e) {
            // expected
        }
        p("tx.nested.inner_row_kept_after_outer_rollback", yn(rows(22) == 1));
        p("tx.nested.outer_row_kept_after_outer_rollback", yn(rows(21) == 1));

        tx(tm, outer -> {
            outer.execute("insert into tx_probe values (23, 'outer')");
            try {
                tx(tm, inner -> {
                    inner.execute("insert into tx_probe values (24, 'inner')");
                    throw new RuntimeException("inner fails");
                });
            } catch (RuntimeException e) {
                // caught by the outer block
            }
        });
        p("tx.nested.inner_failure_caught.inner_row_kept", yn(rows(24) == 1));
        p("tx.nested.inner_failure_caught.outer_row_kept", yn(rows(23) == 1));

        tx(tm, outer -> {
            outer.execute("update tx_probe set note = 'outer' where id = 500");
            try {
                tx(tm, inner -> {
                    inner.execute("set local lock_timeout = '1s'");
                    inner.execute("update tx_probe set note = 'inner' where id = 500");
                });
                res[3] = "ok";
            } catch (RuntimeException e) {
                res[3] = String.valueOf(e.getMessage()).contains("lock timeout") ? "blocked (lock timeout)" : "error";
            }
        });
        p("tx.nested.inner_updates_row_outer_locked", res[3]);

        // two managers for the same shard, different databases
        var tm2 = tm("replicadb", 2, "parity-c");
        tx(tm, dsl -> {
            res[4] = tm2.currentTransactionDbContext().isPresent() ? "present" : "empty";
            res[5] = tm2.inTransaction((java.util.function.Function<DSLContext, String>) other -> db(other));
        });
        p("tx.two_managers_same_shard.second_current_inside_first", res[4]);
        p("tx.two_managers_same_shard.second_tx_database", res[5]);
        tm.close();
        tm2.close();
    }

    // ------------------------------------------------------------ failures

    void failures() throws Exception {
        var tm = tm("testdb", 2, "parity-c");
        LOGS.clear();
        var got = new AtomicReference<String>();
        try {
            tx(tm, dsl -> {
                dsl.execute("insert into tx_probe values (30, 'doomed')");
                var mine = pid(dsl);
                try (var c = side()) {
                    c.createStatement().execute("select pg_terminate_backend(" + mine + ")");
                } catch (SQLException e) {
                    throw new RuntimeException(e);
                }
                throw new RuntimeException("boom");
            });
        } catch (RuntimeException e) {
            got.set(e.getMessage());
        }
        p("tx.failed_rollback.propagated", got.get());
        p("tx.failed_rollback.discard_logged", yn(logs("Evicting connection") + logs("Failed to rollback") > 0));
        p("tx.failed_rollback.row_kept", yn(rows(30) == 1));
        try {
            tx(tm, dsl -> {
                dsl.execute("insert into tx_probe values (31, 'after')");
            });
            p("tx.failed_rollback.next_tx_ok", yn(rows(31) == 1));
        } catch (RuntimeException e) {
            p("tx.failed_rollback.next_tx_ok", "no: " + e.getMessage());
        }

        String sqlstate = "none";
        try {
            tx(tm, dsl -> {
                dsl.execute("insert into tx_deferred values (1)");
                dsl.execute("insert into tx_deferred values (1)");
            });
        } catch (RuntimeException e) {
            for (Throwable c = e; c != null; c = c.getCause()) {
                if (c instanceof SQLException s) {
                    sqlstate = s.getSQLState();
                    break;
                }
            }
        }
        p("tx.commit_failure.sqlstate", sqlstate);
        p("tx.commit_failure.rows", count("select count(*) from tx_deferred"));
        try {
            tx(tm, dsl -> {
                dsl.execute("insert into tx_probe values (32, 'after-commit-failure')");
            });
            p("tx.commit_failure.next_tx_ok", yn(rows(32) == 1));
        } catch (RuntimeException e) {
            p("tx.commit_failure.next_tx_ok", "no");
        }
        tm.close();
    }

    // ------------------------------------------------------------ replica

    void replica() throws Exception {
        var member = shardMemberConfig()
                .member(0)
                .primaryConfig(cfg("testdb", 3, "parity-c"))
                .secondaryConfig(cfg("replicadb", 3, "parity-c"))
                .build();
        var layout = shardingConfig()
                .defaultShard(ShardIdentifier.DEFAULT)
                .withGroup(shardGroupConfig().group(0).name("g").withMember(member).build())
                .build();
        var registry = DatabaseRegistry.fromConfig(layout);
        var tm = registry.defaultTransactionManager();
        var repo = new Repo(registry);
        p("rep.secondary_database", db(registry.secondary.get(ShardIdentifier.DEFAULT)));
        p("rep.repo_readonly_database", db(repo.readonlyDefault()));
        var res = new String[8];
        tx(tm, dsl -> {
            dsl.execute("insert into tx_probe values (40, 'in-tx')");
            res[0] = db(repo.readonlyDefault());
            res[1] = db(registry.secondary.get(ShardIdentifier.DEFAULT));
            var plain = repo.dbDefault();
            res[2] = yn(pid(plain) == pid(dsl));
            res[3] = yn(((Number) plain.fetchValue("select count(*) from tx_probe where id = 40")).intValue() == 1);
            res[4] = yn(pid(repo.txElse()) == pid(dsl));
            res[5] = ((java.util.Optional<?>) repo.txDefault()).isPresent() ? "present" : "empty";
        });
        p("rep.repo_readonly_inside_tx_database", res[0]);
        p("rep.manager_level_readonly_inside_tx_database", res[1] + " (registry.secondary; Java has no manager-level call)");
        p("rep.repo_db_inside_tx_same_connection", res[2]);
        p("rep.repo_db_inside_tx_sees_uncommitted", res[3]);
        p("rep.repo_txDbElseDb_inside_tx_same_connection", res[4]);
        p("rep.repo_txDb_inside_tx", res[5]);
        p("rep.repo_txDb_outside_tx", ((java.util.Optional<?>) repo.txDefault()).isPresent() ? "present" : "empty");
        repo.txElse().execute("insert into tx_probe values (41, 'tx-else-outside')");
        p("rep.repo_txDbElseDb_outside_autocommits", yn(rows(41) == 1));
        registry.close();

        var noReplica = shardingConfig()
                .defaultShard(ShardIdentifier.DEFAULT)
                .withGroup(shardGroupConfig()
                        .group(0)
                        .name("g")
                        .withMember(shardMemberConfig()
                                .member(0)
                                .primaryConfig(cfg("testdb", 3, "parity-c"))
                                .build())
                        .build())
                .build();
        var r2 = DatabaseRegistry.fromConfig(noReplica);
        p("rep.no_replica.secondary_database", db(r2.secondary.get(ShardIdentifier.DEFAULT)));
        r2.close();
    }

    // ------------------------------------------------------------ accessors and unknown shards

    void accessors() throws Exception {
        var registry = databaseRegistry().withDatabase(tm("testdb", 3, "parity-c")).build();
        var repo = new Repo(registry);
        var unknown = ShardIdentifier.of(5, 5);
        String r;
        try {
            var d = repo.dbAt(unknown);
            r = d == null ? "null context (fails on first use)" : db(d);
        } catch (RuntimeException e) {
            r = "error: " + e.getMessage();
        }
        p("acc.db_unknown_shard", r);
        try {
            var d = repo.readonlyAt(unknown);
            r = d == null ? "null context (fails on first use)" : db(d);
        } catch (RuntimeException e) {
            r = "error: " + e.getMessage();
        }
        p("acc.readonly_unknown_shard", r);
        try {
            r = String.valueOf(repo.txAt(unknown));
        } catch (RuntimeException e) {
            r = "error: " + e.getMessage();
        }
        p("acc.txDb_unknown_shard", r);
        LOGS.clear();
        var id = ShardedUUID.generate(unknown).value();
        p("acc.db_by_id_unknown_shard", db(repo.dbById(id)));
        p("acc.db_by_id_unknown_shard.warned", yn(logs("is not registered") > 0));
        registry.close();
    }

    // ------------------------------------------------------------ pools

    void pools() throws Exception {
        var tm = tm("testdb", 3, "parity-c-idle");
        tx(tm, dsl -> {
            dsl.fetchValue("select 1");
        });
        Thread.sleep(3000);
        p("pool.open_connections_after_one_query",
                count("select count(*) from pg_stat_activity where application_name = 'parity-c-idle'"));
        tm.close();

        var busy = tm("testdb", 3, "parity-c-max");
        var threads = new ArrayList<Thread>();
        for (int i = 0; i < 5; i++) {
            var t = new Thread(() -> tx(busy, dsl -> {
                dsl.execute("select pg_sleep(2)");
            }));
            t.start();
            threads.add(t);
        }
        Thread.sleep(1000);
        p("pool.max_connections_under_load",
                count("select count(*) from pg_stat_activity where application_name = 'parity-c-max' and state = 'active'"));
        for (var t : threads) t.join();
        p("pool.driver_setting_reaches_server",
                yn(count("select count(*) from pg_stat_activity where application_name = 'parity-c-max'") > 0));
        busy.close();
    }

    // ------------------------------------------------------------ redaction

    void redaction() throws Exception {
        var ok = tm("testdb", 1, "parity-c-login");
        p("red.login_with_marker_password", yn(ok.inTransaction((java.util.function.Function<DSLContext, Integer>) dsl -> ((Number) dsl.fetchValue("select 1")).intValue()) == 1));
        p("red.printed_forms.marker", yn((String.valueOf(ok) + ok.primaryConnectionProvider
                        + ok.primaryConnectionProvider.getDataSource() + ok.primaryConnectionProvider.jdbcUrl())
                .contains(MARKER)));
        ok.close();

        var wrong = dataSourceConfig()
                .jdbcUrl(url("testdb"))
                .username("probe")
                .password(MARKER + "-wrong")
                .maximumPoolSize(1)
                .build();
        var cp = ConnectionProvider.hikariConnectionProvider(wrong);
        var bad = new TransactionManager(cp, cp, SQLDialect.POSTGRES);
        long start = System.currentTimeMillis();
        String text = "";
        try {
            tx(bad, dsl -> {
                dsl.fetchValue("select 1");
            });
        } catch (Throwable t) {
            text = chain(t);
        }
        long secs = Math.round((System.currentTimeMillis() - start) / 1000.0);
        p("red.wrong_password.failed", yn(!text.isEmpty()));
        p("red.wrong_password.seconds_to_fail", secs >= 5 ? "about " + secs : "under 5");
        p("red.wrong_password.marker_in_error", yn(text.contains(MARKER)));
        p("red.wrong_password.error_names_auth", yn(text.contains("password authentication failed")));
        bad.close();

        var noUser = dataSourceConfig().jdbcUrl(url("testdb")).password(MARKER).build();
        String t2 = "";
        try (var c = io.ekbatan.core.persistence.DataSources.driverDataSource(noUser).getConnection()) {
            t2 = "connected";
        } catch (Throwable t) {
            t2 = chain(t);
        }
        p("red.no_username.logs_in_as_os_user", yn(t2.contains("\"" + System.getProperty("user.name") + "\"")));
        p("red.no_username.marker_in_error", yn(t2.contains(MARKER)));
        p("red.no_username.warned", yn(logs("No username is configured") > 0));
        synchronized (ALL) {
            p("red.all_captured_logs.marker", yn(ALL.stream().anyMatch(l -> l.contains(MARKER))));
            p("red.all_captured_logs.count_nonzero", yn(!ALL.isEmpty()));
            p("red.all_captured_logs.debug_lines_present", yn(ALL.stream().anyMatch(l -> l.startsWith("FINE"))));
        }
    }
}
