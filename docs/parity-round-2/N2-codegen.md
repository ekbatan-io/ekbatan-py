# N2: code generation - parity round 2

Part of [the round-2 ledger](../PARITY-ROUND-2-LEDGER.md).

**Scope and what was run.** Two generators, each against its Java twin, from the user's side.
(1) Tables from migrations: the same three migration files (`migrations/pg`, `mysql`, `mariadb`:
uuid, decimal, float, int, boolean, text, JSON/JSONB, timestamps with and without zone, date,
time, bytes, array, enum, defaults, unique key, index, foreign key, a view, a second schema, a
keyword table name) were fed to Java's real setup - a copy of the Spring Boot Gradle examples'
`generateJooqClasses` block with their `forcedTypes`, run on Docker - and to
`python -m ekbatan.schemagen` on the same images. Each generated file was compared column by
column; Python's output was then imported, and the columns where the two chose different types
were exercised against PostgreSQL 17 and MariaDB 11.8 (ports 58301/58302). Python's schemagen
was also run on the Java examples' own migrations. Regeneration was checked in both (Gradle
rerunning the task; Python's fingerprint check on import). (2) Builders: a probe compiled with
the real `ekbatan-annotation-processor` (`javac -processorpath`) against a Python package whose
builders `python -m ekbatan.codegen` wrote, plus `mypy` over the generated file and a usage file.
Round 1 area G's builder rows (build, copy, missing/null fields, unknown setter) are not
repeated. Sources and outputs: [`parity-probes/round2-N2/`](../../parity-probes/round2-N2/)
(`java/` holds the Java probe copies, the build files and the generated Java).

