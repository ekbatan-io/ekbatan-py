package io.ekbatan.test.local_event_handler_pg;

import static io.ekbatan.core.action.ActionExecutor.Builder.actionExecutor;
import static io.ekbatan.core.action.ActionRegistry.Builder.actionRegistry;
import static io.ekbatan.core.config.DataSourceConfig.Builder.dataSourceConfig;
import static io.ekbatan.core.persistence.ConnectionProvider.hikariConnectionProvider;
import static io.ekbatan.core.repository.RepositoryRegistry.Builder.repositoryRegistry;
import static io.ekbatan.core.shard.DatabaseRegistry.Builder.databaseRegistry;
import static io.ekbatan.events.localeventhandler.EventHandlerRegistry.eventHandlerRegistry;
import static io.ekbatan.events.localeventhandler.job.EventFanoutJob.eventFanoutJob;
import static io.ekbatan.events.localeventhandler.job.EventHandlingJob.eventHandlingJob;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.github.kagkarlsson.scheduler.task.ExecutionContext;
import com.github.kagkarlsson.scheduler.task.schedule.FixedDelay;
import com.github.kagkarlsson.scheduler.task.schedule.Schedule;
import io.ekbatan.core.action.Action;
import io.ekbatan.core.action.ActionExecutor;
import io.ekbatan.core.concurrent.KeyedLockProvider;
import io.ekbatan.core.config.DataSourceConfig;
import io.ekbatan.core.domain.ModelEvent;
import io.ekbatan.core.domain.ModelId;
import io.ekbatan.core.domain.ShardedId;
import io.ekbatan.core.persistence.ConnectionProvider;
import io.ekbatan.core.persistence.TransactionManager;
import io.ekbatan.core.shard.DatabaseRegistry;
import io.ekbatan.core.shard.ShardIdentifier;
import io.ekbatan.core.shard.ShardedUUID;
import io.ekbatan.core.shard.UnknownShardPolicy;
import io.ekbatan.distributedjobs.DistributedJob;
import io.ekbatan.distributedjobs.JobRegistry;
import io.ekbatan.events.localeventhandler.EventEnvelope;
import io.ekbatan.events.localeventhandler.EventHandler;
import io.ekbatan.test.local_event_handler.widget.action.WidgetCreateAction;
import io.ekbatan.test.local_event_handler.widget.models.Widget;
import io.ekbatan.test.local_event_handler.widget.models.events.WidgetCreatedEvent;
import io.ekbatan.test.local_event_handler_pg.widget.repository.WidgetRepository;
import io.ekbatan.testsupport.time.VirtualClock;
import java.math.BigDecimal;
import java.security.Principal;
import java.sql.Connection;
import java.sql.DriverManager;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.TreeSet;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import org.jooq.DSLContext;
import org.jooq.SQLDialect;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

/** Parity round 2, area N5: temporary twin-run probe. Never committed. Generated from a template. */
@TestMethodOrder(MethodOrderer.MethodName.class)
class ParityN5ProbeTest {

    static final String D = "pg";
    static final SQLDialect DIALECT = SQLDialect.POSTGRES;
    static final String MARKER = "N5-s3cret-marker";
    static final Instant T0 = Instant.parse("2026-01-01T10:00:00Z");
    static final ObjectMapper OM = new ObjectMapper();

    static String url(String db) {
        return "jdbc:postgresql://localhost:58601/" + db;
    }

    static DataSourceConfig cfg(String db, int pool) {
        return dataSourceConfig()
                .jdbcUrl(url(db))
                .username("test")
                .password(MARKER)
                .maximumPoolSize(pool)
                .build();
    }

    static void p(String key, Object value) {
        var s = String.valueOf(value);
        if (s.contains(MARKER)) throw new AssertionError("password leaked in " + key);
        System.out.println("PROBE " + key + ": " + s);
    }

    static String yn(boolean b) {
        return b ? "yes" : "no";
    }

    static String secs(double s) {
        return String.format("%.1fs", Math.round(s * 2) / 2.0);
    }

    static String chain(Throwable t) {
        var sb = new StringBuilder();
        for (var c = t; c != null; c = c.getCause()) sb.append(c).append(" | ");
        return sb.toString();
    }

    static Connection raw(String db) throws Exception {
        return DriverManager.getConnection(url(db), "test", MARKER);
    }

    static Object scalar(String sql) throws Exception {
        return scalarOn("testdb", sql);
    }

    static Object scalarOn(String db, String sql) throws Exception {
        try (var c = raw(db);
                var rs = c.createStatement().executeQuery(sql)) {
            return rs.next() ? rs.getObject(1) : null;
        }
    }

    static long count(String sql) throws Exception {
        return ((Number) scalar(sql)).longValue();
    }

    static void exec(String db, String... sqls) throws Exception {
        try (var c = raw(db)) {
            for (var s : sqls) c.createStatement().execute(s);
        }
    }

    static void truncate(String db) throws Exception {
        exec(
                db,
                "delete from eventlog.event_notifications",
                "delete from eventlog.events",
                "delete from widgets",
                "delete from side_rows",
                "delete from scheduled_tasks");
    }

