# Time kinds ledger (EKB-0419-)

The time-kind labels, as designed with the user: `ekbatan.time.Instant`, `LocalDateTime`,
`OffsetDateTime` (`typing.NewType` over `datetime`). Closes problem (c): event JSON times sent
to Java lacked the `Z`. Judgement calls: `docs/DECISIONS-TO-REVIEW.md`, "time kinds (EKB-0419-)".

Status: `[ ]` open, `[x]` done (with the commit), `[-]` decided not to (see decisions).

## Code

- [ ] 1. `src/ekbatan/time.py`: the three labels; exported from `ekbatan`
- [ ] 2. `Clock.instant() -> Instant`; `now()` removed: `clock.py`, `Action.now()` -> `Action.instant()`, every caller in `src/`
- [ ] 3. db-scheduler: its clock keeps Java db-scheduler's `now()` (decision); callers that hand it an ekbatan clock
- [ ] 4. Models `created_date` / `updated_date` typed `Instant`; builders; framework-written times (outbox, notifications, dispatch, executor, jobs) typed `Instant`
- [ ] 5. JSON: label-directed writing and reading (`_json.py`), event payloads and action params; refusals
- [ ] 6. schemagen: column -> label (records), forced names `Instant` / `LocalDateTime` / `OffsetDateTime`, `DEFAULT_FORCED_TYPES`, `SCHEMA_FORMAT` 4
- [ ] 7. time-as-is guard stays green

## Tests (each seen red)

- [ ] T1. mypy proof: Instant vs LocalDateTime, plain datetime into Instant, OffsetDateTime vs Instant, arithmetic keeps Instant
- [ ] T2. JSON unit: each label's output, Java fixture byte-for-byte, refusals + messages, decode round trips, nested/list/Optional/dict
- [ ] T3. JSON integration: refusal inside `executor.execute` writes nothing, 4 databases x 2 modes
- [ ] T4. schemagen: column type -> label on 4 databases, LocalDateTime opt-out, records type-check
- [ ] T5. clock: `instant()` naive and near UTC now; `now()` gone (guard)
- [ ] T6. existing suites updated (tests/, db-scheduler/tests)

## Docs, website, examples

- [ ] D1. Always-UTC / "What you give is what is stored": "Time kinds" section + table (docs + website)
- [ ] D2. schemagen page: column -> label table, LocalDateTime opt-out, TIMESTAMPTZ offset caveat, MySQL/MariaDB have no zoned type (docs + website)
- [ ] D3. events / JSON pages (docs + website)
- [ ] D4. models pages
- [ ] D5. clock pages: `instant()`
- [ ] D6. every other `now()` in docs/website
- [ ] D7. `docs/beside-java.md` (+ website copy): declare the field `Instant`
- [ ] D8. AGENTS.md time section
- [ ] D9. DRACULAS.md: event-time row
- [ ] D10. DECISIONS: close "(b) event-JSON time without Z"; log the calls
- [ ] D11. examples: 14 uv projects (events declare `Instant`, `clock.instant()`), sync-examples.py for pip/Poetry

## Verification (before -> after)

- [ ] V1. make lint
- [ ] V2. unit tests
- [ ] V3. integration all four databases, both modes
- [ ] V4. make test-psycopg-async
- [ ] V5. make test-gevent
- [ ] V6. db-scheduler tests on all four databases
- [ ] V7. examples: regenerate schemas; 14 uv; pip + Poetry for two families
- [ ] V8. setup-guide checker uv x 4 databases x 2 modes
- [ ] V9. website npm run build + npm run check
