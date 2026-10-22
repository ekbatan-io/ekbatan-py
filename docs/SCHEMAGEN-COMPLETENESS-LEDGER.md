# Ledger: schemagen completeness and correctness (EKB-0427-)

Asked on 2026-10-22: audit `ekbatan.schemagen` for completeness and correctness against what
users need, with jOOQ's code generator (the Java port's tool) as the bar, and fix what is missing
or wrong. "It's very very very very important ... we don't want to limit the capabilities of the
framework users." File-first: every feature is a row here, measured, before its fix.

Judgement calls: [DECISIONS-TO-REVIEW.md](DECISIONS-TO-REVIEW.md), "schemagen completeness
(EKB-0427-)".

## How each row is proven

Reading the database is SQLAlchemy reflection; writing `schema.py` is our own renderer. So the
risk is what reflection sees and the renderer drops or writes wrong. Each row is measured by
running, not by reading the code:

- **round trip**: migrations -> database A -> schemagen -> import the module ->
  `metadata.create_all` into an empty database B -> compare A and B. The comparison is the
  database's own description of itself - `pg_dump --schema-only` on PostgreSQL, `SHOW CREATE
  TABLE` on MySQL and MariaDB, the `PRAGMA`s and `sqlite_master` on SQLite - plus SQLAlchemy's
  inspector on all four. Anything that differs is a loss.
- **data round trip**: a value per type written and read back through the framework's sync and
  async engines; the value and the record's type must match what the driver gives.
- **sqlacodegen as an oracle**: run in a throwaway environment on the same database, to spot
  what it writes that we drop. Never a dependency.

States: `todo`, `ok` (measured faithful before any change), `fixed` (was lost or wrong, now
faithful - commit named), `noted` (intentionally not written; a generation-time note says so and
the docs say why), `proposal` (a big new subsystem, written up for the user, not built), `n/a`.

## The feature matrix

Filled in as measured. Columns: jOOQ generates it / SQLAlchemy reflects it (pg, my = MySQL,
ma = MariaDB, sl = SQLite) / sqlacodegen renders it / our schemagen before this work (measured) /
needed by ekbatan users / action.

Measured 2026-10-22 with the round-trip probe (PostgreSQL 17, MySQL 8.4, MariaDB 11.4, SQLite)
before any change, on HEAD `2bbea45`. "faithful" = `create_all` from the module built the same
thing the migrations built, by the database's own description. sqlacodegen 3.x was run on the
same databases. Short forms: **FAIL** = the module's `create_all` raises; **lost** = built
without it, no note; **wrong** = built differently, no note.

### Column types

| # | feature | jOOQ | SA reflects | sqlacodegen | ours before | needed | action |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T1 | PG numeric family (smallint..bigint, numeric(p,s), numeric(p), real, double, float(p), money) | yes | pg | yes | faithful | yes | ok |
| T2 | PG serial / bigserial / smallserial as key | yes (identity) | pg | yes | faithful | yes | ok |
| T3 | PG serial not the key: its sequence | yes | pg | `Sequence(...)` | sequence built without `AS integer`/`smallint` and not `OWNED BY` the column | low | fixed EKB-0429: `data_type` written; OWNED BY noted |
| T4 | PG char/varchar/text, lengths, `COLLATE` | yes | pg | yes | faithful | yes | ok |
| T5 | PG bytea, bool, uuid, json, jsonb, inet, cidr, macaddr(8), interval (+fields, precision), tsvector, oid, regclass, citext, hstore, ranges, multiranges | yes | pg | yes | faithful | yes | ok |
| T6 | PG bit(n), varbit(n), varbit | yes | pg | yes | faithful; record `Any` (psycopg `str`, asyncpg `BitString`, measured) | yes | ok (note kept) |
| T7 | PG xml, tsquery, jsonpath | yes | no (warning) | `NullType` | `sql_type(...)`, faithful; record `Any`, but both drivers read `str` (measured) | yes | fixed EKB-0428: record `str` |
| T8 | PG geometric (point, line, lseg, box, path, polygon, circle), pg_lsn, txid_snapshot | yes (Object) | no | `NullType` | `sql_type(...)`, faithful; record `Any` (drivers differ, measured) | yes | ok |
| T9 | PG arrays, 1-D (of any type above, incl. enum, citext, varchar(n), numeric(p,s)) | yes | pg | yes | faithful | yes | ok |
| T10 | PG multi-dimensional arrays `INTEGER[][]` | yes | pg: dims not reflected | dims lost | `ARRAY(INTEGER())`: DDL same (PG ignores), but record `list[int]` where both drivers read `list[list[int]]` (measured) | yes | fixed EKB-0429: dimensions read from `pg_attribute.attndims`, record nested |
| T11 | PG array of a type SA cannot read (`xml[]`) | yes | no | no | `sql_type("xml[]")`, record `Any`; drivers read `list[str]` | low | fixed EKB-0428: record `list[str]` |
| T12 | PG enum (named), shared by tables, in another schema, array of enum | yes: Java `enum` class | pg | yes, + Python `enum.Enum` class | faithful (`Enum(..., name=, schema=)`), record `Literal[...]` | yes | ok; Python enum classes: DECISION (not generated) |
| T13 | PG domain | yes (Domain + base type) | pg (`get_domains`) | `DOMAIN(..., create_type=False)` - `create_all` cannot build it | `postgresql.DOMAIN("d", INTEGER())`: **lost** CHECK, NOT NULL, DEFAULT, base length (`varchar(10)` -> `varchar`); the domain's NOT NULL/DEFAULT were put on the column instead | yes | fixed EKB-0429 |
| T14 | PG composite type (UDT) column | yes: UDT + record classes | no | `NullType` | `sql_type("address")`: **FAIL** on an empty database (type missing), no note | rare | noted EKB-0429 (+ proposal for UDT classes) |
| T15 | PG extension types (citext, hstore, vector) | yes | pg (citext, hstore) / no (vector) | partly | faithful once the extension exists; `create_all` does not create extensions; no note | yes | noted EKB-0429 (extension list at generation) |
| T16 | MySQL ints all sizes, unsigned, bool/tinyint(1) | yes | my, ma | yes | faithful | yes | ok |
| T17 | MySQL ZEROFILL with display width `INT(5) ZEROFILL` | yes | my, ma | yes | **wrong**: width dropped, `int(10) zerofill` built | low | fixed EKB-0429: width kept with zerofill |
| T18 | MySQL decimal, float, double, double(m,d), bit(n), year, enum, set | yes | my, ma | yes (enum as a class) | faithful | yes | ok |
| T19 | MySQL char/varchar with charset/collation, national, binary attr, text/blob sizes | yes | my, ma | yes | faithful | yes | ok |
| T20 | MySQL spatial (point, geometry, linestring, polygon, ...) | yes (Object) | no | `NullType` | `sql_type(...)` faithful; record `Any`; both drivers read `bytes` (WKB) | low | fixed EKB-0428: record `bytes` |
| T21 | MySQL spatial `SRID n` | n/a | no | no | **lost** | low | fixed EKB-0428: `sql_type("point SRID 4326")` |
| T22 | MariaDB uuid, inet4, inet6, json (longtext + check) | yes | ma | partly | faithful; inet4/inet6 record `Any`, drivers read `str` | yes | fixed EKB-0428: record `str` |
| T23 | SQLite affinities (INT, VARCHAR(n), NUMERIC, REAL, BLOB, "UNSIGNED BIG INT", any spelling) | yes | sl (by affinity) | yes | written by affinity, same affinity in `create_all` | yes | ok |
| T24 | SQLite column with no declared type | yes | sl: `NullType` | `NullType` | **FAIL**: `NullType()` cannot be built | low | fixed EKB-0428: `sql_type("")`, record `Any` |
| T25 | SQLite column `COLLATE NOCASE` | n/a | no | no | **lost** | yes | fixed EKB-0428: read from the table's SQL |

### Columns

| # | feature | jOOQ | SA reflects | sqlacodegen | ours before | needed | action |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C1 | nullability | yes | all | yes | faithful | yes | ok |
| C2 | server defaults: literals, functions, `CURRENT_TIMESTAMP`, expressions, casts | yes | all | yes | faithful | yes | ok |
| C3 | MySQL `TIMESTAMP`/`DATETIME`/`DATETIME(3)` `DEFAULT CURRENT_TIMESTAMP[(n)] [ON UPDATE ...]` (the column forced to `timestamp_column()`, DATETIME(6)) | yes | my, ma | yes | **FAIL** on MySQL ("Invalid default value"); MariaDB adjusts silently | yes - the most common MySQL timestamp | fixed EKB-0428: the default written at `(6)` to match the column the helper builds, with the note |
| C4 | PG default `nextval(...)` (serial not the key) | yes | pg | yes | faithful | yes | ok |
| C5 | MariaDB default `NEXT VALUE FOR seq` | yes | ma: **dropped by reflection** | no | **lost** | yes | fixed EKB-0429: read from `information_schema.COLUMNS` |
| C6 | identity: always / by default, start, increment, cycle | yes | pg | yes | faithful | yes | ok |
| C7 | identity minvalue, maxvalue, cache | yes | pg | yes | **lost** | low | fixed EKB-0429 |
| C8 | MySQL `AUTO_INCREMENT` on the key | yes | my, ma | yes | faithful | yes | ok |
| C9 | MySQL `AUTO_INCREMENT` outside the key | yes | my, ma | no | noted (SA cannot declare it) | rare | ok (noted) |
| C10 | SQLite `INTEGER PRIMARY KEY` (rowid alias, filled by the database) | yes | sl | yes | DDL faithful; but `autoincrement=False` and the record says the key is required | yes | fixed EKB-0428: auto, `NotRequired` |
| C11 | SQLite `AUTOINCREMENT` | n/a | no | no | **lost** | low | fixed EKB-0428: `sqlite_autoincrement=True` |
| C12 | computed columns, stored and virtual | yes | all | yes | faithful | yes | ok |
| C13 | column and table comments | yes | pg, my, ma | yes | faithful | yes | ok |
| C14 | MySQL/MariaDB `INVISIBLE` column | n/a | no | no | **lost** | rare | noted EKB-0429 |
| C15 | PG column defaults inherited from a domain | yes | pg (merged) | merged | merged onto the column | yes | fixed EKB-0429 with T13 |

### Keys, constraints, indexes

| # | feature | jOOQ | SA reflects | sqlacodegen | ours before | needed | action |
| --- | --- | --- | --- | --- | --- | --- | --- |
| K1 | primary key, composite, named | yes (Keys) | all | yes | faithful | yes | ok |
| K2 | unique constraints, composite, named | yes (Keys) | all | yes | faithful | yes | ok |
| K3 | PG `UNIQUE NULLS NOT DISTINCT` (constraint) | yes | pg | no | **lost** | yes | fixed EKB-0429 |
| K4 | PG `DEFERRABLE` unique / primary key | yes | no | no | **lost** | low | fixed EKB-0429: read from `pg_constraint` |
| K5 | check constraints, table and column level, named | yes (Checks) | all; MariaDB column-level via our own read | yes | faithful; MariaDB column check loses its name | yes | fixed EKB-0429: name kept |
| K6 | MySQL `CHECK ... NOT ENFORCED` | n/a | no | no | **wrong**: enforced | rare | noted EKB-0429 |
| K7 | foreign keys: composite, named, ON DELETE/UPDATE (all actions), self-referencing, cross-schema | yes | all | yes | faithful | yes | ok |
| K8 | FK `DEFERRABLE INITIALLY DEFERRED` | yes | pg, sl | yes | faithful | yes | ok |
| K9 | FK `MATCH FULL` | yes | pg | yes | **lost** | low | fixed EKB-0429 |
| K10 | PG `EXCLUDE` constraints | no | no (seen as an index) | no | **lost** | yes (only way to say "no overlap") | fixed EKB-0429: `ExcludeConstraint` |
| K11 | indexes: unique, composite, partial `WHERE`, `INCLUDE`, `USING gin/gist/brin/hash/spgist`, `WITH (...)`, op classes on columns, `NULLS NOT DISTINCT` | yes (Indexes) | pg | mostly | faithful | yes | ok |
| K12 | PG expression index | yes | pg | **dropped** | faithful | yes | ok |
| K13 | PG index sort order `DESC NULLS LAST` | yes | pg | dropped | **FAIL**: `text("t.a DESC NULLS LAST")` is not valid in CREATE INDEX | yes | fixed EKB-0428 |
| K14 | PG index element `COLLATE`, sort order on an expression | yes | pg (partly) | dropped | **lost** | yes | fixed EKB-0428: each element as PostgreSQL prints it |
| K15 | MySQL functional index `((lower(x)))` | yes | my: no expression | dropped | **FAIL**: `Index("t_func")` with no columns; mixed ones lose the expression | yes | fixed EKB-0428: read `information_schema.STATISTICS.EXPRESSION` |
| K16 | MySQL/MariaDB index `DESC` | yes | no | dropped | **lost** | yes | fixed EKB-0428 |
| K17 | MySQL prefix lengths, FULLTEXT, SPATIAL | yes | my, ma | yes | faithful | yes | ok |
| K18 | MySQL/MariaDB index `COMMENT`, `INVISIBLE`/`IGNORED`, `USING BTREE` | n/a | no | no | **lost** | rare | noted EKB-0429 (USING written) |
| K19 | SQLite expression index | yes | **skipped by reflection** (warning) | dropped | **lost**, no note | yes | fixed EKB-0428: read from `sqlite_master` |
| K20 | SQLite index `DESC`, `COLLATE` | yes | no | dropped | **lost** | low | fixed EKB-0428 with K19 |
| K21 | SQLite partial index | yes | sl | yes | faithful | yes | ok |

### Tables and other objects

| # | feature | jOOQ | SA reflects | sqlacodegen | ours before | needed | action |
| --- | --- | --- | --- | --- | --- | --- | --- |
| O1 | several schemas, cross-schema FK, enum in another schema | yes | pg | yes | faithful | yes | ok |
| O2 | quoted, mixed-case, reserved-word, spaces, dashes, unicode, 63-char names | yes | all | yes | faithful | yes | ok |
| O3 | views | yes (as tables) | all | yes, as tables | `view_metadata`, faithful | yes | ok |
| O4 | PG materialized views | yes (as tables) | pg | as a table | **wrong**: in `metadata`, so `create_all` builds a *table* by its name | yes | fixed EKB-0429: `view_metadata`, note |
| O5 | PG partitioned table + partitions | parent + partitions as tables | pg (as plain tables) | plain tables | **wrong**: parent built unpartitioned, each partition a separate table | yes | fixed EKB-0429: `postgresql_partition_by`, partitions created by DDL after the parent |
| O6 | PG table inheritance | yes | pg | no | **wrong**: built as unrelated tables (and `capitals` sorts before `cities`) | rare | fixed EKB-0429: `postgresql_inherits` + dependency |
| O7 | PG `UNLOGGED` | n/a | no | no | **lost** | low | fixed EKB-0429: `prefixes=["UNLOGGED"]` |
| O8 | MySQL/MariaDB table options: ENGINE, DEFAULT CHARSET, COLLATE, ROW_FORMAT, ... | n/a | my, ma | no | **lost** - a `latin1` table's columns become `utf8mb4` | yes | fixed EKB-0429 |
| O9 | MariaDB system-versioned table | n/a | no | no | **lost** | rare | noted EKB-0429 |
| O10 | SQLite `WITHOUT ROWID`, `STRICT` | n/a | no | no | **lost** | low | fixed EKB-0428 |
| O11 | standalone sequences (PG, MariaDB) | yes (Sequences) | names only | used ones only | not generated; a used one loses START/INCREMENT/MIN/MAX/CACHE/CYCLE | yes | fixed EKB-0429: every sequence, a module constant, all options |
| O12 | routines (functions, procedures) | yes (Routines) | no | no | not generated, no note | some | noted EKB-0429 + proposal |
| O13 | triggers | no | no | no | not generated, no note | n/a | noted EKB-0429 |
| O14 | composite types / UDT classes | yes | no | no | see T14 | rare | noted EKB-0429 + proposal |
| O15 | temporary tables | n/a | n/a | n/a | gone after migration | n/a | n/a |
| O16 | MySQL events | no | no | no | not generated | n/a | noted EKB-0429 |

### Naming, configuration, records

| # | feature | jOOQ | ours before | action |
| --- | --- | --- | --- | --- |
| N1 | table constant, `schema_table` for other schemas, keyword/collision handling, record names | yes | faithful (EKB-0355, EKB-0416 tests) | ok - re-verified |
| N2 | column access `T.c["Some Col"]`, columns differing only in case | yes | faithful | ok |
| N3 | forced types: names, types, expressions, nullability, dialect filters, converters, sqlalchemy-type | yes | faithful (EKB-0355 grid) | ok - re-verified by the suite |
| N4 | includes / excludes / views / schemas | yes | faithful | ok |
| N5 | synthetic keys (jOOQ `syntheticObjects`) | yes | not supported | n/a: a hand-written `Table` or a converter covers the use; noted in DECISIONS |
| N6 | typed records vs driver values, sync and async | yes | faithful (`test_schemagen_types`), except T7, T10, T11, T20, T22 | fixed per row |

## Results

Each row is proven by `tests/integration/test_schemagen_round_trip.py` - a round trip per
database: the migrated and the rebuilt database describe themselves the same, line for line,
except for the differences a case names, each of which a note announces at generation time - by
the type matrix (`tests/integration/test_schemagen_types.py`: new cases `tsquery`, `jsonpath`,
`integer_array_2d`, `domain`, `inet4`, `point`, each written, rebuilt and read back through the
sync and async drivers), and by `tests/unit/test_schemagen_completeness.py` (the readers of the
database's own text, and the spelled-type records).

**Able to fail**, run: with the new tests and the source as it was before each fix, the round
trips fail - `index_elements` on all four databases before EKB-0428; `keys_and_sequences`,
`domains_and_arrays`, `relation_kinds`, `unwritten` (PostgreSQL), `tables_and_notes` (MySQL)
and `sequences_and_notes` (MariaDB) before EKB-0429.

Differences left on purpose, each with a note and in the docs:

- a column forced to `timestamp_column()` is `DATETIME(6)` / `TIMESTAMP` at six digits, as before
  (EKB-0321); its default of the current time follows it (C3);
- a non-key `SERIAL`'s sequence is not `OWNED BY` its column (T3);
- views and materialized views are the migrations' (O3, O4);
- an inherited table's columns are declared on it as well as on its parent; PostgreSQL merges
  them (O6);
- MariaDB's column-level check is built as a table check of the same name (K5);
- MariaDB's `JSON` (`LONGTEXT` + `json_valid`) is built by `json_column()` with `utf8mb4_bin`
  (unchanged since EKB-0321);
- a domain that is NOT NULL with a named check keeps the check's meaning, not its name
  (SQLAlchemy writes the name on the NOT NULL); a domain with several checks gets one, joined by
  AND (T13);
- what SQLAlchemy cannot declare at all is listed in one note per kind: MySQL/MariaDB invisible
  columns, unenforced checks, index comments, invisible/ignored indexes, system versioning,
  `AUTO_INCREMENT` outside the key; PostgreSQL functions, procedures, triggers, composite types,
  extensions; MySQL/MariaDB routines, triggers, events; SQLite triggers.

## Review fixes (EKB-0450-)

2026-10-22, unattended, in the worktree `ekbatan-py-sgfix` (branch `schemagen-review-fixes`). An
independent review ran probes against the work above and found 14 things wrong, each proven by a
run. Each is fixed here with a regression test that failed on the code before the fix and passes
after it. Judgement calls: [DECISIONS-TO-REVIEW.md](DECISIONS-TO-REVIEW.md), "schemagen review
fixes (EKB-0436-)".

The round trip itself was widened first: views are now compared (their columns in A against the
tables `view_metadata.create_all` builds in a third database), and MySQL/MariaDB other schemas -
databases of the same server, which A and B would share - are described with A, then dropped
and made again before B is built.

| # | finding | what was done | red before, green after | commit |
| --- | --- | --- | --- | --- |
| R0 | (found by the widened round trip) MySQL/MariaDB view columns lost NOT NULL and a collation other than the database's - reflection reads a view by `DESCRIBE`; a view's record said `X \| None` of a NOT NULL column | read from `information_schema.COLUMNS` | `mysql-tables_and_notes`, `{mysql,mariadb}-columns_enums_keys_views`, `{mysql,mariadb}-other_schema` | EKB-0450 |
| R1 | (finding 1, HIGH) PG: a key defaulting to a sequence it does not own was written with no default, and `create_all` made a new `<table>_<col>_seq` from 1; a table in schema `other` drawing on `public.shared_seq` got `Sequence("shared_seq", schema="other")` (reflection qualifies an unqualified sequence with the table's schema) | the sequence a default draws on is read from `pg_depend`, with its real schema, and the default as the server prints it. Only a SERIAL key (sequence owned by this very column) is left to SQLAlchemy. A sequence of its own: the module constant + the default. One another table's SERIAL owns: the default, and the table built after that one (`add_is_dependent_on`). One in a schema not read: `Sequence(name, schema=..., options)` on the column | `postgres-sequence_defaults` (key on a standalone sequence, cross-schema, a borrowed SERIAL sequence, a sequence in an unread schema) | EKB-0451 |
| R2 | (finding 2, HIGH, from EKB-0429) MySQL/MariaDB RANGE and LIST partitioned tables: `table_options()` wrote `mysql_partition_definitions=...`, and `create_all` failed (1064). Reflection also misreads a LIST partition's values (`IN (1,2)` comes back `IN (12)`) | partitioning is read whole from `SHOW CREATE TABLE` and applied right after the table is created, `sqlalchemy.event.listen(T, "after_create", sqlalchemy.DDL("ALTER TABLE %(fullname)s PARTITION BY ..."))`, with a note; reflection's partition options are no longer written | `{mysql,mariadb}-partitions`: RANGE, LIST, HASH, KEY, RANGE + SUBPARTITION, RANGE COLUMNS | EKB-0452 |
| R14 | (finding 14, LOW) MySQL `STATS_PERSISTENT=0` (and `STATS_AUTO_RECALC`, MariaDB's `PAGE_CHECKSUM`...) - options reflection does not read - lost with no note | read from the table's own text and **written** (`mysql_stats_persistent="0"`), not only noted - the round trip shows `create_all` rebuilds them; what of the options line is not `NAME=value` (a `TABLESPACE` name) gets a note | `{mysql,mariadb}-unread_table_options` | EKB-0452 |
| R3 | (finding 3, MEDIUM) PG objects lost with no note: row level security and policies, rules, extended statistics, index comments, NOT VALID checks (rebuilt valid), custom collations (`create_all` then fails "collation does not exist") | each read from the catalog and said in a note naming it; a NOT VALID check or foreign key gets a note of its own; the collation note says to create it before `create_all`. Found on the way, by widening the round trip's description (reloptions, replica identity, column storage): **written**, as statements after the table (`ALTER TABLE %(fullname)s SET (fillfactor=70)`, `REPLICA IDENTITY FULL / USING INDEX`, `ALTER COLUMN v SET STORAGE / COMPRESSION / STATISTICS / (n_distinct=...)`); a table in its own tablespace and publications are noted | `postgres-unwritten_table_parts` | EKB-0453 |
| R4 | (finding 4, MEDIUM) SQLite foreign key options lost: SQLAlchemy reads no action of a column's own `REFERENCES ... ON DELETE CASCADE`, and loses a `DEFERRABLE` on a line of its own; `test_schemagen.py`'s SQLite `children` check was vacuous (both sides read by the same inspector) | actions read from `PRAGMA foreign_key_list`, DEFERRABLE / INITIALLY DEFERRED from the table's text, and written on the key; the shape test now compares `PRAGMA foreign_key_list` and the text's DEFERRABLE of A and B, with a table-level DEFERRABLE key on a line of its own added to its SQLite schema | `sqlite-foreign_keys`; `test_create_all_builds_the_keys_indexes_defaults_and_checks_the_migrations_built[sqlite]` | EKB-0454 |
| R15 | (found by the new SQLite case) SQLite generated columns: SQLAlchemy reads them with one pattern over the whole table text, so a `STORED` column ran into the next column's `AS (...)` - `create_all` failed ("duplicate column name") | each column's expression and STORED/VIRTUAL read from the table's text with the tokenizer the other SQLite readers use | `sqlite-columns_keys_views` | EKB-0455 |
| R5 | (finding 5, MEDIUM) PG: a domain used only as an array element lost its CHECK (`ARRAY(postgresql.DOMAIN("posint", INTEGER()))`) | the array's item written as a domain column is, whole. Found on the way, measured: psycopg does not know a domain's array type and hands back its text, which SQLAlchemy splits into characters (`['{', '5', ',', '6', '}']`); asyncpg reads `[5, 6]`. So the record says `Any`, with a note that names the way out (cast to the base type's array, measured to read a list on psycopg) | `postgres-domain_array`; `test_an_array_of_a_domain_is_written_whole_and_its_record_says_what_both_drivers_agree_on` | EKB-0456 |
