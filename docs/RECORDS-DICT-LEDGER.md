# Ledger: records as typed dicts with `column_` methods (EKB-0485-)

Asked on 2026-10-22, decided by the user after rejecting attribute properties on SQLAlchemy rows
(too slow): every `<Table>Record` stops being a `TypedDict` and becomes a subclass of
`dict[str, Any]` with `__slots__ = ()`, a keyword-only `__init__`, `__getitem__` overloads for
mypy only, and one `column_<name>()` method per column. Both `record["wallet_id"]` and
`record.column_wallet_id()` work and are checked by mypy. Worked alone from this file; judgement
calls are in `docs/DECISIONS-TO-REVIEW.md`, "records as typed dicts with column_ methods
(EKB-0485-)".

State: `todo`, `doing`, `done`, `n/a` (says why).

## Library

| # | item | state |
| --- | --- | --- |
| L1 | `ekbatan.schema.UNSET` / `Unset`: the default of a column the database fills | done - `Unset(Enum)`, `UNSET: Final`, in `__all__` |
| L2 | `schemagen/_records.py`: render the dict subclass (init, overloads, `column_` methods) | done - `render()` writes the class; mypy-clean but for one `type: ignore[override]` per record |
| L3 | naming: method `column_<sanitized>`, parameter sanitizing, collisions, notes | done - `method_names`, `parameter_name(s)`, `_settle`; notes on collisions |
| L4 | `_render.py`: `SCHEMA_FORMAT` 6 -> 7, module docstring, notes plumbed | done |
| L5 | `repository.py` core: the record class read off the generics; one record per row on every read path (`_read_back`, batch reads) | done - `_record_type_of` (generic bases, `dict` fallback); `_read_all` / `_read_back` via `fetching.records` / `record_of` |
| L6 | MySQL read-back (`add_all`, `update_all` without RETURNING) hands `from_record` the record class | done - `_as_record` |
| L7 | `columns_by_field_name` builds the record class | done |
| L8 | `aio/repository.py`: every read path through the core | done - `find_many`, `find_where`/`all`, `add_all` go through `_read_all`; `find`/`find_one_where` through `_read_back` |
| L9 | `fetching.py`: what a mapper receives | done - annotation of the first parameter, else a repository method's record, else the mapping; follows `__wrapped__` |
| L10 | docstrings in `repository.py`, `aio/repository.py`, `fetching.py`, `_records.py` | done |

## Tests

| # | item | state |
| --- | --- | --- |
| T1 | `tests/fixtures/schema.py` hand-written records rewritten | done |
| T2 | other hand-written records in tests (`test_fetching`, `test_id_persistence`, `test_update_writes_the_record`, inline ones) | done - written with the renderer (`Field` lists), formatted |
| T3 | `test_schemagen_records.py` (unit): new shape, prefix rule, sanitizing, collisions, UNSET, keyword-only | done - 63 tests; red: no `column_` prefix (12 red), no disambiguation (1), filled columns required (2) |
| T4 | `test_typed_records_mypy.py`: unknown key, wrong value type, unknown method, wrong keyword/type, converter both ways; generated module clean | done - 14 mistakes + clean + ignore-count test; red: overloads say `Any` (3 red), catch-all `str` overload (2), methods typed `Any` (2) |
| T5 | schemagen integration tests reading record hints (`test_schemagen_types`, `_time_kinds`, `_round_trip`, `_table_names`, `_cli`, `_hardening`) | done - via `tests/records.py`; `unused_and_undefined` knows method parameters |
| T6 | new integration: odd column names round trip (`class`, `keys`, `items`, `order date`, `2fa`), UNSET omitted and filled, `from_record` gets the class, the exact object reaches insert/update - 4 dbs x 2 modes | done - `tests/integration/test_record_columns.py`; red: reads into plain dict, mapper always gets the mapping, insert copies, update copies (each red) |
| T7 | `test_update_writes_the_record.py` stays green | done - `PeopleRecord` rewritten (`class_=`, `UNSET`) |
| T8 | benchmark: 10k find_where and 10k insert, before vs after, SQLite and PostgreSQL | done - see DECISIONS / final report |

## Examples (uv; pip and Poetry from `scripts/sync-examples.py`)

| # | item | state |
| --- | --- | --- |
| E1 | SQLite examples' hand-written `schema.py` (sync, async) | done |
| E2 | generated schemas regenerated (12 uv projects) | done - all 12 deleted and regenerated; `mypy --strict` clean |
| E3 | repositories' `from_record` / `to_record` still right; READMEs | done - no code change needed (`record["x"]`); READMEs' record snippets |
| E4 | pip/Poetry copies synced | done |

## Docs

| # | item | state |
| --- | --- | --- |
| D1 | `docs/database/repositories.md` + `website/src/pages/reference/repository.mdx` | done |
| D2 | `docs/database/schemagen.md` + `website/src/pages/reference/schemagen.mdx` | done |
| D3 | other docs/website pages mentioning `TypedDict` records | done - getting-started, your-first-action, complete-project-setup, examples README |
| D4 | `AGENTS.md` | done |
| D5 | maintainer skill, three copies | done - three copies identical |
| D6 | `DIVERGENCE.md` | done (+ DRACULAS row 49) |

## Verification

| # | run | before | after |
| --- | --- | --- | --- |
| V1 | `make lint` | not run | pass |
| V2 | unit | in V3's count | pass (in V3's count; +11 record-type, +5 mapper, +14 naming, +5 mypy cases) |
| V3 | integration, all four databases, both modes | 6389 passed, 0 failed | 6516 passed, 3 failed - stale text checks of the old record (`dd: Any`, a view's fields); fixed and rerun green on all four |
| V4 | `make test-psycopg-async` | 1276 passed | 1302 passed |
| V5 | `make test-gevent` | 2689 passed | 2744 passed |
| V6 | 14 uv examples + pip/Poetry for two families | - | 14 uv + fastapi-wallet-rest pip/Poetry pg + wallet-service pip/Poetry sqlite, and both SQLite `make run`: all exit 0 |
| V7 | setup-guide checker, uv x 4 dbs x 2 modes | - | 8 of 8 ok |
| V8 | website build + check; docs-website sync | - | build 58 pages; check: 0 broken, parity 76/76; docs-website sync 0 below 80% |

## Benchmark (`/tmp/recbench/bench_repo.py`: the fixtures' `WalletRepository`, 10 columns, median of 7, three interleaved runs each)

| | before (414c085) | after |
| --- | --- | --- |
| PostgreSQL 17, insert 10k (`add_all_no_result`) | 261 / 259 / 257 ms | 259 / 265 / 261 ms |
| PostgreSQL 17, `find_where` 10k | 89 / 90 / 90 ms | 92 / 92 / 96 ms |
| SQLite (file), insert 10k | 2924 / 2941 / 2962 ms | 2933 / 2948 / 2924 ms |
| SQLite (file), `find_where` 10k | 92 / 91 / 93 ms | 97 / 92 / 92 ms |

Inserts: no change. Reads: about 3% slower on PostgreSQL, within noise on SQLite - building a
`dict` per row costs a little more than handing over the row's mapping, and reading from it
costs a little less.
