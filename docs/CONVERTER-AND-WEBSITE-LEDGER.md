# Ledger: converters for schemagen, and a website that stands on its own

Asked on 2026-10-18, after the schemagen work (`EKB-0321`-`EKB-0326`):

- "in the java one can assign a converter, but here i see that the types should be of
  TypeDecorator ... the type that i'm interested in might not be of type TypeDecorator ... can you
  suggest something better?" - the design below was shown and accepted: "yes and also fix the
  docs/website/agents.md, and specially the website need extra love".
- "i want the python website to be independent from the java, unless we are really talking about
  the colaboration between a java and python".

Two tracks, run in parallel in separate worktrees, merged onto `main` at the end.

## Track A - converters (agreed design)

The user writes a plain class with two methods; no SQLAlchemy in it. `None` never reaches it.

```python
# the base class is optional, for type hints only
from ekbatan.schema import Converter

class MoneyConverter(Converter[Decimal, Money]):
    def from_db(self, value: Decimal) -> Money: return Money(value)
    def to_db(self, value: Money) -> Decimal: return value.amount
```

```toml
[[tool.ekbatan.schemagen.forced-types]]
converter = "app.converters.MoneyConverter"
include-expression = ".*\\.amount"
```

schemagen keeps the column's own database type and wraps the converter around it:

```python
Column("amount", converted(NUMERIC(precision=19, scale=4), MoneyConverter()), nullable=False)
Column("embedding", converted(sql_type("vector(3)"), VectorConverter()), nullable=False)
```

- `converter` is the way the docs teach. Any object with `from_db`/`to_db` works; the
  `Converter[DbT, UserT]` base is optional.
- A type SQLAlchemy does not know is written as `sql_type("<the database's own spelling>")`,
  so it builds with `create_all` and works without a forced type. It replaces today's `NullType()`
  + note.
- Today's `user-type` is renamed `sqlalchemy-type` (added today, nobody uses it): an optional extra
  for a ready-made SQLAlchemy type from a library (pgvector's `Vector`, GeoAlchemy2's `Geometry`).
  It adds convenience only - measured: a plain converter on a real pgvector column saved, read,
  and ordered by `<->` correctly.
- `converter` combines with any matcher (`include-types`, `include-expression`, the excludes,
  `nullability`), as the other targets do.

## Track B - the website without Java

The website (and its `docs/` copies, which stay in sync) speaks to a Python developer. Java,
jOOQ, Gradle and Maven appear only where a Java service and a Python service actually work
together (shared tables, the Redis lock, the JSON format, events read by a Java consumer) -
gathered so a reader finds them. Maintainer files keep their Java comparisons: `AGENTS.md`,
`DIVERGENCE.md`, `DECISIONS-TO-REVIEW.md`, the ledgers and parity records, `SHORTCOMINGS.md`.

## Steps

| # | Step | Track | State |
|---|---|---|---|
| A1 | `Converter`, `converted()`, `sql_type()` in `ekbatan.schema`; typed, `cache_ok`, `None` passed through | A | done (`EKB-0328`) |
| A2 | schemagen: `converter` target, `sqlalchemy-type` rename, unknown types as `sql_type(...)`; settings errors name the problem | A | done (`EKB-0328`) |
| A3 | Tests on every database: a converter by name and by type, round trip + `WHERE`, `create_all`; an unknown type (PostgreSQL `xml`, MariaDB `INET6`) builds and round-trips; pgvector with a converter (test-only image); the rename; a typo in the path fails before a database starts | A | done (`EKB-0328`) |
| A4 | Docs + website schemagen page, Java-free: a default mapping table per database, "write your own converter", when `sqlalchemy-type` is worth it; AGENTS.md and skills | A | done (`EKB-0328`, `EKB-0329`) |
| B1 | Inventory every Java/jOOQ/Gradle/Maven mention on the website and in the `docs/` copies, each marked remove / keep (working together) | B | done (`docs/website-nojava-inventory.md`, `EKB-0346`) |
| B2 | Rewrite: each feature explained on its own terms; the working-together facts gathered in one place, linked where needed | B | done (`EKB-0336`-`EKB-0344`) |
| B3 | Website polish ("extra love"): read every changed page rendered, desktop and phone width; build passes; docs/website in sync | A+B | A: done for the schemagen page (`EKB-0329`); B: done (`EKB-0344`, `EKB-0345`) |
| C | Merge both tracks; targeted tests, lint, examples, site build; full suite once | both | done: merged with the examples' forced types (`EKB-0350`-`EKB-0351`). Full suite 3,494 passed, 3 failed; gevent 1,579 passed, the same 3 failed - two tests that did not know `converters.py`, fixed in `EKB-0348` (17 passed). The pip and Poetry examples imported the wheel `make test` installs, not the source; fixed in `EKB-0348`, then all 21 examples passed. Setup guide 9 of 9 after `EKB-0347`; lint clean; site 55 pages |

