package io.ekbatan.test.local_event_handler_pg;

import static io.ekbatan.core.config.DataSourceConfig.Builder.dataSourceConfig;
import static io.ekbatan.core.persistence.ConnectionProvider.hikariConnectionProvider;
import static io.ekbatan.core.shard.DatabaseRegistry.Builder.databaseRegistry;
import static io.ekbatan.events.localeventhandler.EventHandlerRegistry.eventHandlerRegistry;
import static io.ekbatan.events.localeventhandler.job.EventFanoutJob.eventFanoutJob;
import static io.ekbatan.events.localeventhandler.job.EventHandlingJob.eventHandlingJob;

import io.ekbatan.core.domain.ModelEvent;
import io.ekbatan.core.persistence.ConnectionProvider;
import io.ekbatan.core.persistence.TransactionManager;
import io.ekbatan.core.shard.DatabaseRegistry;
import io.ekbatan.core.shard.ShardIdentifier;
import io.ekbatan.events.localeventhandler.EventEnvelope;
import io.ekbatan.events.localeventhandler.EventHandler;
import io.ekbatan.events.localeventhandler.EventHandlerRegistry;
import io.ekbatan.events.localeventhandler.config.LocalEventHandlerConfig;
import io.ekbatan.events.localeventhandler.job.EventFanoutJob;
import io.ekbatan.events.localeventhandler.job.EventHandlingJob;
import io.ekbatan.events.localeventhandler.repository.EventEntityRepository;
import io.ekbatan.events.localeventhandler.repository.EventNotificationRepository;
import io.ekbatan.flyway.FlywayMigrator;
import io.ekbatan.test.local_event_handler.note.models.events.NoteCreatedEvent;
import io.ekbatan.test.local_event_handler.widget.models.Widget;
import io.ekbatan.test.local_event_handler.widget.models.events.WidgetCreatedEvent;
import java.sql.DriverManager;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;
import org.jooq.SQLDialect;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
import org.testcontainers.postgresql.PostgreSQLContainer;
import tools.jackson.databind.ObjectMapper;

/** Parity scan area D: temporary twin-run probe. Never committed. */
@TestMethodOrder(MethodOrderer.MethodName.class)
class ParityDProbeTest {

    static final ShardIdentifier S0 = ShardIdentifier.DEFAULT;
    static final ShardIdentifier S1 = ShardIdentifier.of(1, 0);
    static final String EV = "eventlog.events";
    static final String NT = "eventlog.event_notifications";
    static final Instant T0 = Instant.parse("2026-01-01T10:00:00Z");
    static final ObjectMapper OM = new ObjectMapper();
    static final DateTimeFormatter F =
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss").withZone(ZoneOffset.UTC);

    static PostgreSQLContainer DB;
    static Map<ShardIdentifier, ConnectionProvider> CPS;
    static ConnectionProvider REPLICA_CP;
    static DatabaseRegistry REG;
    static DatabaseRegistry REPLICA_REG;

    static final class MutableClock extends Clock {
        volatile Instant now;

        MutableClock(Instant now) {
            this.now = now;
        }

        @Override
        public ZoneId getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public Clock withZone(ZoneId zone) {
            return this;
        }

        @Override
        public Instant instant() {
            return now;
        }
    }

    /** A same-simple-name event, for the registry collision check. */
    static final class Other {
        static final class WidgetCreatedEvent extends ModelEvent<Widget> {
            WidgetCreatedEvent() {
                super("x", Widget.class);
            }
        }
    }

    static final AtomicInteger CURRENT = new AtomicInteger();
    static final AtomicInteger MAX = new AtomicInteger();

    static final class Rec<E extends ModelEvent<?>> implements EventHandler<E> {
        final String name;
        final Class<E> type;
        final Set<String> aliases;
        final String mode;
        final List<EventEnvelope<E>> received = new CopyOnWriteArrayList<>();
        final List<String> order = Collections.synchronizedList(new ArrayList<>());

        Rec(String name, Class<E> type, String mode, Set<String> aliases) {
            this.name = name;
            this.type = type;
            this.mode = mode;
            this.aliases = aliases;
        }

        Rec(String name, Class<E> type, String mode) {
            this(name, type, mode, Set.of());
        }

        @Override
        public String name() {
            return name;
        }

        @Override
        public Set<String> aliases() {
            return aliases;
        }

        @Override
        public Class<E> eventType() {
            return type;
        }

        @Override
        public void handle(EventEnvelope<E> envelope) throws Exception {
            received.add(envelope);
            order.add(envelope.modelId);
            switch (mode) {
                case "fail" -> throw new RuntimeException("boom");
                case "error" -> throw new AssertionError("error-boom");
                case "slow" -> {
                    int c = CURRENT.incrementAndGet();
                    MAX.accumulateAndGet(c, Math::max);
                    Thread.sleep(400);
                    CURRENT.decrementAndGet();
                }
                default -> {}
            }
        }
    }

    // ---------------------------------------------------------------- setup