    static long side(int id) throws Exception {
        return count("select count(*) from side_rows where id = " + id);
    }

    static long widgets(String where) throws Exception {
        return count("select count(*) from widgets where " + where);
    }

    static String wid(Widget w) {
        return w.id.getValue().toString();
    }

    static final Principal TESTER = () -> "tester";

    static class Boom extends RuntimeException {
        Boom(String m) {
            super(m);
        }
    }

    /** Exposes the protected accessors. */
    static class Repo extends WidgetRepository {
        Repo(DatabaseRegistry r) {
            super(r);
        }

        DSLContext txElse() {
            return txDbElseDb();
        }
    }

    public record RenameParams(UUID id, String name) {}

    public static class WidgetRenameAction extends Action<RenameParams, Widget> {
        final Repo repo;

        public WidgetRenameAction(Repo repo, Clock clock) {
            super(clock);
            this.repo = repo;
        }

        @Override
        protected Widget perform(Principal principal, RenameParams params) {
            var found = repo.findById(params.id())
                    .orElseThrow(() -> new IllegalStateException("widget " + params.id() + " not visible to the action"));
            return plan().update(found.copy().name(params.name()).build());
        }
    }

    public static class ThrowAction extends Action<WidgetCreateAction.Params, Widget> {
        public ThrowAction(Clock clock) {
            super(clock);
        }

        @Override
        protected Widget perform(Principal principal, WidgetCreateAction.Params params) {
            plan().add(Widget.createWidget(params.shard(), params.name(), params.color(), clock.instant())
                    .build());
            throw new RuntimeException("action failed on purpose");
        }
    }

    record App(TransactionManager tm, ConnectionProvider cp, DatabaseRegistry reg, Repo repo, ActionExecutor ex) {}

    static App build(int pool, Clock clock) {
        var cp = hikariConnectionProvider(cfg("testdb", pool));
        var tm = new TransactionManager(cp, cp, DIALECT);
        var reg = databaseRegistry().withDatabase(tm).build();
        var repo = new Repo(reg);
        return new App(tm, cp, reg, repo, executor(reg, repo, clock));
    }

    static ActionExecutor executor(DatabaseRegistry reg, Repo repo, Clock clock) {
        return actionExecutor()
                .namespace("test.local_event_handler")
                .databaseRegistry(reg)
                .objectMapper(OM)
                .clock(clock)
                .repositoryRegistry(
                        repositoryRegistry().withModelRepository(Widget.class, repo).build())
                .actionRegistry(actionRegistry()
                        .withAction(WidgetCreateAction.class, new WidgetCreateAction(clock))
                        .withAction(WidgetRenameAction.class, new WidgetRenameAction(repo, clock))
                        .withAction(ThrowAction.class, new ThrowAction(clock))
                        .withAction(PriceAction.class, new PriceAction(clock))
                        .build())
                .build();
    }

    static WidgetCreateAction.Params create(String name) {
        return new WidgetCreateAction.Params(ShardIdentifier.DEFAULT, name, "red");
    }

    // ================================================================== (a) nested transactions

    @Test
    void a_nested() throws Exception {
        truncate("testdb");
        var app = build(5, Clock.systemUTC());
        p("dialect", D);
        a1a2(app);
        a3(app);
        a4(app);
        if (D.equals("pg")) a5();
        a6();
        a7(app);
        a8(app);
        app.reg().close();
    }

    void a1a2(App app) throws Exception {
        var created = new AtomicReference<Widget>();
        try {
            app.tm().inTransactionChecked((TransactionManager.CheckedConsumer<DSLContext>) dsl -> {
                dsl.execute("insert into side_rows values (1, 'outer')");
                created.set(app.ex().execute(TESTER, WidgetCreateAction.class, create("a1")));
                p(
                        "a1.action_row_visible_outside_while_outer_open",
                        yn(widgets("id = '" + wid(created.get()) + "'") == 1));
                throw new Boom("outer fails");
            });
        } catch (Boom e) {
            // expected
        }
        p("a1.outer_row_after_outer_rollback", side(1));
        p("a1.action_widget_after_outer_rollback", widgets("id = '" + wid(created.get()) + "'"));
        p(
                "a1.action_outbox_rows_after_outer_rollback",
                count("select count(*) from eventlog.events where action_name = 'WidgetCreateAction'"));

        app.tm().inTransactionChecked(dsl -> {
            dsl.execute("insert into side_rows values (2, 'outer')");
            try {
                app.ex().execute(TESTER, ThrowAction.class, create("a2"));
                p("a2.action_error", "none");
            } catch (Exception e) {
                p("a2.action_error_message", e.getMessage());
            }
        });
        p("a2.outer_row_after_commit", side(2));
        p("a2.thrown_action_widget", widgets("name = 'a2'"));
        p(
                "a2.thrown_action_outbox_rows",
                count("select count(*) from eventlog.events where action_name = 'ThrowAction'"));
    }

