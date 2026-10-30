# schemagen

schemagen writes `schema.py` for you, from your migrations. It starts a throwaway database, runs
your migrations into it and reads the tables back. Then it writes one Python module: a SQLAlchemy
`Table` for each table, and beside each one a typed record of its row. **The module comes from the
real database, so it cannot drift from it.**

The throwaway database runs in Docker, so Docker has to be running. Two settings do without it:
`image = "sqlite"` uses a temporary file, and `url` uses a database you already have.

Install the extra, tell schemagen where your migrations are and where to write, and run it:

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

## What you get

From this migration:

```sql
CREATE TABLE orders (
    id UUID PRIMARY KEY,
    amount NUMERIC(19, 4) NOT NULL,
    note TEXT,
    created_date TIMESTAMP NOT NULL
);
```

schemagen writes a `Table`:

```python
ORDERS = sqlalchemy.Table(
    "orders",
    metadata,
    sqlalchemy.Column("id", uuid_column(), primary_key=True),
    sqlalchemy.Column("amount", sqlalchemy.NUMERIC(precision=19, scale=4), nullable=False),
    sqlalchemy.Column("note", sqlalchemy.TEXT()),
    sqlalchemy.Column("created_date", timestamp_column(), nullable=False),
)
```

and, beside it, the table's record:

```python
class OrdersRecord(dict[str, Any]):
    __slots__ = ()

    def __init__(self, *, id: UUID, amount: Decimal, note: str | None, created_date: Instant) -> None:
        super().__init__(id=id, amount=amount, note=note, created_date=created_date)

    if TYPE_CHECKING:  # for mypy only
        @overload  # type: ignore[override]
        def __getitem__(self, key: Literal["id"]) -> UUID: ...
        @overload
        def __getitem__(self, key: Literal["amount"]) -> Decimal: ...
        ...
        def __getitem__(self, key: str) -> Any: ...

    def column_id(self) -> UUID:
        return self["id"]

    def column_amount(self) -> Decimal:
        return self["amount"]
    ...
```

