# Time kinds ledger (EKB-0419-)

The time-kind labels, as designed with the user: `ekbatan.time.Instant`, `LocalDateTime`,
`OffsetDateTime` (`typing.NewType` over `datetime`). Closes problem (c): event JSON times sent
to Java lacked the `Z`. Judgement calls: `docs/DECISIONS-TO-REVIEW.md`, "time kinds (EKB-0419-)".

Status: `[ ]` open, `[x]` done (with the commit), `[-]` decided not to (see decisions).

## Code

- [x] 1. `src/ekbatan/time.py`: the three labels - only in `ekbatan.time`, not re-exported (decision) - EKB-0419
- [x] 2. `Clock.instant() -> Instant`; `now()` removed: `clock.py`, an action's shorthand renamed to `instant()`, every caller in `src/` - EKB-0419
- [x] 3. db-scheduler: its clock keeps Java db-scheduler's `now()` (decision), no code change; examples hand it `SystemClock().instant()`
- [x] 4. Models `created_date` / `updated_date` typed `Instant`; builders; framework-written times (outbox, notifications, dispatch, executor, jobs) typed `Instant`; codegen writes labelled setters; repository mapping unwraps labels - EKB-0419
- [x] 5. JSON: label-directed writing and reading (`_json.py`, `canonical_event`), event payloads and action params; refusals - EKB-0420
- [x] 6. schemagen: column -> label (records), forced names `Instant` / `LocalDateTime` / `OffsetDateTime`, `DEFAULT_FORCED_TYPES`, `SCHEMA_FORMAT` 4 - EKB-0421
- [x] 7. time-as-is guard stays green (allow-listed clock line re-worded, not grown)

## Tests (each seen red)

- [x] T1. mypy proof: Instant vs LocalDateTime, plain datetime into Instant, OffsetDateTime vs Instant, arithmetic keeps Instant - `tests/unit/test_time_kinds_mypy.py`; red with the labels made plain aliases (7 of 7 fail)
- [x] T2. JSON unit: each label's output, Java fixture byte-for-byte, refusals + messages, decode round trips, nested/list/Optional/dict - `tests/unit/test_time_kinds_json.py`, `test_json.py`; red with the `Z` dropped (11 fail) and with plain `datetime` accepted (2 fail)
- [x] T3. JSON integration: refusal inside `executor.execute` writes nothing, 4 databases x 2 modes - `tests/integration/test_time_kinds_events.py`
- [x] T4. schemagen: column type -> label on 4 databases, LocalDateTime opt-out, records type-check - `tests/integration/test_schemagen_time_kinds.py`, `tests/unit/test_schemagen_records.py`
- [x] T5. clock: `instant()` naive and near UTC now; `now()` gone (guard) - `tests/unit/test_clock.py`; red with a `now()` put back
- [x] T6. existing suites updated (tests/, db-scheduler/tests)

## Docs, website, examples

- [x] D1. Always-UTC / "What you give is what is stored": "Time kinds" section + table (docs + website)
- [x] D2. schemagen page: column -> label table, LocalDateTime opt-out, TIMESTAMPTZ offset caveat, MySQL/MariaDB have no zoned type (docs + website)
- [x] D3. events / JSON pages (docs + website)
- [x] D4. models pages
- [x] D5. clock pages: `instant()`
- [x] D6. every other `now()` in docs/website
- [x] D7. `docs/beside-java.md` (+ website copy): declare the field `Instant`
- [x] D8. AGENTS.md time section
- [x] D9. DRACULAS.md: event-time row
- [x] D10. DECISIONS: close "(b) event-JSON time without Z"; log the calls
- [x] D11. examples: 14 uv projects (events declare `Instant`, `clock.instant()`), sync-examples.py for pip/Poetry

## Verification (before -> after)

- [x] V1. make lint
- [x] V2. unit tests
- [x] V3. integration all four databases, both modes
- [x] V4. make test-psycopg-async
- [x] V5. make test-gevent
- [x] V6. db-scheduler tests on all four databases
- [x] V7. examples: regenerate schemas; 14 uv; pip + Poetry for two families
- [x] V8. setup-guide checker uv x 4 databases x 2 modes - `tests/integration/test_time_kinds_events.py`
- [x] V9. website npm run build + npm run check

## Results (before = bb5f615, after = EKB-0423)

| run | before | after |
| --- | --- | --- |
| unit | 1832 passed | 1876 passed |
| `--dialects=all` (unit + integration, 4 databases, both modes) | 5869 passed, 143 skipped | 5965 passed, 143 skipped |
| `make test-psycopg-async` | 1175 passed, 14 skipped | 1188 passed, 14 skipped |
| `make test-gevent` | 2472 passed, 440 skipped | 2524 passed, 445 skipped |
| db-scheduler `--dialects=all` | 2371 passed, 28 skipped | 2371 passed, 28 skipped (no code change) |
| `make lint` (ruff, mypy --strict src + examples, checks) | - | clean |
| examples: 14 uv + pip/Poetry of rest-pg, wallet-service-sqlite, job-worker-async-pg; both SQLite `make run` | - | all green (schemas regenerated, format 4) |
| setup guide, uv x 4 databases x 2 modes | - | 8/8 |
| website `npm run build` / `npm run check` | - | 58 pages / 0 broken links |