## Findings

Track A, 2026-10-18:

- **The cache key.** Keyed on the converter's class, two converters of one class with different
  state shared a cached statement: `select(literal(100, converted(INTEGER(), Plus(10))))` read
  102 after `Plus(2)` ran, not 110 (run, with the key replaced). Keyed on the converter object
  (identity for an unhashable `@dataclass`) each gets its own. Pinned in
  `tests/unit/test_schema_converters.py`; no `cache_ok` warning.
- **Unknown types, on the old code.** With the new type matrix, `d97999d` errors on every
  PostgreSQL and MariaDB case at setup: `CompileError: Can't generate DDL for NullType()` for
  `c_xml` and `c_inet6`. Now `sql_type("xml")`, `sql_type("point")`, `sql_type("inet6")` build
  with `create_all`, reflect back as the same type and round-trip as `str`.
- **pgvector with a plain converter** (`pgvector/pgvector:pg17`, test-only): generated
  `converted(sql_type("vector(3)"), Vector())`; saved, read back as `list[float]`, ordered by
  `.op("<->")` nearest first, on the migrated database and on one built by `create_all`. A vector
  column with no forced type reads pgvector's text. `sqlalchemy-type =
  "pgvector.sqlalchemy.Vector(3)"` also checked, in a throwaway `uv run --with pgvector` overlay
  (no dependency added): `Vector(3)` generated, `.l2_distance` ordered correctly.
- **A converter receives what the column would give without it** - on a timestamp,
  `timestamp_column()`'s aware UTC `datetime`, on all four databases.
- **The stale-module guard** still fires with a converter imported (the `EKB-0325` test now runs
  for `sqlalchemy-type` and `converter`).
- **Typing** (`mypy --strict` on a probe): `converted(NUMERIC(19, 4), MoneyConverter())` is
  `Converted[Decimal, Money]`; a wrong `DbT` and a subclass missing `to_db` are both errors.
- **Counts**, schemagen tests (`test_schemagen*.py`): SQLite 120 passed, 5 skipped; PostgreSQL
  233 passed; MySQL 239 passed, 2 skipped; MariaDB 248 passed, 1 skipped. Unit tests: 1018
  passed. Skips are the dialect-only tests (pgvector, xml/INET6) and earlier ones.
- **Website.** The site's tables are `max-content` wide: the first draft's mapping table was 1332
  px in an 816 px column, the Python column off screen. Now three short columns, notes below;
  every table fits at 1400 px; at 390 px the page does not scroll sideways and the mapping
  tables wrap (page-scoped style). The sticky database picker is bounded to the mapping section.
  `npm run check` fails on `reference/wiring` ESCAPES, as it does on `d97999d` - not this track.
- **Final runs** on `EKB-0329`: `make lint` clean; `make examples` 21 of 21 examples passed and
  the SQLite run finished (poetry is not installed on this machine; it ran through a `uvx poetry`
  shim on `PATH`, nothing added to any project); `npm run build` 54 pages.

Track B, 2026-10-18:

- **Counts.** 701 lines on `main` (`97d726a`) named Java, jOOQ, Gradle, Maven, Flyway, Spring,
  Quarkus, Micronaut, Jackson, Redisson, the JVM, Hikari or JDBC: 647 on the website and the
  `docs/` copies of its pages, 54 in the example comments the setup guide shows. 583 removed,
  90 moved to the new page with a short link left, 28 kept. Per line in
  `docs/website-nojava-inventory.md`. The schemagen page and `docs/database/schemagen.md` had
  none.
- **The new page** is `reference/beside-java` ("Running beside a Java service"), docs copy
  `docs/beside-java.md`, paired in `scripts/docs-website-sync.py`, linked from the reference
  index (group "Other languages"), the footer and `docs/README.md`. It covers what to set
  (schema, namespace, shard layout), the shared tables, ids, event payloads, keyed locks,
  streaming, traces and logs, and the config file.
- **Wrong statements fixed on the way**, each checked in `src/` (list in DECISIONS-TO-REVIEW):
  retry matching, `Model`'s type parameters, `to_row`/`from_row`, handler and fan-out
  concurrency, the in-process lock provider, who creates indexes, the cross-shard commit log
  level, a backward-scan claim, stale anchors (`#turning-rows-into-objects`,
  `#5-add-a-migration`, `#wiring`, `#always-utc-and-no-zone`) and a link written as a docs path.