    @BeforeAll
    static void start() throws Exception {
        DB = new PostgreSQLContainer("postgres:17-alpine")
                .withDatabaseName("shard_0")
                .withUsername("test")
                .withPassword("test")
                .withEnv("TZ", "UTC");
        DB.start();
        try (var root = DriverManager.getConnection(
                        DB.getJdbcUrl().replace("/shard_0", "/postgres"), DB.getUsername(), DB.getPassword());
                var st = root.createStatement()) {
            st.execute("CREATE DATABASE shard_1");
            st.execute("CREATE DATABASE replica_0");
        }
        var u0 = DB.getJdbcUrl();
        var u1 = u0.replace("/shard_0", "/shard_1");
        var ur = u0.replace("/shard_0", "/replica_0");
        var cp0 = cp(u0);
        var cp1 = cp(u1);
        REPLICA_CP = cp(ur);
        CPS = Map.of(S0, cp0, S1, cp1);
        FlywayMigrator.migrate(u0, DB.getUsername(), DB.getPassword());
        FlywayMigrator.migrate(u1, DB.getUsername(), DB.getPassword());
        FlywayMigrator.migrate(ur, DB.getUsername(), DB.getPassword());
        REG = databaseRegistry()
                .withDefaultDatabase(new TransactionManager(cp0, cp0, SQLDialect.POSTGRES, S0))
                .withDatabase(new TransactionManager(cp1, cp1, SQLDialect.POSTGRES, S1))
                .build();
        REPLICA_REG = databaseRegistry()
                .withDefaultDatabase(new TransactionManager(cp0, REPLICA_CP, SQLDialect.POSTGRES, S0))
                .build();
    }

    static ConnectionProvider cp(String url) {
        return hikariConnectionProvider(dataSourceConfig()
                .jdbcUrl(url)
                .username(DB.getUsername())
                .password(DB.getPassword())
                .maximumPoolSize(8)
                .build());
    }

    @AfterAll
    static void stop() {
        DB.stop();
    }

    @BeforeEach
    void clean() throws Exception {
        for (var c : List.of(CPS.get(S0), CPS.get(S1), REPLICA_CP)) {
            try (var conn = c.acquire();
                    var st = conn.createStatement()) {
                st.execute("TRUNCATE TABLE " + NT + ", " + EV);
            }
        }
    }

    // ---------------------------------------------------------------- helpers

    static void p(String k, Object v) {
        System.out.println("PROBE " + k + ": " + v);
    }

    static UUID id(int n) {
        return UUID.fromString(String.format("00000000-0000-0000-0000-%012d", n));
    }

    static String lbl(String uuid) {
        return "e" + Long.parseLong(uuid.substring(24));
    }

    static String ts(Instant i) {
        return "'" + F.format(i) + "'";
    }

    static void exec(ConnectionProvider c, String sql) throws Exception {
        try (var conn = c.acquire();
                var st = conn.createStatement()) {
            st.execute(sql);
        }
    }

    static List<String> rows(ConnectionProvider c, String sql) throws Exception {
        var out = new ArrayList<String>();
        try (var conn = c.acquire();
                var st = conn.createStatement();
                var rs = st.executeQuery(sql)) {
            int n = rs.getMetaData().getColumnCount();
            while (rs.next()) {
                var sb = new StringBuilder();
                for (int i = 1; i <= n; i++) {
                    if (i > 1) sb.append(" | ");
                    sb.append(rs.getString(i));
                }
                out.add(sb.toString());
            }
        }
        return out;
    }

    static String one(ConnectionProvider c, String sql) throws Exception {
        return rows(c, sql).getFirst();
    }

    static String widgetPayload(int n, String extra) {
        return "{\"modelId\":\"m-" + n + "\",\"modelName\":\"Widget\",\"name\":\"name-" + n + "\",\"color\":\"red\""
                + extra + "}";
    }

    static String notePayload(int n) {
        return "{\"modelId\":\"m-" + n + "\",\"modelName\":\"Note\",\"widgetId\":\"w-" + n + "\",\"text\":\"t-" + n
                + "\"}";
    }

    static String eventSql(int n, String type, Instant date, String payload) {
        return "INSERT INTO " + EV + " (id, namespace, action_id, action_name, action_params, started_date,"
                + " completion_date, model_id, model_type, event_type, payload, event_date, delivered) VALUES ('"
                + id(n) + "', 'ns', '" + id(1000 + n) + "', 'SomeAction', '{\"k\":\"v\"}', " + ts(date) + ", "
                + ts(date) + ", " + (type == null ? "NULL" : "'m-" + n + "'") + ", "
                + (type == null ? "NULL" : "'Widget'") + ", " + (type == null ? "NULL" : "'" + type + "'") + ", "
                + (payload == null ? "NULL" : "'" + payload + "'") + ", " + ts(date) + ", FALSE)";
    }

    static void event(ShardIdentifier s, int n, String type, Instant date, String payload) throws Exception {
        exec(CPS.get(s), eventSql(n, type, date, payload));
    }

