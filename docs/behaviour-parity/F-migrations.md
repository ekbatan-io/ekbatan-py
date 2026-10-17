# F. Migrations: Flyway against the yoyo-based migrator

**Scope.** Java's `FlywayMigrator` (ekbatan-flyway, which runs Flyway 12.0.0 with its defaults,
so Flyway's own behaviour is the reference) against Python's `migrations.py` and `_yoyo.py`.
Every row below was settled by running the same scenario in both ports, against the same
databases: PostgreSQL 17, MySQL 9.4.0 and MariaDB 11.4 in three containers started by hand on
fixed ports (55432, 53306, 53307), and removed afterwards. Both ports migrated the same SQL
folders (`parity-probes/F-migrations/sql/`, built by `make-sql.sh`). Java ran as temporary JUnit
tests in a throwaway worktree (sources kept in `parity-probes/F-migrations/java/`, the extra test
dependencies in `java/build.gradle.kts.diff`); Python as scripts beside them. Each probe printed
`PROBE <key>: <value>` lines; `<probe>.java.out` and `<probe>.python.out` are the outputs and
`<probe>.compare.txt` the side-by-side. The cross-over (F6) ran both ports in turn against one
database (`cross.sh`). This scan fixes nothing.

Probes: **f1** naming and files (PG, MySQL), **f2** history and lifecycle (PG, MySQL, MariaDB),
**f3** lock (PG, MySQL), **f4** shards, logs and MDC (PG), **f5** locations, schemas, scripts (PG),
**f6** cross-over (PG, MySQL; rewritten for the fix, PG, MySQL, MariaDB).

**Parity fix M (`EKB-0277`-`EKB-0282`)** made the migrator Flyway-compatible; the rows it changed
say "fixed in" below, and [`parity-fix/M-migrations.md`](../parity-fix/M-migrations.md) has the
evidence. The probes were rerun on 57332/57306/57307; their `.python.out` files are the new runs,
and `f6_cross.*` is the new cross-over: both ports in turn and at once on one database.

## Table

