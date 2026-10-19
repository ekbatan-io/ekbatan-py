# Time as is - ledger

Asked 2026-10-19 (the user's "very big bad dracula"): the persistence layer writes and reads
exactly the time it is given - no automatic shifting, stamping or normalising. The default
`SystemClock().now()` returns the current UTC time with no zone attached. UTC everywhere stays a
strong recommendation; getting time right is the user's job.

Commits from `EKB-0384`. Judgement calls: `docs/DECISIONS-TO-REVIEW.md#time-as-is-ekb-0384-`.

## Baseline (before any change, HEAD 1e63c14)

- unit: 1283 passed, 4 xfailed
- `--dialects=all`: 5196 passed, 143 skipped, 4 xfailed, 8 failed - the 8 are MySQL schemagen tests that start a subprocess, which imported `src/` while it was being edited mid-run (an artefact of the run, not of the code); they pass after the change

## Places that converted (found by grep + reading; see "after" column)

| # | where | did | now |
| --- | --- | --- | --- |
| 1 | `schema.py` `_UtcTimestamp` / `timestamp_column()` | bind: aware -> UTC naive, naive taken as UTC; result: stamped `+00:00` | column type only: no bind/result step; `DATETIME(6)` on MySQL/MariaDB kept |
| 2 | `repository.py` `_writer_for` / `_reader_for` / `_to_column` | datetime fields `to_storage` on write, `to_instant` on read | datetime is verbatim both ways |
| 3 | `domain.py` `Model.__post_init__` | `to_instant` on created/updated date | kept as given; `updated_date` falls back to `created_date` |
| 4 | `action/outbox.py` `EventEntity` | `to_instant` in `__post_init__`, `to_storage` in `to_row` | kept as given both ways |
| 5 | `events/notification.py` `EventNotification` | same | kept as given both ways |
| 6 | `events/repository.py` `find_due`, `_mark` | `to_storage` on bound times | bound as given |
| 7 | `events/dispatch.py` (4 places), `events/fanout.py` | `to_instant(clock.now())` | `clock.now()` as given |
| 8 | `action/executor.py`, `aio/executor.py` | `to_instant(clock.now())` for started/completed | `clock.now()` as given |
| 9 | `streaming.py` `ActionEvent` post-init | stamped UTC on zone-less CDC values | removed: values kept as given |
| 10 | `streaming.py` epoch-micros decoding | `fromtimestamp(v / 1e6, tz=UTC)` (aware, float) | naive `epoch + timedelta(microseconds=n)`, exact |
| 11 | `_json.py` `_datetime` | `astimezone(UTC)` on a zero-offset value | written as given (no `astimezone`) |
| 12 | `clock.py` | `SystemClock` aware UTC; `VirtualClock` `_as_utc` on given instants; `to_instant`, `to_storage`, `_as_utc` | `SystemClock` naive UTC (`_utc_now`); `VirtualClock` keeps instants as given; `to_instant`/`to_storage`/`_as_utc` removed |
| 13 | `sharding.py` `uuid_instant` | aware UTC from the id's milliseconds | naive UTC (`epoch + timedelta(milliseconds=n)`) |
| 14 | `migrations.py` `installed_on` | `datetime.now(UTC)` text stamp in the history table | left as is, allow-listed in the guard (not user time) |
| 15 | `db-scheduler/` repository `_to_column` / `_aware`, clock `_aware` | aware -> UTC naive on write, naive stamped UTC on read | `_bind` returns the value; reads kept as the driver returns them; `SystemClock` naive UTC; `SettableClock` keeps what it is given; task data `isoformat()` as given; token counts from the value's own epoch. Schedules keep zone arithmetic (naive read as UTC, answer in the asked kind) |

## Steps

| step | state | commit |
| --- | --- | --- |
| baseline runs | done | |
| library change (1-13), tests rewritten, examples | done; `--dialects=all` 5224 passed, 0 failed | EKB-0384 |
| docs, website, AGENTS.md ("Time: the framework never converts it", "What you give is what is stored") | done; `make lint` green | EKB-0385 |
| layer tests strengthened (exact object at the outbox insert and the dispatch outcome) | done | EKB-0386 |
| red/green proof: 11 conversions reintroduced one at a time, each turned behavioural tests red (guard excluded); the guard also fires on all 11 | done, see below | |
| db-scheduler: same rule; suite 2340 -> 2371 passed, 0 failed, all four databases, both shapes; zoned PostgreSQL tests given an aware clock | done | EKB-0387 |
| examples | | |
| full verification | | |

## Red/green proof (sqlite + postgres, sync + async, guard test excluded)

Each row: one conversion put back into `src/`, the time tests run, the file restored.

- **clock: SystemClock aware UTC** - red: test_a_clock_with_no_instant_reads_the_wall_clock, test_a_virtual_clock_in_real_time_reads_as_the_system_clock_does, test_resume_returns_to_the_wall_clock, test_the_system_clock_is_utc_with_no_zone_attached
- **model: created_date stamped UTC** - red: test_a_models_timestamps_are_kept_exactly_as_given, test_a_naive_time_reaches_the_driver_as_it_is_and_comes_back_equal, test_an_action_writes_the_clocks_own_value_to_the_outbox, test_an_aware_clock_reaches_the_driver_unchanged, test_an_aware_time_is_handed_to_the_driver_unchanged, ERROR    io.ekbatan.core.action.ActionExecutor:executor.py:316 CreateWalletAction failed: DBAPIError: (sqlalch
- **repository mapping: to_storage on write** - red: test_the_by_name_mapping_hands_a_time_on_as_it_is
- **timestamp_column: stamps UTC on read** - red: test_a_naive_time_reaches_the_driver_as_it_is_and_comes_back_equal, test_a_retry_time_is_the_clocks_value_plus_the_backoff_as_it_is, test_an_action_writes_the_clocks_own_value_to_the_outbox, test_an_aware_time_is_handed_to_the_driver_unchanged, test_fan_out_and_dispatch_write_the_clocks_own_value, test_the_outbox_columns_round_trip_a_naive_time_to_the_microsecond, test_the_timestamp_column
- **outbox row: to_storage in to_row** - red: test_an_outbox_row_keeps_its_times_as_given
- **notification row: stamps UTC** - red: test_a_notification_keeps_its_times_as_given, test_fan_out_and_dispatch_write_the_clocks_own_value
- **event JSON: astimezone(UTC)** - red: test_event_json_writes_a_time_as_it_is
- **CDC rows: stamped UTC** - red: test_a_cdc_row_keeps_its_times_as_given
- **dispatch: to_storage on updated_date** - red: test_fan_out_and_dispatch_write_the_clocks_own_value
- **executor: to_instant(clock.now())** - red: test_an_action_writes_the_clocks_own_value_to_the_outbox
- **async executor: to_instant(clock.now())** - red: test_an_action_writes_the_clocks_own_value_to_the_outbox, ERROR    io.ekbatan.core.action.ActionExecutor:executor.py:316 CreateWalletAction failed: DBAPIError: (sqlalchemy.dialects.postgresql.asyncpg.Error) <class 'asyncpg.exceptions.DataError'>: invalid input for query argument $6: datetime.datetime(2026, 1, 1, 10, 0, 0, ... (can't subtract offset-naive and offset-aware datetimes)

With the guard (`tests/unit/test_time_as_is.py`) included it is red on every one of the 11 too.

## Driver behaviour measured (probe, 2026-10-19)

Naive `10:00:00.123456` is stored and read back unchanged on every driver, session in UTC or
Tehran. An aware `13:30:00.654321+03:30` in a zone-less column: psycopg (sync and async) - the
server moves it to the session zone (10:00 in UTC, 13:30 in Tehran); asyncpg - refused
(`DataError`); PyMySQL/aiomysql on MySQL and MariaDB - digits kept, offset dropped (13:30);
SQLite (SQLAlchemy's text) - digits kept, offset dropped.

## db-scheduler red/green proof (sqlite + postgres, both shapes, guard excluded)

## clock: SystemClock aware UTC
   FAILED tests/unit/test_time_kept_as_given.py::test_a_settable_clock_keeps_what_it_is_given
   red: test_a_settable_clock_keeps_what_it_is_given, test_the_system_clock_is_utc_with_no_zone_attached
   guard: FAILED tests/unit/test_time_kept_as_given.py::test_no_conversion_in_the_persistence_path
## settable clock: normalised to UTC
   FAILED tests/unit/test_time_kept_as_given.py::test_a_settable_clock_keeps_what_it_is_given
   red: test_a_settable_clock_keeps_what_it_is_given
   guard: FAILED tests/unit/test_time_kept_as_given.py::test_no_conversion_in_the_persistence_path
## repository write: to UTC, zone dropped
   FAILED tests/integration/test_time_as_is.py::test_an_aware_time_is_handed_to_the_driver_unchanged[zoned-postgres]
   red: test_an_aware_time_is_handed_to_the_driver_unchanged
   guard: FAILED tests/unit/test_time_kept_as_given.py::test_no_conversion_in_the_persistence_path
## repository read: stamped UTC
   FAILED tests/integration/test_time_as_is.py::test_an_aware_time_is_handed_to_the_driver_unchanged[zoned-sqlite]
   red: test_a_naive_time_is_written_as_it_is_and_read_back_equal, test_an_aware_time_is_handed_to_the_driver_unchanged
   guard: FAILED tests/unit/test_time_kept_as_given.py::test_no_conversion_in_the_persistence_path
## repository read: heartbeat stamped UTC
   FAILED tests/integration/test_time_as_is.py::test_the_clock_s_own_value_is_what_a_pick_writes[utc-postgres]
   red: test_the_clock_s_own_value_is_what_a_pick_writes
   guard: FAILED tests/unit/test_time_kept_as_given.py::test_no_conversion_in_the_persistence_path
## task data: astimezone(UTC)
   FAILED tests/unit/test_time_kept_as_given.py::test_task_data_writes_a_time_as_it_is
   red: test_task_data_writes_a_time_as_it_is
   guard: FAILED tests/unit/test_time_kept_as_given.py::test_no_conversion_in_the_persistence_path

## timestamptz `scheduled_tasks` (probe, PostgreSQL 17)

A one-second job through `job_registry()`, two deploys on one `timestamptz` table. Default
(naive) clock: runs 4 then 3, the second deploy logged `TypeError: can't compare offset-naive
and offset-aware datetimes` while reconciling the recurring job. Aware UTC clock
(`customize_scheduler(lambda b: b.clock(AwareUtcClock()))`): runs 4 then 3, no error.
