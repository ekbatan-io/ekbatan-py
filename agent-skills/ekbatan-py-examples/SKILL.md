---
name: ekbatan-py-examples
description: Maintain the ekbatan-py example applications. Use when adding, updating, auditing or verifying anything under ekbatan-py-examples across the PostgreSQL, MySQL, MariaDB, sharded, saga, job-worker and SQLite variants.
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

Only the **REST example is replicated per database** - the same shape the Java port uses. The
other four are one of a kind.

| | |
| --- | --- |
| replicated | `fastapi_wallet_rest_pg`, `_mysql`, `_mariadb` |
| one of a kind | `fastapi_wallet_rest_sharded_pg`, `fastapi_wallet_saga_pg`, `job_worker_pg`, `wallet_service_sqlite` |

When changing a base wallet behaviour, change all three REST variants. Do not assume they are
generated - they are not. Compare the files directly.

## An example is documentation

- **mypy `--strict` runs over the examples**, because documentation that would not type-check is
  documentation that is wrong.
- They import only the framework, the standard library and the declared `examples` group.
  `tests/test_structure.py` enforces it.
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

## Workflow

1. `ls ekbatan-py-examples` to see the matrix.
2. Read a known-good example for the dialect family before editing.
3. Make the smallest mechanical cross-matrix edit possible.
4. `make examples` - it regenerates schemas first, so a stale module cannot mask a break.

Use `ekbatan-py-verification` for the full matrix.