| # | Behaviour | Java | Python | Verdict | Probe |
|---|---|---|---|---|---|
| 1 | Version order, gaps | 1, 1.5, 1.9, 1.10, 2, 10, 20; gaps fine | same | same | f1 accept |
| 2 | Version stored for `V1_5__x.sql` | `1.5` | `1.5` | same (fixed in EKB-0277) | f1 accept |
| 3 | Subfolders, `.SQL`, empty description `V5__.sql` | run; script column `sub/V3__x.sql` | same | same | f1 accept |
| 4 | Repeatables: after versioned, by description (`Z`, `a`, `b`) | yes | yes | same | f1 accept |
| 5 | Duplicate version (`V1`+`V1.0`, `V1`+`V01`), duplicate repeatable, same version in two locations | refused before anything runs, no history table | same (own wording) | same | f1 dup_*, f5 two_locations |
| 6 | Hidden folder `.hidden/` | skipped | skipped | same | f1 hidden |
| 7 | Misnamed `.sql` (`v2__x`, `create_stuff`, `R1__x`, `U1__x`) | skipped with one INFO line; rest applied | skipped with Flyway's INFO lines; rest applied | same (fixed in EKB-0277) | f1 lowercase_v, misnamed, repeatable_with_version, undo |
| 8 | `V1__x.sql.conf` beside a migration | read, applied | read, applied | same (fixed in EKB-0277) | f1 conf |
| 9 | `afterMigrate.sql` | run | run | same (fixed in EKB-0279) | f1 callback |
| 10 | `post-apply.sql` | ignored | ignored (a skipped name) | same (fixed in EKB-0279) | f1 post_apply |
| 11 | `V2__x.py` | **run with `python`**, recorded type `SCRIPT` | refused | differs | f1 python_file, f5 script_migration |
| 12 | File not UTF-8 | refused, nothing runs | same | same | f1 non_utf8 |
| 13 | Description over 200 characters | applied; history cut to 197 + `...` | applied; history cut to 197 + `...` | same (fixed in EKB-0277) | f1 long_description |
| 14 | Unknown placeholder `${unknown_key}` | fails, nothing applied | fails, nothing applied | same (fixed in EKB-0280) | f1 placeholder |
| 15 | Built-in placeholder `${flyway:table}` | replaced (`flyway_schema_history`) | replaced (`flyway_schema_history`) | same (fixed in EKB-0280) | f1 placeholder_builtin |
| 16 | MySQL `DELIMITER //` | applied | applied | same (fixed in EKB-0277) | f1 delimiter_mysql |
| 17 | Empty folder | warns "No migrations found", empty history table | same | same | f1 empty |
| 18 | Missing folder, or a file as location | warns, skips it, migrates nothing, succeeds | logs Flyway's ERROR line, skips it, migrates nothing, succeeds | same (fixed in EKB-0281) | f5 missing_folder, a_file_not_a_folder |
| 19 | No location given | default `classpath:db/migration` | default `classpath:db/migration` (a package `db`) | same (fixed in EKB-0281) | f5 no_location |
| 20 | `filesystem:` / `classpath:` prefixes | accepted | accepted (`classpath:` = an importable package's folder) | same (fixed in EKB-0281) | f5 filesystem_prefix, classpath_prefix |
| 21 | A plain absolute path, no prefix | read as classpath: finds nothing, succeeds with 0 applied | the folder, migrated | differs on purpose: Java's trap not copied (EKB-0281; "Noticed in the Java") | f5 plain_absolute_path |
| 22 | Blank location | refused | refused | same | f4 refuse.blank_location |
| 23 | PG `CREATE INDEX CONCURRENTLY`, no marker | run outside a transaction, then **hangs** on Flyway's own lock connection (still waiting after 30 s) | run outside a transaction, finishes; V2 recorded | differs on purpose: Java's hang not copied (EKB-0277; "Noticed in the Java") | f5 concurrent_index_no_marker |
| 24 | History table name | `flyway_schema_history` | `flyway_schema_history` | same (fixed in EKB-0277) | f2 a_first.history_tables |
| 25 | History columns | 10 columns; `checksum` int, `installed_on` timestamp | 10 columns; Flyway's DDL per dialect | same (fixed in EKB-0277) | f2 a_first.columns |
| 26 | History indexes | pk `installed_rank`, `_s_idx` on `success` | same | same | f2 a_first.indexes |
| 27 | Checksum of the same file | CRC32 `-1033942919` | CRC32 `-1033942919` | same (fixed in EKB-0277) | f2 history |
| 28 | `installed_by`, `execution_time`, `success`, repeatable row (no version, `SQL`) | db user; >= 0; true | same | same | f2 history |
| 29 | `installed_on` value | db clock in session time zone: on PG the JVM's zone (`20:23` local when UTC was `18:23`), on MySQL UTC | db clock, column default, in the session time zone | closer (EKB-0277): the column's default, as Flyway; the PostgreSQL session zone is the server's, not a JVM's | f6 a1/b1 installed_on_sample |
| 30 | Baseline row | v1, `BASELINE`, `<< Flyway Baseline >>` | v1, `BASELINE`, `<< Flyway Baseline >>` | same (fixed in EKB-0277) | f2 baseline |
| 31 | Re-run: nothing applied, no rows | yes | yes | same | f2 b_again |
| 32 | Result's target version when nothing ran | `null` | `null` | same (fixed in EKB-0278) | f2 b_again.outcome |
| 33 | Applied file edited | refused, nothing runs | same | same | f2 c_edited |
| 34 | Only line endings changed (CRLF) | accepted | accepted | same | f2 d_crlf |
| 35 | Two lines joined into one | accepted (CRC unchanged) | accepted (CRC unchanged) | same (fixed in EKB-0277) | f2 e_joined |
| 36 | Description renamed / version respelled `V001` | refused / accepted | same | same | f2 f_renamed, g_respelled |
| 37 | New file older than the newest applied | refused | refused | same | f2 h_out_of_order |
| 38 | Newest applied file gone, or a newer release's version in the history | warns, carries on | same (also in the result's warnings) | same | f2 i_missing, future |
| 39 | Repeatable changed | applied again, new row, old row kept | same | same | f2 j_repeatable_changed |
| 40 | Failing migration, PG | rolled back, no row | same | same | f2 pg k_failing |
| 41 | Failing migration, MySQL and MariaDB | half left behind, row `success=false` | same | same | f2 mysql/maria k_failing |
| 42 | Run after a failed row | refused until repair | same | same | f2 l_fixed, m_fixed_again |
| 43 | Tables but no history | refused | refused | same | f2 nonempty |
| 44 | Two migrators of one port at once | one applies, the other waits, 0 applied | same | same | f3 concurrent |
| 45 | Lock name / key | PG key `18028:4163491309`; MySQL `flyway--196644676` | PG key `18028:4163491309`; MySQL `flyway--196644676` | same (fixed in EKB-0277) | f3 locks_seen |
| 46 | Lock held elsewhere for 70 s, PG | gives up at 50 s, "Number of retries exceeded" | gives up at 50 s, "Number of retries exceeded" | same (fixed in EKB-0277) | f3 pg.held |
| 47 | Lock held elsewhere for 70 s, MySQL | waits, applies at 70 s | same | same | f3 mysql.held |
| 48 | Lock left after the run | none | none | same | f3 locks_left_after |
| 49 | Shard order, target names (`global/member-0`, `shard-3-1`, `default` at 0:0) | config order | same | same | f4 targets |
| 50 | One shard failing | first migrated, failing one stops, rest untouched | same | same | f4 fail |
| 51 | MDC keys `ekbatanShard` / `ekbatanTarget` on each line, cleared after | yes, `1:0` and `mexico/member-0` | same values, as record attributes | same | f4 *.ekbatan_lines, mdc_after |
| 52 | Start and end log lines | `Migrating X (1:0)`, `X (1:0) is at version 1 (1 applied)` | `Migrating X (1:0)`, `X (1:0) is at version 1 (1 applied)` | same (fixed in EKB-0278) | f4 again.ekbatan_lines |
| 53 | Failure log lines | ERROR `Migrating X failed`, ERROR `Stopped at X; already migrated: [...]` | ERROR `Migrating X failed`, ERROR `Stopped at X; already migrated: [...]` | same (fixed in EKB-0278) | f4 fail.ekbatan_lines |
| 54 | No targets / blank target name | refused (`IllegalStateException`, `IllegalArgumentException`) | refused (`ValidationError`) | same | f4 refuse |
| 55 | A caller's own MDC values put back after | yes | Python has no caller MDC to restore | read: no such thing in Python | - |
| 56 | PG schema in the url that does not exist | created; history row 0 `<< Flyway Schema Creation >>`, type `SCHEMA` | created; history row 0 `<< Flyway Schema Creation >>`, type `SCHEMA` | same (fixed in EKB-0282) | f5 missing_schema |
| 57 | PG schema in the url that exists | history and tables go there | same | same | f5 existing_schema |
| 58 | Java migrated, Python continues | - | continues: applies V3, validate passes | same (fixed in EKB-0277) | f6 a2 |
| 59 | Python migrated, Java continues | (before the fix: refused, "Found non-empty schema(s)") now continues, validate passes | continues: applies V4, validate passes | same (fixed in EKB-0277) | f6 b2 |
| 60 | Python baselines a Java database, then both keep going | (before the fix: ran V3 again) now no baseline is needed | no baseline needed; nothing re-applied | same (fixed in EKB-0277) | f6 a3, a4, a5 |
| 61 | Both ports at once on an empty database | (before the fix: no shared lock, whoever was first won) now one applies, the other waits | one applies, the other waits on Flyway's lock and applies nothing | same (fixed in EKB-0277) | f6 c1, c2 |

