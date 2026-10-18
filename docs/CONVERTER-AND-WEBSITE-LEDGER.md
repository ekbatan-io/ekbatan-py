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
| B1 | Inventory every Java/jOOQ/Gradle/Maven mention on the website and in the `docs/` copies, each marked remove / keep (working together) | B | todo |
| B2 | Rewrite: each feature explained on its own terms; the working-together facts gathered in one place, linked where needed | B | todo |
| B3 | Website polish ("extra love"): read every changed page rendered, desktop and phone width; build passes; docs/website in sync | A+B | A: done for the schemagen page (`EKB-0329`); B: todo |
| C | Merge both tracks; targeted tests, lint, examples, site build; full suite once | both | todo |

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