    static String notifSql(int n, int eventN, String handler, Instant nextRetry, Instant eventDate) {
        return "INSERT INTO " + NT + " (id, event_id, handler_name, namespace, action_id, action_name, action_params,"
                + " started_date, completion_date, model_id, model_type, event_type, payload, event_date, state,"
                + " attempts, next_retry_at, created_date, updated_date) VALUES ('" + id(500 + n) + "', '"
                + id(eventN) + "', '" + handler + "', 'ns', '" + id(1000 + eventN) + "', 'SomeAction', '{}', "
                + ts(eventDate) + ", " + ts(eventDate) + ", 'm-" + eventN + "', 'Widget', 'WidgetCreatedEvent', '"
                + widgetPayload(eventN, "") + "', " + ts(eventDate) + ", 'PENDING', 0, " + ts(nextRetry) + ", "
                + ts(eventDate) + ", " + ts(eventDate) + ")";
    }

    static final String NCOLS = "event_id::text, handler_name, state, attempts,"
            + " to_char(next_retry_at,'YYYY-MM-DD HH24:MI:SS'), to_char(created_date,'YYYY-MM-DD HH24:MI:SS'),"
            + " to_char(updated_date,'YYYY-MM-DD HH24:MI:SS')";

    static void dumpNotifs(String prefix, ShardIdentifier s) throws Exception {
        var all = rows(CPS.get(s), "SELECT " + NCOLS + " FROM " + NT + " ORDER BY event_id, handler_name");
        p(prefix + ".count", all.size());
        for (var r : all) {
            var parts = r.split(" \\| ", 2);
            var rest = parts[1].split(" \\| ");
            p(prefix + "." + lbl(parts[0]) + "." + rest[0], String.join(" | ", List.of(rest).subList(1, rest.length)));
        }
    }

    static String state(ShardIdentifier s, int eventN, String handler) throws Exception {
        var r = rows(
                CPS.get(s),
                "SELECT state, attempts, to_char(next_retry_at,'YYYY-MM-DD HH24:MI:SS'),"
                        + " to_char(updated_date,'YYYY-MM-DD HH24:MI:SS') FROM " + NT + " WHERE event_id = '"
                        + id(eventN) + "' AND handler_name = '" + handler + "'");
        return r.isEmpty() ? "none" : r.getFirst();
    }

    static EventFanoutJob fanout(DatabaseRegistry reg, EventHandlerRegistry r, Clock c, int batch) {
        return eventFanoutJob()
                .databaseRegistry(reg)
                .eventHandlerRegistry(r)
                .clock(c)
                .batchSize(batch)
                .build();
    }

    static EventHandlingJob handling(
            DatabaseRegistry reg, EventHandlerRegistry r, Clock c, int batch, Duration retention, Duration cap) {
        return eventHandlingJob()
                .databaseRegistry(reg)
                .eventHandlerRegistry(r)
                .objectMapper(OM)
                .clock(c)
                .batchSize(batch)
                .retentionWindow(retention)
                .maxBackoffCap(cap)
                .build();
    }

    static EventHandlingJob handling(EventHandlerRegistry r, Clock c) {
        return handling(REG, r, c, 100, Duration.ofDays(7), Duration.ofMinutes(5));
    }

    static String err(Runnable r) {
        try {
            r.run();
            return "ok";
        } catch (Throwable t) {
            return "refused: " + t.getMessage();
        }
    }

    // ---------------------------------------------------------------- probes