    void a3(App app) throws Exception {
        var w = Widget.createWidget(ShardIdentifier.DEFAULT, "a3", "red", Instant.now())
                .build();
        app.tm().inTransactionChecked(dsl -> {
            app.repo().add(w);
            p("a3.repo_add_in_outer_visible_outside", yn(widgets("id = '" + wid(w) + "'") == 1));
            p(
                    "a3.repo_find_in_outer_sees_outer_row",
                    yn(app.repo().findById(w.id.getValue()).isPresent()));
            p(
                    "a3.repo_tx_db_else_db_sees_outer_row",
                    yn(app.repo().txElse().fetchCount(app.repo().txElse().selectFrom("widgets").where("id = '" + wid(w) + "'")) == 1));
            try {
                app.ex().execute(TESTER, WidgetRenameAction.class, new RenameParams(w.id.getValue(), "renamed"));
                p("a3.action_sees_outer_row", "yes");
            } catch (Exception e) {
                p("a3.action_sees_outer_row", "no: " + e.getClass().getSimpleName());
            }
            app.tm().inTransactionChecked(inner -> {
                p(
                        "a3.inner_tx_sees_outer_row",
                        yn(inner.fetchCount(inner.selectFrom("widgets").where("id = '" + wid(w) + "'")) == 1));
            });
            p("a3.after_inner_current_is_outer", yn(app.tm().currentTransactionDbContext().isPresent()));
        });
        p("a3.repo_add_committed_with_outer", yn(widgets("id = '" + wid(w) + "'") == 1));
    }

    void a4(App app) throws Exception {
        var w = app.ex().execute(TESTER, WidgetCreateAction.class, create("a4"));
        var t0 = System.nanoTime();
        app.tm().inTransactionChecked(dsl -> {
            dsl.execute("update widgets set color = 'locked' where id = '" + wid(w) + "'");
            Exception err = null;
            try {
                app.ex().execute(TESTER, WidgetRenameAction.class, new RenameParams(w.id.getValue(), "a4-renamed"));
            } catch (Exception e) {
                err = e;
            }
            p("a4.blocked_for", secs((System.nanoTime() - t0) / 1e9));
            p("a4.action_failed", yn(err != null));
            if (err != null) {
                var m = chain(err).toLowerCase();
                p("a4.error_is_lock_timeout", yn(m.contains("lock") && (m.contains("timeout") || m.contains("wait"))));
            }
            p("a4.outer_still_usable", dsl.fetchValue("select 1"));
        });
        p("a4.final_color", scalar("select color from widgets where id = '" + wid(w) + "'"));
        p("a4.final_name", scalar("select name from widgets where id = '" + wid(w) + "'"));
        p(
                "a4.rename_outbox_rows",
                count("select count(*) from eventlog.events where action_name = 'WidgetRenameAction'"));
    }

    void a5() throws Exception {
        var app = build(1, Clock.systemUTC());
        Thread.sleep(1000);
        var t0 = System.nanoTime();
        app.tm().inTransactionChecked(dsl -> {
            try {
                app.ex().execute(TESTER, WidgetCreateAction.class, create("a5"));
                p("a5.action_inside_outer_on_pool_of_one", "ran");
            } catch (Exception e) {
                p("a5.action_inside_outer_on_pool_of_one", "failed");
                p("a5.error", e.getClass().getSimpleName() + " <- " + chain(e));
            }
            p("a5.waited", secs((System.nanoTime() - t0) / 1e9));
        });
        p("a5.widget_rows", widgets("name = 'a5'"));
        app.reg().close();
    }

    void a6() throws Exception {
        var clock = new VirtualClock();
        clock.pauseAt(T0);
        var app = build(5, clock);
        exec("testdb", "delete from eventlog.event_notifications", "delete from eventlog.events");
        var calls = new CopyOnWriteArrayList<Boolean>();
        EventHandler<WidgetCreatedEvent> handler = new EventHandler<>() {
            public String name() {
                return "n5-tx-handler";
            }

            public Class<WidgetCreatedEvent> eventType() {
                return WidgetCreatedEvent.class;
            }

            public void handle(EventEnvelope<WidgetCreatedEvent> env) throws Exception {
                if (!"a6".equals(env.event.name)) return;
                calls.add(app.tm().currentTransactionDbContext().isPresent());
                var n = calls.size();
                app.tm().inTransactionChecked(dsl -> {
                    dsl.execute("insert into side_rows values (" + (600 + n) + ", 'handler')");
                    app.ex().execute(TESTER, WidgetCreateAction.class, create("a6-from-handler-" + n));
                    if (n == 1) throw new Boom("first attempt fails after its own work");
                });
            }
        };
        var handlers = eventHandlerRegistry().withHandler(handler).build();
        var fan = eventFanoutJob()
                .databaseRegistry(app.reg())
                .eventHandlerRegistry(handlers)
                .clock(clock)
                .build();
        var hand = eventHandlingJob()
                .databaseRegistry(app.reg())
                .eventHandlerRegistry(handlers)
                .objectMapper(OM)
                .clock(clock)
                .build();
        app.ex().execute(TESTER, WidgetCreateAction.class, create("a6"));
        fan.drainOneRound();
        hand.drainOneRound();
        var q = "select concat(state, '/', attempts) from eventlog.event_notifications where handler_name = 'n5-tx-handler' order by created_date limit 1";
        p("a6.handler_saw_ambient_tx", yn(calls.get(0)));
        p("a6.after_fail.handler_side_rows", count("select count(*) from side_rows where id > 600"));
        p("a6.after_fail.handler_widgets", widgets("name like 'a6-from-handler-%'"));
        p("a6.after_fail.notification", scalar(q));
        clock.advance(Duration.ofSeconds(31));
        hand.drainOneRound();
        p("a6.after_retry.calls", calls.size());
        p("a6.after_retry.handler_side_rows", count("select count(*) from side_rows where id > 600"));
        p("a6.after_retry.handler_widgets", widgets("name like 'a6-from-handler-%'"));
        p("a6.after_retry.notification", scalar(q));
        app.reg().close();
    }