| # | Difference (plain, one line) | Java | Python | Verdict | Why (one line) | Evidence |
|---|---|---|---|---|---|---|
| 1 | A PostgreSQL `TIMESTAMPTZ` column is given the zone-less type, so a write is shifted by the session's time zone | `OffsetDateTime`, exact | stored 08:30 UTC for 12:00 UTC when the session zone is Asia/Tehran; read back wrong | leave (user, 2026-10-18) | outside the framework's UTC recommendation; was: silent wrong data on any non-UTC session; one line to stop forcing zoned timestamps | run: `jooq-pg.java.out` zoned_ts; `schemagen_runtime.python.out` pg.timestamptz.* |
| 2 | A `VARCHAR(36)` column named `*_id` is turned into a uuid column, on every database | `String` (Java forces only `CHAR(36)`, only on MySQL) | uuid column; on PostgreSQL `WHERE ext_id = :uuid` fails "operator does not exist: character varying = uuid" | fix | breaks queries on a plain text id; narrow the guess to Java's | run: `jooq-pg.java.out`, `jooq-mysql.java.out` ext_id; `schemagen_runtime.python.out` pg.varchar36Id.* |
| 3 | `copy()` (and `wallet(source)`) crash on a model with a derived field (`field(init=False)`) | derived field recomputed by the constructor; copy works | `TypeError: Wallet.__init__() got an unexpected keyword argument 'label'` | fix | a Java derived field ported the natural way breaks every update of that model | run: `builder.*.out` copyModelWithDerivedField, factoryFromSource |
| 4 | `schemagen` writes a `schema.py` that cannot be imported when a table has a common type | `Byte` (BOOLEAN on MySQL/MariaDB), generated enum class, `byte[]`, `String[]`, `String` (MariaDB JSON) | `ImportError`/`TypeError`: emits `TINYINT`, `ENUM`, `BYTEA`, `LONGTEXT` from `sqlalchemy` (not there) and `ARRAY()` with no item type | fix | any BOOLEAN column on MySQL/MariaDB stops the app importing; emit importable types | run: `schemagen_import.python.out`, `py-schema/*/schema.py`, `jooq-*.java.out` |
| 5 | Generated setter types are wrong for `Literal[...]` and `tuple[X, ...]` | exact (`TypeName.get` of the field) | `Any[Any, Any]` with `Any` not imported (mypy errors in the generated file); `tuple[int, Any]` (mypy refuses a valid 3-tuple) | fix | the file exists only for the type checker, and here it misleads it | run: `n2pkg/builders.py`, `mypy.python.out` |
| 6 | The generated `builders.py` header says a stale builder "is a type error rather than a wrong answer" | n/a: regenerated on every compile | false: mypy accepts `wallet().no_such_field(1)`; codegen's own docstring says so | fix | a wrong promise in every user's generated file; one-line text change | run: `mypy.python.out` (no error on usage.py:14) |
| 7 | Tables in another schema (PostgreSQL `audit`, Java's `eventlog`) are skipped without a word | generated for every schema listed in `schemas` | not in `schema.py`; no option, no warning | leave | app tables in a second schema are rare, and the framework tables come from helpers; a warning would be cheap | run: `jooq-pg.java.out` files (Entries.java); `py-schema/pg/schema.py`; `py-schema-from-java/` |
| 8 | Views, foreign keys, indexes (incl. unique indexes, MySQL unique keys) and column defaults are dropped | generated (`ProbeView`, `Keys`, `Indexes`, `defaultValue(...)`) | absent from `schema.py` (a PostgreSQL `UNIQUE (...)` constraint is kept) | leave | matters only if `schema.py` feeds `create_all` in a test; Ekbatan never uses them at run time | run: `jooq-*.java.out` keysAndIndexes; `py-schema/*/schema.py` |
| 9 | A model extending another model: Java refuses it, Python generates a builder whose model's `copy()` then crashes | compile error: "@AutoBuilder requires ... to directly extend Model or Entity" | builder generated; `SavingsWallet.copy()` returns the parent's `WalletBuilder` and `build()` fails "unexpected keyword argument 'rate'" | leave | rare in Ekbatan models; codegen could refuse it as Java does, cheaply | run: `builder.java.out` subclass compile; `builder.python.out` builder.subclass.* |
| 10 | A derived field gets a setter in both; Java ignores the value, Python refuses at build | `label("x")` accepted, model keeps "wallet" | `TypeError` at `build()` | leave | Java's silent ignore is its own accident; the fix of row 3 removes the setter here too | run: `builder.*.out` derivedFieldSetter |
| 11 | The generated builder has no getters | `builder.balance()` returns the value set | `builder.balance()` is the setter: `TypeError` | leave | Java needs them for the model's constructor; a dataclass does not | run: `builder.*.out` getterAfterSet |
| 12 | Generic model: the builder is not generic | `BoxBuilder<T>`, `content(T)` | `BoxBuilder`, `content(value: Any)` | leave | works; only loses checking on a rare kind of model | run: `builder.*.out` builder.generic.*; `n2pkg/generic_builders.py` |
| 13 | A setter given the wrong type is accepted at run time | compile error | model built with a `str` balance; mypy refuses it | leave | Python limit; the checker catches it, which is the generator's purpose | run: `builder.python.out` wrongTypeAtRuntime; `mypy.python.out` usage.py:13 |
| 14 | Which models get a builder | only classes marked `@AutoBuilder` | every model defined in the module given; no opt-out | leave | a hand-written builder elsewhere still wins; the extra one is dead code | run: `n2pkg/builders.py` (Wallet and HTTPRequest, unasked) |
| 15 | The factory takes an optional source to start from | `wallet()` only; `copy()` for a source | `wallet(source=None)` | leave | an addition, not a difference in what existing calls do | run: `builder.*.out` factoryFromSource |
| 16 | Factory name of an acronym model | `hTTPRequest()` | `h_t_t_p_request()` | leave | both mechanical and both odd; nobody writes this name twice | run: `builder.*.out` factoryNameAcronym |
| 17 | Builder class is `final` with a private constructor | yes | open class, `WalletBuilder()` works | leave | Python has no `final`; harmless | run: `builder.*.out` builderClassFinal, builderConstructorPublic |
| 18 | A MariaDB `CHAR(36)` id column | `String` (Java's MariaDB setup forces nothing) | uuid column | leave | Python's MariaDB convention stores uuids as `CHAR(36)`; Java's uses native `UUID` | run: `jooq-mariadb.java.out` legacy_id; `py-schema/mariadb/schema.py` |
| 19 | A PostgreSQL plain `JSON` column | `org.jooq.JSON` (not forced) | `json_column()` (dict) | leave | Python's is friendlier and reads the same text | run: `jooq-pg.java.out` plain_json; `py-schema/pg/schema.py` |

**Checked and the same** (run): regeneration of tables - Gradle reruns `generateJooqClasses` when a
migration changes and Python's `schema.py` refuses to import with a "Regenerate it" message
(`stale.*.out`); MariaDB native `UUID` columns - insert, `WHERE`, read back as `UUID` all work
through `uuid_column()` (`schemagen_runtime.python.out`); a MySQL `CHAR(36)` column not named
like an id stays text in both; `TIMESTAMP`, `TIMESTAMP(3)`, `DATETIME`, `DATETIME(6)` and MySQL
`TIMESTAMP(6)` become `Instant` / `timestamp_column()` in both; `NUMERIC(19,4)`/`DECIMAL` keep
precision and scale (`BigDecimal` / `Decimal`), and `DOUBLE`, `REAL`, `INTEGER`, `SMALLINT`,
`BIGINT`, `DATE`, `TIME`, `TEXT`, `CHAR`, `VARCHAR` map alike; `NOT NULL` carried in both; column
names kept verbatim (`camelCol`, `class`) and a keyword table (`order`) usable in both; Java's own
example migrations generate a `schema.py` that imports on all three databases
(`py-schema-from-java/run.out`).

## Details: the `fix` rows

> **Leave, by the user's decision (2026-10-18):** `TIMESTAMPTZ` is outside the framework's strong
> recommendation - UTC in code, zone-less `TIMESTAMP` columns, the database in UTC
> (`docs/database/multi-database.md#always-utc`). A schema that follows it never meets this.

**1. `TIMESTAMPTZ` forced to the zone-less type.** Java: the example's forced type
`.withIncludeTypes("TIMESTAMP")` does not match `TIMESTAMP WITH TIME ZONE`, so jOOQ gives
`OffsetDateTime` and binds the offset (`ekbatan-examples/spring-boot-wallet-rest-gradle-pg/build.gradle.kts:110`).
Python: `FORCED["TIMESTAMP"]` matches SQLAlchemy's class name, which is the same for both kinds
(`src/ekbatan/schemagen.py:85`); `timestamp_column()` then binds a naive UTC value
(`src/ekbatan/schema.py:80-83`, `clock.py:141-148`), which PostgreSQL reads in the session's zone.
Matching: force only when `column.type.timezone` is false and emit `DateTime(timezone=True)`
otherwise - a few lines in `_column_type`. No effect on Ekbatan's own tables (zone-less).

**2. `VARCHAR(36)` ids turned into uuids.** Java forces `CHAR\(36\)` named `id`/`*_id`, and only in
the MySQL build (`ekbatan-examples/spring-boot-wallet-rest-gradle-mysql/build.gradle.kts:151-155`);
PostgreSQL and MariaDB have real uuid columns. Python's guess also takes `VARCHAR`/`String` and runs
on every dialect (`src/ekbatan/schemagen.py:131-141`). Matching: accept `CHAR` only, and only when
generating from MySQL/MariaDB - two conditions.

**3. `copy()` on a model with a derived field.** Java: the processor gives a builder field to every
instance field (`AutoBuilderProcessor.java:194-226`) but the model's constructor computes the
derived one, so copy works. Python: `_field_names` lists `init=False` fields
(`src/ekbatan/domain.py:317-318`), `copy_base` copies them into the builder (`domain.py:233-245`),
and the constructor refuses them; codegen also writes a setter for them (`codegen.py:193-199`).
Matching: skip `init=False` fields in `_field_names`/`copy_base` and in `_fields` - three
one-line filters.

**4. `schema.py` that does not import.** Java: jOOQ maps every type it meets, generating an enum
class for `ENUM`. Python: `_generic` looks a reflected type up in `sqlalchemy`'s top level and,
when absent, writes its `repr()` and imports that name from `sqlalchemy` anyway
(`src/ekbatan/schemagen.py:164-186`); `ARRAY` is emitted without its item type. Matching: emit
dialect types from `sqlalchemy.dialects.<name>` (or map `TINYINT(1)` to `BOOLEAN`, `ENUM` to
`Enum(...)`, `BYTEA` to `LargeBinary`, `LONGTEXT` to `Text`, `ARRAY` with its item type), plus a
test that imports a generated module covering these types. Small. One small choice inside it: on
MariaDB a `JSON` column reflects as `LONGTEXT`; Java leaves it a `String`, so text is the
matching answer (forcing `json_column()` there would be friendlier but go beyond Java).

**5. Wrong setter types.** Java: `TypeName.get(field.asType())` renders any type exactly
(`AutoBuilderProcessor.java:205`). Python: `_annotation` turns anything that is not a class into
`Any` and drops the import (`src/ekbatan/codegen.py:202-221`), so `Literal["a","b"]` becomes
`Any[Any, Any]` and the `...` of `tuple[int, ...]` becomes `Any`. Matching: render `Literal`
values with `repr`, `Ellipsis` as `...`, and fall back to the annotation's own text; add both
to the codegen test. Small.

**6. The header's promise.** `src/ekbatan/codegen.py:112-113` writes "A stale builder is a type
error rather than a wrong answer"; the module's own docstring (`codegen.py:18-24`) records that
this was measured false, and mypy accepts a setter that does not exist (`mypy.python.out`).
Matching: replace the two lines with the truth (a stale builder is caught by regenerating, e.g.
`scripts/check-builders.py`, not by the checker) and regenerate the committed builders. Trivial.

## Totals

26 checked: 7 same, 6 fix, 13 leave, 0 needs you.