    @Test
    void f_fanout_and_happy_path() throws Exception {
        var clock = new MutableClock(T0);
        var email = new Rec<>("email", WidgetCreatedEvent.class, "ok");
        var indexer = new Rec<>("indexer", WidgetCreatedEvent.class, "ok");
        var reg = eventHandlerRegistry().withHandler(email).withHandler(indexer).build();

        event(S0, 1, "WidgetCreatedEvent", T0.minusSeconds(300), widgetPayload(1, ""));
        event(S0, 2, "WidgetCreatedEvent", T0.minusSeconds(600), widgetPayload(2, ""));
        event(S0, 3, "NoteCreatedEvent", T0.minusSeconds(420), notePayload(3));
        event(S0, 4, null, T0.minusSeconds(60), null);
        event(S1, 5, "WidgetCreatedEvent", T0.minusSeconds(180), widgetPayload(5, ""));
        event(S0, 6, "WidgetCreatedEvent", T0.minusSeconds(200), widgetPayload(6, ",\"extra\":1"));

        var fj = fanout(REG, reg, clock, 200);
        p("f.round1.any", fj.drainOneRound());
        for (var s : List.of(S0, S1)) {
            for (var r : rows(CPS.get(s), "SELECT id::text, delivered FROM " + EV + " ORDER BY id")) {
                var parts = r.split(" \\| ");
                p("f.delivered." + lbl(parts[0]), parts[1]);
            }
        }
        dumpNotifs("f.s0", S0);
        dumpNotifs("f.s1", S1);
        // copied columns: notification vs its event, column by column
        p(
                "f.copied_mismatch",
                one(
                        CPS.get(S0),
                        "SELECT count(*) FROM " + NT + " n JOIN " + EV + " e ON e.id = n.event_id WHERE"
                                + " (n.namespace, n.action_id, n.action_name, n.action_params, n.started_date,"
                                + " n.completion_date, n.model_id, n.model_type, n.event_type, n.payload, n.event_date)"
                                + " IS DISTINCT FROM (e.namespace, e.action_id, e.action_name, e.action_params,"
                                + " e.started_date, e.completion_date, e.model_id, e.model_type, e.event_type,"
                                + " e.payload, e.event_date)"));

        p("f.round2.any", fj.drainOneRound());
        exec(CPS.get(S0), "UPDATE " + EV + " SET delivered = FALSE WHERE id = '" + id(1) + "'");
        p("f.refanout.any", fj.drainOneRound());
        p("f.refanout.count", one(CPS.get(S0), "SELECT count(*) FROM " + NT));
        p(
                "f.refanout.e1_delivered",
                one(CPS.get(S0), "SELECT delivered FROM " + EV + " WHERE id = '" + id(1) + "'"));

        // handling
        clock.now = T0;
        var hj = handling(reg, clock);
        p("f.handle1.any", hj.drainOneRound());
        dumpNotifs("f.after.s0", S0);
        dumpNotifs("f.after.s1", S1);
        for (var h : List.of(email, indexer)) {
            var lines = new ArrayList<String>();
            for (var e : h.received) {
                lines.add(lbl(e.eventId.toString()) + "/" + e.modelId + "/" + e.modelType + "/" + e.event.name + "/"
                        + e.event.color + "/" + e.event.modelId + "/" + e.event.modelName + "/" + e.namespace + "/"
                        + e.actionId.toString().substring(24) + "/" + e.actionName + "/" + e.actionParams + "/"
                        + F.format(e.eventDate) + "/" + F.format(e.startedDate) + "/" + F.format(e.completionDate));
            }
            Collections.sort(lines);
            p("f.received." + h.name, lines);
        }
        p("f.handle2.any", hj.drainOneRound());

        // a handler registered after its events exist
        var audit = new Rec<>("note-audit", NoteCreatedEvent.class, "ok");
        var reg2 = eventHandlerRegistry()
                .withHandler(email)
                .withHandler(indexer)
                .withHandler(audit)
                .build();
        p("f.late.any", fanout(REG, reg2, clock, 200).drainOneRound());
        p("f.late.e3_delivered", one(CPS.get(S0), "SELECT delivered FROM " + EV + " WHERE id = '" + id(3) + "'"));
        p("f.late.e3_row", state(S0, 3, "note-audit"));
        p("f.late.e4_sentinel_delivered", one(CPS.get(S0), "SELECT delivered FROM " + EV + " WHERE id = '" + id(4)
                + "'"));
        handling(reg2, clock).drainOneRound();
        p(
                "f.late.audit_received",
                audit.received.stream()
                        .map(e -> e.event.widgetId + "/" + e.event.text)
                        .toList());
    }

    @Test
    void b_backoff_schedule() throws Exception {
        var clock = new MutableClock(T0);
        var failing = new Rec<>("failing", WidgetCreatedEvent.class, "fail");
        var reg = eventHandlerRegistry().withHandler(failing).build();
        event(S0, 1, "WidgetCreatedEvent", T0, widgetPayload(1, ""));
        fanout(REG, reg, clock, 200).drainOneRound();
        p("b.initial", state(S0, 1, "failing"));
        var hj = handling(reg, clock);
        for (int step = 1; step <= 8; step++) {
            var nextRetry = Instant.parse(state(S0, 1, "failing").split(" \\| ")[2].replace(' ', 'T') + "Z");
            if (nextRetry.isBefore(T0)) nextRetry = T0;
            clock.now = nextRetry.minusSeconds(1);
            p("b.step" + step + ".early_any", hj.drainOneRound());
            clock.now = nextRetry;
            p("b.step" + step + ".any", hj.drainOneRound());
            var st = state(S0, 1, "failing").split(" \\| ");
            var nr = Instant.parse(st[2].replace(' ', 'T') + "Z");
            p(
                    "b.step" + step,
                    st[0] + " attempts=" + st[1] + " delay=" + Duration.between(clock.now, nr).toSeconds() + "s"
                            + " updated=" + st[3]);
        }
        p("b.calls", failing.received.size());
        p("b.attempt_seen_by_handler", "n/a (Java envelope has no attempt)");
    }