    void a7(App app) throws Exception {
        var ambient = new AtomicBoolean();
        var done = new CountDownLatch(1);
        var job = new DistributedJob() {
            public String name() {
                return "n5-tx-job";
            }

            public Schedule schedule() {
                return FixedDelay.of(Duration.ofHours(1));
            }

            public void execute(ExecutionContext ctx) {
                try {
                    ambient.set(app.tm().currentTransactionDbContext().isPresent());
                    app.tm().inTransactionChecked((TransactionManager.CheckedConsumer<DSLContext>) dsl -> {
                        dsl.execute("insert into side_rows values (700, 'job')");
                        app.ex().execute(TESTER, WidgetCreateAction.class, create("a7-from-job"));
                        throw new Boom("job fails after the action");
                    });
                } catch (RuntimeException e) {
                    throw e;
                } catch (Exception e) {
                    throw new RuntimeException(e);
                } finally {
                    done.countDown();
                }
            }
        };
        var jobsCp = hikariConnectionProvider(cfg("testdb", 3));
        var jobs = JobRegistry.jobRegistry()
                .connectionProvider(jobsCp)
                .pollInterval(Duration.ofMillis(200))
                .registerShutdownHook(false)
                .withJob(job)
                .build();
        jobs.start();
        p("a7.job_ran", yn(done.await(20, TimeUnit.SECONDS)));
        Thread.sleep(500);
        jobs.stop();
        jobsCp.close();
        p("a7.job_saw_ambient_tx", yn(ambient.get()));
        p("a7.job_side_row", side(700));
        p("a7.job_action_widget", widgets("name = 'a7-from-job'"));
        p(
                "a7.job_failures_recorded",
                scalar("select consecutive_failures from scheduled_tasks where task_name = 'n5-tx-job'"));
    }

    static KeyedLockProvider locks(ConnectionProvider cp) {
        return io.ekbatan.core.concurrent.PostgresKeyedLockProvider.Builder.postgresKeyedLockProvider().connectionProvider(cp).build();
    }

    void a8(App app) throws Exception {
        var lockCp = hikariConnectionProvider(cfg("testdb", 3));
        var locks = locks(lockCp);
        var other = new java.util.concurrent.ConcurrentHashMap<String, Boolean>();
        java.util.function.Consumer<String> tryOther = tag -> {
            var th = new Thread(() -> {
                try {
                    var lease = locks.tryAcquire("n5-key", Duration.ofMillis(500), Duration.ofSeconds(30));
                    other.put(tag, lease.isPresent());
                    lease.ifPresent(KeyedLockProvider.Lease::close);
                } catch (InterruptedException e) {
                    throw new RuntimeException(e);
                }
            });
            th.start();
            try {
                th.join(10_000);
            } catch (InterruptedException e) {
                throw new RuntimeException(e);
            }
        };
        var lease = locks.acquire("n5-key", Duration.ofSeconds(30));
        var created = new AtomicReference<Widget>();
        app.tm().inTransactionChecked(dsl -> {
            dsl.execute("insert into side_rows values (800, 'locked')");
            created.set(app.ex().execute(TESTER, WidgetCreateAction.class, create("a8")));
            var again = locks.tryAcquire("n5-key", Duration.ZERO, Duration.ofSeconds(30));
            p("a8.reentrant_in_nested_scope", yn(again.isPresent()));
            again.ifPresent(KeyedLockProvider.Lease::close);
            tryOther.accept("while_held");
        });
        p("a8.other_thread_while_held", yn(other.get("while_held")));
        p("a8.work_committed_under_lock", side(800) + "/" + widgets("id = '" + wid(created.get()) + "'"));
        lease.close();
        tryOther.accept("after_release");
        p("a8.other_thread_after_release", yn(other.get("after_release")));
        lockCp.close();
    }

    static String sh(ShardIdentifier s) {
        return s.group + ":" + s.member;
    }

    // ================================================================== (b) sharded flows, PostgreSQL

    static final ShardIdentifier S1 = ShardIdentifier.of(0, 1);
    static final ShardIdentifier S5 = ShardIdentifier.of(0, 5);

    static long countOn(String db, String sql) throws Exception {
        return ((Number) scalarOn(db, sql)).longValue();
    }

    record Sharded(DatabaseRegistry reg, Repo repo, ActionExecutor ex) {}

