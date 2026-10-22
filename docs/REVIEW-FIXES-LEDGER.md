# Review fixes ledger (EKB-0436-)

Findings from independent reviews of the time work, the transaction thread guard, the docs and
the examples, each proven by a run. Worked alone, 2026-10-22; judgement calls are in
`DECISIONS-TO-REVIEW.md` under "review fixes (EKB-0436-)". Commits EKB-0436..EKB-0449 only.

## Time

- [x] T1. `beside-java.md` (+ website): never one database; keep broker/Redis facts; DECISIONS row done
- [x] T2. `json_column()` writes a datetime by value again; event/param refusal kept; tests; DECISIONS; docs
- [x] T3. `_revive` unwraps `TypeAliasType` / `Annotated`; round-trip test
- [x] T4. schemagen forced-kind on wrong column - handed over to the schemagen stream (not touched)
- [x] T6. "an Instant"; `+00:00` into Instant points to OffsetDateTime only; tests
- [x] T7. union of two kinds: "declared as more than one kind; pick one"; tests
- [x] T8. stale `now()` names: async.md (+ website), dracula A, two parity probes, DECISIONS ~990
- [x] T9. RUF009 `extend-immutable-calls` lines in the time-kinds section (docs + website)
- [x] T10. `test_time_as_is.py` CONVERSIONS: five more patterns, each proven by injection
- [x] T11. integration: action -> outbox -> fan-out -> dispatch -> handler decode, each kind, 4 dbs, both modes

## Guard, docs, examples

- [x] G1. (user's revised plan) no example holds the environment: `environ` removed from `Settings` / `build()` / `create_app()` in 14 uv + synced; tests use `monkeypatch.setenv`; the loader reads each variable by name; a marker-password test per example
- [x] G2. docs presenting one shared database with Java: trimmed (docs + website)
- [x] G3. FastAPI sync yield dependency: documented (docs + website); loud error on a reset in another context; test
- [x] G4. sync REST twins get `test_many_requests_at_once_each_commit`; README counts
- [x] G5. `TransactionThreadError` text names anyio/Starlette; concurrency page links the guard
- [x] G6. guard tests: repository write, own transaction in thread, anyio, starlette, gevent, sync endpoint

## Verification (before = 15bf3e9, after = EKB-0445)

| run | before | after |
| --- | --- | --- |
| `--dialects=all` (unit + integration, 4 databases, both modes) | 6071 passed, 143 skipped | 6137 passed, 143 skipped, 1 failed* |
| `make test-psycopg-async` | 1209 passed, 14 skipped | 1224 passed, 14 skipped |
| `make test-gevent` | 2591 passed, 445 skipped | 2609 passed, 449 skipped |
| `make lint` (ruff, mypy --strict, links, sync-examples, check-api, docs-website sync) | - | clean |
| examples: 14 uv `make` + pip/Poetry of rest-pg, wallet-service-sqlite, job-worker-async-pg; both SQLite `make run` | - | all green (rest 20 each, sharded 23/24, saga 19/21, job worker 11/13, SQLite 20/20) |
| setup guide, uv x 4 databases x 2 modes | - | 8/8 |
| website `npm run build` / `npm run check` | - | 58 pages / clean |

\* `test_schemagen_table_names.py::...[abstractrange-sqlite]`: `RuntimeError: dictionary changed
size during iteration` inside the test's own loop over its cached baseline; the module passed
501/501 three times on rerun. Flaky, in the schemagen stream's files - handed over, not touched.

Red then green: the five new JSON tests on the old `_json.py`; the JSON column test on the old
engine writer; each of the six new guard patterns injected into `src/`; the guard message and
`TransactionContextError` tests on the old `persistence.py`; the loader-frames test on the old
`config_files.py`; the examples' marker test with an `os.environ` field put back
(`fastapi-wallet-rest-uv-pg` Settings, `wallet-service-uv-sqlite` service).