    @Test
    void x_expiry() throws Exception {
        var retention = Duration.ofMinutes(10);
        var cap = Duration.ofMinutes(5);
        var clock = new MutableClock(T0);
        var failing = new Rec<>("failing", WidgetCreatedEvent.class, "fail");
        var reg = eventHandlerRegistry().withHandler(failing).build();
        event(S0, 1, "WidgetCreatedEvent", T0, widgetPayload(1, ""));
        fanout(REG, reg, clock, 200).drainOneRound();
        var hj = handling(REG, reg, clock, 100, retention, cap);
        for (int step = 1; step <= 8; step++) {
            var st0 = state(S0, 1, "failing").split(" \\| ");
            if (st0[0].equals("EXPIRED")) break;
            var nextRetry = Instant.parse(st0[2].replace(' ', 'T') + "Z");
            if (nextRetry.isBefore(T0)) nextRetry = T0;
            clock.now = nextRetry;
            hj.drainOneRound();
            var st = state(S0, 1, "failing").split(" \\| ");
            p(
                    "x.step" + step,
                    "at=+" + Duration.between(T0, clock.now).toSeconds() + "s " + st[0] + " attempts=" + st[1]
                            + " next=+" + Duration.between(T0, Instant.parse(st[2].replace(' ', 'T') + "Z"))
                                    .toSeconds() + "s calls=" + failing.received.size());
        }

        // boundary: preflight expiry at exactly event_date + retention
        clean();
        var ok = new Rec<>("ok", WidgetCreatedEvent.class, "ok");
        var reg2 = eventHandlerRegistry().withHandler(ok).build();
        event(S0, 11, "WidgetCreatedEvent", T0.minus(retention), widgetPayload(11, ""));
        event(S0, 12, "WidgetCreatedEvent", T0.minus(retention).minusSeconds(1), widgetPayload(12, ""));
        event(S0, 13, "WidgetCreatedEvent", T0.minus(retention).plusSeconds(1), widgetPayload(13, ""));
        clock.now = T0;
        fanout(REG, reg2, clock, 200).drainOneRound();
        clock.now = T0;
        handling(REG, reg2, clock, 100, retention, cap).drainOneRound();
        p("x.boundary.exact", state(S0, 11, "ok"));
        p("x.boundary.one_second_older", state(S0, 12, "ok"));
        p("x.boundary.one_second_younger", state(S0, 13, "ok"));
        p("x.boundary.calls", ok.received.stream().map(e -> e.modelId).sorted().toList());

        // a failure close to the deadline: next retry would land past it
        clean();
        var f2 = new Rec<>("failing", WidgetCreatedEvent.class, "fail");
        var reg3 = eventHandlerRegistry().withHandler(f2).build();
        event(S0, 21, "WidgetCreatedEvent", T0.minusSeconds(590), widgetPayload(21, ""));
        clock.now = T0;
        fanout(REG, reg3, clock, 200).drainOneRound();
        clock.now = T0;
        handling(REG, reg3, clock, 100, retention, cap).drainOneRound();
        p("x.near_deadline.first_failure", state(S0, 21, "failing"));
    }

    @Test
    void e_errors_missing_alias() throws Exception {
        var clock = new MutableClock(T0);
        var ok = new Rec<>("ok", WidgetCreatedEvent.class, "ok");
        var error = new Rec<>("error", WidgetCreatedEvent.class, "error");
        var fail = new Rec<>("fail", WidgetCreatedEvent.class, "fail");
        var ghost = new Rec<>("ghost", WidgetCreatedEvent.class, "ok");
        var reg = eventHandlerRegistry()
                .withHandler(ok)
                .withHandler(error)
                .withHandler(fail)
                .withHandler(ghost)
                .build();
        event(S0, 1, "WidgetCreatedEvent", T0, widgetPayload(1, ""));
        fanout(REG, reg, clock, 200).drainOneRound();
        var without = eventHandlerRegistry()
                .withHandler(ok)
                .withHandler(error)
                .withHandler(fail)
                .build();
        var hj = handling(without, clock);
        p("e.round1.any", hj.drainOneRound());
        for (var h : List.of("ok", "error", "fail", "ghost")) p("e.round1." + h, state(S0, 1, h));
        clock.now = T0.plusSeconds(30);
        hj.drainOneRound();
        for (var h : List.of("ok", "error", "fail", "ghost")) p("e.round2." + h, state(S0, 1, h));
        clock.now = T0.plusSeconds(90);
        hj.drainOneRound();
        p("e.round3.ghost", state(S0, 1, "ghost"));
        p("e.calls", ok.received.size() + "/" + error.received.size() + "/" + fail.received.size());

        // alias: queued under the old name, drained by the renamed handler
        clean();
        clock.now = T0;
        var oldH = new Rec<>("old-name", NoteCreatedEvent.class, "ok");
        event(S0, 2, "NoteCreatedEvent", T0, notePayload(2));
        fanout(REG, eventHandlerRegistry().withHandler(oldH).build(), clock, 200)
                .drainOneRound();
        var renamed = new Rec<>("new-name", NoteCreatedEvent.class, "ok", Set.of("old-name"));
        var reg2 = eventHandlerRegistry().withHandler(renamed).build();
        handling(reg2, clock).drainOneRound();
        p("e.alias.old_row", state(S0, 2, "old-name"));
        p("e.alias.calls", renamed.received.size());
        event(S0, 3, "NoteCreatedEvent", T0, notePayload(3));
        fanout(REG, reg2, clock, 200).drainOneRound();
        p(
                "e.alias.new_rows",
                rows(CPS.get(S0), "SELECT handler_name FROM " + NT + " WHERE event_id = '" + id(3) + "'"));
    }

