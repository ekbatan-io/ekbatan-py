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
| L6 | `repository.py`: `[M, R, K]`, `to_record -> R`, `from_record(R)`, refusal text, helpers typed | todo |
| L7 | `aio/repository.py`: the same | todo |
| L8 | every framework call of `from_record` hands it the row's mapping (find/get/find_many/find_where/find_one_where/all/add_all/update_all, sync and async, sharded) | todo |
| L9 | `fetching.py`: the terminals hand the mapper the row's mapping, typed by the mapper | todo |
| L10 | `tests/modes.py` mapping names | todo |
| L11 | the outbox / notification / `ActionEvent` `to_row`/`from_row` (not repository mappings) | todo |

## Tests

| # | item | state |
| --- | --- | --- |
| T1 | `tests/fixtures/repositories.py`: hand-written records, new names | todo |
| T2 | every test using the old names or the old generics | todo |
| T3 | unit: generator - plain, nullable, forced types, converter by annotation, by base, neither, clashes | done |
| T4 | integration, four databases: generated records name each column with the type the driver really returns, and mypy accepts the module | done - `test_schemagen_types`: every case read back through the framework's sync engine and its async one is what `TTypesRecord` says; went red on PostgreSQL first (INET/CIDR read as `ipaddress` objects, BIT as asyncpg's `BitString`), fixed in the type table |
| T5 | mypy test: missing column, extra/misspelled column, wrong type, converter type, wrong key read - each proven red | done - `tests/unit/test_typed_records_mypy.py`, 9 mistakes + 1 clean file in one mypy run. Red probes: every hint `Any` -> wrong_type, converter_type, read_as_wrong_type go green-for-mypy (test red); every key `NotRequired` -> missing_column red; repositories typed `dict[str, Any]` (the old mapping) -> all 9 red |
| T6 | `test_fetching.py`: the mapper gets the mapping | todo |

## Examples (uv; pip and Poetry written by `scripts/sync-examples.py`)

| # | example | state |
| --- | --- | --- |
| E1 | fastapi-wallet-rest-uv-pg | todo |
| E2 | fastapi-wallet-rest-uv-mysql | todo |
| E3 | fastapi-wallet-rest-uv-mariadb | todo |
| E4 | fastapi-wallet-rest-uv-sharded-pg | todo |
| E5 | fastapi-wallet-rest-uv-async-sharded-pg | todo |
| E6 | fastapi-wallet-rest-uv-async-pg | todo |
| E7 | fastapi-wallet-rest-uv-async-mysql | todo |
| E8 | fastapi-wallet-rest-uv-async-mariadb | todo |
| E9 | fastapi-wallet-saga-uv-pg | todo |
| E10 | fastapi-wallet-saga-uv-async-pg | todo |
| E11 | job-worker-uv-pg | todo |
| E12 | job-worker-uv-async-pg | todo |
| E13 | wallet-service-uv-sqlite (hand-written schema: hand-written records) | todo |
| E14 | wallet-service-uv-async-sqlite (the same) | todo |
| E15 | `sync-examples.py` writes pip/Poetry; `--check` clean | todo |

## Docs and website

| # | item | state |
| --- | --- | --- |
| D1 | AGENTS.md: the record rule, the new names | todo |
| D2 | DIVERGENCE.md: "SQLAlchemy Table objects, not record classes" | todo |
| D3 | docs/DRACULAS.md: fixed row "row mapping was an untyped dict" | todo |
| D4 | docs/database/schemagen.md + website schemagen page | todo |
| D5 | docs/database/repositories.md + website reference/repository | todo |
| D6 | the rest of docs/ and website/ naming the old methods (getting-started, sync-or-async, going-async, async, wiring, sharding, streaming, model-event, index, setup guide) | todo |
| D7 | skill files (agent-skills, and the .claude / .agents copies) | todo |
| D8 | historical ledgers and parity-probes outputs: left as history | todo |

## Runs

| # | run | before | after |
| --- | --- | --- | --- |
| R1 | unit | | |
| R2 | sqlite (both modes) | | |
| R3 | postgres | | |
| R4 | mysql | | |
| R5 | mariadb | | |
| R6 | psycopg async | | |
| R7 | make lint | | |
| R8 | 14 uv examples' `make`; pip + Poetry for one per family | | |
| R9 | setup-guide checker, uv x 4 databases x 2 modes | | |
| R10 | website build + check | | |
