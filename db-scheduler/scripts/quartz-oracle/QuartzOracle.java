import com.cronutils.model.Cron;
import com.cronutils.model.CronType;
import com.cronutils.model.definition.CronDefinitionBuilder;
import com.cronutils.model.time.ExecutionTime;
import com.cronutils.parser.CronParser;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Optional;

/**
 * Reads lines "pattern TAB zone TAB start(ISO offset) TAB count" - the pattern escaped, a tab as
 * backslash-t and a backslash as two - and answers each with one line:
 * "OK TAB t1 TAB t2 ..." (each the next execution after the one before, as db-scheduler chains
 * them), "END TAB t1 ..." when cron-utils runs out of times, "ERR TAB class TAB message" when it refuses
 * the pattern, or "RUNERR TAB class: message TAB t1 ..." when it throws while computing a time
 * (the times are the ones it gave before that).
 * Exactly what db-scheduler's CronSchedule does for CronStyle.QUARTZ.
 */
public class QuartzOracle {
  public static void main(String[] args) throws Exception {
    CronParser parser = new CronParser(CronDefinitionBuilder.instanceDefinitionFor(CronType.QUARTZ));
    BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
    PrintStream out = new PrintStream(System.out, false, StandardCharsets.UTF_8);
    String line;
    while ((line = in.readLine()) != null) {
      String[] f = line.split("\t", -1);
      StringBuilder sb = new StringBuilder();
      ExecutionTime et;
      try {
        Cron cron = parser.parse(unescape(f[0]));
        et = ExecutionTime.forCron(cron);
      } catch (Throwable e) {
        out.println("ERR\t" + e.getClass().getSimpleName() + "\t" + clean(e.getMessage()));
        continue;
      }
      ZonedDateTime t = OffsetDateTime.parse(f[2]).atZoneSameInstant(ZoneId.of(f[1]));
      int n = Integer.parseInt(f[3]);
      String status = "OK";
      try {
        for (int i = 0; i < n; i++) {
          Optional<ZonedDateTime> next = et.nextExecution(t);
          if (next.isEmpty()) {
            status = "END";
            break;
          }
          t = next.get();
          sb.append('\t').append(t.format(DateTimeFormatter.ISO_OFFSET_DATE_TIME));
        }
      } catch (Throwable e) {
        status = "RUNERR\t" + e.getClass().getSimpleName() + ": " + clean(e.getMessage());
      }
      out.println(status + sb);
    }
    out.flush();
  }

  /** The pattern travels escaped, so that it can hold tabs: backslash-t, backslash-backslash. */
  private static String unescape(String text) {
    StringBuilder out = new StringBuilder();
    for (int i = 0; i < text.length(); i++) {
      char c = text.charAt(i);
      if (c == '\\' && i + 1 < text.length()) {
        char next = text.charAt(++i);
        out.append(next == 't' ? '\t' : next == 'n' ? '\n' : next);
      } else {
        out.append(c);
      }
    }
    return out.toString();
  }

  private static String clean(String message) {
    return String.valueOf(message).replace('\t', ' ').replace('\n', ' ');
  }
}
