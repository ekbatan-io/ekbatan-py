# The migrator against Flyway

> **History.** This ledger settled the yoyo-based migrator (`EKB-0233`-`EKB-0235`). Parity fix M
> (`EKB-0277`-`EKB-0282`) replaced it with a Flyway-compatible one that writes Flyway's own history
> and takes Flyway's lock; [`parity-fix/M-migrations.md`](parity-fix/M-migrations.md) is the
> ledger for that, and `docs/database/migrations.md` describes what the migrator does now. The
> Flyway behaviour recorded below still holds; where a row says this migrator differs, the parity
> fix ledger says whether it still does.

Java's `FlywayMigrator` runs Flyway with its defaults and nothing else. This port's migrator runs
SQL files through yoyo. This ledger lists every behaviour of a Flyway `migrate` that a user could
notice - what runs, in what order, under which lock, on which connection, in which transaction,
what is refused, what is recorded, and how a failure is recovered from - and settles each one.

    python scripts/ledger.py docs/MIGRATOR-LEDGER.md

**How a row is settled.** Flyway's behaviour is read in its source (12.0.0, the Java port's
version) **and run**: `scripts/flyway-parity.py` runs `flyway/flyway:12.0.0` in docker against
PostgreSQL 17, MariaDB 11.4 and SQLite, step by step, and prints what it did and what it left in
the database. The scenario ids in the evidence column are that script's. Ours is run by the same
script and pinned by a test. A row is `done` only when all three exist.

**The decision on each row** is one of:

- **match** - ours does what Flyway does. The spelling may be yoyo's where yoyo has its own way
  of saying the same thing (`-- transactional: false`, `post-apply` files); the behaviour is
  Flyway's.
- **keep** - ours differs on purpose, because Flyway's default was run and found less safe. The
  row says what was run.
- **n/a** - a Flyway feature this port does not take. yoyo is used as it is, not rewritten, so a
  feature that would need a new SQL parser or a rewrite of yoyo is not taken.

Every **keep** and every judgement call is also in `DECISIONS-TO-REVIEW.md`.

**Iterations**, each ending with its tests passing and the rows it settles marked:

1. ~~Flyway run through every row below; the results written into the rows.~~
2. ~~The history table gets Flyway's columns; a failure that cannot be rolled back is recorded
   and blocks the next run until repaired.~~
3. ~~Repeatable migrations (`R__`).~~
4. ~~A database with tables but no history is refused; `baseline`.~~
5. ~~Files: subdirectories, `.SQL`, `post-apply` hooks, what yoyo cannot split.~~
6. ~~`repair` and `info`.~~
7. ~~Every scenario through both tools, side by side, as the final proof.~~ 61 scenarios: 57 the
   same in every step, in what they left in the database and in the history they wrote; the
   other 4 are this ledger's **keep** rows F06, F16, F20 and X07 (parity R19, F16, H07, X05c).
8. ~~Docs, website, `DIVERGENCE.md`, skills.~~

All done 2026-10-01: the migrator, its tests and this ledger in `EKB-0235`, the docs and the
website in `EKB-0236`.

## What Flyway was found to do

The lifecycle of one `migrate`, from `Flyway.migrate()` and `DbMigrate`:

1. Validate the files against the history - **before** any lock, ignoring pending files.
2. If there is no history table: refuse a schema that has anything in it; otherwise create the
   table (under the lock, retried ten times).
3. Then, per migration: take the lock, re-read the history, refuse if a failed migration is
   recorded, apply the **next** pending migration, record it, let go of the lock. Versioned
   migrations first, in version order; then repeatables, in order of description.
4. Run the `afterMigrate` callback - on every migrate, even when nothing was pending.

Where the history row goes: on MySQL, MariaDB and SQLite, one connection, and the row is
written in the migration's own transaction. On PostgreSQL by default, two connections: the
migration on one, the lock (a transaction-scoped advisory lock) and the row on the other.

## 1. One migrate, start to end

