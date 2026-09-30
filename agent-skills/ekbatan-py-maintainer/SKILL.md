---
name: ekbatan-py-maintainer
description: Maintain and evolve the ekbatan-py persistence and action framework. Use for implementing or reviewing changes to the core, repositories, actions, outbox behaviour, sharding, keyed locks, distributed jobs, migrations, schemagen, docs that describe framework semantics, or release-facing library behaviour.
---

# ekbatan-py maintainer

Use this when modifying the framework itself, not an application that depends on it.

## First reads

- `AGENTS.md` - architecture, conventions and the durable rules.
- `DIVERGENCE.md` before "fixing" something that looks wrong. It may be deliberate.
- `docs/SHORTCOMINGS.md` before claiming parity with the Java port.
- The relevant page under `docs/` when changing behaviour that is documented.
- `references/architecture-map.md` for a compact refresher.

## This is a port

`../ekbatan` is the reference implementation, and the two are expected to interoperate on the
same tables. **Read the Java file whole before changing the Python counterpart.** Every defect
this port found in its own code was found that way.

**Never commit in the Java repository.** Write the files and say what is ready. Defects found in
the Java while porting go in an openspec change, not a code change.

## Rules

- A library, not an application.
- Domain objects stay immutable. A mutation returns a new instance. This is load-bearing: the
  plan holds references, and the executor writes them after `perform()` returns.
- `Action.perform()` declares intent. It stages on the plan; the executor persists afterwards.
- No nested or composable actions.
- Preserve optimistic locking. Every update carries `WHERE version = ?`.
- SQLAlchemy **Core**, used as a typed statement builder. Never introduce `Session`, an identity
  map, autoflush or lazy loading - two units of work fighting over one transaction is the worst
  thing that could happen to this design.
- Keep PostgreSQL, MySQL and MariaDB aligned unless a dialect difference is intentional and
  documented. SQLite is not a production dialect and proves less than it looks like it proves.
- `timestamp_column()`, never a zoned type. `DATETIME(6)`, never bare `DATETIME`.
- Nothing in `src/` may import a web framework. `tests/test_structure.py` enforces it.
- The ambient transaction is a `ContextVar`. Work handed to a pool does not inherit it, and that
  is correct.
- Migrations are `V<version>__<description>.sql` files; `validate_scripts` checks them with no
  database. On MySQL and MariaDB keep one schema change per file - each commits on its own.

## Workflow

1. Map the change to the owning module before editing.
2. Read nearby code and tests. Prefer local patterns over new abstractions.
3. Write the test so it **fails for the right reason** first. Reinstate the bug and watch it go
   red before believing it - several "passing" tests here turned out to assert nothing.
4. Keep changes narrow. No unrelated refactors.
5. Update `docs/` when public semantics change, and record a new divergence in `DIVERGENCE.md`
   or a step backwards in `docs/SHORTCOMINGS.md`.
6. Run focused tests, then the broader suite.

## Checks

- Fast loop: `make test`
- Anything touching SQL: `make test-all` - dialects disagree, and SQLite agrees with everyone.
- `make lint` covers ruff, mypy `--strict` over `src` *and* the examples, and the docs link
  checker.
- Examples or generated schemas: `make examples`.