    static Sharded sharded(UnknownShardPolicy policy, Clock clock) {
        var cp0 = hikariConnectionProvider(cfg("testdb", 3));
        var cp1 = hikariConnectionProvider(cfg("shard1", 3));
        var tm0 = new TransactionManager(cp0, cp0, DIALECT, ShardIdentifier.DEFAULT);
        var tm1 = new TransactionManager(cp1, cp1, DIALECT, S1);
        var b = databaseRegistry().withDatabase(tm0).withDatabase(tm1).withDefaultDatabase(tm0);
        if (policy != null) b = b.withUnknownShardPolicy(policy);
        var reg = b.build();
        var repo = new Repo(reg);
        return new Sharded(reg, repo, executor(reg, repo, clock));
    }

    static String counts(String table) throws Exception {
        return countOn("testdb", "select count(*) from " + table) + "/" + countOn("shard1", "select count(*) from " + table);
    }

    @Test
    void b_sharded() throws Exception {
        if (!D.equals("pg")) return;
        truncate("testdb");
        truncate("shard1");
        var clock = new VirtualClock();
        clock.pauseAt(T0);
        var s = sharded(null, clock);
        var calls = new CopyOnWriteArrayList<String>();
        EventHandler<WidgetCreatedEvent> handler = new EventHandler<>() {
            public String name() {
                return "n5-shard-handler";
            }

            public Class<WidgetCreatedEvent> eventType() {
                return WidgetCreatedEvent.class;
            }

            public void handle(EventEnvelope<WidgetCreatedEvent> env) {
                calls.add(env.event.name);
                if (env.event.name.equals("b1") && calls.stream().filter("b1"::equals).count() == 1)
                    throw new Boom("first attempt on shard 1 fails");
            }
        };
        var handlers = eventHandlerRegistry().withHandler(handler).build();
        var fan = eventFanoutJob()
                .databaseRegistry(s.reg())
                .eventHandlerRegistry(handlers)
                .clock(clock)
                .build();
        var hand = eventHandlingJob()
                .databaseRegistry(s.reg())
                .eventHandlerRegistry(handlers)
                .objectMapper(OM)
                .clock(clock)
                .build();

        var w = s.ex().execute(TESTER, WidgetCreateAction.class, new WidgetCreateAction.Params(S1, "b1", "red"));
        p("b1.id_shard", sh(ShardedUUID.from(w.id.getValue()).resolveShardIdentifier()));
        p("b1.widgets_shard0/shard1", counts("widgets"));
        p("b1.events_shard0/shard1", counts("eventlog.events"));
        p("b1.found_by_id", yn(s.repo().findById(w.id.getValue()).isPresent()));
        fan.drainOneRound();
        p("b1.after_fanout.notifications_shard0/shard1", counts("eventlog.event_notifications"));
        p("b1.after_fanout.delivered_on_shard1", countOn("shard1", "select count(*) from eventlog.events where delivered"));
        hand.drainOneRound();
        var q = "select concat(state, '/', attempts, '/', extract(epoch from next_retry_at - event_date)::int) from eventlog.event_notifications";
        p("b1.after_fail.notification_on_shard1", scalarOn("shard1", q));
        clock.advance(Duration.ofSeconds(31));
        hand.drainOneRound();
        p("b1.after_retry.notification_on_shard1", scalarOn("shard1", "select concat(state, '/', attempts) from eventlog.event_notifications"));
        p("b1.handler_calls", String.join(",", calls));

        // unknown shard, default policy: falls back to the default shard
        var w5 = s.ex().execute(TESTER, WidgetCreateAction.class, new WidgetCreateAction.Params(S5, "b2", "red"));
        p("b2.id_shard", sh(ShardedUUID.from(w5.id.getValue()).resolveShardIdentifier()));
        p("b2.widget_in_shard0/shard1", countOn("testdb", "select count(*) from widgets where name = 'b2'") + "/" + countOn("shard1", "select count(*) from widgets where name = 'b2'"));
        p("b2.event_in_shard0/shard1", countOn("testdb", "select count(*) from eventlog.events where model_id = '" + wid(w5) + "'") + "/" + countOn("shard1", "select count(*) from eventlog.events where model_id = '" + wid(w5) + "'"));
        p("b2.found_by_id", yn(s.repo().findById(w5.id.getValue()).isPresent()));
        fan.drainOneRound();
        hand.drainOneRound();
        p("b2.handled_from_default_shard", scalarOn("testdb", "select concat(state, '/', attempts) from eventlog.event_notifications where model_id = '" + wid(w5) + "'"));
        p("b2.handler_calls", String.join(",", calls));
        s.reg().close();

        // unknown shard, fail policy
        var f = sharded(UnknownShardPolicy.FAIL, clock);
        try {
            f.ex().execute(TESTER, WidgetCreateAction.class, new WidgetCreateAction.Params(S5, "b3", "red"));
            p("b3.write_unknown_shard", "written");
        } catch (Exception e) {
            p("b3.write_unknown_shard", "refused");
            p("b3.error_message", e.getMessage());
        }
        p("b3.widget_rows_anywhere", countOn("testdb", "select count(*) from widgets where name = 'b3'") + countOn("shard1", "select count(*) from widgets where name = 'b3'"));
        try {
            f.repo().findById(w5.id.getValue());
            p("b3.read_unknown_shard", "read");
        } catch (Exception e) {
            p("b3.read_unknown_shard", "refused");
            p("b3.read_error_message", e.getMessage());
        }
        f.reg().close();
    }

