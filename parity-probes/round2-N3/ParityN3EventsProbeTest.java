package io.ekbatan.test.local_event_handler_pg;

import static io.ekbatan.core.config.DataSourceConfig.Builder.dataSourceConfig;
import static io.ekbatan.core.persistence.ConnectionProvider.hikariConnectionProvider;
import static io.ekbatan.core.shard.DatabaseRegistry.Builder.databaseRegistry;
import static io.ekbatan.distributedjobs.JobRegistry.jobRegistry;
import static io.ekbatan.events.localeventhandler.EventHandlerRegistry.eventHandlerRegistry;
import static io.ekbatan.events.localeventhandler.job.EventFanoutJob.eventFanoutJob;
import static io.ekbatan.events.localeventhandler.job.EventHandlingJob.eventHandlingJob;

import com.github.kagkarlsson.scheduler.task.ExecutionContext;
import com.github.kagkarlsson.scheduler.task.schedule.FixedDelay;
import com.github.kagkarlsson.scheduler.task.schedule.Schedule;
import io.ekbatan.core.persistence.ConnectionProvider;
import io.ekbatan.core.persistence.TransactionManager;
import io.ekbatan.core.shard.DatabaseRegistry;
import io.ekbatan.core.shard.ShardIdentifier;
import io.ekbatan.distributedjobs.DistributedJob;
import io.ekbatan.events.localeventhandler.EventEnvelope;
import io.ekbatan.events.localeventhandler.EventHandler;
import io.ekbatan.flyway.FlywayMigrator;
import io.ekbatan.test.local_event_handler.widget.models.events.WidgetCreatedEvent;
import io.opentelemetry.sdk.OpenTelemetrySdk;
import io.opentelemetry.sdk.metrics.SdkMeterProvider;
import io.opentelemetry.sdk.metrics.data.MetricData;
import io.opentelemetry.sdk.testing.exporter.InMemoryMetricReader;
import io.opentelemetry.sdk.testing.exporter.InMemorySpanExporter;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.text.MessageFormat;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.logging.Handler;
import java.util.logging.Level;
import java.util.logging.LogManager;
import java.util.logging.LogRecord;
import java.util.logging.Logger;
import org.jooq.SQLDialect;
import org.junit.jupiter.api.Test;
import org.testcontainers.postgresql.PostgreSQLContainer;
import tools.jackson.databind.ObjectMapper;

/** Parity round 2, area N3: events, jobs, metrics. Temporary; never committed. */
class ParityN3EventsProbeTest {