    @Test
    void t_transactions() throws Exception {
        var tm = REG.transactionManager(S0);
        var events = new EventEntityRepository(REG);
        var notifs = new EventNotificationRepository(REG);

        // a) uncommitted event, read inside the same transaction
        var seen = new String[1];
        try {
            tm.inTransaction((java.util.function.Consumer<org.jooq.DSLContext>) ctx -> {
                ctx.execute(eventSql(1, "WidgetCreatedEvent", T0, widgetPayload(1, "")));
                seen[0] = String.valueOf(events.findUndelivered(S0, List.of("WidgetCreatedEvent"), 10)
                        .size());
                throw new RuntimeException("rollback");
            });
        } catch (RuntimeException ignored) {
        }
        p("t.find_undelivered_sees_own_uncommitted", seen[0]);
        p("t.after_rollback_events", one(CPS.get(S0), "SELECT count(*) FROM " + EV));

        // b) uncommitted notification, findDue inside the same transaction
        try {
            tm.inTransaction((java.util.function.Consumer<org.jooq.DSLContext>) ctx -> {
                ctx.execute(notifSql(1, 1, "h", T0, T0));
                seen[0] = String.valueOf(notifs.findDue(S0, 10, T0).size());
                throw new RuntimeException("rollback");
            });
        } catch (RuntimeException ignored) {
        }
        p("t.find_due_sees_own_uncommitted", seen[0]);

        // c) committed event, mark delivered inside a tx, then read inside it, then roll back
        event(S0, 2, "WidgetCreatedEvent", T0, widgetPayload(2, ""));
        try {
            tm.inTransaction((java.util.function.Consumer<org.jooq.DSLContext>) ctx -> {
                events.markDelivered(List.of(id(2)), S0);
                seen[0] = String.valueOf(events.findUndelivered(S0, List.of("WidgetCreatedEvent"), 10)
                        .size());
                throw new RuntimeException("rollback");
            });
        } catch (RuntimeException ignored) {
        }
        p("t.undelivered_after_mark_in_same_tx", seen[0]);
        p("t.mark_delivered_rolled_back", one(CPS.get(S0), "SELECT delivered FROM " + EV + " WHERE id = '" + id(2)
                + "'"));

        // d) addAllNoResult joins the tx (rolled back with it)
        exec(CPS.get(S0), "TRUNCATE " + NT + ", " + EV);
        // e) outcome writes: are they atomic? A trigger refuses any FAILED update.
        exec(
                CPS.get(S0),
                "CREATE OR REPLACE FUNCTION refuse_failed() RETURNS trigger AS $$ BEGIN IF NEW.state = 'FAILED'"
                        + " THEN RAISE EXCEPTION 'no FAILED allowed'; END IF; RETURN NEW; END $$ LANGUAGE plpgsql");
        exec(
                CPS.get(S0),
                "CREATE TRIGGER refuse_failed BEFORE UPDATE ON " + NT
                        + " FOR EACH ROW EXECUTE FUNCTION refuse_failed()");
        var clock = new MutableClock(T0);
        var ok = new Rec<>("ok", WidgetCreatedEvent.class, "ok");
        var fail = new Rec<>("fail", WidgetCreatedEvent.class, "fail");
        var reg = eventHandlerRegistry().withHandler(ok).withHandler(fail).build();
        event(S0, 3, "WidgetCreatedEvent", T0, widgetPayload(3, ""));
        fanout(REG, reg, clock, 200).drainOneRound();
        var threw = "false";
        try {
            handling(reg, clock).drainOneRound();
        } catch (Throwable t) {
            threw = "true";
        }
        p("t.outcome_write_failed.threw", threw);
        p("t.outcome_write_failed.ok_row", state(S0, 3, "ok").split(" \\| ")[0]);
        p("t.outcome_write_failed.fail_row", state(S0, 3, "fail").split(" \\| ")[0]);
        try {
            handling(reg, clock).drainOneRound();
        } catch (Throwable ignored) {
        }
        p("t.outcome_write_failed.ok_calls_after_two_rounds", ok.received.size());
        exec(CPS.get(S0), "DROP TRIGGER refuse_failed ON " + NT);
    }