    // ================================================================== (c) data across the ports

    static final Instant T1 = Instant.parse("2026-03-29T01:30:00.123456Z");
    static final UUID REF = UUID.fromString("123e4567-e89b-12d3-a456-426614174000");
    static final UUID OWNER = UUID.fromString("01a14c40-e6a9-7bdc-8000-cdb4562ddd98");

    public static class PricedEvent extends ModelEvent<Widget> {
        public final BigDecimal amount;
        public final BigDecimal tiny;
        public final BigDecimal big;
        public final double ratio;
        public final LocalDate day;
        public final Instant at;
        public final LocalDateTime local;
        public final LocalTime clock;
        public final byte[] blob;
        public final UUID ref;

        public PricedEvent(ModelId<UUID> widgetId, PriceParams p) {
            super(widgetId.getId().toString(), Widget.class);
            this.amount = p.amount();
            this.tiny = p.tiny();
            this.big = p.big();
            this.ratio = p.ratio();
            this.day = p.day();
            this.at = p.at();
            this.local = p.local();
            this.clock = p.clock();
            this.blob = p.blob();
            this.ref = p.ref();
        }

        @JsonCreator
        private PricedEvent(
                @JsonProperty("modelId") String modelId,
                @JsonProperty("modelName") String modelName,
                @JsonProperty("amount") BigDecimal amount,
                @JsonProperty("tiny") BigDecimal tiny,
                @JsonProperty("big") BigDecimal big,
                @JsonProperty("ratio") double ratio,
                @JsonProperty("day") LocalDate day,
                @JsonProperty("at") Instant at,
                @JsonProperty("local") LocalDateTime local,
                @JsonProperty("clock") LocalTime clock,
                @JsonProperty("blob") byte[] blob,
                @JsonProperty("ref") UUID ref) {
            super(modelId, Widget.class);
            this.amount = amount;
            this.tiny = tiny;
            this.big = big;
            this.ratio = ratio;
            this.day = day;
            this.at = at;
            this.local = local;
            this.clock = clock;
            this.blob = blob;
            this.ref = ref;
        }
    }

    public record PriceParams(
            ShardIdentifier shard,
            BigDecimal amount,
            BigDecimal tiny,
            BigDecimal big,
            double ratio,
            LocalDate day,
            Instant at,
            LocalDateTime local,
            LocalTime clock,
            byte[] blob,
            UUID ref,
            ShardedId<Widget> owner) {}

    static PriceParams priceParams() {
        return new PriceParams(
                ShardIdentifier.DEFAULT,
                new BigDecimal("10.50"),
                new BigDecimal("0.0000001"),
                new BigDecimal("12345678901234567890.123456789"),
                0.1,
                LocalDate.parse("2026-03-29"),
                Instant.parse("2026-03-29T01:30:00.123456Z"),
                LocalDateTime.parse("2026-03-29T02:30:00.5"),
                LocalTime.parse("13:45:30.25"),
                new byte[] {0, 1, 2, (byte) 255},
                REF,
                ShardedId.of(Widget.class, ShardedUUID.from(OWNER)));
    }

    public static class PriceAction extends Action<PriceParams, Widget> {
        public PriceAction(Clock clock) {
            super(clock);
        }

        @Override
        protected Widget perform(Principal principal, PriceParams params) {
            var id = ShardedId.generate(Widget.class, params.shard());
            var w = io.ekbatan.test.local_event_handler.widget.models.WidgetBuilder.widget()
                    .id(id)
                    .state(io.ekbatan.test.local_event_handler.widget.models.WidgetState.ACTIVE)
                    .name("priced")
                    .color("gold")
                    .createdDate(clock.instant())
                    .withInitialVersion()
                    .withEvent(new PricedEvent(id, params))
                    .build();
            return plan().add(w);
        }
    }

    static String dec(BigDecimal b) {
        return b == null ? "null" : b + " (scale " + b.scale() + ")";
    }

    static String jsonText(String col) {
        return switch (D) {
            case "pg" -> col + "::text";
            default -> "cast(" + col + " as char)";
        };
    }

    static String tsText(String col) {
        return switch (D) {
            case "pg" -> "to_char(" + col + ", 'YYYY-MM-DD HH24:MI:SS.US')";
            default -> "date_format(" + col + ", '%Y-%m-%d %H:%i:%s.%f')";
        };
    }