| aspect | lines | Flyway, and ours | status | evidence |
| --- | - | --- | --- | --- |
| M01 connections | - | Flyway: PostgreSQL uses two, MySQL and SQLite one. Ours: one everywhere. **keep** - two connections ran a migration twice when the lock's connection died | done | EKB-0233; test_when_the_holders_session_is_dropped_the_migration_still_runs_once |
| M02 checks before running | - | Flyway validates before its lock. Ours checks after taking the lock, against the history read under it. **keep** - the same checks, never against a history another process is changing | done | test_migration_concurrency: test_four_processes_apply_a_changed_repeatable_once, test_four_processes_and_a_recorded_failure_leave_one_failed_row; parity C03, C04 |
| M03 anything in the schema but no history table | - | Flyway refuses "Found non-empty schema(s) ... but no schema history table" - a table, a view, a sequence, a function. Ours: not checked. **match** | done | test_migrations: test_a_database_with_tables_but_no_history_is_refused, test_a_database_with_only_a_view_or_a_sequence_is_not_empty_either; parity M03, M03b, M03c |
| M04 creating the history table | - | Flyway creates it under its lock. Ours creates it under the lock. **match** | done | EKB-0233; test_four_processes_at_once_apply_each_migration_once |
| M05 how long the lock is held | - | Flyway: once per migration, re-reading the history each time, so four processes can split the work (parity C03-maria). Ours: once per run. **keep** - the same outcome, each migration applied once and in order, with one lock instead of many | done | test_migration_concurrency: test_four_processes_apply_a_changed_repeatable_once; parity C03 |
| M06 order in one run | - | Flyway: versioned by version, then repeatables by description, then afterMigrate. Ours: versioned only. **match**, with yoyo's `post-apply` for afterMigrate | done | test_migrations: test_repeatables_run_after_every_versioned_migration_in_description_order, test_a_post_apply_hook_runs_at_the_end_of_every_migrate; parity R01, R12, F18 |
| M07 migration and its history row | - | Flyway with one connection: one transaction. Flyway on PostgreSQL by default: two, on two connections. Ours: one transaction everywhere. **match** the one-connection case | done | EKB-0233; test_a_migration_and_its_history_row_commit_together |
| M08 a file that cannot run in a transaction | - | Flyway: `executeInTransaction=false` in a `.conf` file, or detected from the statement. Ours: yoyo's `-- transactional: false`. **match**, yoyo's spelling | done | EKB-0233; test_a_postgres_index_can_be_built_concurrently; parity R15 |
| M09 applied by a newer release | - | Flyway warns and carries on. Ours warns. **match** | done | EKB-0233; test_a_database_a_newer_release_migrated_is_left_alone; parity R16 |
| M10 a new file older than the newest applied | - | Flyway refuses: "Detected resolved migration not applied to database". Ours refuses. **match** | done | EKB-0233; test_a_migration_older_than_one_applied_is_refused_and_nothing_runs |
| M11 an applied file was edited | - | Flyway refuses: "Migration checksum mismatch". Ours refuses. **match**; ours also catches two lines joined, which Flyway's checksum misses | done | EKB-0233; test_an_edited_migration_is_refused_and_nothing_new_runs |
| M12 an applied file was renamed | - | Flyway refuses a new **description** ("Migration description mismatch"), and accepts a respelled version (`V2` to `V002`) or a move into a subdirectory without a word. Ours refuses any new file name. **match** | done | test_migrations: test_a_renamed_migration_is_refused, test_moving_or_respelling_an_applied_file_is_not_a_change; test_migration_model; parity X04c, M12b, M12c |
| M13 an applied file is gone | - | Flyway refuses: "Detected applied migration not resolved locally". Ours refuses. **match** | done | EKB-0233; test_an_applied_migration_whose_file_is_gone_is_refused |
| M14 what migrate returns | - | Flyway: versions before and after, each migration and its category, warnings. Ours: no repeatables yet. **match** | done | test_migrations: test_repeatables_run_after_every_versioned_migration_in_description_order, test_a_directory_of_only_repeatable_migrations |
| M15 what migrate logs | - | Flyway logs the current version, each migration, a summary, and "up to date". **match** in substance | done | test_migrations: test_the_log_says_what_it_did; parity R01 |
| M16 no migration files at all | - | Flyway warns "No migrations found. Are your locations set up correctly?" and carries on. **match** | done | test_migrations: test_no_migration_files_at_all_is_a_warning; parity M16 |
| M17 a location that does not exist | - | Flyway logs an error and exits 0. Ours refuses. **keep** - a typo in a path should not migrate nothing | done | EKB-0233; test_locations_are_required_and_must_exist |
| M18 what the database says beside a statement | - | Flyway logs the server's warnings and notices (parity F16: MySQL's "PROCEDURE does not exist"). yoyo drops them. **match** - logged at `WARNING` | done | test_migrations: test_the_databases_own_warnings_are_logged; parity F16, F17m |

