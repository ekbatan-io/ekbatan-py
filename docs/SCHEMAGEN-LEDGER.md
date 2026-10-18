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
| 1 | Inventory Java: every jOOQ codegen setting the Java examples use (Gradle and Maven), every forced type and converter Ekbatan ships, what jOOQ generates for each SQL type per dialect, and what triggers regeneration | done - F1 |
| 2 | Inventory Python: what schemagen does today per type and per dialect, its CLI, its stale check, and round 2's N2 findings (VARCHAR(36) uuid guess, the unimportable types) | done - F2 |
| 3 | Design: forced types (match by column, by type; what to force to), includes/excludes, schemas, dialect, output, where the configuration lives; defaults equal to what Ekbatan forces in Java | done - F3 |
| 4 | The type table: every common SQL type on each database, its generated Python/SQLAlchemy type, decided and proven | done - F4 |
| 5 | Build it: configuration, forced types, the type table, the uuid guess as Java's (MySQL/MariaDB, `CHAR(36)` only), keys/indexes/defaults as decided | done - `EKB-0321` |
| 6 | Tests: per type per database, generate + import + create_all + round trip; forced types; includes/excludes; stale check; a test that fails on the old code for each N2 finding | done - F5 |
| 7 | Examples regenerated and passing; docs, website, AGENTS.md, skills | done - F6 |
| 8 | Full run: suite on every database, examples, lint, site | done - F7 |

## Findings

