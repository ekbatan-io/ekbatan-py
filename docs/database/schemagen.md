# schemagen

Table definitions that cannot drift from the database, because they are generated from it:
**start a throwaway database, run your migrations into it, read the result, write `schema.py`.**
Java does this with jOOQ in the build; this is the same job, configured the same way.

```
uv add --dev "ekbatan[codegen]"
```

```toml
# pyproject.toml
[tool.ekbatan.schemagen]
migrations = "src/myapp/migrations"
out = "src/myapp/schema.py"
image = "postgres:17-alpine"
```

```
uv run python -m ekbatan.schemagen
```

## Settings

They live in `pyproject.toml`, under `[tool.ekbatan.schemagen]` - where a Python project keeps
build-time settings, as Java keeps jOOQ's in `build.gradle.kts` or `pom.xml`. A flag on the
command wins over the file. Paths in the file are relative to the file.

| key | flag | default | what it is |
| --- | --- | --- | --- |
| `migrations` | `--migrations` | - | your migrations directory |
| `out` | `--out` | - | the module to write |
| `image` | `--image` | `postgres:17-alpine` | the throwaway database: `postgres:*`, `mysql:*`, `mariadb:*`, or `sqlite` (a temporary file, no container) |
| `url` | `--url` | - | an existing, empty database to use instead |
| `schemas` | `--schema` (repeat it) | `[]` | other schemas (MySQL: databases) to read too |
| `includes` | `--include` | `".*"` | tables to generate, a regular expression |
| `excludes` | `--exclude` | none | tables to leave out, a regular expression |
| `views` | `--no-views` | `true` | generate the views |
| `default-forced-types` | `--no-default-forced-types` | `true` | apply Ekbatan's built-in forced types |
| `forced-types` | - | `[]` | your forced types, below |
| - | `--config` | `./pyproject.toml` | another TOML file holding the table |

An unknown key is an error, so a typo does not silently do nothing. Keep `url` out of a
committed file if it has a password; pass it with `--url`. schemagen never prints it.

## Java's build, and the setting here