## 2. Files

| aspect | lines | Flyway, and ours | status | evidence |
| --- | - | --- | --- | --- |
| F01 versioned names | - | `V<version>__<description>.sql`, numeric order, `V1` = `V01` = `V1.0`. **match** | done | EKB-0233; test_versions_are_ordered_as_numbers |
| F02 `.SQL` in capitals | - | Flyway runs `V2__x.SQL` and `R__x.SQL`. Ours ignores them without a word. **match** | done | test_migration_scripts: test_sql_in_capitals_is_read; test_migrations: test_migrations_in_subdirectories_and_in_capitals_run; parity F02 |
| F03 `v1__x.sql` in lower case | - | Flyway skips it, with one line saying files were skipped. Ours refuses. **keep** - a file that never runs | done | EKB-0233; test_a_sql_file_with_the_wrong_name_is_refused |
| F04 subdirectories | - | Flyway reads them, at any depth, skipping hidden ones (`.hidden/`), and records the path (`sub/V2__two.sql`). Ours does not read them, silently. **match** | done | test_migration_scripts: test_subdirectories_are_read_at_any_depth_and_hidden_ones_are_not; test_migrations: test_migrations_in_subdirectories_and_in_capitals_run; parity F04 |
| F05 two files, one version | - | Both refuse. **match** | done | EKB-0233; test_two_files_with_the_same_version_are_refused |
| F06 `R1__x.sql` | - | Flyway skips it, as a misnamed file. Ours: refuse, as any misnamed file. **keep** | done | test_migration_scripts: test_a_repeatable_with_a_version_is_refused; parity R19 |
| F07 two repeatables, one description | - | Flyway refuses: "Found more than one repeatable migration with description", listing both - `R__a_b.sql` and `R__a b.sql`, or one name in two locations. **match** | done | test_migration_scripts: test_two_repeatables_with_one_description_are_refused, test_one_repeatable_in_two_locations_is_refused; parity R21, R22 |
| F08 a misnamed `.sql` | - | Flyway skips it. Ours refuses. **keep** | done | EKB-0233; test_a_sql_file_with_the_wrong_name_is_refused |
| F09 a migration that is not SQL | - | Flyway runs Java migrations. Ours refuses `V2__x.py`. **n/a** | done | EKB-0233; test_a_migration_that_is_not_sql_is_refused |
| F10 `V1__x.sql.conf` | - | Flyway reads it, and refuses a key it does not know. Ours refuses the file, pointing at `-- transactional: false`. **keep** | done | test_migration_scripts: test_a_flyway_conf_file_is_refused; parity F10 (both refuse) |
| F11 not UTF-8 | - | Flyway refuses: "Unable to calculate checksum". Ours refuses. **match** | done | EKB-0233; test_a_file_that_is_not_utf8_is_refused; parity F11 |
| F12 byte-order mark, CRLF | - | Neither counts it as an edit. **match** | done | EKB-0233; test_a_different_line_ending_is_not_an_edit; parity R05 |
| F13 `${...}` placeholders | - | Flyway replaces them and fails on an unknown one. Ours runs the text as written. **n/a** | n/a | EKB-0233 comparison row 17 |
| F14 undo and rollback files | - | Flyway: paid edition. Ours refuses `.rollback.sql`. **n/a** | done | EKB-0233; test_a_rollback_file_is_refused |
| F15 `-- depends:` | - | Flyway has none. Ours refuses it. **keep** | done | EKB-0233; test_depends_is_refused |
| F16 MySQL `DELIMITER` | - | Flyway understands it. yoyo glues it onto the next statement, which fails after the statements before it ran. Ours refuses the file before anything runs; a procedure works without it (F17). **keep** | done | test_migration_scripts: test_delimiter_is_refused; parity F16 |
| F17 bodies with `;` inside | - | PostgreSQL `$$`, a MySQL procedure without `DELIMITER` and a SQLite trigger each stay one statement, in Flyway and in yoyo. **match** | done | test_migration_scripts: test_a_body_with_semicolons_inside_stays_one_statement; test_migrations: test_bodies_with_semicolons_inside_run_whole; parity F17p, F17m, F17s |
| F18 afterMigrate and yoyo's `post-apply` | - | Flyway runs `afterMigrate.sql` at the end of every migrate, even when nothing was pending. yoyo runs `post-apply` files only when something was applied, so one that failed is never retried. Ours refuses them as misnamed. **match** Flyway's timing, yoyo's name | done | test_migration_scripts: test_post_apply_files_are_hooks_not_migrations; test_migrations: test_a_post_apply_hook_runs_at_the_end_of_every_migrate; parity F18 |
| F18b afterMigrate fails | - | Flyway: migrate fails, what was applied stays recorded, and the next migrate runs the fixed callback. **match** | done | test_migrations: test_a_failing_hook_fails_the_migrate_and_runs_again_next_time; parity F18b |
| F19 an empty description | - | Flyway accepts `V2__.sql` and `R__.sql`, with an empty description. Ours refuses them as misnamed. **match** | done | test_migration_scripts: test_an_empty_description_is_accepted; parity R20 |
| F20 a description over 200 characters | - | Flyway cuts it to 197 and `...` in the history, and then cannot match a repeatable to its row: the next migrate fails with "not resolved locally". Ours: refuse the file up front. **keep** - Flyway's is a bug | done | test_migration_scripts: test_a_description_longer_than_the_history_keeps_is_refused; parity H07 |

