# Repository keys are the raw id, as Java's - ledger (Dracula 47)

The brief (the user, 2026-10-25): "i like the java model, can you change it?" A repository's key
is what its id column holds - Java's `DB_ID` - and the reads that take an id take that type:

- declarations: `ModelRepository[Wallet, WalletsRecord, UUID]`,
  `EntityRepository[Notification, NotificationsRecord, UUID]` - never `ShardedId[...]` /
  `Id[...]` as the key type, since no example forces its id column;
- callers pass the raw value, as Java's `getValue()`: `wallet_repository.get_by_id(wallet.id.value)`;
- **the user's correction, the same day**: the rule is "the key is the id column's own type", not
  "never a typed value". A column a schemagen forced type converts to a typed id holds that typed
  id - as a jOOQ forced type changes Java's `DB_ID` - so there the typed id is the key and a raw
  `UUID` is refused. `find_by_id`, `get_by_id`, `find_all_by_ids`, `exists_by_id`, `id_matches`
  and `shard_for` (sync and async) refuse a key of another type, naming the type the column holds;
  where nothing says what it holds, nothing is refused. Keys reach the column unconverted, for its
  converter. Code holding a model keeps unwrapping its id where the column is plain.

Judgement calls go in `DECISIONS-TO-REVIEW.md` under "Repository keys are the raw id, as Java's
(EKB-0541-)". Status: `todo` / `doing` / `done`. A row is `done` only when a run shows it.

## Framework

| # | item | status | proof |
| --- | --- | --- | --- |
| F1 | the check: a key not of the id column's own type is refused, naming that type (sync and async, one copy in the core); no key is unwrapped on its way to the column | done | `_RepositoryCore._own_key` and `_key_type` (a `Converted` column's converter's user type, else `python_type`, else nothing), called by `find_by_id`, `get_by_id`, `find_all_by_ids` (each key), `exists_by_id`, `id_matches` and `shard_for` (which also takes a `ShardIdentifier`); `_by_id` no longer unwraps; ruff, ruff format and mypy clean |
| F2 | docstrings in `src/` say what the key is (`repository.py`, `aio/repository.py`, `ids.py`, the action docstrings) | done | ruff and ruff format clean on `src` |
| F3 | `test_async_engine.py`: the sync and async surfaces still match | done | unit 2142 tests, 0 failed, 1 skipped, `test_async_engine.py` among them |

## Tests

| # | item | status | proof |
| --- | --- | --- | --- |
| T1 | new test, sync and async, both cases: a plain id column refuses a typed id and reads by the raw one; a converted id column takes its typed id - round-tripping through add, the reads, `IN` / `= ANY`, `id_matches` and update - and refuses a raw `UUID` | done | `tests/integration/test_repository_raw_keys.py`, 28 tests. Red on the original code: 26 of 28 failed (nothing refused; the converted round trip broke, its key unwrapped before the converter). Red on the first, blanket version: the 14 converted-column cases failed. Green: 28 passed on SQLite; 84 passed on PostgreSQL, MySQL and MariaDB |
| T2 | the suite's repositories declare the raw key; every call passes the id column's own type | done | fixtures declare `UUID`; 138 lines given `.value` (AST rewrite of `x.id` / `params.x_id` / typed constructors, then the callable-style and variable cases by hand); the whole suite on SQLite: 3383 tests, 0 failed, 109 skipped |

## Examples and the guide

| # | item | status | proof |
| --- | --- | --- | --- |
| E1 | the 14 uv examples: `UUID` keys, `.value` at the call, an endpoint passes its `UUID`; `sync-examples.py` writes the pip and Poetry copies, `--check` | done | the edits; mypy clean on the 14 sources; `sync-examples.py --check` in step; the 14 uv examples pass (R3). The pip and Poetry copies carry the uv sources' `src/` byte for byte (`--check`), so they were not run |
| E2 | the setup guide's project (`website/src/setup-guide/`) | done | `{sync,async}/repository.py` declare `UUID`; `actions.py` passes `params.wallet_id.value`; ruff and ruff format clean; its run is R4 |

## Docs and the rest

| # | item | status | proof |
| --- | --- | --- | --- |
| D1 | docs/ and website pages, in step | done | 26 files: declarations to `UUID`, `.value` at the calls, the repository reference's paragraph on `K` (with the forced-type case); links, check-api (0 wrong), docs-website-sync (34 pairs), divergence pass |
| D2 | `AGENTS.md`, `agent-skills/` and its mirrors | done | `AGENTS.md`'s record section; `architecture-map.md`; `agent-skills/scripts/sync.sh` mirrored it to `.claude/skills` and `.agents/skills` |
| D3 | parity-probe scripts, `DIVERGENCE.md`, `README.md` | done | 18 probe scripts (rewrite, then 9 call sites by hand), each compiles; `DIVERGENCE.md`'s two notes; `README.md`'s sample |
| D4 | `DRACULAS.md` #47 and its A6 detail: matched | done | the row and the detail, with the forced-type case |

## Runs

| # | item | status | proof |
| --- | --- | --- | --- |
| R1 | unit, `--dialects=sqlite`, then `--dialects=postgres,mysql,mariadb` on its own, `make test-psycopg-async`, `make test-gevent` | done | unit 2142 tests, 0 failed, 1 skipped; the whole suite on SQLite 3383 tests, 0 failed, 109 skipped; postgres, mysql, mariadb 5950 tests, 0 failed, 48 skipped, run on its own after the examples; `make test-psycopg-async` 1364 passed, 15 skipped; `make test-gevent` 2881 passed, 502 skipped (the async runs); all exit 0 |
| R2 | ruff, mypy, links, divergence, builders, sync, check-api, docs-website-sync, `npm run build`, `npm run check` | done | ruff and ruff format over `make lint`'s scope: 735 files clean; mypy 224 files clean; links, divergence, check-builders (15), `sync-examples --check`, check-api (546 snippets, 0 wrong), docs-website-sync (34 pairs) pass; `npm run build` (59 pages) and `npm run check` exit 0 |
| R3 | the 14 uv examples, both SQLite `make run`s | done | 14 of 14 (`make -C <example>`, each its wheel installed, the framework reinstalled from this checkout): REST 6 passed each, saga 6, job worker 4, SQLite 4; both SQLite `make run`s exit 0 |
| R4 | the setup-guide check, every database, uv, sync and async | done | 8 of 8 ok - PostgreSQL, MySQL, MariaDB and SQLite, with uv, sync and async; ports 5432/3306/8000 free first |

## Commits

| # | item | status | proof |
| --- | --- | --- | --- |
| C1 | EKB-0541 framework and tests; EKB-0542 examples and the guide's project; EKB-0543 docs, website, AGENTS.md, skills, Draculas, ledger, decisions | done | EKB-0541 the framework, the suite and the parity probes; EKB-0542 the examples and the guide's project; EKB-0543 docs, website, `AGENTS.md`, `README.md`, `DIVERGENCE.md`, the skills and their mirrors, Draculas, this ledger, decisions |