    @Test
    void r_replica() throws Exception {
        var clock = new MutableClock(T0);
        var ok = new Rec<>("ok", WidgetCreatedEvent.class, "ok");
        var reg = eventHandlerRegistry().withHandler(ok).build();
        // primary has the event, the replica (a separate, empty database) does not
        event(S0, 1, "WidgetCreatedEvent", T0, widgetPayload(1, ""));
        p(
                "r.find_undelivered",
                new EventEntityRepository(REPLICA_REG)
                        .findUndelivered(S0, List.of("WidgetCreatedEvent"), 10)
                        .size());
        p("r.fanout.any", fanout(REPLICA_REG, reg, clock, 200).drainOneRound());
        p("r.fanout.primary_notifications", one(CPS.get(S0), "SELECT count(*) FROM " + NT));
        // put a due notification on the primary only
        exec(CPS.get(S0), "TRUNCATE " + NT);
        exec(CPS.get(S0), notifSql(1, 1, "ok", T0, T0));
        p("r.find_due", new EventNotificationRepository(REPLICA_REG).findDue(S0, 10, T0).size());
        p(
                "r.handling.any",
                handling(REPLICA_REG, reg, clock, 100, Duration.ofDays(7), Duration.ofMinutes(5))
                        .drainOneRound());
        p("r.handling.calls", ok.received.size());
    }

    @Test
    void c_batches_concurrency_order() throws Exception {
        var clock = new MutableClock(T0);
        var ok = new Rec<>("ok", WidgetCreatedEvent.class, "ok");
        var reg = eventHandlerRegistry().withHandler(ok).build();
        var sb = new StringBuilder();
        for (int i = 1; i <= 250; i++) {
            event(S0, i, "WidgetCreatedEvent", T0.minusSeconds(1000 - i), widgetPayload(i, ""));
        }
        // default-built jobs: no batchSize given
        var fj = eventFanoutJob()
                .databaseRegistry(REG)
                .eventHandlerRegistry(reg)
                .clock(clock)
                .build();
        fj.drainOneRound();
        p("c.default_fanout_batch", one(CPS.get(S0), "SELECT count(*) FROM " + EV + " WHERE delivered"));
        p(
                "c.fanout_took_oldest",
                one(CPS.get(S0), "SELECT max(event_date) < (SELECT min(event_date) FROM " + EV
                        + " WHERE NOT delivered) FROM " + EV + " WHERE delivered"));
        var hj = eventHandlingJob()
                .databaseRegistry(REG)
                .eventHandlerRegistry(reg)
                .objectMapper(OM)
                .clock(clock)
                .build();
        hj.drainOneRound();
        p("c.default_handling_batch", one(CPS.get(S0), "SELECT count(*) FROM " + NT + " WHERE state = 'SUCCEEDED'"));

        // concurrency: 3 on shard 0, 2 on shard 1, a slow handler
        clean();
        CURRENT.set(0);
        MAX.set(0);
        var slow = new Rec<>("slow", WidgetCreatedEvent.class, "slow");
        var regSlow = eventHandlerRegistry().withHandler(slow).build();
        for (int i = 1; i <= 3; i++) event(S0, i, "WidgetCreatedEvent", T0, widgetPayload(i, ""));
        for (int i = 4; i <= 5; i++) event(S1, i, "WidgetCreatedEvent", T0, widgetPayload(i, ""));
        fanout(REG, regSlow, clock, 200).drainOneRound();
        handling(regSlow, clock).drainOneRound();
        p("c.max_concurrent_handlers", MAX.get());
        p("c.slow_calls", slow.received.size());

        // order: an older event that commits after a newer one was already fanned out
        clean();
        var rec = new Rec<>("rec", WidgetCreatedEvent.class, "ok");
        var regRec = eventHandlerRegistry().withHandler(rec).build();
        event(S0, 1, "WidgetCreatedEvent", T0.minusSeconds(300), widgetPayload(1, ""));
        clock.now = T0;
        fanout(REG, regRec, clock, 200).drainOneRound();
        event(S0, 2, "WidgetCreatedEvent", T0.minusSeconds(600), widgetPayload(2, ""));
        clock.now = T0.plusSeconds(60);
        fanout(REG, regRec, clock, 200).drainOneRound();
        dumpNotifs("c.order", S0);
        clock.now = T0.plusSeconds(120);
        var one = handling(REG, regRec, clock, 1, Duration.ofDays(7), Duration.ofMinutes(5));
        one.drainOneRound();
        one.drainOneRound();
        p("c.order.handled", rec.order);

        // a notification whose event_date is in the future relative to the clock
        clean();
        var fut = new Rec<>("fut", WidgetCreatedEvent.class, "ok");
        var regFut = eventHandlerRegistry().withHandler(fut).build();
        event(S0, 9, "WidgetCreatedEvent", T0.plusSeconds(3600), widgetPayload(9, ""));
        clock.now = T0;
        fanout(REG, regFut, clock, 200).drainOneRound();
        handling(regFut, clock).drainOneRound();
        p("c.future_event.row", state(S0, 9, "fut"));
        p("c.future_event.calls", fut.received.size());
    }