## 3. Repeatable migrations

| aspect | lines | Flyway, and ours | status | evidence |
| --- | - | --- | --- | --- |
| R01 when they run | - | After every pending versioned migration, in order of description compared as Java compares strings: capitals first, `a` before `a b` before `a-b` before `ab`. | done | test_migrations: test_repeatables_run_after_every_versioned_migration_in_description_order; test_migration_scripts: test_repeatables_come_after_versioned_ones_in_javas_order, test_descriptions_compare_by_utf16_unit_as_java_compares_them; parity R01 on all three |
| R02 first run | - | Applied; one row with no version and type `SQL`. | done | test_migrations: test_every_application_is_ranked_in_the_order_it_happened; parity R01 |
| R03 unchanged | - | Not applied again: "up to date". | done | test_migrations: test_a_repeatable_runs_again_only_when_its_file_changes; parity R03 |
| R04 changed | - | Applied again; a new row; the old row stays and shows as superseded. | done | test_migrations: test_a_repeatable_runs_again_only_when_its_file_changes; test_migration_model: test_a_changed_repeatable_migration_runs_again_and_the_old_row_is_superseded; parity R04 on all three |
| R05 only line endings changed | - | Not applied again. | done | test_migrations: test_a_line_ending_change_does_not_run_a_repeatable_again; parity R05 |
| R06 changed back, A to B to A | - | Applied three times: only the newest row counts. | done | test_migrations: test_a_repeatable_changed_back_runs_again; parity R06 |
| R07 file deleted | - | Refused: "Detected applied migration not resolved locally: x. If you removed this migration intentionally, run repair to mark the migration as deleted." | done | test_migrations: test_a_repeatable_whose_file_is_gone_is_refused_until_repair_marks_it_deleted; parity R07 |
| R08 file renamed | - | The old description is missing, so refused as R07; the new one waits as pending. | done | test_migration_model: test_a_repeatable_whose_file_is_gone_is_refused; parity R08 |
| R09 fails where DDL is transactional | - | Rolled back, no row; applied when fixed. | done | test_migrations: test_a_failing_repeatable; parity R09-pg, R09-sqlite |
| R10 fails on MariaDB | - | A failed row; the next migrate refuses "Detected failed repeatable migration"; after repair it runs again. | done | test_migrations: test_a_failing_repeatable; parity R10 |
| R11 only repeatables | - | Applied; no version to report. | done | test_migrations: test_a_directory_of_only_repeatable_migrations; parity R11 |
| R12 with a pending versioned migration | - | The versioned one first. | done | test_migrations: test_repeatables_run_after_every_versioned_migration_in_description_order; parity R12 |
| R13 a versioned migration fails | - | Repeatables do not run. | done | test_migrations: test_repeatables_do_not_run_when_a_versioned_migration_fails; parity R13 |
| R14 two repeatables, one changed | - | Only the changed one runs. | done | test_migrations: test_a_repeatable_runs_again_only_when_its_file_changes; parity R14 |
| R15 outside a transaction | - | Runs outside one, recorded after. | done | test_migrations: test_a_repeatable_can_run_outside_a_transaction; parity R15 |
| R16 an older release starts after a newer one | - | The older content is applied again, over the newer, with a warning about the newer version. Flyway's behaviour; said plainly in the docs. | done | test_migrations: test_an_older_release_applies_its_own_repeatable_again; parity R16 |
| R17 in the result | - | Listed, with no version. | done | test_migrations: test_a_repeatable_runs_again_only_when_its_file_changes |
| R18 a newer release added a repeatable, an older one starts | - | Refused as missing (R07): the older release does not start until repaired. Flyway's behaviour; said plainly in the docs. | done | test_migration_model: test_a_repeatable_a_newer_release_added_stops_an_older_one; parity R23 |

