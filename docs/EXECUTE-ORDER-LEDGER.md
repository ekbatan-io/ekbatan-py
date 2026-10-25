# `execute` takes the principal first, as Java's - ledger (Dracula 68)

The brief (the user, 2026-10-25, option 1: "match java"): Java's executor is
`execute(Principal principal, Class<A> actionClass, P params)`, with an overload that takes an
`ExecutionConfiguration` fourth. The Python executors take the same order:
`execute(principal, action, params, config=None)`. The principal comes first, positionally, with
`None` allowed. Its type stays `str | None`: Python has no standard `Principal`, and Java's code
only reads `getName()`. `perform(self, principal, params)` is already in Java's order.

A call in the old order must fail loudly rather than run wrong. Judgement calls go in
`DECISIONS-TO-REVIEW.md` under "execute takes the principal first, as Java's (EKB-0556-)".
Status: `todo` / `doing` / `done`. A row is `done` only when a run shows it.

## Framework

| # | item | status | proof |
| --- | --- | --- | --- |
| F1 | `ActionExecutor.execute(principal, action, params, /, config=None)` | done | `src/ekbatan/action/executor.py`: positional-only principal, action, params; `config` fourth or by name |
| F2 | `AsyncActionExecutor.execute`, the same | done | `src/ekbatan/aio/executor.py`, the same signature, through the shared `_running` |
| F3 | an old-order call is refused with a `TypeError` naming Java's order | done | `_running` refuses a non-`str`/`None` principal or a non-action in the action's place with `TypeError` naming `execute(principal, WalletDepositMoneyAction, params)`; the old `principal=` keyword and the old two-argument call fail in Python itself |
| F4 | helpers that mirror `execute`: `run_action` / `async_run_action`, the suite's `run(...)` | done | `run_action(principal, action, params)` / `async_run_action(...)` positional-only, the old order refused; the suite's `run(...)` and the parity harness keep their shape and forward in Java's order (DECISIONS-TO-REVIEW) |
| F5 | docstrings and examples inside `src/` | done | docstring examples in `executor.py`, `aio/executor.py`, `registry.py`, `accounting.py`, `testing.py`, `aio/testing.py` |

## Tests

| # | item | status | proof |
| --- | --- | --- | --- |
| T1 | new: principal-first works, sync and async; an old-order call is refused; red on the old code | done | `tests/integration/test_execute_order.py` (sync and async) and `test_run_action_takes_the_principal_first_and_refuses_the_old_order`: 7 of 7 failed on the old framework, 7 of 7 pass |
| T2 | every test and fixture call site | done | calls moved by a parsing script (executor calls only), the rest by hand (`ready.execute`, the `run` helper, `svc.execute`); the whole SQLite suite: 3296 passed, 109 skipped, 0 failed |

## Examples and guide

| # | item | status | proof |
| --- | --- | --- | --- |
| E1 | the 14 uv examples; `sync-examples.py` writes the pip and Poetry copies, `--check` | done | 68 calls in the uv examples moved by the script, the copies written by `sync-examples.py`; `--check`: 14 examples x 3 builds, in step; each uv example's tests pass (R5). The pip and Poetry copies were not run: their `src/` is the uv one, byte for byte, as `--check` shows |
| E2 | the setup guide's project (`website/src/setup-guide/`) | done | 12 calls moved; the setup-guide check passes on every database (R6) |

## Docs and the rest

| # | item | status | proof |
| --- | --- | --- | --- |
| D1 | docs/ and website pages, in step | done | the Python blocks of docs/ and website pages moved by the script, block text otherwise byte for byte; `README.md`, the homepage, `reference/action.mdx` and `run_action` calls by hand; links, check-api (0 wrong), docs-website-sync and the site build pass |
| D2 | `AGENTS.md`, `agent-skills/` and its mirrors, `DIVERGENCE.md` | done | `DIVERGENCE.md`: the order noted as matched, the parameterless example fixed; `AGENTS.md` and `agent-skills/` show no call's arguments - unchanged |
| D3 | parity-probe scripts | done | 148 calls in 14 probe scripts moved, without re-formatting; each compiles |
| D4 | `DRACULAS.md` #68 and its B detail: the order matched, the string type Python's | done | #68's row and its B detail: the order matched, the string type Python's, with why |
| D5 | a last sweep: every `execute(` and `run_action(` in the repo as text, not only in Python blocks, and `execute` passed by reference | done | moved: the docstrings in `src/ekbatan/aio/locks/` (2), the prose in `outbox.py` and `test_event_persister.py` (2), `scripts/bench-async.py` (2 - mypy's "Too few arguments for execute" is gone), the diagrams in `learn/your-first-action`, `learn/actions` with `docs/concepts/actions.md`, and `reference/action`, the prose in `reference/action.mdx` and the `reference/action-executor` lead, `ModeTabs.astro`'s comment, two current Dracula "What" lines, 5 probe calls in 4 scripts, `README.md`'s `run_action`. A second sweep finds none left outside history |

## Runs

| # | item | status | proof |
| --- | --- | --- | --- |
| R1 | unit, `--dialects=sqlite` | done | unit 2154 passed, 1 skipped; the whole suite on SQLite 3296 passed, 109 skipped, 0 failed |
| R2 | `--dialects=postgres,mysql,mariadb`, on its own | done | 5942 passed, 48 skipped, 0 failed |
| R3 | `make test-psycopg-async`, `make test-gevent` | done | psycopg-async 1373 passed, 15 skipped; gevent 2898 passed, 507 skipped. The user then asked to stop the remaining runs: neither was still running, both had finished |
| R4 | ruff, ruff format, mypy, links, divergence, builders, sync, check-api, docs-website-sync, site build and check | done | all pass on the tree before D5. After D5, at the user's request, only ruff, ruff format, mypy, links, check-api and docs-website-sync were re-run: all pass. Divergence, builders, sync-examples and the site build and check were not re-run after D5: skipped by the user |
| R5 | the 14 uv examples, both SQLite `make run`s | done | the 14 uv examples pass (each REST service 6 tests, the saga 6, each job worker 4, each SQLite service 4); both SQLite `make run`s pass |
| R6 | the setup-guide check, every database, uv, sync and async | done | 8 of 8: PostgreSQL, MySQL, MariaDB and SQLite, with uv, sync and async; ports checked free first |

## Commits

| # | item | status | proof |
| --- | --- | --- | --- |
| C1 | EKB-0556 framework and tests; EKB-0557 examples and the guide; EKB-0558 docs and the rest | done | EKB-0556 `6fad795d` (with `scripts/bench-async.py`), EKB-0557 `a360ae3e`, EKB-0558 this commit; on main, not pushed |