## Differences

Most surprising first.

**60, 58, 59. The ports cannot carry on each other's database.** Each looks only at its own
history table and refuses the other's tables as a schema "not empty". A Python baseline gets past
that, but then each port re-applies what the other already did; on MySQL the failed re-run leaves a
row that blocks Java until `repair`. Java: Flyway's `flyway_schema_history` (`FlywayMigrator.java:202-208`).
Python: `migrations.py:163`, `:549-570`. Could match: partly - same table name, CRC32 and a
timestamp column would make them share one history (cost: the stronger edit check, row 35). Dracula 3.
Surprise: **high**.

**61, 45. No shared lock between the ports.** Different advisory-lock keys and `GET_LOCK` names,
so a Java and a Python deploy at the same moment do not wait for each other; which one wins is luck.
Java: Flyway's lock. Python: `_yoyo.py:58-61`. Could match: yes, with Flyway's key and name (and
then also row 58). Surprise: **high**.

**11. Flyway runs a `V2__x.py` file with `python`** and records it as `SCRIPT`; Python refuses
it. Not in the Draculas as such (84 says "code migrations"). Java: Flyway's script migrations.
Python: `migrations.py:436-441`. Could match: yes but should not - refusing is safer. Surprise for
a Java developer: low (Python is stricter); for anyone relying on it: medium.

**23. `CREATE INDEX CONCURRENTLY` without a marker.** Flyway sees the statement, runs it outside a
transaction, and then waits forever on its own lock connection's open transaction (seen in
`pg_stat_activity`: waiting on `virtualxid`; killed by the probe after 30 s). Python runs it in a
transaction and fails at once; `-- transactional: false` is how it is run in Python. Java: Flyway's
PG two-connection lock. Python: `migrations.py:1319-1328`, lock note `_yoyo.py:82-90`. Could match:
Python is better here; reading the statement as Flyway does would be new work. Dracula 81.
Surprise: medium.

**53. A failed migration writes no ERROR line in Python.** Java logs `Migrating X failed` and
`Stopped at X; already migrated: [...]` at ERROR; Python says it only in the exception. An alert on
ERROR log lines stays quiet. Java: `FlywayMigrator.java:280`, `:464-467`. Python: `migrations.py:1160-1180`.
Could match: yes, two log calls. Surprise: medium.