## 4. Failure and recovery

| aspect | lines | Flyway, and ours | status | evidence |
| --- | - | --- | --- | --- |
| X01 fails where DDL is transactional | - | Rolled back, nothing recorded, "Changes successfully rolled back". **match** | done | EKB-0233; test_a_migration_and_its_history_row_commit_together; parity R09 |
| X02 fails where it cannot be rolled back | - | Flyway records a failed row: MySQL and MariaDB always, and any database for a file outside a transaction. Ours records nothing and runs it again next time - on MySQL, over what it half did. **match** | done | test_migrations: test_a_half_applied_migration_is_recorded_and_blocks_until_repaired, test_a_failure_outside_a_transaction_is_recorded_on_every_dialect; parity X02, X02b |
| X03 the run after a failed row | - | Flyway refuses "Detected failed migration to version 2 (half). Please remove any half-completed changes then run repair", even once the file is fixed. **match** | done | test_migrations: test_a_half_applied_migration_is_recorded_and_blocks_until_repaired; test_migration_model: test_a_failed_migration_is_refused_until_repaired; parity X02 |
| X04 a failed migration a newer release recorded | - | Flyway warns "contains a failed future migration" and carries on. **match** | done | test_migrations: test_a_failure_a_newer_release_recorded_does_not_stop_an_older_one; parity X02c |
| X05 repair | - | Flyway removes failed rows; updates the checksum and description of an applied file that changed; marks an applied file that is gone as deleted with a `DELETE` row, so a file that later comes back is refused as out of order, and a repeatable that comes back runs again. With nothing to do it does nothing. **match** | done | test_migrations: test_repair_records_what_an_edited_or_renamed_file_now_says, test_repair_marks_a_gone_migration_deleted_and_it_cannot_come_back, test_repair_with_nothing_to_repair_changes_nothing, test_repair_leaves_what_a_newer_release_applied_alone; parity X04a, X04b, X04c, X04d, R24 |
| X06 baseline | - | Flyway records a `BASELINE` row at version 1 (or the one given); files at or below it never run. Refused once anything is recorded. **match** | done | test_migrations: test_baseline_records_what_is_there_and_migrate_carries_on_from_it, test_baseline_is_refused_where_it_would_skip_or_rewrite_history; parity X05, X05b, X05d |
| X07 baseline an empty database | - | Flyway baselines it, and then its first migration never runs - the next one fails (parity X05c). Ours: refuse; there is nothing to baseline. **keep** | done | test_migrations: test_baseline_is_refused_where_it_would_skip_or_rewrite_history; parity X05c |
| X08 info | - | Flyway: each migration with its state - Pending, Success, Failed, Superseded, Missing, Deleted, Baseline, Below Baseline, Ignored (Baseline), Failed (Future). **match** | done | test_migrations: test_info_says_where_each_migration_stands_and_changes_nothing; test_migration_model; parity R04, R06, R07, X02, X04b, X05 |
| X09 connection lost during a migration | - | Ours says so; rolled back where DDL is transactional. No failed row can be written without a connection - true of Flyway too. | done | EKB-0233; test_a_lost_connection_is_said_out_loud |
| X10 the migrating process is killed | - | Ours: the database lets go of the lock, and the next run applies the migration once. | done | EKB-0233; test_a_process_killed_mid_migration_does_not_block_the_next |

