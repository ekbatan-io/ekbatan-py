---
name: ekbatan-py-examples
description: Maintain the ekbatan-py example applications. Use when adding, updating, auditing or verifying anything under ekbatan-py-examples across the PostgreSQL, MySQL, MariaDB, sharded, saga, job-worker and SQLite examples, each built with uv, pip and Poetry.
---

# ekbatan-py examples

Use this when a change affects an example application, its docs, its wiring, or consistency
across the matrix.

## First reads

- `ekbatan-py-examples/README.md`.
- The closest existing example for the same dialect.
- `references/example-matrix.md`.
- `docs/wiring.md` and `docs/wiring-fastapi.md`.

## Matrix discipline

Seven examples, **each carried three times** - `-uv-`, `-pip-`, `-poetry-` - as Java carries its
examples per Gradle and Maven. Only the REST example is replicated per database.

| | |
| --- | --- |
| per database | `fastapi-wallet-rest-{build}-pg`, `-mysql`, `-mariadb` |
| one of a kind | `fastapi-wallet-rest-{build}-sharded-pg`, `fastapi-wallet-saga-{build}-pg`, `job-worker-{build}-pg`, `wallet-service-{build}-sqlite` |

**Edit only the uv project**, then run `uv run python scripts/sync-examples.py`: it writes the
pip and Poetry twins (the same `src/`, byte for byte) and every project's `pyproject.toml`,
`Makefile`, `.gitignore` and README build section. Never hand-edit a twin - `make lint` runs
`sync-examples.py --check` and fails on any drift. When changing a base wallet behaviour, change
all three REST databases' uv projects; those are not generated from one another.

## An example is documentation

- **mypy `--strict` runs over the examples' uv projects** (their twins are the same code),
  because documentation that would not type-check is documentation that is wrong.
- They import only the framework, the standard library and what each project's `pyproject.toml`
  declares. `tests/test_structure.py` enforces it.
- They are **not** shared with `tests/fixtures/`. The suite's domain exists to exercise every
  shape worth exercising; an example exists to show what good code looks like. Coupled, every
  coverage change lands in the documentation.
- A claim in an example's README must be backed by its source. An example nobody runs rots -
  and one did, silently, because this repository had no remote and CI had never executed.

## Conventions

- Actions are `OpenWalletAction`, `DepositMoneyAction` - imperative, verb first, `Action` suffix.
  Events are `WalletOpenedEvent`, `MoneyDepositedEvent` - past tense, `Event` suffix. Handlers
  end `EventHandler`, jobs end `Job`.
- Endpoints are plain `def`, never `async def`. FastAPI runs a plain `def` in a threadpool; an
  `async def` calling into the synchronous framework blocks the whole event loop.
- Three pools - requests, jobs, locks - and the jobs pool is at least twice the worker count.
- A keyed lock lease must span `executor.execute(...)`, not just the read.
- Generated `schema.py` is gitignored. Never commit one, and never edit one.

- **UTC everywhere**, the framework's strong recommendation: an example takes time from the clock
  (`self.now()`, `clock.now()`), never `datetime.now()`; its migrations use `TIMESTAMP` (PostgreSQL)
  or `DATETIME(6)` (MySQL, MariaDB), never `TIMESTAMPTZ`; its database runs in UTC. See
  `docs/database/multi-database.md#always-utc`.

## Workflow

1. `ls ekbatan-py-examples` to see the matrix.
2. Read a known-good uv project for the dialect family before editing.
3. Make the smallest mechanical cross-matrix edit possible, in the uv projects.
4. `uv run python scripts/sync-examples.py` to carry it to the pip and Poetry twins.
5. `make examples` - every project builds its wheel and tests it installed, so a file a build
   tool leaves out fails here. Needs Docker, `poetry` on the path, and a Python 3.13+ (found
   through uv).

Use `ekbatan-py-verification` for the full matrix.