**46. A held lock on PostgreSQL.** Flyway gives up after about 50 s; Python waits as long as it
takes. On MySQL both waited the full 70 s, so Dracula 83's "Flyway gives up after 50 s" holds for
PostgreSQL only. Java: Flyway's `lockRetryCount`. Python: `_yoyo.py:100-116`. Could match: yes, a
retry limit. Dracula 83. Surprise: medium.

**7, 8, 13, 16, 18. Python refuses what Flyway skips or allows.** Misnamed files, `.conf` files,
long descriptions, `DELIMITER`, a missing folder: Flyway skips (one INFO line) or applies; Python
stops the whole run before anything runs. Java: Flyway defaults. Python: `migrations.py:379-441`,
`:345`, `:470-485`. Could match: yes, each refusal could be dropped; chosen on purpose
(MIGRATOR-LEDGER F03, F06, F08, F10, F16, F20, M17). Dracula 82. Surprise: medium.

**9, 10. Callbacks.** `afterMigrate.sql` runs in Java and is refused in Python; `post-apply.sql`
runs in Python and is ignored in Java - the same file does different things. Python:
`migrations.py:174`, `:180`, `:417-425`. Could match: partly, by reading `afterMigrate.sql` as the
hook. Dracula 80. Surprise: medium.

**14, 15. Placeholders.** Flyway replaces `${...}` and fails on an unknown one; Python runs the text
as written, so `${unknown_key}` lands in the table. Python: none (yoyo has no placeholders). Could
match: partly, a text replace before yoyo reads the file. Dracula 84. Surprise: medium.

**19, 20, 21. Locations.** Java defaults to `classpath:db/migration` and takes `filesystem:` and
`classpath:`; Python needs a plain folder path. A plain absolute path given to Java is read as a
classpath location: nothing is found and the run "succeeds" with 0 applied - a trap on the Java
side. Java: `FlywayMigrator.java:67`, `:300-312`. Python: `migrations.py:337-346`. Could match:
partly - strip `filesystem:`, default to a package folder. Dracula 15. Surprise: medium.

**56. A schema that does not exist.** Flyway creates it and records a `SCHEMA` row at rank 0;
Python fails. Java: Flyway's `createSchemas=true`. Python: `migrations.py:573-583`. Could match:
yes, `CREATE SCHEMA IF NOT EXISTS` and the row. Surprise: medium.

**2. `V1_5` is stored as `1_5`.** Flyway stores `1.5`. Same order and duplicate checks, but the
history and `info` read differently, and it matters for row 60. Python: `migrations.py:411`.
Could match: yes, store the normalised version. Surprise: low.

**24, 25, 27. History table name, column types, checksum.** See rows 58-60. Python:
`migrations.py:218-230`, `:495-518`. Dracula 3. Surprise: high only through the cross-over.

**35. Two lines joined.** Flyway's CRC32 is over lines without their endings, so joining two lines
is not an edit to it; Python's SHA-256 sees it. Python: `migrations.py:218-230`. Could match: yes
(costs the check). Surprise: low.

**29. `installed_on`.** Flyway writes the database clock in the session's time zone, which on
PostgreSQL is the JVM's zone, so the same moment reads differently per client; Python writes UTC
text. Python: `migrations.py:647`. Could match: partly. Surprise: low.

**30. Baseline description.** `<< Flyway Baseline >>` against `<< Baseline >>`; Java reaches
baseline through `customize(c -> c.baselineOnMigrate(true))`, Python through `baseline()`.
Python: `migrations.py:166`. Could match: yes, one string. Surprise: low.

**32. Result's target version.** Java's `targetSchemaVersion` is `null` when nothing ran; Python
returns the current version. Python: `migrations.py:1276`. Could match: yes. Surprise: low.

**52. Start and end log lines.** Python has no `Migrating X` line and leaves the shard out of
`is at version`. Java: `FlywayMigrator.java:273`, `:277`. Python: `migrations.py:1187-1193`.
Could match: yes. Surprise: low.

## Totals

61 rows: 29 same, 31 differs, 1 read. Already known as Draculas (3, 15, 80, 81, 82, 83, 84): rows
7-10, 13-16, 18-21, 23-25, 27, 46, 58, 59. New here: Python and Java do not share a lock or a
history in practice (45, 60, 61), Flyway runs `.py` files (11), no ERROR log line on failure in
Python (53), a missing PostgreSQL schema (56), `1_5` stored as written (2), `installed_on` time
zone (29), the baseline description (30), the result's target version (32), the shape of the log
lines (52), the joined-lines checksum (35, known only in MIGRATOR-LEDGER), and that Flyway's
50-second give-up is PostgreSQL only (46, 47).

After parity fix M: 27 of the 31 differences are `same`; 2 differ on purpose because Java's
behaviour is a bug (rows 21, 23); row 11 (script migrations) and row 29 (the PostgreSQL session
time zone of `installed_on`) remain, as limits recorded in `DECISIONS-TO-REVIEW.md`.
