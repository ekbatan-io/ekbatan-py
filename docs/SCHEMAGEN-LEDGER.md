# Ledger: a thorough schemagen, with forced types as Java's build has

Asked on 2026-10-18: "if it's really really our generator, i think i would like you to very very
very very very thoroughly improve the generator ... in the java code we had the option of forced
types (inside the gradle script), could we have similar thing here? ... similar to what we have in
java with sane choices, and good defaults and types and decisions".

`schemagen` (`src/ekbatan/schemagen.py`) is this port's own code: SQLAlchemy reflects the database
the migrations built, and schemagen writes `schema.py` from it. Java uses jOOQ's code generator,
configured in each example's Gradle or Maven build (`database.withForcedTypes(...)`, includes and
excludes, the input schema, the output package). `sqlacodegen` stays out, for the reason
`pyproject.toml` gives - that decision is not reopened here.

## Rules for this work

- **Java's build is the reference for what a user can configure** - forced types matched by
  column name and by SQL type, a user type or converter, includes and excludes, the schema - and
  for the defaults Ekbatan sets (timestamps, JSON, UUID). Copy what Java means, not its
  accidents.
- **The framework's strong recommendations hold**: UTC everywhere, zone-less `TIMESTAMP` /
  `DATETIME(6)` columns, never `TIMESTAMPTZ` (see `docs/database/multi-database.md#always-utc`).
  A type outside them is handled correctly but not encouraged.
- **No dependency is added or replaced** without the user. Configuration lives where a Python
  project keeps build-time settings, decided below and recorded.
- **Every type is proven on a real database**: generate `schema.py` from a migration using it,
  import it, `create_all` from it on a fresh database, and round-trip a value - on PostgreSQL,
  MySQL and MariaDB (and SQLite where it applies).
- **Judgement calls go to `docs/DECISIONS-TO-REVIEW.md`**, not to the user mid-way.
- Docs and website change together; the agent skills too.

## Steps

| # | Step | State |
|---|---|---|
| 1 | Inventory Java: every jOOQ codegen setting the Java examples use (Gradle and Maven), every forced type and converter Ekbatan ships, what jOOQ generates for each SQL type per dialect, and what triggers regeneration | todo |
| 2 | Inventory Python: what schemagen does today per type and per dialect, its CLI, its stale check, and round 2's N2 findings (VARCHAR(36) uuid guess, the unimportable types) | todo |
| 3 | Design: forced types (match by column, by type; what to force to), includes/excludes, schemas, dialect, output, where the configuration lives; defaults equal to what Ekbatan forces in Java | todo |
| 4 | The type table: every common SQL type on each database, its generated Python/SQLAlchemy type, decided and proven | todo |
| 5 | Build it: configuration, forced types, the type table, the uuid guess as Java's (MySQL/MariaDB, `CHAR(36)` only), keys/indexes/defaults as decided | todo |
| 6 | Tests: per type per database, generate + import + create_all + round trip; forced types; includes/excludes; stale check; a test that fails on the old code for each N2 finding | todo |
| 7 | Examples regenerated and passing; docs, website, AGENTS.md, skills | todo |
| 8 | Full run: suite on every database, examples, lint, site | todo |

## Findings

(filled in as the work runs)