- **Rendered check** (dev server on 4325, stopped after): all 41 changed pages load; no page
  scrolls sideways at 390 px; no empty heading; no "Java's", "this port" or "both ports" in
  page text; every in-site `#anchor` on the website and in `docs/` resolves (a scratch script).
  At 1400 px the new page's three tables fit their column. Tables on many other pages scroll
  sideways inside their box - the site's own design, left alone.
- **The setup-guide check fails, on `main` too.** `uv run python scripts/check-setup-guide.py`
  fails on every database and installer, and `--db pg --install uv` fails the same way on a
  detached checkout of `main` (`97d726a`). Cause: `EKB-0350` gave the examples' model a
  `Currency` type, and the guide's project has no `converters.py` and no forced-types settings,
  so `schema.py` has a plain `CHAR(3)` and psycopg cannot write a `Currency` (9 of 15 tests
  fail). Not this track's to fix; belongs to C.
- **Final runs**: `make lint` clean (docs-website-sync 30 pairs, 0 below 80%; links resolve;
  sync-examples in step); `npm run build` 55 pages.
- **What is left**, from `grep -rniE 'java|jooq|gradle|maven|flyway|spring|quarkus|micronaut|jackson|redisson|jvm|hikari|jdbc'`
  over `website/src/pages`, the footer and nav, and the `docs/` copies (plus the example files
  the setup guide shows): 208 lines. 74 are the new page itself (both copies); many others match
  only because they hold a `beside-java` link. The rest, each a working-together sentence and/or
  its link unless marked:
  - shared tables / schema: `tables/index.mdx` 73-74, `tables/events.mdx` 128-129,
    `tables/scheduled-tasks.mdx` 18-19, `jobs/distributed-jobs.mdx` 272-273,
    `dialects/postgresql.mdx` 97-98, `dialects/mariadb.mdx` 130-131, `multi-database.mdx`
    69-70, 156-157; docs `tables.md` 70-71, `tables/events.md` 127-128,
    `tables/scheduled-tasks.md` 16-17, `distributed-jobs.md` 271-272, `postgresql.md` 95-96,
    `mariadb.md` 128-129, `multi-database.md` 67-68, 154-155, `database/README.md` 28-32,
    `concepts/README.md` 16-18, `concepts/outbox.md` 89
  - payload JSON: `learn/models-and-entities.mdx` 139, 141, 161; `model-event.mdx` 78-79;
    `learn/your-first-action.mdx` 219-220; `streaming.mdx` 327, 329; docs
    `models-and-entities.md` 177, 179, 199, `outbox.md` 104-105, `event-streaming.md` 322, 324;
    the MySQL example test 134-135
  - ids and shard bits: `learn/sharding.mdx` 37, 39; `learn/sharding-strategies.mdx` 70;
    `reference/sharding.mdx` 77-78, 213-214, 228; docs `concepts/sharding.md` 64,
    `database/sharding.md` 76-77, 275-276, 290
  - the lock: `keyed-locks.mdx` 167, 185-186; docs `keyed-locks.md` 166, 184-185
  - streaming (Kafka Connect runs Java; the transform JARs come from the Java project):
    `learn/outbox.mdx` 198, 200; `learn/consuming-events.mdx` 199-200; `streaming.mdx` 77, 79,
    317-318; docs `outbox.md` 124, 126, `event-streaming.md` 74, 76, 312-313
  - traces, logger names, config keys: `observability.mdx` 8-10, 90, 92, 107;
    `migrations.mdx` 34, 323, 330-331, 402 (the `io.ekbatan.flyway.FlywayMigrator` logger, and
    the See also link); `config-files.mdx` 60-61; docs `observability.md` 8-10, 90, 92, 107,
    `migrations.md` 31, 320, 327-328, 399, `config-files.md` 55-56
  - links to the new page only: `reference/index.astro` 82, `Footer.astro` 9, 61, 63,
    `docs/README.md` 26-27
  - not working-together, kept on purpose: `docs/README.md` 32, 36, 38 (the repository's list of
    maintainer files); code comments not shown on any page: `Footer.astro` 2, `Nav.astro` 2,
    `index.astro` 5, `learn/index.astro` 2-3, `reference/index.astro` 6, 9, 12
