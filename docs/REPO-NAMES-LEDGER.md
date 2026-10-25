# Repository read methods take Java's names - ledger (Dracula 48)

The decision (the user, 2026-10-25): the repository read methods take Java's names, in
snake_case. The old names go, with no aliases (the package is unreleased), and the async twins
get the same names, awaited.

| old | new | Java |
| --- | --- | --- |
| `find(key)` | `find_by_id(id)` - the object or `None` | `findById` (`Optional`) |
| `get(key)` | `get_by_id(id)` - raises if missing | `getById` |
| `find_many(keys)` | `find_all_by_ids(ids)` | `findAllByIds` |
| `all()` | `find_all()` | `findAll` |
| `exists(key)` | `exists_by_id(id)` | `existsById` |
| `count()` | `count()` - no conditions | `count()` |
| `count(c, ...)` | `count_where(c, ...)` | `countWhere` |
| `find_one_where(...)` | unchanged | `findOneWhere` |
| `find_where(...)` | `find_all_where(...)` | `findAllWhere` |
| `exists_where(...)` | unchanged | `existsWhere` |

What stays Python's: `None` instead of `Optional`; the `*_where` methods accept several
conditions and an optional `shard=`. Judgement calls go in `DECISIONS-TO-REVIEW.md` under
"Repository read methods take Java's names (EKB-0538-)".

Status: `todo` / `doing` / `done`. A row is `done` only when a run shows it.

## Framework

| # | item | status | proof |
| --- | --- | --- | --- |
| F1 | sync: `AbstractRepository`'s reads renamed, `count` split, `Repository.find_all` | done | `src/ekbatan/repository.py`; mypy strict clean (224 files); the new test green |
| F2 | async: `AbstractAsyncRepository`'s reads renamed, `count` split, `AsyncRepository.find_all` | done | `src/ekbatan/aio/repository.py`; the new test green for the async classes; `test_async_engine.py` green |
| F3 | a `*_where` call with no condition: decided by Java | done | Java decides: each `*_where` read's first condition is required - a call with none is a `TypeError` (and a mypy error); `find_all()` and `count()` read with none. Proven by `test_each_where_read_needs_one_condition_and_takes_more` (24 cases) |
| F4 | docstrings and internal callers in `src/` | done | docstrings (`ids.py`, `action/action.py`, `aio/action.py`, `aio/repository.py`), `_found` / `_keys_by_shard` / `_among` docs, the protocol's, the refusal text |

## Tests

| # | item | status | proof |
| --- | --- | --- | --- |
| T1 | new unit test: exactly the new read names, sync and async; red on the old code, then green | done | `tests/unit/test_repository_read_names.py`: on the old code 44 failed; on the new, all pass |
| T2 | `test_async_engine.py`: the sync and async surfaces still match | done | its list of awaited methods carries the new names; green |
| T3 | every test and fixture call site renamed | done | 171 calls and 11 passed references rewritten by AST, only on repository receivers; one wrong hit (the outbox repository's `count(shard)`) put back; the whole suite on SQLite exit 0 |

## Examples and the guide

| # | item | status | proof |
| --- | --- | --- | --- |
| E1 | the 14 uv example sources; `sync-examples.py` writes the pip and Poetry copies, `--check` | done | 108 calls in the 14 uv sources; mypy strict clean; `sync-examples.py` wrote the pip and Poetry copies, `--check` in step; each uv example's `make` green (R3) - the copies' `src/` is the uv projects', byte for byte |
| E2 | the setup guide's project (`website/src/setup-guide/`) | done | 6 calls in `website/src/setup-guide/`; the setup-guide check green (R4) |

## Docs

| # | item | status | proof |
| --- | --- | --- | --- |
| D1 | docs/ pages | done | 23 pages; the reference's read list rewritten (`docs/database/repositories.md`); links, check-api (546 snippets, 0 wrong) |
| D2 | website pages, in step with docs/ | done | their website pages, in step (docs-website-sync: 34 pairs, 0 below 80%); `npm run build` and `npm run check` exit 0 |
| D3 | `AGENTS.md`, `agent-skills/` and its mirrors | done | no old name left in `AGENTS.md` or `agent-skills/` (the one hit is the dialect list's `all`), so nothing to sync to the mirrors |
| D4 | parity-probe scripts, `DIVERGENCE.md` | done | 221 calls in 17 probe scripts; `DIVERGENCE.md`'s protocol line; recordings and probe labels kept (history) |
| D5 | `DRACULAS.md` #48 and its A8 detail: the names fixed, `None` stays Python's | done | row 48: names matched; A8: the matched note |

## Runs

| # | item | status | proof |
| --- | --- | --- | --- |
| R1 | unit, sqlite, postgres/mysql/mariadb, psycopg async, gevent | done | unit 2141 passed, 1 skipped; the whole suite on SQLite 3246 passed, 109 skipped; postgres, mysql, mariadb 5817 passed, 48 skipped, 1 failed - `test_requests_beyond_the_pool_wait_their_turn_and_all_commit[postgres]` saw one connection out at once where it waits to see two, while the examples and the setup-guide check ran beside it; alone it passed 3 of 3, and `test_async.py` on all three servers 33 passed; `make test-psycopg-async` 1336 passed; `make test-gevent` 2867 passed, 488 skipped |
| R2 | ruff, mypy, links, divergence, builders, sync, check-api, docs-website-sync, site build and check | done | ruff and ruff format over `make lint`'s scope; mypy strict, 224 files; links, divergence, check-builders (15), `sync-examples --check`, check-api (546 snippets, 65 attribute claims, 0 wrong), docs-website-sync (34 pairs); `npm run build` and `npm run check` exit 0 |
| R3 | the 14 uv examples, both SQLite `make run`s | done | 14 of 14 (`make -C`, each its wheel installed): REST 6 passed each, saga 6, job worker 4, SQLite 4; both SQLite `make run`s exit 0. The pip and Poetry copies were not run: their `src/` is the uv sources', byte for byte (`--check`) |
| R4 | the setup-guide check, every database, uv, sync and async | done | 8 of 8 ok - PostgreSQL, MySQL, MariaDB and SQLite, uv, sync and async; ports 5432/3306/8000 free first |

## Commits

| # | item | status | proof |
| --- | --- | --- | --- |
| C1 | EKB-0538 framework and tests; EKB-0539 examples and the guide; EKB-0540 docs and the rest | done | EKB-0538 the framework, the suite and the parity probes; EKB-0539 the examples and the guide's project; EKB-0540 docs, website, `README.md`, `DIVERGENCE.md`, Draculas, this ledger, decisions |