    static final String MARK = "n3MarkerPw7731q";
    static final InMemorySpanExporter SPANS = InMemorySpanExporter.create();
    static final InMemoryMetricReader METRICS = InMemoryMetricReader.create();
    static volatile String SCEN = "setup";
    static final List<String[]> RECORDS = new CopyOnWriteArrayList<>();
    static final List<String> ALL_TEXT = new CopyOnWriteArrayList<>();
    static final Map<String, Integer> OTHER = new ConcurrentHashMap<>();

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
                var name = r.getLoggerName() == null ? "?" : r.getLoggerName();
                if (name.startsWith("io.ekbatan")) {
                    RECORDS.add(new String[] {
                        SCEN, level(r.getLevel()), name, msg, t == null ? "-" : t.getClass().getSimpleName()
                    });
                } else if (r.getLevel().intValue() >= Level.INFO.intValue()) {
                    if (name.startsWith("com.github.kagkarlsson")) {
                        RECORDS.add(new String[] {
                            SCEN, level(r.getLevel()), name, msg, t == null ? "-" : t.getClass().getSimpleName()
                        });
                    } else {
                        var parts = name.split("\\.");
                        var top = parts.length >= 3 ? parts[0] + "." + parts[1] + "." + parts[2] : name;
                        OTHER.merge(SCEN + " " + level(r.getLevel()) + " " + top, 1, Integer::sum);
                    }
                }
            }

            @Override
            public void flush() {}

            @Override
            public void close() {}
        });
        Logger.getLogger("io.ekbatan").setLevel(Level.ALL);
    }

    static String level(Level l) {
        if (l == Level.SEVERE) return "ERROR";
        if (l == Level.WARNING) return "WARN";
        if (l == Level.INFO) return "INFO";
        if (l == Level.FINE) return "DEBUG";
        return "TRACE";
    }

    static final ShardIdentifier S0 = ShardIdentifier.DEFAULT;
    static final Instant T0 = Instant.parse("2026-01-01T10:00:00Z");
    static final DateTimeFormatter F =
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss").withZone(ZoneOffset.UTC);
    static final ObjectMapper OM = new ObjectMapper();
    static ConnectionProvider CP;

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

    static final class Rec implements EventHandler<WidgetCreatedEvent> {
        final String name;
        final String mode;

        Rec(String name, String mode) {
            this.name = name;
            this.mode = mode;
        }

        @Override
        public String name() {
            return name;
        }

        @Override
        public Set<String> aliases() {
            return Set.of();
        }

        @Override
        public Class<WidgetCreatedEvent> eventType() {
            return WidgetCreatedEvent.class;
        }

        @Override
        public void handle(EventEnvelope<WidgetCreatedEvent> envelope) throws Exception {
            switch (mode) {
                case "fail" -> throw new RuntimeException("handler boom");
                case "error" -> throw new AssertionError("error boom");
                default -> {}
            }
        }
    }

    static void p(String k, Object v) {
        System.out.println("PROBE " + k + ": " + v);
    }

    static UUID id(int n) {
        return UUID.fromString(String.format("00000000-0000-0000-0000-%012d", n));
    }

    static String ts(Instant i) {
        return "'" + F.format(i) + "'";
    }

    static void exec(String sql) throws Exception {
        try (var conn = CP.acquire();
                var st = conn.createStatement()) {
            st.execute(sql);
        }
    }

    static void event(int n, Instant date) throws Exception {
        exec("INSERT INTO eventlog.events (id, namespace, action_id, action_name, action_params, started_date,"
                + " completion_date, model_id, model_type, event_type, payload, event_date, delivered) VALUES ('"
                + id(n) + "', 'ns', '" + id(1000 + n) + "', 'SomeAction', '{\"k\":\"v\"}', " + ts(date) + ", "
                + ts(date) + ", 'm-" + n + "', 'Widget', 'WidgetCreatedEvent', "
                + "'{\"modelId\":\"m-" + n + "\",\"modelName\":\"Widget\",\"name\":\"name-" + n
                + "\",\"color\":\"red\"}', " + ts(date) + ", FALSE)");
    }

    static void notif(int n, int eventN, String handler, Instant eventDate, Instant nextRetry) throws Exception {
        exec("INSERT INTO eventlog.event_notifications (id, event_id, handler_name, namespace, action_id, action_name,"
                + " action_params, started_date, completion_date, model_id, model_type, event_type, payload, event_date,"
                + " state, attempts, next_retry_at, created_date, updated_date) VALUES ('" + id(500 + n) + "', '"
                + id(eventN) + "', '" + handler + "', 'ns', '" + id(1000 + eventN) + "', 'SomeAction', '{}', "
                + ts(eventDate) + ", " + ts(eventDate) + ", 'm-" + eventN + "', 'Widget', 'WidgetCreatedEvent', "
                + "'{\"modelId\":\"m-" + eventN + "\",\"modelName\":\"Widget\",\"name\":\"n\",\"color\":\"red\"}', "
                + ts(eventDate) + ", 'PENDING', 0, " + ts(nextRetry) + ", " + ts(eventDate) + ", " + ts(eventDate)
                + ")");
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
            p(name + ".outcome", t.getClass().getSimpleName() + ": " + t.getMessage());
        }
        Thread.sleep(100);
        p(name + ".spans", SPANS.getFinishedSpanItems().size());
        int i = 0;
        for (var r : RECORDS) {
            if (!r[0].equals(name)) continue;
            p(name + ".log." + (i++), r[1] + " " + r[2] + " | " + r[3].replace("\n", "\\n") + " | exc=" + r[4]);
        }
    }

    static void dumpMetrics() {
        var all = new TreeMap<String, String>();
        for (MetricData m : METRICS.collectAllMetrics()) {
            var head = m.getName() + " scope=" + m.getInstrumentationScopeInfo().getName() + " type=" + m.getType()
                    + " unit=" + m.getUnit() + " desc=" + m.getDescription();
            switch (m.getType()) {
                case LONG_SUM -> {
                    var d = m.getLongSumData();
                    head += " monotonic=" + d.isMonotonic() + " temporality=" + d.getAggregationTemporality();
                    for (var pt : d.getPoints()) {
                        var a = new TreeMap<String, String>();
                        pt.getAttributes().forEach((k, v) -> a.put(k.getKey(), String.valueOf(v)));
                        all.put(m.getName() + " " + a, "value=" + pt.getValue());
                    }
                }
                case HISTOGRAM -> {
                    var d = m.getHistogramData();
                    head += " temporality=" + d.getAggregationTemporality();
                    for (var pt : d.getPoints()) {
                        var a = new TreeMap<String, String>();
                        pt.getAttributes().forEach((k, v) -> a.put(k.getKey(), String.valueOf(v)));
                        all.put(m.getName() + " " + a, "count=" + pt.getCount() + " boundaries=" + pt.getBoundaries().size());
                    }
                }
                default -> {}
            }
            all.put(m.getName() + " (instrument)", head);
            if (m.toString().contains(MARK)) p("secret.metric_with_marker", m.getName());
        }
        all.forEach((k, v) -> p("metric." + k, v));
    }

    static DistributedJob job(String name, Schedule schedule, Runnable body) {
        return new DistributedJob() {
            @Override
            public String name() {
                return name;
            }

            @Override
            public Schedule schedule() {
                return schedule;
            }

            @Override
            public void execute(ExecutionContext ctx) {
                body.run();
            }
        };
    }

    @Test
    void probe() throws Exception {
        var db = new PostgreSQLContainer("postgres:17-alpine")
                .withDatabaseName("events_db")
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
        FlywayMigrator.migrate(db.getJdbcUrl(), db.getUsername(), db.getPassword());
        CP = hikariConnectionProvider(dataSourceConfig()
                .jdbcUrl(db.getJdbcUrl())
                .username(db.getUsername())
                .password(db.getPassword())
                .maximumPoolSize(8)
                .build());
        DatabaseRegistry reg = databaseRegistry()
                .withDefaultDatabase(new TransactionManager(CP, CP, SQLDialect.POSTGRES, S0))
                .build();
        var clock = new MutableClock(T0);
        var handlers = eventHandlerRegistry()
                .withHandler(new Rec("ok-handler", "ok"))
                .withHandler(new Rec("fail-handler", "fail"))
                .withHandler(new Rec("error-handler", "error"))
                .build();
        var fanout = eventFanoutJob()
                .databaseRegistry(reg)
                .eventHandlerRegistry(handlers)
                .clock(clock)
                .build();
        var handling = eventHandlingJob()
                .databaseRegistry(reg)
                .eventHandlerRegistry(handlers)
                .objectMapper(OM)
                .clock(clock)
                .retentionWindow(Duration.ofDays(1))
                .maxBackoffCap(Duration.ofMinutes(5))
                .build();

        scenario("e1_fanout", () -> {
            event(1, T0);
            event(2, T0);
            fanout.drainOneRound();
        });
        scenario("e2_handle_round", () -> handling.drainOneRound());
        scenario("e3_expired_preflight", () -> {
            exec("TRUNCATE eventlog.event_notifications");
            notif(1, 1, "ok-handler", T0.minus(Duration.ofDays(2)), T0);
            notif(2, 2, "ok-handler", T0.minus(Duration.ofDays(2)), T0);
            handling.drainOneRound();
        });
        scenario("e4_expired_postfailure", () -> {
            exec("TRUNCATE eventlog.event_notifications");
            notif(3, 3, "fail-handler", T0.minus(Duration.ofDays(1)).plusSeconds(1), T0);
            handling.drainOneRound();
        });
        dumpMetrics();

        // Jobs
        try (var c = CP.acquire();
                var st = c.createStatement()) {
            st.execute("drop table if exists scheduled_tasks");
            st.execute("create table scheduled_tasks (task_name text not null, task_instance text not null,"
                    + " task_data bytea, execution_time timestamp with time zone not null, picked BOOLEAN not null,"
                    + " picked_by text, last_success timestamp with time zone, last_failure timestamp with time zone,"
                    + " consecutive_failures INT, last_heartbeat timestamp with time zone, version BIGINT not null,"
                    + " priority SMALLINT, PRIMARY KEY (task_name, task_instance))");
        }
        var jobs = jobRegistry()
                .connectionProvider(CP)
                .registerShutdownHook(false)
                .pollInterval(Duration.ofMillis(100))
                .withJob(job("ok-job", FixedDelay.of(Duration.ofHours(1)), () -> {}))
                .withJob(job("failing-job", FixedDelay.of(Duration.ofHours(1)), () -> {
                    throw new IllegalStateException("job boom");
                }))
                .build();
        scenario("j1_start", jobs::start);
        scenario("j2_run", () -> Thread.sleep(2000));
        scenario("j3_stop", jobs::stop);

        var hits = 0;
        for (var s : ALL_TEXT)
            if (s.contains(MARK)) {
                hits++;
                var parts = s.split(" ");
                p("secret.hit." + hits, parts[0] + " " + parts[1]);
            }
        p("secret.log_records_with_marker", hits);
        for (var e : new TreeMap<>(OTHER).entrySet()) p("library_info_plus." + e.getKey(), e.getValue());
    }
}