Import `ORDERS` and query it like any SQLAlchemy table. Type a repository's mapping with
`OrdersRecord` ([below](#the-record-beside-each-table)). Every column has a type, and every type
is listed in [the default mapping](#the-default-mapping) below. To get your own class back from a
column, such as a `Money` instead of a `Decimal`, [write a converter](#write-your-own-converter).

Each table is exported under its name in capitals: `orders` is `ORDERS`. That is why every
SQLAlchemy name is written through `import sqlalchemy` - `sqlalchemy.TEXT()`, not `TEXT()`. A
table named `text` is exported as `TEXT`, and would otherwise replace a `TEXT` imported from
`sqlalchemy`. Dialect types are written through their module the same way: `postgresql.JSONB()`.

## Settings

Settings go in `pyproject.toml`, under `[tool.ekbatan.schemagen]`. A flag on the command line
wins over the file. Paths in the file are relative to the file.

| key | flag | default | what it is |
| --- | --- | --- | --- |
| `migrations` | `--migrations` | - | your migrations directory |
| `out` | `--out` | - | the module to write |
| `image` | `--image` | `postgres:17-alpine` | the throwaway database |
| `url` | `--url` | - | use this database instead |
| `schemas` | `--schema`, repeated | `[]` | more schemas to read |
| `includes` | `--include` | `".*"` | tables to generate |
| `excludes` | `--exclude` | none | tables to leave out |
| `views` | `--no-views` | `true` | generate the views |
| `default-forced-types` | `--no-default-forced-types` | `true` | the built-in forced types |
| `forced-types` | - | `[]` | your forced types |
| - | `--config` | `./pyproject.toml` | another TOML file |

`image` is `postgres:*`, `mysql:*`, `mariadb:*`, or `sqlite` (a temporary file, no container). On
MySQL and MariaDB, a schema in `schemas` is a database. `includes` and `excludes` are regular
expressions over table names. Forced types are [below](#forced-types).

A setting left empty (`image = ""`) is refused: remove the line to get the default. Empty never
means the default, because `excludes = ""` would be a pattern that matches every table.

An unknown key is an error, so a typo does not silently do nothing. `url` must point at an empty
database. If it holds a password, keep it out of a committed file and pass it with `--url`.
schemagen never prints it.

The schema you connect to is always read. Its tables are written without a schema name, so the
same module works whatever the database is called. The migration history table is never
generated.

## Wire it into Make

`uv` has no task runner. Make fills the gap:

```make
src/myapp/schema.py: $(wildcard src/myapp/migrations/*.sql) pyproject.toml
	uv run python -m ekbatan.schemagen

test: src/myapp/schema.py
	uv run pytest
```

**It starts a database only when a migration or the settings have changed.** Make `lint` depend
on it too: mypy reads the generated module.

## The output is gitignored

Keep `schema.py` out of git: list it in `.gitignore`. A fresh clone has none, and running `make`
writes one.

That is the opposite of the *other* generator: `ekbatan.codegen`'s typed builders **are**
committed. A builder comes from source you already have; a schema comes from a database you have
to start.

## The stale-module guard

Python has no build step before your application starts, so nothing else would notice a
`schema.py` that is older than its migrations. The module checks itself instead. It carries a
fingerprint of the migrations it came from, and checks it when it is imported. A stale module
raises `ValidationError` telling you to regenerate, instead of failing later on a missing column.

It also carries `SCHEMA_FORMAT`, the shape of module this schemagen writes. A newer ekbatan may
write a different shape. Then a module written by the older schemagen refuses to import, even
though its migrations have not changed:

```
schema.py was written by an older schemagen (format 6; this ekbatan writes 7). Regenerate it ...
```

A module written by a *newer* ekbatan than the one installed says so - "written by a newer
schemagen" - and tells you to upgrade ekbatan, because regenerating with the older one would lose
what the newer one wrote.

A build that regenerates only when the migrations change, like the examples' `Makefile`, keeps
the old file. So run `make clean && make`, or delete the file and run `python -m ekbatan.schemagen`.

The fingerprint check is skipped where the migrations are not on disk, as in an installed wheel.
The format check is not: a module and an ekbatan that disagree fail there too.

## The record beside each table

Beside every table, schemagen writes the table's **record**: a `dict` class named after the
table, with one key per column, each typed as the column reads. `orders` gives `OrdersRecord`,
and `event_notifications` gives `EventNotificationsRecord`.

You build one by keyword, `OrdersRecord(id=..., amount=..., ...)`. You read a column as
`record.column_amount()` - the preferred way - or as `record["amount"]`, which is type-checked
the same.

A repository's `to_record` returns a record, and its `from_record` takes one
([repositories](repositories.md)). So mypy checks every mapping against the table: a column left
out, misspelt, added or given the wrong type is a type error, and so is reading a key or a method
the table does not have.

To SQLAlchemy, a record is just the `dict` an insert is given. The `__getitem__` overloads that
type each key sit under `TYPE_CHECKING`, so they cost nothing when the code runs.

Each key's type is read from the type the module builds for that column:

| the column | its type in the record |
| --- | --- |
| a plain SQL type | what SQLAlchemy reads it as: `int`, `str`, `Decimal` for `NUMERIC`, `float` for `DOUBLE`, `bool`, `bytes`, `date`, `time`, `timedelta` |
| `uuid_column()` | `UUID` |
| `timestamp_column()` | `Instant` - the value as written, no zone added; `LocalDateTime` where a forced type says so ([time columns](#time-columns)) |
| `TIMESTAMPTZ` | `OffsetDateTime` |
| a JSON column, `json_column()` | `JsonValue`, from `ekbatan.schema`: an object, an array, text, a number, a boolean or `None` |
| an enum | a `Literal` of its values: `Literal["NEW", "DONE"]` |
| an array, a MySQL `SET` | `list[...]` of its item, `set[str]` |
| PostgreSQL `INET`, `CIDR`, ranges | `IPv4Address \| IPv6Address`, `IPv4Network \| IPv6Network`, `Range[int]` and the like |
| a column with your **converter** | your converter's own type - `Money`, not `Decimal` ([converters](#write-your-own-converter)) |
| a `sqlalchemy-type` | what its `process_result_value` is annotated to return, or what its `impl` reads as ([sqlalchemy-type](#when-to-use-sqlalchemy-type)) |
| a type nothing says anything about (`sql_type("xml")`, PostgreSQL `BIT`, which two drivers read differently) | `Any`, and the run prints a note saying so - give it a converter to type it |

A nullable column is `X | None`.

A column the database fills itself - a server default, an identity, a generated column, an
auto-increment - is a keyword that defaults to `UNSET`: `created: Instant | Unset = UNSET`. Left at
that, the key is not in the record, so an insert leaves the column out, as it must for a generated
one. Read from the database, the key is there, so `record.column_created()` is an `Instant`.

**Every column gets a method named `column_` and its name.** The prefix keeps any name safe: no
clash with a `dict` method, a keyword or a leading digit.

A schema written by hand, without schemagen, writes its records by hand in the same style.

### Unusual names

A column whose name is not a valid Python name still gets a method and a keyword:

- **The method** has each character a Python name cannot hold made `_`: `order date` gives
  `column_order_date()`.
- **The keyword** of `__init__` is the column's own name where that is valid. Otherwise it is
  `class_` for `class`, `order_date` for `order date`, `_2fa` for `2fa`, `_meta` for `__meta`, and
  `self_` for `self`. The key is always the column's real name.
- **Two columns that would share a keyword or a method** - `order date` and `order_date` - are told
  apart by `_2`, `_3`..., given to the one whose name had to change, and the run says so.

The [repositories page](repositories.md#the-record) has the full table.

The module imports what the records name - `from myapp.money import Money`, `from uuid import
UUID`. A name that is already taken in the module (a SQLAlchemy type, a helper, a table, a record,
another class of the same name) is imported under an alias instead: `myapp_types_UUID`. A column
named like a type its record uses (a `date` column of type `date`) is fine: in the keyword
`date: date` the annotation still means the type. Two tables whose records would share a name stop
the run, as two tables sharing a constant do.

## The default mapping

For each database: the column type in your migration, what `schema.py` writes for it, and the
Python value you read back. Each row is tested on a real database: generated, imported, built
again by `metadata.create_all`, and a value written and read back.

### PostgreSQL

| SQL column | `schema.py` | Python value |
| --- | --- | --- |
| `SMALLINT`, `INTEGER`, `BIGINT` | `SMALLINT()`, `INTEGER()`, `BIGINT()` | `int` |
| `NUMERIC(p, s)`, `DECIMAL(p, s)` | `NUMERIC(precision=p, scale=s)` | `Decimal` |
| `REAL` | `REAL()` | `float` |
| `DOUBLE PRECISION` | `DOUBLE_PRECISION()` | `float` |
| `BOOLEAN` | `BOOLEAN()` | `bool` |
| `CHAR(n)`, `VARCHAR(n)` | `CHAR(length=n)`, `VARCHAR(length=n)` | `str` |
| `TEXT` | `TEXT()` | `str` |
| `CITEXT` | `postgresql.CITEXT()` | `str` |
| `BYTEA` | `LargeBinary()` | `bytes` |
| `UUID` | `uuid_column()` | `uuid.UUID` |
| `JSONB` | `json_column()` | `dict`, `list` |
| `JSON` | `JSON(none_as_null=True)` | `dict`, `list` |
| `TIMESTAMP`, `TIMESTAMP(p)` | `timestamp_column()` | `Instant`, as stored |
| `TIMESTAMPTZ` | `TIMESTAMP(timezone=True)` | `OffsetDateTime` |
| `DATE` | `DATE()` | `date` |
| `TIME`, `TIMETZ` | `TIME()`, `TIME(timezone=True)` | `time` |
| `TIME(p)` | `postgresql.TIME(precision=p)` | `time` |
| `INTERVAL` | `postgresql.INTERVAL()` | `timedelta` |
| `TEXT[]`, `INTEGER[]`, ... | `postgresql.ARRAY(TEXT())`, ... | `list` |
| `INET` | `postgresql.INET()` | `IPv4Address`, `IPv6Address` |
| `CIDR` | `postgresql.CIDR()` | `IPv4Network`, `IPv6Network` |
| `MACADDR`, `MONEY`, `BIT(n)`, ... | `postgresql.MACADDR()`, ... | `str` |
| `INT4RANGE`, `TSRANGE`, ... | `postgresql.INT4RANGE()`, ... | `Range` |
| `OID` | `postgresql.OID()` | `int` |
| an enum type | `Enum("happy", "sad", name="mood")` | `str` |
| a domain | `postgresql.DOMAIN("positive_int", INTEGER(), ...)` | its base type's |
| `INTEGER[][]` | `postgresql.ARRAY(INTEGER(), dimensions=2)` | `list[list[int]]` |
| an array of a domain | `postgresql.ARRAY(postgresql.DOMAIN(...))` | any - see below |
| `"char"`, `name` | `sql_type("\"char\"")`, `sql_type("name")` | any, `str` |
| `xml`, `tsquery`, `jsonpath` | `sql_type("xml")`, ... | `str` |
| `point`, `vector(3)`, ... | `sql_type("point")`, ... | what the driver gives |

- A JSON column reads whatever the JSON holds: a `dict`, a `list`, a number or a string.
- `timestamp_column()` reads the `datetime` exactly as stored - no zone added
  ([What you give is what is stored](multi-database.md#what-you-give-is-what-is-stored)); its
  record says `Instant`. A `TIMESTAMPTZ` reads a `datetime` with a zone, as the driver returns
  it - an `OffsetDateTime` - and a note suggests plain `TIMESTAMP` ([time columns](#time-columns)).
- `INET` and `CIDR` values come from Python's `ipaddress` module. `Range` is
  `sqlalchemy.dialects.postgresql.Range`.
- The `MACADDR` row also covers `MACADDR8`, `VARBIT(n)` and `TSVECTOR`. A `BIT(n)` reads as
  `str` through psycopg and as a `BitString` through asyncpg, so its record says `Any`.
- An enum type is one made with `CREATE TYPE mood AS ENUM ('happy', 'sad')`.
- An array of a domain reads as a `list` through asyncpg, but psycopg does not know a domain's
  array type and hands back its text, which SQLAlchemy splits into characters. So its record
  says `Any`, and a note says so. Read it cast to the base type's array,
  `sqlalchemy.cast(column, postgresql.ARRAY(sqlalchemy.INTEGER()))`, for a `list` on both.
- PostgreSQL's one-byte `"char"` and its `name` are written as PostgreSQL spells them;
  SQLAlchemy reads them as a plain string type that `create_all` would build `VARCHAR`. A
  `"char"` reads as `str` through psycopg and as `bytes` through asyncpg, so its record says
  `Any`.
- The last row is any type SQLAlchemy has no class for, written as PostgreSQL spells it. Its
  value is what the driver gives, usually a `str`.

### MySQL

| SQL column | `schema.py` | Python value |
| --- | --- | --- |
| `SMALLINT`, `INT`, `BIGINT` | `SMALLINT()`, `INTEGER()`, `BIGINT()` | `int` |
| `TINYINT`, `MEDIUMINT` | `mysql.TINYINT()`, `mysql.MEDIUMINT()` | `int` |
| `INT UNSIGNED`, ... | `mysql.INTEGER(unsigned=True)`, ... | `int` |
| `BOOLEAN`, `TINYINT(1)` | `BOOLEAN()` | `bool` |
| `DECIMAL(p, s)`, `NUMERIC(p, s)` | `DECIMAL(precision=p, scale=s)` | `Decimal` |
| `FLOAT` | `FLOAT()` | `float` |
| `DOUBLE`, `REAL` | `DOUBLE()` | `float` |
| `BIT(n)` | `mysql.BIT(length=n)` | `int` |
| `CHAR(n)`, `VARCHAR(n)` | `CHAR(length=n)`, `VARCHAR(length=n)` | `str` |
| `TEXT` | `TEXT()` | `str` |
| `TINYTEXT`, `LONGTEXT`, ... | `mysql.TINYTEXT()`, ... | `str` |
| `VARCHAR(10) CHARACTER SET latin1` | `mysql.VARCHAR(..., charset="latin1")` | `str` |
| `BINARY(n)`, `VARBINARY(n)` | `BINARY(length=n)`, `VARBINARY(length=n)` | `bytes` |
| `BLOB` | `BLOB()` | `bytes` |
| `TINYBLOB`, `LONGBLOB`, ... | `mysql.TINYBLOB()`, ... | `bytes` |
| `JSON` | `json_column()` | `dict`, `list` |
| `DATETIME(6)`, `DATETIME`, `TIMESTAMP` | `timestamp_column()` | `Instant`, as stored |
| `DATE` | `DATE()` | `date` |
| `TIME` | `TIME()` | `time` |
| `TIME(p)` | `mysql.TIME(fsp=p)` | `time` |
| `YEAR` | `mysql.YEAR()` | `int` |
| `ENUM('NEW', 'DONE')` | `Enum("NEW", "DONE")` | `str` |
| `SET('a', 'b')` | `mysql.SET("a", "b")` | `set` |
| `POINT`, `GEOMETRY`, ... | `sql_type("point")`, ... | `bytes` |

- `timestamp_column()` reads the `datetime` exactly as stored - no zone added
  ([What you give is what is stored](multi-database.md#what-you-give-is-what-is-stored)). A bare `DATETIME` and
  MySQL's `TIMESTAMP` type get a note saying how they differ from `DATETIME(6)`.
- A uuid stored as `CHAR(36)` reads as a `str` until you say which columns hold one - see
  [a uuid stored as text](#a-uuid-stored-as-text).
- A character set or a collation of its own makes the column MySQL's own `mysql.VARCHAR`, with
  both written out.
- A `SET` with a blank member is `mysql.SET("", "a", retrieve_as_bitwise=True)`, as SQLAlchemy
  requires. An `ENUM` or `SET` value holding a backslash is written as MySQL spells the type,
  `sql_type("enum(...)")` - SQLAlchemy's own would put the backslash into `CREATE TABLE`
  unescaped - and its record still lists the values. The same goes for MariaDB.
- A type SQLAlchemy has no class for is written as MySQL spells it, `sql_type("...")`. Its value
  is what the driver gives.

### MariaDB

| SQL column | `schema.py` | Python value |
| --- | --- | --- |
| `SMALLINT`, `INT`, `BIGINT` | `SMALLINT()`, `INTEGER()`, `BIGINT()` | `int` |
| `TINYINT`, `MEDIUMINT` | `mysql.TINYINT()`, `mysql.MEDIUMINT()` | `int` |
| `INT UNSIGNED`, ... | `mysql.INTEGER(unsigned=True)`, ... | `int` |
| `BOOLEAN`, `TINYINT(1)` | `BOOLEAN()` | `bool` |
| `DECIMAL(p, s)`, `NUMERIC(p, s)` | `DECIMAL(precision=p, scale=s)` | `Decimal` |
| `FLOAT` | `FLOAT()` | `float` |
| `DOUBLE`, `REAL` | `DOUBLE()` | `float` |
| `BIT(n)` | `mysql.BIT(length=n)` | `int` |
| `CHAR(n)`, `VARCHAR(n)` | `CHAR(length=n)`, `VARCHAR(length=n)` | `str` |
| `UUID` | `UUID()` | `uuid.UUID` |
| `TEXT` | `TEXT()` | `str` |
| `TINYTEXT`, `LONGTEXT`, ... | `mysql.TINYTEXT()`, ... | `str` |
| `VARCHAR(10) CHARACTER SET latin1` | `mysql.VARCHAR(..., charset="latin1")` | `str` |
| `BINARY(n)`, `VARBINARY(n)` | `BINARY(length=n)`, `VARBINARY(length=n)` | `bytes` |
| `BLOB` | `BLOB()` | `bytes` |
| `TINYBLOB`, `LONGBLOB`, ... | `mysql.TINYBLOB()`, ... | `bytes` |
| `JSON` | `json_column()` | `dict`, `list` |
| `DATETIME(6)`, `DATETIME`, `TIMESTAMP` | `timestamp_column()` | `Instant`, as stored |
| `DATE` | `DATE()` | `date` |
| `TIME` | `TIME()` | `time` |
| `TIME(p)` | `mysql.TIME(fsp=p)` | `time` |
| `YEAR` | `mysql.YEAR()` | `int` |
| `ENUM('NEW', 'DONE')` | `Enum("NEW", "DONE")` | `str` |
| `SET('a', 'b')` | `mysql.SET("a", "b")` | `set` |
| `INET4`, `INET6` | `sql_type("inet6")`, ... | `str` |
| `POINT`, `GEOMETRY`, ... | `sql_type("point")`, ... | `bytes` |

- `timestamp_column()` reads the `datetime` exactly as stored - no zone added
  ([What you give is what is stored](multi-database.md#what-you-give-is-what-is-stored)). A bare `DATETIME` and
  the `TIMESTAMP` type get a note saying how they differ from `DATETIME(6)`.
- MariaDB stores `JSON` as `LONGTEXT` with a `json_valid` check. schemagen reads the check and
  writes `json_column()`.
- MariaDB's own `UUID` type reads as a `uuid.UUID` with no setting. A uuid stored as `CHAR(36)`
  is a `str` until you say which columns hold one, as on MySQL.
- The last row is any type SQLAlchemy has no class for, written as MariaDB spells it. Its value
  is what the driver gives - a `str` for `INET6`.

### SQLite

| SQL column | `schema.py` | Python value |
| --- | --- | --- |
| `INTEGER`, `BIGINT`, `SMALLINT` | `INTEGER()`, `BIGINT()`, `SMALLINT()` | `int` |
| `BOOLEAN` | `BOOLEAN()` | `bool` |
| `NUMERIC(p, s)` | `NUMERIC(precision=p, scale=s)` | `Decimal` |
| `REAL`, `FLOAT`, `DOUBLE` | `REAL()`, `FLOAT()`, `DOUBLE()` | `float` |
| `CHAR(n)`, `VARCHAR(n)` | `CHAR(length=n)`, `VARCHAR(length=n)` | `str` |
| `TEXT` | `TEXT()` | `str` |
| `BLOB` | `BLOB()` | `bytes` |
| `JSON` | `json_column()` | `dict`, `list` |
| `DATETIME`, `TIMESTAMP` | `timestamp_column()` | `Instant`, as stored |
| `DATE` | `DATE()` | `date` |
| `TIME` | `TIME()` | `time` |
| any other name - `ANY`, `UNSIGNED BIG INT`, none | `sql_type("ANY")`, ... | by SQLite's affinity: `int`, `str`, `float`, or any |

- `timestamp_column()` reads the `datetime` exactly as stored - no zone added
  ([What you give is what is stored](multi-database.md#what-you-give-is-what-is-stored)).

SQLite has no uuid type, so a uuid column is text, and schemagen cannot tell. Say which columns
hold uuids, with a [forced type](#forced-types):

```toml
[[tool.ekbatan.schemagen.forced-types]]
name = "uuid_column"
include-types = "CHAR\\(32\\)"
include-expression = ".*\\.id|.*_id"
```

### Types SQLAlchemy has no class for

Some column types have no SQLAlchemy class: PostgreSQL's `xml` and `point`, MariaDB's `INET6`,
pgvector's `vector(3)`. schemagen writes them as the database spells them:

```python
sqlalchemy.Column("doc", sql_type("xml"))
```

`metadata.create_all` builds the same column, and the value is what the database driver gives,
usually a `str`. To get your own class, wrap a converter around it - see
[the pgvector example](#a-pgvector-column-without-the-pgvector-package).

## Forced types

A forced type is a rule in your settings. It picks some columns and says what they become,
instead of their default mapping:

```toml
[[tool.ekbatan.schemagen.forced-types]]
converter = "myapp.converters.MoneyConverter"
include-expression = ".*\\.amount"

[[tool.ekbatan.schemagen.forced-types]]
name = "Text"
include-types = "MEDIUMTEXT|LONGTEXT"
```

The first rule turns every column named `amount` into a `Money`, through your
[converter](#write-your-own-converter). The second writes every `MEDIUMTEXT` and `LONGTEXT` column
as a plain `Text`.

**What the columns become** - exactly one of:

- `name`: a time kind (`Instant`, `LocalDateTime`, `OffsetDateTime` - see
  [time columns](#time-columns)), one of Ekbatan's helpers (`uuid_column`, `json_column`,
  `timestamp_column`), or a SQLAlchemy type written as in Python: `Text`, `String(36)`,
  `Numeric(19, 4)`, `postgresql.INET`, `mysql.TINYINT(unsigned=True)`. A time kind only labels a
  timestamp column. One that picks a column holding no timestamp (`VARCHAR`, `INTEGER`,
  `DATE`...) is refused. So is `OffsetDateTime` on a zone-less timestamp, which reads back with no
  offset and keeps none - use `Instant` or `LocalDateTime` there.
- `converter`: your own class with `from_db` and `to_db`. The column keeps its type, and your
  class turns its values into yours. See [Write your own converter](#write-your-own-converter).
- `sqlalchemy-type`: a ready-made SQLAlchemy type from a library. See
  [When to use sqlalchemy-type](#when-to-use-sqlalchemy-type).

**Which columns** - regular expressions, each matching the whole text:

- `include-expression`: the column name, tried as `column`, as `table.column`, and, for a schema
  in `schemas`, as `schema.table.column`. Case matters. `.*\.id|.*_id` means "`id`, or anything
  ending in `_id`".
- `include-types`: the column type, tried in every spelling: the database's own
  (`character varying(36)`, `tinyint(1)`, `int unsigned`), SQLAlchemy's (`VARCHAR(36)`), each with
  and without its arguments, and the short names (`TIMESTAMP`, `TIMESTAMPTZ`). Case does not
  matter.
- `exclude-expression` and `exclude-types` take columns back out. `nullability` is `ALL` (the
  default), `NULL` or `NOT_NULL`.

At least one include is required. **The first forced type that matches a column wins**, and yours
are tried before Ekbatan's. A mistake - a type that does not exist, a module that does not import,
a class without `from_db` - fails before any database starts, and the error names the forced type.

### Ekbatan's built-in forced types

A few columns are not written as their plain type. They get one of Ekbatan's column helpers
instead, so that the value is the same on every database:

| columns | become | on |
| --- | --- | --- |
| `TIMESTAMP`, `DATETIME` (any precision, no zone) | `Instant`: `timestamp_column()`, its record saying `Instant` | all |
| `TIMESTAMPTZ` | `OffsetDateTime`: its own type kept, its record saying `OffsetDateTime` | PostgreSQL |
| `JSONB` | `json_column()` | PostgreSQL |
| `JSON` | `json_column()` | MySQL, MariaDB, SQLite |
| `UUID` | `uuid_column()` | PostgreSQL |

Turn all five off with `default-forced-types = false`.

### A uuid stored as text

MySQL has no uuid type, so a uuid is usually stored as `CHAR(36)`, and nothing in the database
says which `CHAR(36)` columns hold one. schemagen does not guess: a `CHAR(36)` is a `str`, even
when it is named `id` or `owner_id`. When yours hold uuids, say so with one rule:

```toml
[[tool.ekbatan.schemagen.forced-types]]
name = "uuid_column"
include-types = "CHAR\\(36\\)"
include-expression = ".*\\.id|.*_id"
```

Now every `CHAR(36)` named `id` or ending in `_id` is a `uuid_column()` and reads as a
`uuid.UUID`. Any other `CHAR(36)` stays a `str`. The same works on MariaDB. Its own `UUID` type
needs no rule.

**Leave one column out** with `exclude-expression`. The column keeps its own type, `CHAR(36)`,
and reads as a `str`:

```toml
[[tool.ekbatan.schemagen.forced-types]]
name = "uuid_column"
include-types = "CHAR\\(36\\)"
include-expression = ".*\\.id|.*_id"
exclude-expression = "orders\\.tracking_id"
```

**Leave a whole table out, and give it a type of its own.** Exclude the table from the uuid
rule, and add a second rule for it. Here `legacy_accounts` holds ids from another system that
are not uuids, so its `CHAR(36)` columns become `String(36)`:

```toml
[[tool.ekbatan.schemagen.forced-types]]
name = "uuid_column"
include-types = "CHAR\\(36\\)"
include-expression = ".*\\.id|.*_id"
exclude-expression = "legacy_accounts\\..*"

[[tool.ekbatan.schemagen.forced-types]]
name = "String(36)"
include-types = "CHAR\\(36\\)"
include-expression = "legacy_accounts\\..*"
```

With these tables:

```sql
CREATE TABLE wallets (id CHAR(36) NOT NULL PRIMARY KEY, owner_id CHAR(36) NOT NULL, note CHAR(36));
CREATE TABLE legacy_accounts (id CHAR(36) NOT NULL PRIMARY KEY, owner_id CHAR(36) NOT NULL);
```

`schema.py` comes out as:

```python
LEGACY_ACCOUNTS = sqlalchemy.Table(
    "legacy_accounts",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.String(36), primary_key=True),
    sqlalchemy.Column("owner_id", sqlalchemy.String(36), nullable=False),
)

WALLETS = sqlalchemy.Table(
    "wallets",
    metadata,
    sqlalchemy.Column("id", uuid_column(), primary_key=True),
    sqlalchemy.Column("owner_id", uuid_column(), nullable=False),
    sqlalchemy.Column("note", sqlalchemy.CHAR(length=36)),
)
```

`note` is not id-like, so no rule takes it. The second rule can be anything a forced type can
be: a SQLAlchemy type, one of Ekbatan's helpers, or your own converter.

Instead of the exclude, you can put the table's rule **first**: the first rule that matches a
column wins. The exclude says it in one place, so the order does not matter.

Names are case-sensitive. Write the table as the database reports it.

## Time columns

Each time column's record says which kind of time it holds
([time kinds](multi-database.md#time-kinds), from `ekbatan.time`), so mypy keeps a moment and a
wall-clock time apart:

| the column | `schema.py` | its type in the record |
| --- | --- | --- |
| PostgreSQL `TIMESTAMP`, MySQL and MariaDB `DATETIME` and `TIMESTAMP`, SQLite `DATETIME` | `timestamp_column()` | `Instant` - a moment, UTC digits with no zone attached |
| any of those, forced to `LocalDateTime` | `timestamp_column()` | `LocalDateTime` - a wall date and time |
| PostgreSQL `TIMESTAMPTZ` | `sqlalchemy.TIMESTAMP(timezone=True)`, its precision kept | `OffsetDateTime` - a zone attached |
| `TIME` | `sqlalchemy.TIME()` | `time` |
| `DATE` | `sqlalchemy.DATE()` | `date` |

A zone-less timestamp is an `Instant` unless you say otherwise. For a column that holds a
wall-clock time - "remind me at 09:00", whatever the zone - add a rule of your own that forces it
to `LocalDateTime`. Your rule is tried before the built-in one. The column stays the same
zone-less timestamp; only the record's label changes:

```toml
[[tool.ekbatan.schemagen.forced-types]]
name = "LocalDateTime"
include-types = "TIMESTAMP|DATETIME"
include-expression = "reminders\\.at"
```

```python
class RemindersRecord(dict[str, Any]):
    ...
    def __init__(self, *, id: UUID, at: LocalDateTime, created_date: Instant) -> None: ...
    def column_at(self) -> LocalDateTime: ...
```

**A `TIMESTAMPTZ` keeps the moment, not the offset.** PostgreSQL turns what you write into UTC,
and gives it back at the session's offset. So a value written at `+03:30` reads back as the same
moment at another offset: the session's zone through psycopg, UTC through asyncpg. Compare such
values as moments; do not read the offset back as the writer's. MySQL and MariaDB have no zoned
column at all - nothing there keeps an offset - so on them every timestamp is an `Instant` or a
`LocalDateTime`.

## UTC

Ekbatan strongly recommends UTC everywhere, in columns without a zone - see
[Always UTC](multi-database.md#always-utc). schemagen keeps to it, and says where a schema does
not:

- `TIMESTAMP` (PostgreSQL) and `DATETIME(6)` (MySQL, MariaDB) become `timestamp_column()`: a
  zone-less column with microseconds, holding and returning exactly what was written - the
  framework converts no time. Their records say `Instant`.
- a `TIMESTAMPTZ` keeps its zone, its record says `OffsetDateTime`, and a note says what it gives
  back ([time columns](#time-columns)).
- MySQL's `TIMESTAMP` type, a bare `DATETIME`, a `TIMESTAMP(3)`: they become `timestamp_column()`
  too, with a note saying how the column differs from `DATETIME(6)`.

## Write your own converter

A converter is a plain class with two methods. There is no SQLAlchemy in it:

```python
# myapp/converters.py
from dataclasses import dataclass
from decimal import Decimal

from ekbatan.schema import Converter


@dataclass(frozen=True)
class Money:
    amount: Decimal


class MoneyConverter(Converter[Decimal, Money]):
    def from_db(self, value: Decimal) -> Money:
        return Money(value)

    def to_db(self, value: Money) -> Decimal:
        return value.amount
```

Tell schemagen which columns it is for:

```toml
# pyproject.toml
[[tool.ekbatan.schemagen.forced-types]]
converter = "myapp.converters.MoneyConverter"
include-expression = ".*\\.amount"
```

The column keeps its own type, and the converter is wrapped around it:

```python
sqlalchemy.Column("amount", converted(sqlalchemy.NUMERIC(precision=19, scale=4), MoneyConverter()), nullable=False)
```

Now the column takes and gives `Money`, in inserts, reads and comparisons alike. That works on a
sync connection, as below, or awaited on an async one; the generated module is the same for both:

```python
from decimal import Decimal

from sqlalchemy import insert, select

from myapp.converters import Money
from myapp.schema import ORDERS

connection.execute(
    insert(ORDERS).values(id=order_id, amount=Money(Decimal("9.99")), created_date=now)
)
cheap = connection.execute(
    select(ORDERS).where(ORDERS.c.amount < Money(Decimal("10")))
).all()
```

### Pick the columns by name or by type

By name, as above: `include-expression = ".*\\.amount"` takes every column called `amount`.
By type, it takes every column of that type, whatever it is called:

```toml
[[tool.ekbatan.schemagen.forced-types]]
converter = "myapp.converters.MoneyConverter"
include-types = "NUMERIC\\(19,4\\)"
```

Use both to take only the columns that match both.

### What your converter gets

- **`None` never reaches it.** A `NULL` column reads as `None`, and writing `None` writes `NULL`.
  Your methods only see real values.
- **`from_db` gets the value from [the default mapping](#the-default-mapping)**: whatever the
  column would read as without the converter. On a `NUMERIC` column that is a `Decimal`. On a
  `TIMESTAMP` column it is the `datetime` the driver read, as it is, because the converter wraps
  `timestamp_column()`.
- **`to_db` returns that same kind of value.**

### The Converter base class is optional

`Converter[Decimal, Money]` is there for type hints: your editor and mypy then check that
`from_db` takes a `Decimal` and returns a `Money`. Any class with `from_db` and `to_db` works
without it, as long as `from_db` says what it returns, because that is the type the table's record
gives the column.

schemagen reads that type from the return annotation of `from_db` first
(`def from_db(self, value: Decimal) -> Money`), and else from the second argument of a
`Converter[Decimal, Money]` base. A converter that says neither stops the run with an error naming
it. schemagen does not guess, and does not fall back to `Any`:

```
error: the forced converter 'myapp.converters.MoneyConverter': schemagen cannot tell what type it
gives, so it cannot type the column in the record - from_db() has no return annotation, and the
class does not extend Converter[DbT, UserT]. Annotate it: `def from_db(self, value: str) -> Currency:`
```

A converter that takes arguments is written with them: `converter =
"myapp.converters.MoneyConverter(4)"`. A converter object made once in your module works too:
`converter = "myapp.converters.MONEY"`.

schemagen imports your converter when it runs, to check it. So keep converters in a module that
does not import the generated `schema.py`.

### A pgvector column, without the pgvector package

pgvector adds a `vector` column type to PostgreSQL. SQLAlchemy has no class for it, so schemagen
writes `sql_type("vector(3)")`, and the value is pgvector's text, `"[0.1,0.2,0.3]"`. A converter
makes it a list of floats.

The migration:

```sql
-- migrations/V0001__items.sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE items (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    embedding vector(3) NOT NULL
);
```

The converter:

```python
# myapp/converters.py
class VectorConverter:
    def from_db(self, value: str) -> list[float]:
        return [float(x) for x in value.strip("[]").split(",")]

    def to_db(self, value: list[float]) -> str:
        return "[" + ",".join(str(x) for x in value) + "]"
```

The settings. The image is PostgreSQL with pgvector installed:

```toml
# pyproject.toml
[tool.ekbatan.schemagen]
image = "pgvector/pgvector:pg17"

[[tool.ekbatan.schemagen.forced-types]]
converter = "myapp.converters.VectorConverter"
include-types = "VECTOR.*"
```

schemagen writes:

```python
sqlalchemy.Column("embedding", converted(sql_type("vector(3)"), VectorConverter()), nullable=False)
```

Nearest first, with pgvector's `<->` distance operator:

```python
from sqlalchemy import select

from myapp.schema import ITEMS

nearest = connection.execute(
    select(ITEMS.c.name).order_by(ITEMS.c.embedding.op("<->")([0.0, 0.0, 0.0])).limit(5)
).scalars()
```

The list you pass to `.op("<->")` goes through `to_db` too. `metadata.create_all` builds the
`vector(3)` column again, once the database has the extension (`CREATE EXTENSION vector`).

## When to use sqlalchemy-type

Use it when a library already ships a SQLAlchemy type for the column, such as pgvector's `Vector`
or GeoAlchemy2's `Geometry`:

```toml
[[tool.ekbatan.schemagen.forced-types]]
sqlalchemy-type = "pgvector.sqlalchemy.Vector(3)"
include-types = "VECTOR.*"
```

It is a convenience, not a need. The library's type may add helpers, such as
`embedding.l2_distance(...)`, but a converter gives you the same values without the extra
package, as the example above shows.

Write it as a dotted path, with arguments if it takes any. It may be a type, a function returning
a type, or a type object made once in a module. In the table's record, the column is what the
type's `process_result_value` is annotated to return, or what its `impl` reads as when it converts
nothing. A type that says neither is `Any`, with a note.

## The framework's own tables

`events`, `event_notifications` and `scheduled_tasks` are not read column by column. Other
services read these tables, so their shape is fixed. schemagen writes them as calls to the
framework's helpers - `outbox_table(metadata)`, `notifications_table(metadata)` and
`scheduled_tasks_table(metadata)` - with their indexes from your migrations. If your migrations
differ from a helper, a note says how.

The two event tables are read from `eventlog`: the schema on PostgreSQL, the database on MySQL and
MariaDB. Only those two tables are read from there, unless `schemas` names `eventlog`. They are
still exported as `EVENTS` and `EVENT_NOTIFICATIONS`.

Each gets its record like any table - `EventsRecord`, `EventNotificationsRecord`,
`ScheduledTasksRecord` - typed from the helper's columns. `excludes = "scheduled_tasks"` leaves
one out.

## What else is generated

Enough for `metadata.create_all` to build, in a test, the database your migrations built. Ekbatan
uses none of it at run time. Each "yes" below is tested on that database: the migrations build one
database, `create_all` from the module builds a second, and the two are compared, views included.
A "-" is something that database does not have, or schemagen does not write there.

### What schemagen generates

| | PostgreSQL | MySQL | MariaDB | SQLite |
| --- | --- | --- | --- | --- |
| columns: type, `NOT NULL`, server default | yes | yes | yes | yes |
| comments on tables and columns | yes | yes | yes | - |
| identity (with START, INCREMENT, MIN/MAX, CACHE, CYCLE), `SERIAL`, `AUTO_INCREMENT` on the key | yes | yes | yes | yes (`AUTOINCREMENT`) |
| generated columns, stored and virtual | yes | yes | yes | yes |
| column collation | yes | yes | yes | yes |
| primary keys, unique constraints, checks - named, composite | yes | yes | yes | yes |
| `DEFERRABLE` keys, `UNIQUE NULLS NOT DISTINCT`, `UNIQUE ... INCLUDE` | yes | - | - | - |
| foreign keys: composite, `ON DELETE`/`ON UPDATE`, self-referencing, to a unique key | yes | yes | yes | yes |
| foreign keys: `DEFERRABLE` | yes | - | - | yes |
| foreign keys: `MATCH FULL`; to another schema | yes | -; yes | -; yes | - |
| `EXCLUDE` constraints | yes | - | - | - |
| indexes: unique, partial, on expressions, `DESC`, `NULLS LAST`, `COLLATE`, op classes, `INCLUDE`, `USING gin/gist/brin/hash`, `WITH (...)` | yes | - | - | - |
| indexes: unique, functional, `DESC`, prefix lengths, `FULLTEXT`, `SPATIAL` | - | yes | yes (no functional) | - |
| indexes: unique, partial, on expressions, `DESC`, `COLLATE` | - | - | - | yes |
| enum types: shared, in other schemas, in arrays | yes | - | - | - |
| `ENUM` and `SET` | - | yes | yes | - |
| domains, with their checks, `NOT NULL` and default | yes | - | - | - |
| sequences, with every option, as module constants | yes | - | yes | - |
| partitioned tables and their partitions | yes | yes | yes | - |
| inheritance, `UNLOGGED` | yes | - | - | - |
| table options: `ENGINE`, `DEFAULT CHARSET`, `COLLATE`, `ROW_FORMAT`, `STATS_PERSISTENT`, ... | - | yes | yes | - |
| storage parameters (`fillfactor`...), `REPLICA IDENTITY`, a column's `STORAGE` and `STATISTICS` | yes | - | - | - |
| `STRICT`, `WITHOUT ROWID` | - | - | - | yes |
| views, in `view_metadata` (and materialized views, on PostgreSQL) | yes | yes | yes | yes |
| other schemas, when listed in `schemas` | yes | yes | yes | - |

- **Views** go in a second `view_metadata`: query them like tables; `create_all` on `metadata`
  does not create a table by a view's name. Their definitions stay in the migrations.
- **Other schemas**, when listed in `schemas`, are written with `schema="audit"` and exported as
  `AUDIT_ENTRIES`. When a schema holds tables and is not listed, a note says so.
- **A sequence of its own** is a constant beside the tables, `ORDER_NUMBERS = sqlalchemy.Sequence(
  "order_numbers", start=1000, metadata=metadata)`: `select(ORDER_NUMBERS.next_value())` reads the
  next number, and `create_all` creates it before the tables.
- **Partitions** are created by `create_all` right after their table, as PostgreSQL wrote them
  (`CREATE TABLE ... PARTITION OF ... FOR VALUES ...`); they are not tables of their own in the
  module - query them through the partitioned table. On MySQL and MariaDB, `create_all`
  partitions the table right after creating it, with the partitioning clause as the server
  prints it (`ALTER TABLE ... PARTITION BY RANGE (...) (PARTITION p0 ...)`).
- **What `Table` cannot say** about a PostgreSQL table - its storage parameters, its replica
  identity, a column's storage, compression or statistics target - is applied the same way, by
  `ALTER TABLE ...` right after `create_all` creates the table.
- **A domain** is written whole, `postgresql.DOMAIN("positive_int", sqlalchemy.INTEGER(), check=...)`,
  and `create_all` creates it. A domain with several checks is written with one, joined by `AND`;
  a `NOT NULL` domain's check is written without its name. A note says which.
- **An index element** that is more than a column's name - `a DESC NULLS LAST`,
  `lower(name) COLLATE "C"`, MySQL's `(lower(name))` - is written as the database writes it,
  `sqlalchemy.text("a DESC NULLS LAST")`.

### What is not generated

Each of these gets a note when the database has one, naming them:

- PostgreSQL functions, procedures and triggers, MySQL and MariaDB routines, triggers and events,
  SQLite triggers. `create_all` does not create them; call a function with
  `sqlalchemy.func.add_one(...)`; a test that needs a trigger runs the migrations.
- PostgreSQL extensions (`CREATE EXTENSION citext` comes before `create_all`) and composite types
  (a column of one is written as the database spells its type; create the type first).
- MySQL and MariaDB invisible columns, unenforced checks (`NOT ENFORCED`), index comments,
  invisible or ignored indexes, system-versioned tables, `AUTO_INCREMENT` on a column outside
  the key: SQLAlchemy cannot declare them, so `create_all` builds the table without them. A
  table option the server prints in a form schemagen cannot read is noted as it is.
- PostgreSQL row level security and policies, rules, extended statistics, index comments, a
  table's own tablespace and publications: `create_all` builds the tables without them; a test
  that needs one runs the migrations. A collation of your own is noted too - a column using it
  is written `COLLATE` it, so create the collation before `create_all`.
- A foreign key to a table that is not generated. On MySQL and MariaDB, a foreign key from a
  table in a listed schema to a table in the connection's own database is left out too: the
  module writes the own database's tables without its name, so it has no way to name the target,
  and a key written without it would point into the listed database instead.

A few things are built a little differently on purpose, each with a note: a timestamp forced to
`timestamp_column()` (see [time columns](#time-columns)) and, on MySQL, its
`DEFAULT CURRENT_TIMESTAMP`, written `CURRENT_TIMESTAMP(6)` as MySQL requires of a
`DATETIME(6)`; a `SERIAL` that is not the key, whose sequence is not owned by its column; a
MariaDB column-level check, built as a table check of the same name; and a PostgreSQL
`NOT VALID` check or foreign key, built as a plain one.

## Notes

What schemagen wants you to know is logged to `io.ekbatan.schemagen` as warnings, and the command
prints each as `note: ...`: a zoned timestamp, a timestamp column unlike `DATETIME(6)`, a schema
not read, a foreign key left out, a framework table that differs from its helper, and everything
the database has that the module does not write ([what is not generated](#what-is-not-generated)).

## Why not sqlacodegen

It cannot force the per-database types, and a schema generated without them corrupts data on
MySQL. Read from PostgreSQL and used on MySQL, a `UUID` column becomes `CHAR(32)` - the hex without
its hyphens - and a `TIMESTAMP` becomes MySQL's `TIMESTAMP`, which shifts with the session's time
zone. The framework needs `CHAR(36)` and `DATETIME(6)` there.

## See also

- [Migrations](migrations.md) - what gets run into the database
- [Multiple dialects](multi-database.md) - the column helpers the forced types use
- [MariaDB](mariadb.md#the-scheme-has-to-say-mariadb) - why schemagen rewrites MariaDB's url
- [Getting started](../getting-started.md) - the Makefile, in context
- [`fastapi-wallet-rest-uv-pg`](../../ekbatan-py-examples/fastapi-wallet-rest-uv-pg) - two converters
  in a working service: one picked by type, one by name
