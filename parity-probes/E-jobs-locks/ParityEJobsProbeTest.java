package io.ekbatan.test.keyed_lock_provider;

import static io.ekbatan.core.config.DataSourceConfig.Builder.dataSourceConfig;
import static io.ekbatan.core.persistence.ConnectionProvider.hikariConnectionProvider;
import static io.ekbatan.distributedjobs.JobRegistry.jobRegistry;

import com.github.kagkarlsson.scheduler.task.ExecutionContext;
import com.github.kagkarlsson.scheduler.task.schedule.DisabledSchedule;
import com.github.kagkarlsson.scheduler.task.schedule.FixedDelay;
import com.github.kagkarlsson.scheduler.task.schedule.Schedule;
import io.ekbatan.core.persistence.ConnectionProvider;
import io.ekbatan.distributedjobs.DistributedJob;
import io.ekbatan.distributedjobs.JobRegistry;
import io.ekbatan.distributedjobs.config.JobsConfig;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

/** Parity scan area E: distributed jobs. Prints PROBE lines; asserts nothing. Temporary. */
class ParityEJobsProbeTest {

    static final String PG_URL = "jdbc:postgresql://localhost:56432/testdb";
    static final String PW = System.getenv().getOrDefault("PARITY_PW", "test");
    static final String DDL = "create table scheduled_tasks (task_name text not null, task_instance text not null,"
            + " task_data bytea, execution_time timestamp with time zone not null, picked BOOLEAN not null,"
            + " picked_by text, last_success timestamp with time zone, last_failure timestamp with time zone,"
            + " consecutive_failures INT, last_heartbeat timestamp with time zone, version BIGINT not null,"
            + " priority SMALLINT, PRIMARY KEY (task_name, task_instance))";

    static ConnectionProvider cp;

    static void p(String k, Object v) {
        System.out.println("PROBE " + k + ": " + v);
    }

    static long bucket(long ms, long size) {
        return Math.round(ms / (double) size) * size;
    }

    interface Body {
        void run() throws Exception;
    }

    static String refused(Body r) {
        try {
            r.run();
            return "accepted";
        } catch (Exception e) {
            return "refused";
        }
    }

    static void sql(String s) throws Exception {
        var c = cp.acquire();
        try (var st = c.createStatement()) {
            st.execute(s);
        } finally {
            cp.release(c);
        }
    }

    static String query1(String s) throws Exception {
        var c = cp.acquire();
        try (var st = c.createStatement();
                var rs = st.executeQuery(s)) {
            if (!rs.next()) {
                return "no-row";
            }
            var cols = rs.getMetaData().getColumnCount();
            var sb = new StringBuilder();
            for (var i = 1; i <= cols; i++) {
                if (i > 1) {
                    sb.append("|");
                }
                sb.append(rs.getString(i));
            }
            return sb.toString();
        } finally {
            cp.release(c);
        }
    }

    interface Exec {
        void run(ExecutionContext ctx) throws Exception;
    }