**F1 - Java (read: the 40 example builds, `grep` over every `build.gradle.kts` and `pom.xml`;
`docs/database/jooq-codegen.md`; round 2's jOOQ runs `parity-probes/round2-N2/jooq-*.java.out`).**
Settings the examples use: `schemas`/`inputSchema` (PG `public` + `eventlog`, MySQL/MariaDB the
one database), `outputSchemaToDefault` (the main schema unqualified), `includes` `.*`, `excludes`
`flyway_schema_history` (`|scheduled_tasks` on 12 Maven builds), `includeFlywayTable(false)`, the
output directory and package, and `forcedTypes`. Every forced type, counted: `Instant` +
`InstantConverter` on `TIMESTAMP` (PG, 10) or `(?i:DATETIME|TIMESTAMP)` (MySQL/MariaDB, 12);
`ObjectNode` + `JSONBObjectNodeConverter` on `JSONB` (PG, 10); `ObjectNode` +
`JSONObjectNodeConverter` on `(?i:JSON)` (MySQL/MariaDB, 12); `UUID` + `mysql.UuidStringConverter`
on `CHAR\(36\)` named `.*\.id|.*_id` (MySQL only, 6). All with `includeExpression` `.*` except the
uuid one. Converters shipped: Instant, JSON(B) Object/ArrayNode (4), mysql UuidString/UuidBinary.
jOOQ maps everything else itself (BOOLEAN on MySQL/MariaDB to `Byte`, ENUM to a generated enum,
BLOB/BYTEA to `byte[]`, arrays with item type, MariaDB JSON to `String` - it reports LONGTEXT so
the JSON forced type never fires there), and generates views, keys, indexes and defaults.
Regeneration: Gradle reruns the task when migrations or the build script change.

**F2 - Python today (read `src/ekbatan/schemagen.py` whole; ran reflection of 48 PG, 44 MySQL and
47 MariaDB column types on real servers, `scratchpad/sg-survey/survey.py`).** Forcing by
SQLAlchemy class name only (`FORCED`), so `TIMESTAMPTZ` is forced like `TIMESTAMP`; uuid guess
takes `CHAR`/`VARCHAR`(36) named `id|*_id` on every dialect (N2 #2); anything else goes through
`_generic`, which imports a dialect name from `sqlalchemy` (N2 #4: `TINYINT`, `ENUM`, `BYTEA`,
`LONGTEXT`, `TINYBLOB`, `MEDIUMINT`, `YEAR`, `SET`, `BIT`, `INET`... do not import) and drops
`ARRAY`'s item type. Keeps only PK, NOT NULL and PG unique constraints; drops FKs, indexes,
defaults, checks, comments, identity, computed columns; reads only the default schema, no views,
no includes/excludes, no configuration file. The docs claim MariaDB JSON is forced to
`json_column()`: false - reflection says `LONGTEXT(collation='utf8mb4_bin')`, and the CHECK
(`json_valid`) is only in `information_schema.CHECK_CONSTRAINTS` (LEVEL `Column`). MySQL `DOUBLE`
reflects `asdecimal=True` (a `Decimal`, not a float); MariaDB adds display widths to every
integer; native MariaDB `UUID` reflects as generic `UUID` and round-trips; PG `xml`/`point` and
MariaDB `INET4`/`INET6` reflect as `NullType`; SQLite reflects a declared `UUID` as `NUMERIC`.

**F3 - Design (decided; each call is in `DECISIONS-TO-REVIEW.md`, "schemagen with forced
types").** Configuration in `[tool.ekbatan.schemagen]` of `pyproject.toml`, CLI flags override.
Forced types as jOOQ's: `name` (a framework helper or a SQLAlchemy type) or `user-type` (a dotted
path), matched by `include-expression` (column, `table.column`, `schema.table.column`) and
`include-types` (the type as the database and SQLAlchemy spell it), with excludes and
nullability; first match wins, the user's before Ekbatan's built-in five (Java's). Includes and
excludes on table names, `schemas` (the first unqualified, as `outputSchemaToDefault`), views in
their own `MetaData`, FKs, indexes, checks, defaults, identity, computed columns and comments
carried so `metadata.create_all` builds what the migrations built.

**F4 - The type table (run: `tests/integration/test_schemagen_types.py --dialects=all`, 636
passed).** 156 cases - PostgreSQL 43, MySQL 47, MariaDB 48, SQLite 18 - each generated from a
migration on a real database, imported, built again by `create_all` on a fresh database (the
column type compared with the migrated one, as the database spells it), and a value written and
read back through both databases, compared by value *and* Python type, plus a `WHERE` on it. The
table itself is in `docs/database/schemagen.md`. Found while proving it: an extension type
(`citext`) needs `CREATE EXTENSION` before `create_all` (documented, not generated); PostgreSQL
`MONEY`/`BIT` cannot be compared with a str through SQLAlchemy, generated or not (`where` off);
MySQL reflects charset and collation together (kept, both).

**F5 - Tests (run).** `tests/unit/test_schemagen_config.py` 60 (configuration, matching,
targets, the type table by hand); `tests/integration/test_schemagen.py` (the framework's tables,
keys/indexes/defaults/checks/identity/computed/comments compared before and after `create_all`,
views, other schemas, forced types by name and by type with user types by dotted path,
precedence, defaults off, includes/excludes, the configuration file and every flag, the password
never printed, the stale guard) - 91 on the servers, 38 + 3 skipped on SQLite;
`tests/integration/test_schemagen_round2.py`: put back on the old generator
(`git show d9bfb35:src/ekbatan/schemagen.py`) 7 of 8 fail (N2 #2 on all four databases, N2 #4 on
the three servers; SQLite's common types imported already), all 8 pass on the new one. Planted
bugs caught: server defaults dropped and indexes dropped each fail the structure test on all four
databases; MySQL `DOUBLE` written as its own type (a Decimal) fails 6 type tests.

**F6 - Examples and docs.** Examples carry `[tool.ekbatan.schemagen]` (written by
`scripts/sync-examples.py`) and run `python -m ekbatan.schemagen` bare; `make schema`
regenerated all six. Docs: `docs/database/schemagen.md` rewritten (settings, the Java-to-Python
table, forced types, built-in defaults, the type table), its website copy, `getting-started.md`
and `install.mdx`, a note in the setup guide, MariaDB/multi-database corrections (the MariaDB JSON
claim is now true; Java's forced type never fires there), `DIVERGENCE.md` (one **chosen**
section), `SHORTCOMINGS.md`, `AGENTS.md`, the three skills (synced).

**F7 - The full run (2026-10-18).** `pytest -q --dialects=all`: 3423 passed, 58 skipped, 2 failed.
The 2 are `test_redis_locks.py`'s recording tests: `tests/fixtures/redisson/java-3.39.0.monitor.log`
is caught by `.gitignore`'s `*.log`, so it exists in the main checkout and in no clone; with it
copied in they pass. Not this work's; the main session should decide (un-ignore it, or rename it).
The first full run also found a fault of this work's: the type matrix cached its databases per
dialect, and the PostgreSQL container is restarted between modules - 87 failures, fixed by caching
per backend (`EKB-0324`). `make lint` passes; `make examples` passes all 21; `npm run build` builds
54 pages.