    static void printStored(String who) throws Exception {
        var k = "c." + D + "." + who + "_wrote.";
        p(k + "payload_text", scalar("select " + jsonText("payload") + " from eventlog.events where event_type = 'PricedEvent'"));
        p(k + "params_text", scalar("select " + jsonText("action_params") + " from eventlog.events where event_type = 'PricedEvent'"));
        p(k + "event_dates_text", scalar("select concat(" + tsText("started_date") + ", ' | ', " + tsText("completion_date") + ", ' | ', " + tsText("event_date") + ") from eventlog.events where event_type = 'PricedEvent'"));
        p(k + "widget_dates_text", scalar("select concat(" + tsText("created_date") + ", ' | ', " + tsText("updated_date") + ") from widgets where name = 'priced'"));
    }

    @Test
    void c1_readPython() throws Exception {
        var clock = new VirtualClock();
        clock.pauseAt(T1.plusSeconds(60));
        var app = build(3, clock);
        var k = "c." + D + ".read_other.";
        var got = new AtomicReference<EventEnvelope<PricedEvent>>();
        EventHandler<PricedEvent> handler = new EventHandler<>() {
            public String name() {
                return "n5-price-reader";
            }

            public Class<PricedEvent> eventType() {
                return PricedEvent.class;
            }

            public void handle(EventEnvelope<PricedEvent> env) {
                got.set(env);
            }
        };
        var handlers = eventHandlerRegistry().withHandler(handler).build();
        eventFanoutJob().databaseRegistry(app.reg()).eventHandlerRegistry(handlers).clock(clock).build().drainOneRound();
        eventHandlingJob().databaseRegistry(app.reg()).eventHandlerRegistry(handlers).objectMapper(OM).clock(clock).build().drainOneRound();
        p(k + "notification", scalar("select concat(state, '/', attempts) from eventlog.event_notifications where handler_name = 'n5-price-reader'"));
        var env = got.get();
        if (env == null) {
            p(k + "handled", "no: " + scalar("select payload from eventlog.event_notifications"));
            app.reg().close();
            return;
        }
        var e = env.event;
        p(k + "event.amount", dec(e.amount));
        p(k + "event.tiny", dec(e.tiny));
        p(k + "event.big", dec(e.big));
        p(k + "event.ratio", e.ratio);
        p(k + "event.day", e.day);
        p(k + "event.at", e.at);
        p(k + "event.local", e.local);
        p(k + "event.clock", e.clock);
        p(k + "event.blob", HexFormat.of().formatHex(e.blob));
        p(k + "event.ref", e.ref);
        p(k + "event.model_id_matches_column", yn(e.modelId.equals(env.modelId)));
        p(k + "envelope.dates", env.startedDate + " | " + env.completionDate + " | " + env.eventDate);
        p(k + "envelope.model_type", env.modelType);
        try {
            var params = OM.treeToValue(env.actionParams, PriceParams.class);
            p(k + "params.decoded", "yes");
            p(k + "params.amount", dec(params.amount()));
            p(k + "params.big", dec(params.big()));
            p(k + "params.at", params.at());
            p(k + "params.owner", params.owner());
        } catch (Exception ex) {
            p(k + "params.decoded", "no: " + ex.getClass().getSimpleName() + ": " + ex.getMessage().lines().findFirst().orElse(""));
            var n = env.actionParams;
            p(k + "params.amount", dec(OM.treeToValue(n.get("amount"), BigDecimal.class)));
            p(k + "params.big", dec(OM.treeToValue(n.get("big"), BigDecimal.class)));
            p(k + "params.at", OM.treeToValue(n.get("at"), Instant.class));
            p(k + "params.owner", n.get("owner"));
        }
        var w = app.repo().findAll().stream().filter(x -> x.name.equals("priced")).findFirst().orElseThrow();
        p(k + "widget.created_date", w.createdDate);
        p(k + "widget.updated_date", w.updatedDate);
        p(k + "widget.id_shard", sh(ShardedUUID.from(w.id.getValue()).resolveShardIdentifier()));
        app.reg().close();
    }

    @Test
    void c2_writeJava() throws Exception {
        truncate("testdb");
        var clock = new VirtualClock();
        clock.pauseAt(T1);
        var app = build(3, clock);
        app.ex().execute(TESTER, PriceAction.class, priceParams());
        printStored("java");
        app.reg().close();
    }

    // ================================================================== (d) start and stop a whole app

    static Map<String, Integer> threadNames() {
        var m = new java.util.TreeMap<String, Integer>();
        for (var t : Thread.getAllStackTraces().keySet()) {
            if (!t.isAlive()) continue;
            var n = t.getName().replaceAll("[0-9]+", "#");
            m.merge(n, 1, Integer::sum);
        }
        return m;
    }

    static Map<String, Integer> newThreads(Map<String, Integer> before) {
        var now = threadNames();
        var out = new java.util.TreeMap<String, Integer>();
        now.forEach((k, v) -> {
            var d = v - before.getOrDefault(k, 0);
            if (d > 0) out.put(k, d);
        });
        return out;
    }

    static long conns() throws Exception {
        // every connection the test user holds, minus the one asking
        return count("select count(*) from pg_stat_activity where usename = 'test'") - 1;
    }

    record Whole(DatabaseRegistry reg, ActionExecutor ex, KeyedLockProvider locks, ConnectionProvider lockCp, ConnectionProvider jobsCp, JobRegistry jobs) {
        void stop() {
            jobs.stop();
            jobsCp.close();
            locks.close();
            lockCp.close();
            reg.close();
        }
    }

