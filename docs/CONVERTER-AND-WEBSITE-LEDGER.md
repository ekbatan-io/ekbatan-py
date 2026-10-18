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
| A1 | `Converter`, `converted()`, `sql_type()` in `ekbatan.schema`; typed, `cache_ok`, `None` passed through | A | todo |
| A2 | schemagen: `converter` target, `sqlalchemy-type` rename, unknown types as `sql_type(...)`; settings errors name the problem | A | todo |
| A3 | Tests on every database: a converter by name and by type, round trip + `WHERE`, `create_all`; an unknown type (PostgreSQL `xml`, MariaDB `INET6`) builds and round-trips; pgvector with a converter (test-only image); the rename; a typo in the path fails before a database starts | A | todo |
| A4 | Docs + website schemagen page, Java-free: a default mapping table per database, "write your own converter", when `sqlalchemy-type` is worth it; AGENTS.md and skills | A | todo |
| B1 | Inventory every Java/jOOQ/Gradle/Maven mention on the website and in the `docs/` copies, each marked remove / keep (working together) | B | todo |
| B2 | Rewrite: each feature explained on its own terms; the working-together facts gathered in one place, linked where needed | B | todo |
| B3 | Website polish ("extra love"): read every changed page rendered, desktop and phone width; build passes; docs/website in sync | A+B | todo |
| C | Merge both tracks; targeted tests, lint, examples, site build; full suite once | both | todo |

## Findings

(filled in as the work runs)
