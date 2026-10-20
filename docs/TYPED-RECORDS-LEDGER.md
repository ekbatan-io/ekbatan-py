# Ledger: typed table records (EKB-0408-)

Asked on 2026-10-20, approved by the user: give the port typed table records, like jOOQ's
generated `WalletsRecord`, and rename the mapping methods. The untyped `dict[str, Any]` mapping
was "a big dracula". Worked alone from this file; judgement calls are in
`docs/DECISIONS-TO-REVIEW.md`, "typed records (EKB-0408-)".

The design (the user's):

1. schemagen writes a `TypedDict` `<Table>Record` next to each `Table`: one key per column, the
   column's real Python type, `| None` when nullable.
2. Types follow forced types and converters exactly. A converter's user type comes from its
   `from_db` return annotation, else its `Converter[DbT, UserT]` base; neither is an error.
3. `to_row` -> `to_record`, `from_row` -> `from_record`, everywhere, no aliases.
4. Repositories are generic in the record type; `from_record` takes the record type.
5. Hand-written schemas get hand-written records.
6. Tests: generator unit tests, all-database integration, a mypy test that goes red.
7. Docs, website, AGENTS.md, DIVERGENCE.md, DRACULAS.md, every example.

State: `todo`, `doing`, `done`, `n/a` (says why).

## Library

| # | item | state |
| --- | --- | --- |
| L1 | `ekbatan.schema.JsonValue`: what a JSON column reads back | done |
| L2 | schemagen: a `<Table>Record` per table and view, framework tables included | done |
| L3 | schemagen: column -> Python type (plain, nullable, forced name, sqlalchemy-type, converter) | done |
| L4 | schemagen: converter user type from `from_db` annotation, else generic base, else error | done |
| L5 | schemagen: imports for the types, aliased on a clash; record names that clash fail | done |
| L6 | `repository.py`: `[M, R, K]`, `to_record -> R`, `from_record(R)`, refusal text, helpers typed | done |
| L7 | `aio/repository.py`: the same | done |
| L8 | every framework call of `from_record` hands it the row's mapping (find/get/find_many/find_where/find_one_where/all/add_all/update_all, sync and async, sharded) | done |
| L9 | `fetching.py`: the terminals hand the mapper the row's mapping, typed by the mapper | done |
| L10 | `tests/modes.py` mapping names | done |
| L11 | the outbox / notification / `ActionEvent` `to_row`/`from_row` (not repository mappings) | n/a - kept: `EventEntity`, `EventNotification` and `ActionEvent` are the framework's own row classes, not a repository's mapping (DECISIONS) |

## Tests

| # | item | state |
| --- | --- | --- |
| T1 | `tests/fixtures/repositories.py`: hand-written records, new names | done |
| T2 | every test using the old names or the old generics | done |
| T3 | unit: generator - plain, nullable, forced types, converter by annotation, by base, neither, clashes | done |
| T4 | integration, four databases: generated records name each column with the type the driver really returns, and mypy accepts the module | done - `test_schemagen_types`: every case read back through the framework's sync engine and its async one is what `TTypesRecord` says; went red on PostgreSQL first (INET/CIDR read as `ipaddress` objects, BIT as asyncpg's `BitString`), fixed in the type table |
| T5 | mypy test: missing column, extra/misspelled column, wrong type, converter type, wrong key read - each proven red | done - `tests/unit/test_typed_records_mypy.py`, 9 mistakes + 1 clean file in one mypy run. Red probes: every hint `Any` -> wrong_type, converter_type, read_as_wrong_type go green-for-mypy (test red); every key `NotRequired` -> missing_column red; repositories typed `dict[str, Any]` (the old mapping) -> all 9 red |
| T6 | `test_fetching.py`: the mapper gets the mapping | done |

## Examples (uv; pip and Poetry written by `scripts/sync-examples.py`)

| # | example | state |
| --- | --- | --- |
| E1 | fastapi-wallet-rest-uv-pg | done |
| E2 | fastapi-wallet-rest-uv-mysql | done |
| E3 | fastapi-wallet-rest-uv-mariadb | done |
| E4 | fastapi-wallet-rest-uv-sharded-pg | done |
| E5 | fastapi-wallet-rest-uv-async-sharded-pg | done |
| E6 | fastapi-wallet-rest-uv-async-pg | done |
| E7 | fastapi-wallet-rest-uv-async-mysql | done |
| E8 | fastapi-wallet-rest-uv-async-mariadb | done |
| E9 | fastapi-wallet-saga-uv-pg | done |
| E10 | fastapi-wallet-saga-uv-async-pg | done |
| E11 | job-worker-uv-pg | done |
| E12 | job-worker-uv-async-pg | done |
| E13 | wallet-service-uv-sqlite (hand-written schema: hand-written records) | done |
| E14 | wallet-service-uv-async-sqlite (the same) | done |
| E15 | `sync-examples.py` writes pip/Poetry; `--check` clean | done |

## Docs and website

| # | item | state |
| --- | --- | --- |
| D1 | AGENTS.md: the record rule, the new names | done |
| D2 | DIVERGENCE.md: "SQLAlchemy Table objects, not record classes" | done |
| D3 | docs/DRACULAS.md: fixed row "row mapping was an untyped dict" | done |
| D4 | docs/database/schemagen.md + website schemagen page | done |
| D5 | docs/database/repositories.md + website reference/repository | done |
| D6 | the rest of docs/ and website/ naming the old methods (getting-started, sync-or-async, going-async, async, wiring, sharding, streaming, model-event, index, setup guide) | done |
| D7 | skill files (agent-skills, and the .claude / .agents copies) | done |
| D8 | historical ledgers and parity-probes outputs: left as history | n/a - history: `ASYNC-*`, `TIME-AS-IS-LEDGER`, `CONVERTER-AND-WEBSITE-LEDGER`, `dracula/`, `behaviour-parity/`, `parity-round-2/`, `parity-probes/` keep the names they were written with |

## Runs

| # | run | before | after |
| --- | --- | --- | --- |
| R1 | unit | 1298 passed, 4 xfailed | 1337 passed, 4 xfailed (+39: `test_schemagen_records`, `test_typed_records_mypy`, one fetching test) |
| R2 | sqlite (both modes) | 2262 passed, 103 skipped, 4 xfailed | 2303 passed, 103 skipped, 4 xfailed |
| R3 | postgres | 2467 passed, 14 skipped, 4 xfailed | 2508 passed, 14 skipped, 4 xfailed |
| R4 | mysql | 2471 passed, 14 skipped, 4 xfailed | 2512 passed, 14 skipped, 4 xfailed |
| R5 | mariadb | 2481 passed, 12 skipped, 4 xfailed | 2522 passed, 12 skipped, 4 xfailed |
| R6 | psycopg async | 1162 passed, 14 skipped | 1164 passed, 14 skipped |
| R7 | make lint | - | clean: ruff, mypy --strict over src and the 14 uv examples (228 files), links, divergence, builders, sync-examples --check, check-api, docs-website-sync |
| R8 | 14 uv examples' `make`; pip + Poetry for one per family | - | 14 uv: all pass (REST 18/19, sharded 22/23, saga 18/20, job worker 10/12, SQLite 19/19, both SQLite `make run`); pip+Poetry: rest-mariadb, rest-async-pg, saga-pg, job-worker-async-pg, wallet-service-sqlite - all pass |
| R9 | setup-guide checker, uv x 4 databases x 2 modes | - | 8/8 ok (pg, mysql, mariadb, sqlite x sync, async) |
| R10 | website build + check | - | 58 pages built; check: 0 broken, 76/76 settled |
