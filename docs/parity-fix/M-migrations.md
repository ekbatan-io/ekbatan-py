# Parity fix group M: migrations

Items 13, 14, 15, 16, 37, 38, 39, 40, 41, 42, 43, 44 of the importance table in
[`PARITY-FIX-LEDGER.md`](../PARITY-FIX-LEDGER.md). Scan rows in
[`behaviour-parity/F-migrations.md`](../behaviour-parity/F-migrations.md). Commits `EKB-0277`..`EKB-0289`.

**The goal, in one line:** a Java service (Ekbatan's Flyway wrapper) and a Python service migrate
the same database, in either order, taking turns, at the same time. So the Python migrator reads
and writes what Flyway 12 reads and writes. yoyo could not be bent that far - it splits
statements its own way, has no placeholders and cannot tell which statements refuse a
transaction - so it is gone; the migrator ports Flyway's classes instead.

How each row was proven: a test that fails on the old code (the yoyo migrator at `33b249e`), the
scan's probe rerun in both ports on PostgreSQL 17, MySQL 9.4.0 and MariaDB 11.4 (containers on
57332/57306/57307, `PARITY_F_*_PORT`), and the cross-over (`parity-probes/F-migrations/cross.sh`,
`f6_cross.*`): both ports against one database, in turn and at once.

| Item | Scan rows | Decision | State | Commit | Evidence |
|---|---|---|---|---|---|
| 13, 14, 15 (+78) | 2, 24, 25, 27, 29, 30, 35, 58-60 | Flyway's `flyway_schema_history`: its DDL per dialect, ranks, version text (`V1_5` is `1.5`), CRC32 checksum over lines, `installed_on` from the database clock, baseline row `<< Flyway Baseline >>`. `ekbatan_schema_history` dropped, no shim | done | `EKB-0277` | `test_the_history_table_is_flyways_column_for_column` (PG/MySQL/MariaDB), `test_the_checksum_is_flyways_number_for_the_same_file`, `test_the_version_is_written_as_flyway_writes_it` fail on the old code; f2 history/columns/indexes keys all `=`; cross-over: 51 mirrored keys `=` on three servers, validate OK in both ports, nothing re-applied |
| 16 | 45, 61 | Flyway's lock: PG advisory key `0x466C79776179 + hashCode("\"schema\".\"flyway_schema_history\"")` (session form), MySQL `GET_LOCK('Flyway-<hashCode>')` | done | `EKB-0277` | `test_the_postgres_lock_key_is_flyways` (18028:4163491309, as pg_locks showed for Java), `test_the_mysql_lock_name_is_flyways`; `test_a_database_whose_lock_is_held_waits...` holds Flyway's key; cross-over C: both ports at once, Java waited 4 s and applied 0, Python applied 1, on three servers |
| 38 | 46 | PostgreSQL gives up after `lock_retry_count` retries a second apart (50), with Flyway's message; MySQL waits | done | `EKB-0277` | `test_postgres_gives_up_on_a_held_lock_after_lock_retry_count_as_flyway_does` fails on the old code (waits forever); cross-over E shows Java's own give-up line |
| 44 | 23 | Flyway's parser decides: a migration with `CREATE INDEX CONCURRENTLY` (and the rest of Flyway's list) runs outside a transaction, no marker; on the lock's own connection, so it never waits on itself. `-- transactional: false` gone (Dracula 81) | done | `EKB-0277` | `test_a_postgres_index_built_concurrently_runs_outside_a_transaction_by_itself` fails on the old code (PostgreSQL refused it); cross-over E: Python built the index while Java waited. Java's hang recorded under "Noticed in the Java" |
| 39 | 7, 8, 13, 16 | Skip what Flyway skips (misnamed, `R1__`, `U1__`) with its two INFO lines; read `.sql.conf` (`executeInTransaction`, `encoding`, `placeholderReplacement`); cut long descriptions to 197 + `...`; `DELIMITER` read by Flyway's MySQL parser | doing | `EKB-0277` | `test_a_sql_file_flyway_cannot_read_is_skipped_with_flyways_log_line`, `test_a_conf_file_beside_a_migration_is_read`, `test_mysql_delimiter_is_read_as_flyway_reads_it`, `test_a_long_description_is_accepted...` fail on the old code; f1 rows `lowercase_v`, `misnamed`, `repeatable_with_version`, `undo`, `conf`, `long_description`, `delimiter_mysql` now `=` |
| 37 (+84) | 52, 53, 32 | Java's lines: `Migrating X`, `X (s:m) is at version V (n applied)`, ERROR `Migrating X failed`, ERROR `Stopped at X; already migrated: [...]` (not for the single-database call, as Java). Files read inside the first target's context, as Flyway reads them inside Java's. Flyway's own lines under its class names (`DbMigrate`, `DbValidate`, ...), MDC on them too; Flyway's future-version warnings word for word; `target_schema_version` empty when nothing ran and the result's `warnings` empty, as Flyway's | done | `EKB-0278` | `test_a_failing_shard_logs_javas_error_lines`, `test_running_again_says_where_each_target_is`, `test_one_database_outside_a_layout_logs_no_stopped_at_line` fail on `EKB-0277`; f4 `fail.ekbatan_lines`, `again.ekbatan_lines`, `single.ekbatan_lines`, `*.flyway_lines_present`, `*.flyway_lines_mdc` now `=` (18 of 22; the 4 left are exception class names) |
| 40 | 9, 10 | Flyway's SQL callbacks by file name, run as Flyway runs them; `post-apply*.sql` dropped | todo | | |
| 41 | 14, 15 | Flyway's placeholders, built-ins, errors, prefix/suffix, switch; through `customize`, as Java | todo | | |
| 42 | 18-21 | `filesystem:` and `classpath:` (importable package resources); default `classpath:db/migration`; a missing folder warns and is skipped | todo | | |
| 43 | 56 | `createSchemas`: a schema that is not there is created, with Flyway's rank-0 `SCHEMA` row | todo | | |
