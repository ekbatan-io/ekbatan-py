# Time as is - ledger

Asked 2026-10-19 (the user's "very big bad dracula"): the persistence layer writes and reads
exactly the time it is given - no automatic shifting, stamping or normalising. The default
`SystemClock().now()` returns the current UTC time with no zone attached. UTC everywhere stays a
strong recommendation; getting time right is the user's job.

Commits from `EKB-0384`. Judgement calls: `docs/DECISIONS-TO-REVIEW.md#time-as-is-ekb-0384-`.

## Baseline (before any change, HEAD 1e63c14)

- unit: 1283 passed, 4 xfailed
- `--dialects=all`: (pending)

## Places that converted (found by grep + reading; see "after" column)

| # | where | did | now |
| --- | --- | --- | --- |
| 1 | `schema.py` `_UtcTimestamp` / `timestamp_column()` | bind: aware -> UTC naive, naive taken as UTC; result: stamped `+00:00` | pending |
| 2 | `repository.py` `_writer_for` / `_reader_for` / `_to_column` | datetime fields `to_storage` on write, `to_instant` on read | pending |
| 3 | `domain.py` `Model.__post_init__` | `to_instant` on created/updated date | pending |
| 4 | `action/outbox.py` `EventEntity` | `to_instant` in `__post_init__`, `to_storage` in `to_row` | pending |
| 5 | `events/notification.py` `EventNotification` | same | pending |
| 6 | `events/repository.py` `find_due`, `_mark` | `to_storage` on bound times | pending |
| 7 | `events/dispatch.py` (4 places), `events/fanout.py` | `to_instant(clock.now())` | pending |
| 8 | `action/executor.py`, `aio/executor.py` | `to_instant(clock.now())` for started/completed | pending |
| 9 | `streaming.py` `ActionEvent` post-init | stamped UTC on zone-less CDC values | pending |
| 10 | `streaming.py` epoch-micros decoding | `fromtimestamp(v / 1e6, tz=UTC)` (aware, float) | pending |
| 11 | `_json.py` `_datetime` | `astimezone(UTC)` on a zero-offset value | pending |
| 12 | `clock.py` | `SystemClock` aware UTC; `VirtualClock` `_as_utc` on given instants; `to_instant`, `to_storage`, `_as_utc` | pending |
| 13 | `sharding.py` `uuid_instant` | aware UTC from the id's milliseconds | pending |
| 14 | `migrations.py` `installed_on` | `datetime.now(UTC)` text stamp in the history table | pending (not user time) |
| 15 | `db-scheduler/` repository `_to_column` / `_aware`, clock `_aware` | aware -> UTC naive on write, naive stamped UTC on read | pending |

## Steps

| step | state | commit |
| --- | --- | --- |
| baseline runs | in progress | |
| library change (1-13) | | |
| tests rewritten + new as-is tests | | |
| db-scheduler decision | | |
| docs, website, AGENTS.md | | |
| examples | | |
| full verification | | |
