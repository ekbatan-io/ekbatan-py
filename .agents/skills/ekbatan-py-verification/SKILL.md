---
name: ekbatan-py-verification
description: Choose and run ekbatan-py verification. Use when asked to test, validate, run CI-equivalent checks, run the examples, run the dialect matrix, inspect the GitHub Actions workflows, or decide which commands prove a change.
---

# ekbatan-py verification

Test planning and execution in this repository.

## First reads

- `references/test-matrix.md`.
- `.github/workflows/ci.yml` for what CI actually runs.
- `Makefile` - every command this repository has, in one place.

## Default strategy

1. **Focused tests first.** `uv run pytest tests/unit/test_plan.py -k stages`.
2. **The fast loop** when shared code changed:

   ```bash
   make test          # unit, then the whole suite on SQLite
   ```

3. **The dialect matrix** whenever the change touches SQL. Not optional - dialects disagree, and
   SQLite agrees with everyone:

   ```bash
   make test-all      # PostgreSQL, MySQL and MariaDB. Needs Docker
   ```

4. **The examples** when they or the generated schemas changed:

   ```bash
   make examples      # regenerates schemas first, then runs their tests
   ```

## Reporting

Report exactly what ran and what did not. Distinguish:

- unit tests (no database at all);
- the SQLite suite;
- each of PostgreSQL, MySQL and MariaDB;
- the non-UTC PostgreSQL pass, which is where timestamp defects hide;
- the examples' own tests;
- lint, mypy and the docs link checker.

**Do not claim the suite passes when only SQLite ran.** SQLite's types are advisory, so a mapping
bug it accepts will fail in production. The claim that this framework works is a claim about the
three real dialects.

If a command fails on Docker, sandboxing or network access, say so rather than reporting a pass.

## Traps this suite has already fallen into

Each was a real bug in the tests, found by running:

- **MariaDB announcing itself as MySQL.** The container hands back a `mysql+pymysql` url. The
  scheme must be rewritten to `mariadb+pymysql` or "the MariaDB suite" tests MySQL.
- **A frozen clock makes outbox rows tie.** `event_date` is the only ordering key, so under
  `VirtualClock` every row of a run ties and order falls back to a random uuid. Assert on
  `action_name` or `action_id`.
- **`str(engine.url)` masks the password**, so a "separate connection" built from it connects to
  nothing.
- **A module-scoped container shares state between tests.** Truncate in the fixture.

## CI

Six jobs: lint and types, the fast suite, the examples, a tracing pass, the three dialects, and
PostgreSQL again under `TZ=America/New_York`. The website has its own workflow, triggered by
`docs/**` as well as `website/**`.