## 5. Several processes at once

| aspect | lines | Flyway, and ours | status | evidence |
| --- | - | --- | --- | --- |
| C01 lock mechanics | - | Flyway: PostgreSQL transaction lock, gives up after about 50s; MySQL `GET_LOCK`; SQLite none. Ours: a session lock on the one connection, waited on as long as it takes. **keep** | done | EKB-0233 comparison rows 21 to 26 |
| C02 many processes, fresh database | - | Each migration once. **match** | done | test_four_processes_at_once_apply_each_migration_once |
| C03 many processes, a changed repeatable | - | Flyway: applied once. **match** | done | test_migration_concurrency: test_four_processes_apply_a_changed_repeatable_once; parity C03 |
| C04 many processes, a failure that is recorded | - | Flyway: one failed row; the others refuse "contains a failed migration". **match** | done | test_migration_concurrency: test_four_processes_and_a_recorded_failure_leave_one_failed_row; parity C04 |
| C05 lock per database | - | Two shards on one server do not wait on each other. | done | test_a_database_whose_lock_is_held_waits_and_another_database_does_not |

## 6. The history table

| aspect | lines | Flyway, and ours | status | evidence |
| --- | - | --- | --- | --- |
| H01 name | - | `ekbatan_schema_history`; Flyway's is `flyway_schema_history`. **keep** - its checksums are not Flyway's | done | EKB-0233 |
| H02 columns | - | Flyway: installed_rank, version (empty for a repeatable), description, type, script, checksum, installed_by, installed_on, execution_time, success. **match**; the checksum stays sha256 | done | test_migrations: test_the_history_records_what_each_file_contained; parity R01 |
| H03 installed_rank | - | One more than the last. After repair removes a failed row the rank is used again. **match** | done | test_migrations: test_every_application_is_ranked_in_the_order_it_happened; parity R10 |
| H04 installed_by | - | The database user; empty on SQLite. **match** | done | test_migrations: test_the_history_records_what_each_file_contained |
| H05 installed_on | - | Flyway: the database's clock, in a timestamp column. Ours: text, UTC, from the application. **keep** - it reads the same on every dialect | done | test_migrations: test_the_history_records_what_each_file_contained |
| H06 a table from an earlier build | - | Refused, saying what to do. | done | test_migrations: test_a_history_table_from_an_earlier_build_is_refused |
| H07 the script column | - | The path inside the location, `sub/V2__two.sql`; not updated by repair. **match** | done | test_migrations: test_migrations_in_subdirectories_and_in_capitals_run, test_repair_records_what_an_edited_or_renamed_file_now_says; parity F04, X04c |

## 7. Not taken

| aspect | lines | why | status | evidence |
| --- | - | --- | --- | --- |
| N01 placeholders | - | yoyo has none; it would mean rewriting the text of every file before yoyo reads it | n/a | |
| N02 callbacks other than afterMigrate | - | yoyo has only `post-apply` | n/a | |
| N03 undo | - | paid edition of Flyway | n/a | |
| N04 target, cherry-pick | - | Java's migrator does not set them | n/a | |
| N05 Java migrations | - | there is no Java | n/a | |
| N06 clean | - | disabled by default in Flyway | n/a | |
| N07 group, mixed, outOfOrder, skipExecutingMigrations, baselineOnMigrate | - | off by default, not set by Java's migrator | n/a | |
| N08 installedBy, schemas, createSchemas, table name, encoding | - | not set by Java's migrator | n/a | |