    static DistributedJob job(String name, Schedule schedule, Exec body) {
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
                try {
                    body.run(ctx);
                } catch (RuntimeException e) {
                    throw e;
                } catch (Exception e) {
                    throw new RuntimeException(e);
                }
            }
        };
    }

    static JobRegistry.Builder base() {
        return jobRegistry().connectionProvider(cp).registerShutdownHook(false).pollInterval(Duration.ofMillis(100));
    }

    @Test
    void probe() throws Exception {
        cp = hikariConnectionProvider(dataSourceConfig()
                .jdbcUrl(PG_URL)
                .username("test")
                .password(PW)
                .maximumPoolSize(10)
                .build());
        sql("drop table if exists scheduled_tasks");
        sql("drop table if exists scheduled_tasks_hidden");
        sql(DDL);

        var noop = job("v", FixedDelay.ofMillis(200), ctx -> {});

        // J1 validation
        p("validate.no_jobs", refused(() -> jobRegistry().connectionProvider(cp).build()));
        p("validate.no_connection", refused(() -> jobRegistry().withJob(noop).build()));
        p("validate.null_job", refused(() -> jobRegistry().withJob(null)));
        p("validate.null_in_jobs", refused(() -> jobRegistry().withJobs(java.util.Arrays.asList(noop, null))));
        p("validate.blank_name", refused(() -> base().withJob(job(" ", FixedDelay.ofMillis(200), ctx -> {}))
                .build()));
        p("validate.duplicate_names", refused(() -> base().withJob(job("d", FixedDelay.ofMillis(200), ctx -> {}))
                .withJob(job("d", FixedDelay.ofMillis(300), ctx -> {}))
                .build()));
        p("validate.null_schedule", refused(() -> base().withJob(job("n", null, ctx -> {})).build()));
        p("validate.zero_poll", refused(() -> base().withJob(noop).pollInterval(Duration.ZERO).build()));
        p("validate.negative_poll",
                refused(() -> base().withJob(noop).pollInterval(Duration.ofMillis(-1)).build()));
        p("validate.zero_heartbeat",
                refused(() -> base().withJob(noop).heartbeatInterval(Duration.ZERO).build()));
        p("validate.negative_shutdown_wait",
                refused(() -> base().withJob(noop).shutdownMaxWait(Duration.ofMillis(-1)).build()));
        p("validate.zero_shutdown_wait", refused(() -> base().withJob(noop).shutdownMaxWait(Duration.ZERO).build()));
        p("config.zero_polling",
                refused(() -> JobsConfig.jobsConfig().pollingInterval(Duration.ZERO).build()));
        p("config.zero_heartbeat",
                refused(() -> JobsConfig.jobsConfig().heartbeatInterval(Duration.ZERO).build()));
        p("config.negative_shutdown",
                refused(() -> JobsConfig.jobsConfig().shutdownMaxWait(Duration.ofSeconds(-1)).build()));
        p("config.zero_shutdown", refused(() -> JobsConfig.jobsConfig().shutdownMaxWait(Duration.ZERO).build()));
        p("config.one_microsecond_polling",
                refused(() -> JobsConfig.jobsConfig().pollingInterval(Duration.ofNanos(1000)).build()));
        var d = JobsConfig.defaults();
        p("config.defaults_all_unset",
                d.pollingInterval.isEmpty() && d.heartbeatInterval.isEmpty() && d.shutdownMaxWait.isEmpty());

        // J2 the row a recurring job writes; a FixedDelay job runs at once on start
        {
            var runs = new AtomicInteger();
            var reg = base().withJob(job("j2-fixed", FixedDelay.of(Duration.ofHours(1)), ctx -> runs.incrementAndGet()))
                    .build();
            reg.start();
            Thread.sleep(1500);
            p("j2.ran_at_start", runs.get());
            p("j2.row", query1("select task_name, task_instance, task_data is null, picked, picked_by is null,"
                    + " consecutive_failures, last_success is not null, last_failure is null, version, priority"
                    + " from scheduled_tasks where task_name = 'j2-fixed'"));
            p("j2.next_run_in_minutes", query1("select round(extract(epoch from execution_time - now()) / 60)"
                    + " from scheduled_tasks where task_name = 'j2-fixed'"));
            reg.stop();
            var reg2 = base().withJob(job("j2-fixed", FixedDelay.of(Duration.ofHours(1)), ctx -> runs.incrementAndGet()))
                    .build();
            reg2.start();
            Thread.sleep(500);
            reg2.stop();
            p("j2.restart.rows", query1("select count(*) from scheduled_tasks where task_name = 'j2-fixed'"));
            p("j2.restart.ran_again", runs.get() > 1);
        }

        // J3 disabled schedule
        {
            var reg = base().withJob(job("j3-disabled", new DisabledSchedule(), ctx -> {})).build();
            p("j3.start", refused(reg::start));
            p("j3.rows", query1("select count(*) from scheduled_tasks where task_name = 'j3-disabled'"));
            reg.stop();
        }

        // J4 runs repeatedly
        {
            var runs = new AtomicInteger();
            var reg = base().withJob(job("j4-repeat", FixedDelay.ofMillis(200), ctx -> runs.incrementAndGet()))
                    .build();
            reg.start();
            Thread.sleep(2000);
            reg.stop();
            p("j4.runs_at_least_4", runs.get() >= 4);
        }

        // J5 a job that throws
        {
            var runs = new AtomicInteger();
            var reg = base().withJob(job("j5-throws", FixedDelay.ofMillis(200), ctx -> {
                        runs.incrementAndGet();
                        throw new IllegalStateException("boom");
                    }))
                    .build();
            reg.start();
            Thread.sleep(1500);
            reg.stop();
            p("j5.runs_at_least_2", runs.get() >= 2);
            p("j5.row", query1("select consecutive_failures >= 2, last_failure is not null, last_success is null,"
                    + " picked from scheduled_tasks where task_name = 'j5-throws'"));
        }

        // J6 start with the table missing
        {
            var reg = base().withJob(job("j6-missing", FixedDelay.ofMillis(200), ctx -> {})).build();
            sql("alter table scheduled_tasks rename to scheduled_tasks_hidden");
            try {
                reg.start();
                p("j6.start", "accepted");
            } catch (RuntimeException e) {
                p("j6.start", "refused");
                p("j6.type", e.getClass().getSimpleName());
                p("j6.message_names_job", String.valueOf(e.getMessage()).contains("j6-missing"));
            } finally {
                sql("alter table scheduled_tasks_hidden rename to scheduled_tasks");
                reg.stop();
            }
        }

        // J7 a row for a task nobody registered
        {
            sql("insert into scheduled_tasks (task_name, task_instance, execution_time, picked, version)"
                    + " values ('ghost', 'recurring', now() - interval '1 minute', false, 1)");
            var reg = base().withJob(job("j7-real", FixedDelay.ofMillis(200), ctx -> {})).build();
            p("j7.start", refused(reg::start));
            Thread.sleep(1500);
            reg.stop();
            p("j7.ghost_row", query1("select picked, consecutive_failures, version from scheduled_tasks"
                    + " where task_name = 'ghost'"));
        }

        // J8a graceful stop waits for a running job
        {
            var started = new AtomicBoolean();
            var done = new AtomicBoolean();
            var reg = base().shutdownMaxWait(Duration.ofSeconds(5))
                    .withJob(job("j8a-graceful", FixedDelay.of(Duration.ofHours(1)), ctx -> {
                        started.set(true);
                        Thread.sleep(1500);
                        done.set(true);
                    }))
                    .build();
            reg.start();
            while (!started.get()) {
                Thread.sleep(10);
            }
            var t0 = System.nanoTime();
            reg.stop();
            p("j8a.stop_elapsed_s", bucket((System.nanoTime() - t0) / 1_000_000, 500) / 1000.0);
            p("j8a.job_done_when_stop_returned", done.get());
            p("j8a.row", query1("select picked, last_success is not null from scheduled_tasks"
                    + " where task_name = 'j8a-graceful'"));
        }

        // J8b stop with a job that outlives shutdownMaxWait
        {
            var started = new AtomicBoolean();
            var done = new AtomicBoolean();
            var interrupted = new AtomicBoolean();
            var reg = base().shutdownMaxWait(Duration.ofSeconds(1))
                    .withJob(job("j8b-slow", FixedDelay.of(Duration.ofHours(1)), ctx -> {
                        started.set(true);
                        try {
                            Thread.sleep(4000);
                            done.set(true);
                        } catch (InterruptedException e) {
                            interrupted.set(true);
                        }
                    }))
                    .build();
            reg.start();
            while (!started.get()) {
                Thread.sleep(10);
            }
            var t0 = System.nanoTime();
            reg.stop();
            p("j8b.stop_elapsed_s", bucket((System.nanoTime() - t0) / 1_000_000, 500) / 1000.0);
            p("j8b.job_interrupted", interrupted.get());
            p("j8b.job_done_when_stop_returned", done.get());
            Thread.sleep(4500);
            p("j8b.job_done_later", done.get());
            p("j8b.row_after", query1("select picked, last_success is not null from scheduled_tasks"
                    + " where task_name = 'j8b-slow'"));
        }

        // J9 jobs due together run together
        {
            var running = new AtomicInteger();
            var max = new AtomicInteger();
            var jobs = new CopyOnWriteArrayList<DistributedJob>();
            for (var i = 0; i < 3; i++) {
                jobs.add(job("j9-par-" + i, FixedDelay.of(Duration.ofHours(1)), ctx -> {
                    var now = running.incrementAndGet();
                    max.accumulateAndGet(now, Math::max);
                    Thread.sleep(1500);
                    running.decrementAndGet();
                }));
            }
            var reg = base().withJobs(jobs).build();
            reg.start();
            Thread.sleep(2500);
            reg.stop();
            p("j9.max_concurrent_of_3", max.get());
        }

        // J10 heartbeat interval knob: last_heartbeat moves while a job runs
        {
            var started = new AtomicBoolean();
            var reg = base().heartbeatInterval(Duration.ofSeconds(1))
                    .withJob(job("j10-hb", FixedDelay.of(Duration.ofHours(1)), ctx -> {
                        started.set(true);
                        Thread.sleep(3500);
                    }))
                    .build();
            reg.start();
            while (!started.get()) {
                Thread.sleep(10);
            }
            Thread.sleep(300);
            var h1 = query1("select last_heartbeat from scheduled_tasks where task_name = 'j10-hb'");
            Thread.sleep(2200);
            var h2 = query1("select last_heartbeat from scheduled_tasks where task_name = 'j10-hb'");
            p("j10.picked_row_has_heartbeat", !h1.equals("null"));
            p("j10.heartbeat_moved_with_1s_interval", !h1.equals(h2));
            reg.stop();
        }

        // J11 default heartbeat (10s) and default polling (10s)
        {
            var starts = new CopyOnWriteArrayList<Long>();
            var t0 = System.nanoTime();
            var reg = jobRegistry()
                    .connectionProvider(cp)
                    .registerShutdownHook(false)
                    .withJob(job("j11-defaults", FixedDelay.ofSeconds(1), ctx -> {
                        starts.add((System.nanoTime() - t0) / 1_000_000);
                        if (starts.size() == 1) {
                            Thread.sleep(12500);
                        }
                    }))
                    .build();
            reg.start();
            Thread.sleep(1500);
            var h1 = query1("select last_heartbeat from scheduled_tasks where task_name = 'j11-defaults'");
            Thread.sleep(6000);
            var h2 = query1("select last_heartbeat from scheduled_tasks where task_name = 'j11-defaults'");
            Thread.sleep(5500);
            var h3 = query1("select last_heartbeat from scheduled_tasks where task_name = 'j11-defaults'");
            p("j11.heartbeat_unchanged_at_7s", h1.equals(h2));
            p("j11.heartbeat_changed_by_13s", !h1.equals(h3));
            Thread.sleep(12000);
            reg.stop();
            p("j11.run_starts_s", starts.stream().map(ms -> bucket(ms, 1000) / 1000).toList());
        }

        System.out.println("PROBE-DONE jobs");
    }
}