| Java (`generateJooqClasses`, `<generator>`) | here |
| --- | --- |
| `migrationLocations` | `migrations` |
| `outputDirectory`, `basePackageName` | `out` |
| `jooq { withContainer { image } }` | `image` |
| `schemas`, `<inputSchema>` | `schemas` (the connection's own schema is always read) |
| `outputSchemaToDefault` | always: the connection's own schema is written unqualified |
| `<includes>`, `<excludes>` | `includes`, `excludes` |
| `includeFlywayTable(false)` | always: migration history tables are never generated |
| `database.withForcedTypes(ForcedType()...)` | `[[tool.ekbatan.schemagen.forced-types]]` |
| `.withName("...")` | `name = "..."` |
| `.withUserType(...)` + `.withConverter(...)` | `user-type = "..."` - a `TypeDecorator` is both |
| `.withIncludeExpression(...)` | `include-expression = "..."` |
| `.withIncludeTypes(...)` | `include-types = "..."` |
| `.withExcludeExpression`, `.withExcludeTypes`, `.withNullability` | `exclude-expression`, `exclude-types`, `nullability` |
| the forced types every example copies | built in; `default-forced-types = false` to drop them |
| Gradle reruns the task when a migration changes | Make reruns it; the module checks its fingerprint on import |

## Forced types

A forced type picks columns and says what type they get - jOOQ's `forcedTypes`, the same rules:

```toml
[[tool.ekbatan.schemagen.forced-types]]
user-type = "myapp.types.Money"
include-expression = ".*\\.amount"

[[tool.ekbatan.schemagen.forced-types]]
name = "Text"
include-types = "MEDIUMTEXT|LONGTEXT"
```

**What the columns become** - exactly one of:

- `name`: one of the framework's helpers (`uuid_column`, `json_column`, `timestamp_column`) or a
  SQLAlchemy type written as in Python - `Text`, `String(36)`, `Numeric(19, 4)`,
  `postgresql.INET`, `mysql.TINYINT(unsigned=True)`.
- `user-type`: your own type by dotted path - `myapp.types.Money`, `myapp.types:Money`, or with
  arguments, `myapp.types.Money(4)`. A class (usually a `TypeDecorator`), a function returning a
  type, or a type instance. schemagen imports it to check it, so keep it in a module that does
  not import the generated schema.

**Which columns** - regular expressions that must match the whole text, as jOOQ's do:

- `include-expression` - the column name, tried as `column`, `table.column` and, for a schema
  in `schemas`, `schema.table.column`. Case matters. Java's `.*\.id|.*_id` works unchanged.
- `include-types` - the column's type, tried in each spelling: the database's own
  (`character varying(36)`, `tinyint(1)`, `int unsigned`), SQLAlchemy's (`VARCHAR(36)`), each
  with and without its arguments, and the short names (`TIMESTAMP`, `TIMESTAMPTZ`). Case does
  not matter.
- `exclude-expression`, `exclude-types` take columns back out; `nullability` is `ALL` (the
  default), `NULL` or `NOT_NULL`.

At least one include is required. **The first forced type that matches wins**, and yours are
tried before Ekbatan's, as in jOOQ. A typo - a type that does not exist, a module that does not
import - fails before any database starts, naming the forced type.

A user type, for example - Java's `userType` and `converter` in one class:

```python
# myapp/types.py
from decimal import Decimal
from sqlalchemy import Numeric
from sqlalchemy.types import TypeDecorator

class Money(TypeDecorator[Decimal]):
    impl = Numeric(19, 2)
    cache_ok = True

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value).quantize(Decimal("0.01"))
```

### Ekbatan's built-in forced types

What every Java example's build forces, built in, after yours:

| columns | become | on | Java's |
| --- | --- | --- | --- |
| `TIMESTAMP`, `DATETIME` (any precision, no zone) | `timestamp_column()` | all | `Instant` + `InstantConverter` |
| `JSONB` | `json_column()` | PostgreSQL | `ObjectNode` + `JSONBObjectNodeConverter` |
| `JSON` | `json_column()` | MySQL, MariaDB, SQLite | `ObjectNode` + `JSONObjectNodeConverter` |
| `UUID` | `uuid_column()` | PostgreSQL | jOOQ's own `UUID` |
| `CHAR(36)` named `.*\.id\|.*_id` | `uuid_column()` | MySQL, MariaDB | `UUID` + `UuidStringConverter` |

The last one is a guess, and a narrow one: nothing in MySQL says a `CHAR(36)` holds a uuid, so
only that exact type, only under an id-like name, as in Java. A `VARCHAR(36)` stays text.
MariaDB's own `UUID` type needs no guess. SQLite has no uuid type: say which columns are uuids,

```toml
[[tool.ekbatan.schemagen.forced-types]]
name = "uuid_column"
include-types = "CHAR\\(32\\)"
include-expression = ".*\\.id|.*_id"
```

## The type table

Every column not forced is written as the SQLAlchemy type that builds the same column: a generic
type from `sqlalchemy` where one says the same, the dialect's own type
(`from sqlalchemy.dialects import mysql, postgresql`) where not. What only reflection adds is
dropped - MariaDB's display widths, `BIGINT(20)`. Each row is proven on a real database: generated,
imported, built again by `create_all`, a value written and read back
(`tests/integration/test_schemagen_types.py`: 43 PostgreSQL types, 47 MySQL, 48 MariaDB, 18 SQLite).

| SQL | written as |
| --- | --- |
| `SMALLINT`, `INTEGER`, `BIGINT` | `SMALLINT()`, `INTEGER()`, `BIGINT()` |
| MySQL `TINYINT`, `MEDIUMINT`, any `UNSIGNED` | `mysql.TINYINT()`, `mysql.INTEGER(unsigned=True)`, ... |
| `BOOLEAN`, and MySQL's `TINYINT(1)` | `BOOLEAN()` |
| `NUMERIC(p, s)`, `DECIMAL(p, s)` | `NUMERIC(precision=p, scale=s)`, `DECIMAL(...)` |
| `REAL`, `DOUBLE PRECISION`, `FLOAT`, MySQL `DOUBLE` | `REAL()`, `DOUBLE_PRECISION()`, `FLOAT()`, `DOUBLE()` - all read a float |
| `CHAR(n)`, `VARCHAR(n)`, `TEXT` | `CHAR(length=n)`, `VARCHAR(length=n)`, `TEXT()`, with any collation |
| MySQL `TINYTEXT`, `MEDIUMTEXT`, `LONGTEXT`; a character set | `mysql.LONGTEXT()`, `mysql.VARCHAR(length=10, charset="latin1", ...)` |
| `BYTEA` | `LargeBinary()` |
| `BINARY(n)`, `VARBINARY(n)`, `BLOB` | `BINARY(length=n)`, `VARBINARY(length=n)`, `BLOB()` |
| MySQL `TINYBLOB`, `MEDIUMBLOB`, `LONGBLOB` | `mysql.TINYBLOB()`, ... |
| PostgreSQL `JSON` | `JSON(none_as_null=True)` - not forced, as in Java |
| MariaDB `JSON` (stored as `LONGTEXT` with a `json_valid` check) | `json_column()` |
| MariaDB `UUID` | `UUID()` |
| `TIMESTAMPTZ` | `TIMESTAMP(timezone=True)`, with a note |
| `DATE`, `TIME`, `TIMETZ` | `DATE()`, `TIME()`, `TIME(timezone=True)` |
| `TIME(p)` | `postgresql.TIME(precision=p)`, `mysql.TIME(fsp=p)` |
| MySQL `YEAR`, `BIT(n)` | `mysql.YEAR()`, `mysql.BIT(length=n)` |
| `INTERVAL` | `postgresql.INTERVAL(...)`, with its fields and precision |
| `ENUM(...)`, a PostgreSQL enum type | `Enum("a", "b")`, `Enum("a", "b", name="mood")` |
| MySQL `SET(...)` | `mysql.SET("a", "b")` |
| `TEXT[]`, `INTEGER[]`, `UUID[]` | `postgresql.ARRAY(TEXT())`, ... - with the item type |
| `INET`, `CIDR`, `MACADDR`, `MACADDR8`, `MONEY`, `BIT`, `VARBIT`, `TSVECTOR`, ranges, `OID`, `CITEXT` | `postgresql.INET()`, ... |
| a type SQLAlchemy cannot read (`xml`, `point`, MariaDB `INET6`) | `NullType()` and a note: force it to a type |

## What else is generated

Enough for `metadata.create_all` to build, in a test, the database the migrations built - what jOOQ
generates as `Keys`, `Indexes` and default values. Ekbatan uses none of it at run time.

- primary keys (composite ones in key order), unique constraints, foreign keys with their
  `ON DELETE`/`ON UPDATE`, checks, indexes (unique, partial, on expressions), server defaults,
  identity and generated columns, comments;
- **views**, in a second `view_metadata`: query them like tables; `create_all` on `metadata`
  does not create a table by a view's name;
- **other schemas**, when listed in `schemas`: written with `schema="audit"` and exported as
  `AUDIT_ENTRIES`. When a schema holds tables and is not listed, a note says so.

Not generated: database extensions (`CREATE EXTENSION citext` comes before `create_all`), and
a foreign key to a table that is not generated (a note says so).

## The framework's own tables

`events`, `event_notifications` and `scheduled_tasks` are written as `outbox_table(metadata)`,
`notifications_table(metadata)` and `scheduled_tasks_table(metadata)`, with their indexes from the
migrations - not read column by column. They are a contract other services bind to. If the
migrations differ from the helper, a note says how. `excludes = "scheduled_tasks"` leaves one
out, as twelve of Java's Maven builds do.

## UTC

Ekbatan strongly recommends UTC everywhere and zone-less columns - see
[Always UTC](multi-database.md#always-utc). The generator keeps to it and says where a schema
does not:

- `TIMESTAMP` (PostgreSQL) and `DATETIME(6)` (MySQL, MariaDB) become `timestamp_column()`:
  stored without a zone, read back as an aware `datetime` in UTC;
- a `TIMESTAMPTZ` keeps its zone, as Java's `OffsetDateTime` does, with a note;
- MySQL's `TIMESTAMP` type, a bare `DATETIME`, a `TIMESTAMP(3)`: forced as in Java, with a note
  saying what the column does differently from `DATETIME(6)`.

## The output is gitignored

Exactly as Java's `build/generated-jooq` is. A fresh clone has no `schema.py`, runs `make`, and
gets one - the same as a fresh clone of the Java project running `./gradlew build`.

That is the opposite of the *other* generator: `ekbatan.codegen`'s typed builders **are**
committed. A builder is derived from source you already have; a schema is derived from a
database you have to start.

## Wire it into Make

`uv` has no task runner and no dependency graph. Make fills it:

```make
src/myapp/schema.py: $(wildcard src/myapp/migrations/*.sql) pyproject.toml
	uv run python -m ekbatan.schemagen

test: src/myapp/schema.py
	uv run pytest
```

**It starts a database when a migration or the settings change, and does nothing at all when
they have not** - Gradle's up-to-date check, in one line. `lint` should depend on it too: mypy
reads the generated module.

## The stale-module guard

Nothing in Python runs before `python -m yourapp`, so the module checks itself: it carries a
fingerprint of the migrations it was generated from and checks it on import. A stale module
raises `ValidationError` saying to regenerate, instead of failing later as a missing column.
Java gets this from Gradle, which reruns the task before it compiles.

## Notes

What schemagen wants you to know is logged to `io.ekbatan.schemagen` as warnings, and the
command prints each as `note: ...`: a type it cannot read, a zoned timestamp, a timestamp
column unlike `DATETIME(6)`, a schema not read, a foreign key left out, a framework table that
differs from its helper.

## Why not sqlacodegen

It cannot force the per-dialect types, and a schema generated without that forcing corrupts data
on MySQL. That is recorded in `pyproject.toml` next to the extra, so the question does not get
re-asked.

## See also

- [Migrations](migrations.md) - what gets run into the database
- [Multiple dialects](multi-database.md) - the column helpers the forced types use
- [MariaDB](mariadb.md#the-scheme-has-to-say-mariadb) - why schemagen rewrites MariaDB's url
- [Getting started](../getting-started.md) - the Makefile, in context
