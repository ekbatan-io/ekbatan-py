# schemagen

schemagen writes `schema.py` for you, from your migrations. It starts a throwaway database, runs
your migrations into it, reads the tables back, and writes one Python module with a SQLAlchemy
`Table` for each, and beside each `Table` a typed record of its row. **The module comes from the
real database, so it cannot drift from it.**

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

schemagen writes:

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

Every SQLAlchemy name is written through `import sqlalchemy`: `sqlalchemy.TEXT()`, not `TEXT()`.
The reason is the table names. Each table is exported under its name in capitals, so a table
named `text` is `TEXT`, and it would replace a `TEXT` imported from `sqlalchemy`. Dialect types
are written through their module the same way: `postgresql.JSONB()`.

and, beside it, the table's record:

```python
class OrdersRecord(TypedDict):
    id: UUID
    amount: Decimal
    note: str | None
    created_date: Instant
```

Import `ORDERS` and query it like any SQLAlchemy table; type a repository's mapping with
`OrdersRecord` ([below](#the-record-beside-each-table)). Every column has a type, and every type
is listed in [the default mapping](#the-default-mapping) below. To get your own class back from
a column, such as a `Money` instead of a `Decimal`, [write a converter](#write-your-own-converter).

## The record beside each table

Beside every table, schemagen writes the table's **record**: a `TypedDict` named after the
table - `orders` gives `OrdersRecord`, `event_notifications` gives `EventNotificationsRecord` -
with one key per column, each typed as the column reads. A repository's `to_record` returns it
and its `from_record` takes it ([repositories](repositories.md)), so mypy checks every mapping
against the table: a column left out, misspelt, added or given the wrong type is a type error.
To SQLAlchemy a record is a plain `dict`; it costs nothing at run time.

Each key's type is read from the type object the module builds for that column:

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
| a column with your **converter** | your converter's own type - `Money`, not `Decimal` |
| a `sqlalchemy-type` | what its `process_result_value` is annotated to return, or what its `impl` reads as |
| a type nothing says anything about (`sql_type("xml")`, PostgreSQL `BIT`, which two drivers read differently) | `Any`, and the run prints a note saying so - give it a converter to type it |

A nullable column is `X | None`. A column the database fills itself - a server default, an
identity, a generated column, an auto-increment - is `NotRequired[...]`: an insert may leave it
out, and must leave out a generated one.

**A converter's type is read off the converter.** First the return annotation of its `from_db`
(`def from_db(self, value: Decimal) -> Money`), so a converter needs no base class; else the
second argument of its `Converter[Decimal, Money]` base. A converter that says neither stops the
run with an error naming it - schemagen does not guess, and does not fall back to `Any`:

```
error: the forced converter 'myapp.converters.MoneyConverter': schemagen cannot tell what type it
gives, so it cannot type the column in the record - from_db() has no return annotation, and the
class does not extend Converter[DbT, UserT]. Annotate it: `def from_db(self, value: str) -> Currency:`
```

The module imports what the records name - `from myapp.money import Money`, `from uuid import
UUID` - and a name that is already taken in the module (a SQLAlchemy type, a helper, a table, a
record, another class of the same name) is imported under an alias: `myapp_types_UUID`. A column
named like a type its record uses (a `date` column of type `date`) makes that record the call
form, `OrdersRecord = TypedDict("OrdersRecord", {...})`, which means the same. Two tables whose
records would share a name stop the run, as two tables sharing a constant do.

A schema written by hand, without schemagen, writes its records by hand in the same style.

## Settings

Settings go in `pyproject.toml`, under `[tool.ekbatan.schemagen]`. A flag on the command wins
over the file. Paths in the file are relative to the file.

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

`image` is `postgres:*`, `mysql:*`, `mariadb:*`, or `sqlite` (a temporary file, no container).
In MySQL and MariaDB, a schema in `schemas` is a database. `includes` and `excludes` are
regular expressions over table names. Forced types are [below](#forced-types).

A setting left empty (`image = ""`) is refused: remove the line to get the default. Empty is never
read as the default, because `excludes = ""` would be a pattern that matches every table.

An unknown key is an error, so a typo does not silently do nothing. `url` must point at an empty
database. Keep it out of a committed file if it has a password; pass it with `--url`. schemagen
never prints it.

The connection's own schema is always read, and written without a schema name, so the same
module works whatever the database is called. The migration history table is never generated.

## The default mapping

For each database: the column type in your migration, what `schema.py` writes for it, and the
Python value you read back. Each row is checked on a real database by the test suite: generated,
imported, built again by `metadata.create_all`, and a value written and read back.

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
| `xml`, `point`, `vector(3)`, ... | `sql_type("xml")`, ... | `str` |

- A JSON column reads whatever the JSON holds: a `dict`, a `list`, a number or a string.
- `timestamp_column()` reads the `datetime` exactly as stored - no zone added
  ([What you give is what is stored](multi-database.md#what-you-give-is-what-is-stored)); its
  record says `Instant`. A `TIMESTAMPTZ` reads a `datetime` with a zone, as the driver returns
  it - an `OffsetDateTime` - and a note suggests plain `TIMESTAMP` ([time columns](#time-columns)).
- `INET` and `CIDR` values come from Python's `ipaddress` module. `Range` is
  `sqlalchemy.dialects.postgresql.Range`.
- The `MACADDR` row also covers `MACADDR8`, `VARBIT(n)` and `TSVECTOR`.
- An enum type is one made with `CREATE TYPE mood AS ENUM ('happy', 'sad')`.
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

- `timestamp_column()` reads the `datetime` exactly as stored - no zone added
  ([What you give is what is stored](multi-database.md#what-you-give-is-what-is-stored)). A bare `DATETIME` and
  MySQL's `TIMESTAMP` type get a note saying how they differ from `DATETIME(6)`.
- A uuid stored as `CHAR(36)` reads as a `str` until you say which columns hold one - see
  [a uuid stored as text](#a-uuid-stored-as-text).
- A character set or a collation of its own makes the column MySQL's own `mysql.VARCHAR`, with
  both written out.
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
| `INET6`, ... | `sql_type("inet6")`, ... | `str` |

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

- `timestamp_column()` reads the `datetime` exactly as stored - no zone added
  ([What you give is what is stored](multi-database.md#what-you-give-is-what-is-stored)).

SQLite has no uuid type, so a uuid column is text, and schemagen cannot tell. Say which columns
hold uuids:

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

## Forced types

A forced type picks some columns and says what they become:

```toml
[[tool.ekbatan.schemagen.forced-types]]
converter = "myapp.converters.MoneyConverter"
include-expression = ".*\\.amount"

[[tool.ekbatan.schemagen.forced-types]]
name = "Text"
include-types = "MEDIUMTEXT|LONGTEXT"
```

**What the columns become** - exactly one of:

- `name`: a time kind (`Instant`, `LocalDateTime`, `OffsetDateTime` - see
  [time columns](#time-columns)), one of Ekbatan's helpers (`uuid_column`, `json_column`,
  `timestamp_column`), or a SQLAlchemy type written as in Python: `Text`, `String(36)`,
  `Numeric(19, 4)`, `postgresql.INET`, `mysql.TINYINT(unsigned=True)`.
- `converter`: your own class with `from_db` and `to_db`. The column keeps its type, and your
  class turns its values into yours. See [Write your own converter](#write-your-own-converter).
- `sqlalchemy-type`: a ready-made SQLAlchemy type from a library. See
  [When to use sqlalchemy-type](#when-to-use-sqlalchemy-type).

**Which columns** - regular expressions. Each must match the whole text:

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

Now the column takes and gives `Money`, in inserts, reads and comparisons alike - on a sync
connection as below, or awaited on an async one; the generated module is the same for both:

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
- **`from_db` gets the value in [the default mapping](#the-default-mapping).** On a `NUMERIC`
  column that is a `Decimal`. On a `TIMESTAMP` column it is the `datetime` the driver read, as it
  is, because the converter wraps `timestamp_column()`: whatever the column would have been without the converter.
- **`to_db` returns that same kind of value.**

### The Converter base class is optional

`Converter[Decimal, Money]` is there for type hints: your editor and mypy then check that
`from_db` takes a `Decimal` and returns a `Money`. Any class with `from_db` and `to_db` works
without it - as long as `from_db` says what it returns, because that is the type the table's
record gives the column. With neither the annotation nor the base, schemagen stops and says so.

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
package, as the example above shows. Write it as a dotted path, with arguments if it takes any.
It may be a type, a function returning a type, or a type object made once in a module. In the
table's record the column is what the type's `process_result_value` is annotated to return, or
what its `impl` reads as when it converts nothing; a type that says neither is `Any`, with a note.

## What else is generated

Enough for `metadata.create_all` to build, in a test, the database the migrations built. Ekbatan
uses none of it at run time.

- primary keys (composite ones in key order), unique constraints, foreign keys with their
  `ON DELETE`/`ON UPDATE`, checks, indexes (unique, partial, on expressions), server defaults,
  identity and generated columns, comments;
- **views**, in a second `view_metadata`: query them like tables; `create_all` on `metadata`
  does not create a table by a view's name;
- **other schemas**, when listed in `schemas`: written with `schema="audit"` and exported as
  `AUDIT_ENTRIES`. When a schema holds tables and is not listed, a note says so.

Not generated: database extensions (`CREATE EXTENSION citext` comes before `create_all`), and a
foreign key to a table that is not generated (a note says so). On MySQL and MariaDB, a foreign key
from a table in a listed schema to a table in the connection's own database is left out too, with
a note: the module writes the own database's tables without its name, so it has no way to name the
target, and a key written without it would point into the listed database instead.

## The framework's own tables

`events`, `event_notifications` and `scheduled_tasks` are written as `outbox_table(metadata)`,
`notifications_table(metadata)` and `scheduled_tasks_table(metadata)`, with their indexes from the
migrations. They are not read column by column, because other services read these tables and
their shape is fixed. If your migrations differ from the helper, a note says how. Each gets its
record like any table - `EventsRecord`, `EventNotificationsRecord`, `ScheduledTasksRecord` - typed
from the helper's columns.
`excludes = "scheduled_tasks"` leaves one out.

## Time columns

Each time column's record says which kind of time it holds ([time kinds](multi-database.md#time-kinds),
from `ekbatan.time`), so mypy keeps a moment and a wall-clock time apart:

| the column | `schema.py` | its type in the record |
| --- | --- | --- |
| PostgreSQL `TIMESTAMP`, MySQL and MariaDB `DATETIME` and `TIMESTAMP`, SQLite `DATETIME` | `timestamp_column()` | `Instant` - a moment, UTC digits with no zone attached |
| any of those, forced to `LocalDateTime` | `timestamp_column()` | `LocalDateTime` - a wall date and time |
| PostgreSQL `TIMESTAMPTZ` | `sqlalchemy.TIMESTAMP(timezone=True)`, its precision kept | `OffsetDateTime` - a zone attached |
| `TIME` | `sqlalchemy.TIME()` | `time` |
| `DATE` | `sqlalchemy.DATE()` | `date` |

A zone-less timestamp is an `Instant` unless you say otherwise. A column that holds a wall-clock
time - "remind me at 09:00", whatever the zone - is forced to `LocalDateTime` by a rule of your
own, which is tried before the built-in one. The column stays the same zone-less timestamp; only
the record's label changes:

```toml
[[tool.ekbatan.schemagen.forced-types]]
name = "LocalDateTime"
include-types = "TIMESTAMP|DATETIME"
include-expression = "reminders\\.at"
```

```python
class RemindersRecord(TypedDict):
    id: UUID
    at: LocalDateTime
    created_date: Instant
```

**A `TIMESTAMPTZ` keeps the moment, not the offset.** PostgreSQL turns what you write into UTC
and gives it back at the session's offset, so a value written at `+03:30` reads back as the same
moment at another offset: the session's zone through psycopg, UTC through asyncpg. Compare such
values as moments; do not read the offset back as the writer's. MySQL and MariaDB have no zoned
column at all - nothing there keeps an offset - so on them every timestamp is an `Instant` or a
`LocalDateTime`.

## UTC

Ekbatan strongly recommends UTC everywhere and columns without a zone - see
[Always UTC](multi-database.md#always-utc). schemagen keeps to it, and says where a schema does
not:

- `TIMESTAMP` (PostgreSQL) and `DATETIME(6)` (MySQL, MariaDB) become `timestamp_column()`:
  a zone-less column with microseconds, holding and returning exactly what was written - the
  framework converts no time - and their records say `Instant`;
- a `TIMESTAMPTZ` keeps its zone, its record says `OffsetDateTime`, and a note says what it
  gives back ([time columns](#time-columns));
- MySQL's `TIMESTAMP` type, a bare `DATETIME`, a `TIMESTAMP(3)`: they become
  `timestamp_column()` too, with a note saying how the column differs from `DATETIME(6)`.

## The output is gitignored

A fresh clone has no `schema.py`. Running `make` writes one.

That is the opposite of the *other* generator: `ekbatan.codegen`'s typed builders **are**
committed. A builder comes from source you already have; a schema comes from a database you have
to start.

## Wire it into Make

`uv` has no task runner. Make fills the gap:

```make
src/myapp/schema.py: $(wildcard src/myapp/migrations/*.sql) pyproject.toml
	uv run python -m ekbatan.schemagen

test: src/myapp/schema.py
	uv run pytest
```

**It starts a database when a migration or the settings change, and does nothing when they have
not.** `lint` should depend on it too: mypy reads the generated module.

## The stale-module guard

Nothing in Python runs before `python -m yourapp`, so the module checks itself. It carries a
fingerprint of the migrations it came from, and checks it on import. A stale module raises
`ValidationError` saying to regenerate, instead of failing later on a missing column.

It also carries `SCHEMA_FORMAT`, the shape of module this schemagen writes. When a new ekbatan
writes a different shape - the typed records, every SQLAlchemy name written through
`import sqlalchemy`, the records' time kinds - a module written by the older schemagen refuses to import, even though its
migrations have not changed:

```
schema.py was written by an older schemagen (format 4; this ekbatan writes 5). Regenerate it ...
```

A build that only regenerates when the migrations change, like the examples' `Makefile`, keeps
the old file, so run `make clean && make` (or delete it and run `python -m ekbatan.schemagen`). Unlike the fingerprint, this check runs in
an installed wheel too: a module and an ekbatan that disagree are broken there as well.

## Notes

What schemagen wants you to know is logged to `io.ekbatan.schemagen` as warnings, and the command
prints each as `note: ...`: a zoned timestamp, a timestamp column unlike `DATETIME(6)`, a schema
not read, a foreign key left out, a framework table that differs from its helper.

## Why not sqlacodegen

It cannot force the per-database types, and a schema generated without them corrupts data on
MySQL. That is recorded in `pyproject.toml` next to the extra, so the question does not get asked
again.

## See also

- [Migrations](migrations.md) - what gets run into the database
- [Multiple dialects](multi-database.md) - the column helpers the forced types use
- [MariaDB](mariadb.md#the-scheme-has-to-say-mariadb) - why schemagen rewrites MariaDB's url
- [Getting started](../getting-started.md) - the Makefile, in context
- [`fastapi-wallet-rest-uv-pg`](../../ekbatan-py-examples/fastapi-wallet-rest-uv-pg) - two converters
  in a working service: one picked by type, one by name
