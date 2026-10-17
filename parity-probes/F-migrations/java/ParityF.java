package io.ekbatan.flyway;

import io.ekbatan.core.config.DataSourceConfig;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.logging.Handler;
import java.util.logging.Level;
import java.util.logging.LogRecord;
import java.util.logging.Logger;
import org.slf4j.MDC;

/** PARITY-PROBE helpers (temporary). Mirrors parity-probes/F-migrations/common.py. */
final class ParityF {

    static final String SQL = "/Users/mike/workspace/ekbatan-py/parity-probes/F-migrations/sql/";
    static final String PW = "parity-pw-F";

    private ParityF() {}

    static void probe(String key, Object value) {
        System.out.println("PROBE " + key + ": " + value);
        System.out.flush();
    }

    static String user(String dialect) {
        return dialect.equals("pg") ? "postgres" : "root";
    }

    static String url(String dialect, String db) {
        return switch (dialect) {
            case "pg" -> "jdbc:postgresql://localhost:55432/" + db;
            case "mysql" -> "jdbc:mysql://localhost:53306/" + db;
            case "maria" -> "jdbc:mariadb://localhost:53307/" + db;
            default -> throw new IllegalArgumentException(dialect);
        };
    }

    static DataSourceConfig cfg(String dialect, String db) {
        return DataSourceConfig.Builder.dataSourceConfig()
                .jdbcUrl(url(dialect, db))
                .username(user(dialect))
                .password(PW)
                .build();
    }

    static Connection connect(String dialect, String db) throws Exception {
        return DriverManager.getConnection(url(dialect, db), user(dialect), PW);
    }

    static Connection admin(String dialect) throws Exception {
        return connect(dialect, dialect.equals("pg") ? "postgres" : "");
    }

    static void fresh(String dialect, String db) throws Exception {
        try (var c = admin(dialect);
                var s = c.createStatement()) {
            if (dialect.equals("pg")) {
                s.execute("DROP DATABASE IF EXISTS " + db + " WITH (FORCE)");
            } else {
                s.execute("DROP DATABASE IF EXISTS " + db);
            }
            s.execute("CREATE DATABASE " + db);
        }
    }

    static void exec(String dialect, String db, String sql) throws Exception {
        try (var c = connect(dialect, db);
                var s = c.createStatement()) {
            s.execute(sql);
        }
    }

    static List<String> query(String dialect, String db, String sql) throws Exception {
        List<String> out = new ArrayList<>();
        try (var c = connect(dialect, db);
                var s = c.createStatement();
                ResultSet rs = s.executeQuery(sql)) {
            int n = rs.getMetaData().getColumnCount();
            while (rs.next()) {
                var row = new StringBuilder();
                for (int i = 1; i <= n; i++) {
                    if (i > 1) {
                        row.append('|');
                    }
                    row.append(rs.getString(i));
                }
                out.add(row.toString());
            }
        }
        return out;
    }

    static final String HISTORY = "flyway_schema_history";

    /** The tables in the database, the history table left out. */
    static List<String> tables(String dialect, String db) throws Exception {
        String schema = dialect.equals("pg") ? "current_schema()" : "DATABASE()";
        return query(
                dialect,
                db,
                "SELECT table_name FROM information_schema.tables WHERE table_schema = " + schema
                        + " AND table_name NOT LIKE '%schema_history' ORDER BY table_name");
    }

    static boolean hasHistory(String dialect, String db) throws Exception {
        return hasHistory(dialect, db, HISTORY);
    }

    static boolean hasHistory(String dialect, String db, String table) throws Exception {
        String schema = dialect.equals("pg") ? "current_schema()" : "DATABASE()";
        return !query(
                        dialect,
                        db,
                        "SELECT 1 FROM information_schema.tables WHERE table_schema = " + schema
                                + " AND table_name = '" + table + "'")
                .isEmpty();
    }

    /** Each history row, the columns that do not vary between runs; time columns reduced. */
    static List<String> history(String dialect, String db) throws Exception {
        return history(dialect, db, HISTORY);
    }

    static List<String> history(String dialect, String db, String table) throws Exception {
        if (!hasHistory(dialect, db, table)) {
            return List.of("<no history table>");
        }
        return query(
                dialect,
                db,
                "SELECT installed_rank, version, description, type, script, checksum, installed_by, "
                        + "CASE WHEN execution_time >= 0 THEN 'time>=0' ELSE 'time<0' END, "
                        + "CASE WHEN success THEN 'true' ELSE 'false' END"
                        + " FROM " + table + " ORDER BY installed_rank");
    }

    static String columns(String dialect, String db) throws Exception {
        String schema = dialect.equals("pg") ? "current_schema()" : "DATABASE()";
        return String.join(
                ", ",
                query(
                        dialect,
                        db,
                        "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE"
                                + " table_schema = " + schema + " AND table_name = '" + HISTORY
                                + "' ORDER BY ordinal_position"));
    }

    static String msg(Throwable e) {
        var m = String.valueOf(e.getMessage()).replaceAll("\\s+", " ").trim();
        if (m.length() > 400) {
            m = m.substring(0, 400) + "...";
        }
        return e.getClass().getSimpleName() + ": " + m;
    }

    /** Captures log records - WARN and above from Flyway, everything from the migrator - with MDC. */
    static final class Captured implements AutoCloseable {
        final List<String> lines = new CopyOnWriteArrayList<>();
        private final Handler handler;
        private final List<Logger> loggers = List.of(Logger.getLogger("org.flywaydb"), Logger.getLogger("io.ekbatan"));

        Captured() {
            handler = new Handler() {
                @Override
                public void publish(LogRecord r) {
                    var text = r.getMessage();
                    if (r.getParameters() != null) {
                        text = java.text.MessageFormat.format(text.replace("'", "''"), r.getParameters());
                    }
                    lines.add(r.getLevel() + "|" + r.getLoggerName() + "|" + MDC.get(FlywayMigrator.MDC_SHARD) + "|"
                            + MDC.get(FlywayMigrator.MDC_TARGET) + "|" + text.replaceAll("\\s+", " "));
                }

                @Override
                public void flush() {}

                @Override
                public void close() {}
            };
            handler.setLevel(Level.ALL);
            for (var l : loggers) {
                l.setLevel(Level.ALL);
                l.addHandler(handler);
            }
        }

        List<String> warnings() {
            return lines.stream()
                    .filter(l -> l.startsWith("WARNING|") || l.startsWith("SEVERE|"))
                    .map(l -> l.substring(l.lastIndexOf('|') + 1))
                    .toList();
        }

        List<String> ekbatan() {
            return lines.stream().filter(l -> l.contains("|io.ekbatan.")).toList();
        }

        @Override
        public void close() {
            for (var l : loggers) {
                l.removeHandler(handler);
            }
        }
    }
}