    static Whole whole(String password, List<String> seen) {
        java.util.function.IntFunction<DataSourceConfig> c = pool -> dataSourceConfig()
                .jdbcUrl(url("testdb"))
                .username("test")
                .password(password)
                .maximumPoolSize(pool)
                .build();
        var cp = hikariConnectionProvider(c.apply(5));
        var tm = new TransactionManager(cp, cp, DIALECT);
        var reg = databaseRegistry().withDatabase(tm).build();
        var repo = new Repo(reg);
        var ex = executor(reg, repo, Clock.systemUTC());
        var lockCp = hikariConnectionProvider(c.apply(3));
        var locks = locks(lockCp);
        var jobsCp = hikariConnectionProvider(c.apply(3));
        EventHandler<WidgetCreatedEvent> handler = new EventHandler<>() {
            public String name() {
                return "n5-life-handler";
            }

            public Class<WidgetCreatedEvent> eventType() {
                return WidgetCreatedEvent.class;
            }

            public void handle(EventEnvelope<WidgetCreatedEvent> env) {
                seen.add(env.event.name);
            }
        };
        var handlers = eventHandlerRegistry().withHandler(handler).build();
        var jobs = JobRegistry.jobRegistry()
                .connectionProvider(jobsCp)
                .pollInterval(Duration.ofMillis(200))
                .registerShutdownHook(false)
                .withJob(eventFanoutJob().databaseRegistry(reg).eventHandlerRegistry(handlers).pollDelay(Duration.ofMillis(200)).clock(Clock.systemUTC()).build())
                .withJob(eventHandlingJob().databaseRegistry(reg).eventHandlerRegistry(handlers).objectMapper(OM).pollDelay(Duration.ofMillis(200)).clock(Clock.systemUTC()).build())
                .build();
        return new Whole(reg, ex, locks, lockCp, jobsCp, jobs);
    }

    @Test
    void d_lifecycle() throws Exception {
        if (!D.equals("pg")) return;
        truncate("testdb");
        // warm the JVM-wide threads (JDBC driver timers etc.) so they are not counted as the app's
        try (var c = raw("testdb")) {
            c.createStatement().execute("select 1");
        }
        var before = threadNames();
        var base = conns();
        var seen = new CopyOnWriteArrayList<String>();
        var t0 = System.nanoTime();
        var app = whole(MARKER, seen);
        var built = (System.nanoTime() - t0) / 1e9;
        app.jobs().start();
        var started = (System.nanoTime() - t0) / 1e9;
        p("d1.build_took", secs(built));
        p("d1.start_took", secs(started));
        Thread.sleep(2000);
        p("d1.connections_open_2s_after_start", conns() - base);
        var t1 = System.nanoTime();
        app.ex().execute(TESTER, WidgetCreateAction.class, create("d1"));
        while (!seen.contains("d1") && System.nanoTime() - t1 < 20e9) Thread.sleep(20);
        p("d1.action_to_handler", seen.contains("d1") ? "within " + (((System.nanoTime() - t1) / 1e9) < 3 ? "3s" : "20s") : "never");
        p("d1.threads_while_running", newThreads(before));
        var t2 = System.nanoTime();
        app.stop();
        p("d1.stop_took", secs((System.nanoTime() - t2) / 1e9));
        Thread.sleep(1500);
        p("d1.connections_left_after_stop", conns() - base);
        p("d1.threads_left_after_stop", newThreads(before));

        // a wrong password everywhere
        var wrong = "N5-WRONG-marker";
        var before2 = threadNames();
        var t3 = System.nanoTime();
        var bad = whole(wrong, seen);
        p("d2.build_took", secs((System.nanoTime() - t3) / 1e9));
        var t4 = System.nanoTime();
        String startOutcome;
        try {
            bad.jobs().start();
            startOutcome = "returned";
        } catch (Exception e) {
            startOutcome = "threw " + e.getClass().getSimpleName();
            var m = chain(e);
            if (m.contains(wrong)) throw new AssertionError("wrong password leaked");
            p("d2.start_error_names_auth", yn(m.toLowerCase().contains("password authentication failed")));
        }
        p("d2.start", startOutcome + " after " + secs((System.nanoTime() - t4) / 1e9));
        var t5 = System.nanoTime();
        try {
            bad.ex().execute(TESTER, WidgetCreateAction.class, create("d2"));
            p("d2.action", "ran");
        } catch (Exception e) {
            var m = chain(e);
            if (m.contains(wrong)) throw new AssertionError("wrong password leaked");
            p("d2.action", "failed after " + secs((System.nanoTime() - t5) / 1e9));
            p("d2.action_error_names_auth", yn(m.toLowerCase().contains("password authentication failed")));
        }
        p("d2.threads_while_running", newThreads(before2));
        var t6 = System.nanoTime();
        bad.stop();
        p("d2.stop_took", secs((System.nanoTime() - t6) / 1e9));
        Thread.sleep(1500);
        p("d2.threads_left_after_stop", newThreads(before2));
    }
}