    @Test
    void j_jobs_and_config() throws Exception {
        var clock = new MutableClock(T0);
        var reg = eventHandlerRegistry()
                .withHandler(new Rec<>("ok", WidgetCreatedEvent.class, "ok"))
                .build();
        var fj = eventFanoutJob()
                .databaseRegistry(REG)
                .eventHandlerRegistry(reg)
                .clock(clock)
                .build();
        var hj = eventHandlingJob()
                .databaseRegistry(REG)
                .eventHandlerRegistry(reg)
                .objectMapper(OM)
                .clock(clock)
                .build();
        p("j.fanout.name", fj.name());
        p("j.handling.name", hj.name());
        p("j.fanout.schedule", fj.schedule().getClass().getSimpleName());
        p("j.handling.schedule_default", hj.schedule().getClass().getSimpleName());
        var cfg = LocalEventHandlerConfig.defaults();
        p("j.config.handling_enabled_default", cfg.handling.enabled);
        p(
                "j.config.defaults_empty",
                cfg.fanoutPollDelay.isEmpty()
                        && cfg.fanoutBatchSize.isEmpty()
                        && cfg.handlingPollDelay.isEmpty()
                        && cfg.handlingBatchSize.isEmpty()
                        && cfg.handlingMaxBackoffCap.isEmpty()
                        && cfg.handlingRetentionWindow.isEmpty());
        p(
                "j.config.batch_zero",
                err(() -> LocalEventHandlerConfig.localEventHandlerConfig()
                        .fanoutBatchSize(0)
                        .build()));
        p(
                "j.config.poll_zero",
                err(() -> LocalEventHandlerConfig.localEventHandlerConfig()
                        .handlingPollDelay(Duration.ZERO)
                        .build()));
        p(
                "j.config.retention_negative",
                err(() -> LocalEventHandlerConfig.localEventHandlerConfig()
                        .handlingRetentionWindow(Duration.ofSeconds(-1))
                        .build()));
        p(
                "j.job.blank_name",
                err(() -> eventFanoutJob()
                        .databaseRegistry(REG)
                        .eventHandlerRegistry(reg)
                        .clock(clock)
                        .name("  ")
                        .build()));
        p(
                "j.job.batch_zero",
                err(() -> eventFanoutJob()
                        .databaseRegistry(REG)
                        .eventHandlerRegistry(reg)
                        .clock(clock)
                        .batchSize(0)
                        .build()));
        p(
                "j.job.no_clock",
                err(() -> eventFanoutJob()
                        .databaseRegistry(REG)
                        .eventHandlerRegistry(reg)
                        .build()));
        p("j.empty_registry_fanout.any", fanout(REG, eventHandlerRegistry().build(), clock, 200).drainOneRound());
    }

    @Test
    void g_registry() {
        var a = new Rec<>("a", WidgetCreatedEvent.class, "ok", Set.of("a-old"));
        p("g.same_handler_twice", err(() -> eventHandlerRegistry().withHandler(a).withHandler(a)));
        p(
                "g.duplicate_name",
                err(() -> eventHandlerRegistry()
                        .withHandler(a)
                        .withHandler(new Rec<>("a", NoteCreatedEvent.class, "ok"))));
        p(
                "g.alias_equals_other_name",
                err(() -> eventHandlerRegistry()
                        .withHandler(a)
                        .withHandler(new Rec<>("b", NoteCreatedEvent.class, "ok", Set.of("a")))));
        p(
                "g.name_equals_other_alias",
                err(() -> eventHandlerRegistry()
                        .withHandler(a)
                        .withHandler(new Rec<>("a-old", NoteCreatedEvent.class, "ok"))));
        p(
                "g.alias_equals_own_name",
                err(() -> eventHandlerRegistry()
                        .withHandler(new Rec<>("c", NoteCreatedEvent.class, "ok", Set.of("c")))));
        p(
                "g.blank_alias",
                err(() -> eventHandlerRegistry()
                        .withHandler(new Rec<>("c", NoteCreatedEvent.class, "ok", Set.of(" ")))));
        p(
                "g.blank_name",
                err(() -> eventHandlerRegistry().withHandler(new Rec<>(" ", NoteCreatedEvent.class, "ok"))));
        var collision = err(() -> eventHandlerRegistry()
                .withHandler(a)
                .withHandler(new Rec<>("d", Other.WidgetCreatedEvent.class, "ok")));
        p("g.simple_name_collision", collision.substring(0, Math.min(collision.length(), collision.indexOf(" maps"))));
        var r = eventHandlerRegistry()
                .withHandler(a)
                .withHandler(new Rec<>("n1", NoteCreatedEvent.class, "ok"))
                .withHandler(new Rec<>("a2", WidgetCreatedEvent.class, "ok"))
                .build();
        p("g.handled_event_types", new ArrayList<>(r.handledEventTypes()));
        p("g.subscribers_widget", r.subscribersFor("WidgetCreatedEvent"));
        p("g.subscribers_unknown", r.subscribersFor("Nope"));
        p("g.lookup_alias", r.handlerFor("a-old").name());
        p("g.lookup_unknown", r.handlerFor("zzz"));
    }
}
