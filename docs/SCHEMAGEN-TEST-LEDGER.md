# Ledger: hardening schemagen's tests

Asked on 2026-10-18: make the `ekbatan.schemagen` test suite "very very very robust and precise
and reliable", with exhaustive cases. File-first: every gap is a row here before its test exists.

Baseline, measured 2026-10-18 (`uv run --with pytest-cov pytest --dialects=all
tests/unit/test_schemagen_config.py tests/integration/ -k schemagen -q -p no:randomly
--cov=ekbatan.schemagen --cov-branch --cov-report=term-missing`): **88%** branch coverage, about
90 s. `__init__` 65%, `__main__` 0%, `_config` 90%, `_reflect` 96%, `_render` 89%, `_types` 93%.
The CLI tests run in a subprocess, which coverage does not see; `main` is covered here by calling
it in-process as well.

Where the new tests live:

- `tests/unit/test_schemagen_hardening.py` - no database: config file, matching, targets, type
  table, rendering edge cases, the image-to-container choice, `_relative`, `main`'s errors.
- `tests/integration/test_schemagen_hardening.py` - real databases (the session's containers):
  the forced-type grid, reflection edge cases, create_all fidelity, regeneration, staleness.
- `tests/integration/test_schemagen_cli.py` - the command: in-process `main` and
  `python -m ekbatan.schemagen` in a subprocess, with real throwaway containers.

State: `todo`, `done`, `bug` (a test found a real bug - see DECISIONS-TO-REVIEW.md, "schemagen
tests (EKB-0355-)"), `open` (a bug or doubt left as it is, for the user), `n/a` (unreachable or
not worth a test - says why).

## Probes run before the ledger (so the rows below are facts)

| # | probe | result |
| --- | --- | --- |
| P1 | `render` of a column comment holding `\r` (and a real PG `COMMENT ... IS E'a\r\nb'`) | **bug**: the module is a `SyntaxError` - `lit` escapes `\n` but not `\r` or other control characters |
| P2 | tables named `text`, `date`, `migrations_fingerprint` | **fixed in `EKB-0416`** (was open): `TEXT = Table(...)` shadowed `from sqlalchemy import TEXT`; a later table's `TEXT()` raised `TypeError: 'Table' object is not callable`. `MIGRATIONS_FINGERPRINT = Table(...)` overwrote the fingerprint. Now every SQLAlchemy name is written `sqlalchemy.X`, and such a table is `MIGRATIONS_FINGERPRINT_` |
| P3 | the same SQLite schema with five unnamed constraints, generated in 8 processes | stable: 8 identical files. The order of unnamed constraints follows no rule but does not vary run to run; not acted on |
| P4 | PG `id INTEGER, CONSTRAINT pk_t PRIMARY KEY (id)` | **bug**: written as `PrimaryKeyConstraint("id", name="pk_t")` with no `autoincrement=False`, so `create_all` builds `id SERIAL` (default `nextval(...)`) |
| P5 | `_start` with `localhost:5000/mysql:8.4`, `mysql@sha256:...`, `registry:443/team/mariadb:11.4` | **bug**: a PostgreSQL container is started for each - the family is read before the registry's port and the digest are taken off |
| P6 | `url = ["postgresql://u:PASSWORD@h/db"]` in the file | **bug**: the error message prints the password |
| P7 | MySQL: a table in a listed schema with a foreign key into the connection's own database | left out with the note "refers to main.parents, which is not generated" - it is generated, unqualified. A fix (write it unqualified) was tried and **reverted**: MySQL then reads it as the listed database's table and `create_all` fails. **Open** - DECISIONS |
| P8 | PG: a non-key `SERIAL` in a listed schema | **bug**: `Sequence("t_seq_seq")` without its schema; `create_all` fails, `relation "audit.tallies_seq_seq" does not exist` |
| P9 | `--config` naming a directory; a config file that is not UTF-8 | **bug**: a traceback (`IsADirectoryError`, `UnicodeDecodeError`) instead of `error: ...` |
| P10 | `[tool.ekbatan] schemagen = "x"`; `[tool] ekbatan = 1` | **bug**: "unknown key(s) ['x']" (the string's letters), and an `AttributeError` traceback |

## (a) Every uncovered line and branch of the baseline

| area | line(s) | what is untested | planned test | state |
| --- | --- | --- | --- | --- |
| `__init__` | 206 | `generate` with no migrations anywhere | unit: `generate(None, out)` names the key and the flag | done |
| `__init__` | 211 | `generate` with no out anywhere | unit: `generate(migrations, None)` | done |
| `__init__` | 235 | a started container is stopped | unit: fake container records `stop()`, also when migrate fails | done |
| `__init__` | 276-306 | `_start`: sqlite, mysql, mariadb, postgres; the MariaDB url rewrite | unit: fake container classes, every image spelling (P5); CLI: real containers | done; **bug** P5 fixed |
| `__init__` | 325-326 | `_relative` on different Windows drives | unit: `os.path.relpath` raising `ValueError` gives `None` | done |
| `__init__` | 331-385 | `main`: every flag, `--config`, the default file, `ValidationError` -> 1 + stderr, `wrote ...` | CLI file, in-process and subprocess | done |
| `__init__` | 390-395 | `_show_notes` installs `note: ...` on stderr | unit: with no handlers, a note prints as `note: ...`; with one, it does not | done |
| `__main__` | 3-6 | `python -m` entry | in-process `runpy.run_module` exits with `main`'s code; subprocess too | done |
| `_config` | 114 | `ForcedType.target` (unused in src) | unit: each of the three targets | done |
| `_config` | 128 | `nullability="NULL"` refusing a NOT NULL column | unit grid + integration grid | done |
| `_config` | 249-254 | missing file when required; bad TOML | unit | done |
| `_config` | 274 | a string key given a non-string | unit grid (every string key) | done |
| `_config` | 289 | `schemas = "one"` as a bare string | unit | done |
| `_config` | 295 | `forced-types` as one table, not an array | unit | done |
| `_config` | 316 | a forced-types entry that is not a table | unit | done |
| `_config` | 327 | a forced-type key given a non-string | unit grid (every forced key) | done |
| `_reflect` | 91->90 | `schemas` naming the connection's own schema, or one twice | integration (PG, MySQL) | done |
| `_reflect` | 104 | a reflected table of another schema in this schema's pass | n/a - `resolve_fks=False` stops reflection pulling in another schema's table; defensive | n/a - see Results |
| `_reflect` | 159->167 | a dialect that is none of the four | n/a - schemagen supports four databases; any other has no raw spelling and still works through SQLAlchemy | n/a - see Results |
| `_reflect` | 195 | a MariaDB column-level CHECK that is not JSON | integration (MariaDB): `qty INT CHECK (qty >= 0)` and `json_valid` on a TEXT column stay checks; create_all rebuilds them | done |
| `_render` | 55 | a table name starting with a digit | unit + integration: `2024_sales` exported as `T_2024_SALES` | done |
| `_render` | 126 | a MariaDB JSON column with the defaults off | integration (MariaDB): `JSON(none_as_null=True)` | done |
| `_render` | 216-223 | identity with start, increment, cycle, BY DEFAULT | integration (PG): create_all rebuilds the same identity | done |
| `_render` | 237 | MySQL AUTO_INCREMENT outside the key: the note | integration (MySQL, MariaDB) | done |
| `_render` | 254, 283-287 | a non-key `SERIAL`: `Sequence(...)` declared | integration (PG): create_all creates the sequence; also in a listed schema (P8) | done; **bug** P8 fixed |
| `_render` | 275 | a server default that is not text | unit: a `FetchedValue` default is left out | done |
| `_render` | 277 | MariaDB's `DEFAULT NULL` | integration (MariaDB) + unit: no `server_default` | done |
| `_render` | 312-313 | MariaDB column checks emitted | integration (MariaDB), with 195 | done |
| `_render` | 341-345 | a foreign key to an excluded table: note, left out | integration (all) | done |
| `_render` | 375 | an index option other than `*_where` | integration (PG `USING hash`, MySQL prefix length) | done |
| `_render` | 403-404 | a framework table that differs from its helper: the note | integration (all) | done |
| `_render` | 420-425 | a framework table's extra index: kept on columns, noted on an expression | integration (all; expression on PG/SQLite) | done |
| `_render` | 468-469 | two tables exported under one name | unit: `order-items` and `order_items` refused naming both | done |
| `_types` | 91 | an array of a type SQLAlchemy cannot read | matrix (PG `XML[]`) -> `sql_type("xml[]")` | done |
| `_types` | 105 | JSONB with the defaults off | integration (PG): `postgresql.JSONB(none_as_null=True)` | done |
| `_types` | 128 | MySQL `FLOAT(7,3) UNSIGNED` | matrix (MySQL, MariaDB) | done |
| `_types` | 146 | MySQL `DECIMAL(10,2) UNSIGNED` | matrix (MySQL, MariaDB) | done |
| `_types` | 166 | `TIMESTAMPTZ(3)` | matrix (PG): `postgresql.TIMESTAMP(timezone=True, precision=3)` | done |
| `_types` | 197, 202, 240 | generic `LargeBinary`, `Uuid`, `String`/`Unicode` (never reflected) | unit: emit_type on hand-built types, and the module imports | done |
| `_types` | 249->251, 253-256 | `_as_reflected` for a non-dialect type, a repr that does not evaluate, one that builds no type | unit | done |
| `_types` | 359-360 | a name with broken arguments `String(36` | unit | done |
| `_types` | 371 | `**` arguments | unit | done |
| `_types` | 394 | a name that is not an identifier (`mysql.`, `Te-xt`) | unit | done |
| `_types` | 462 | a converter path naming a plain value | unit | done |
| `_types` | 489-490 | a sqlalchemy-type that raises when built | unit | done |

## (b) The forced-type behaviour grid

Each forced-type key alone and together, each target kind, on each database where it matters. The
grid runs as one generate per database with many rules, each rule scoped to its own column by
`include-expression`, so a spelling that fails leaves its column unforced and the test names it.

| # | behaviour | where | state |
| --- | --- | --- | --- |
| G-1 | `include-expression` as `column` | all | done |
| G-2 | `include-expression` as `table.column` | all | done |
| G-3 | `include-expression` as `schema.table.column` for a listed schema | PG, MySQL, MariaDB | done |
| G-4 | `schema.table.column` for the connection's own schema (code does it; docs say "for a schema in `schemas`") | all | done; docs gap (see DECISIONS) |
| G-5 | `include-expression` is case-sensitive, whole-text (`WIDGETS.id`, `idx` do not match) | all | done |
| G-6 | `include-types` in every spelling: the database's own (`character varying(36)`, `int unsigned`, `tinyint(1)`), SQLAlchemy's (`VARCHAR(36)`, `INTEGER UNSIGNED`), with and without arguments, `, ` and `,`, short names (`TIMESTAMP`, `TIMESTAMPTZ`, `TIMETZ`, `CHAR` for `character`), lower case | per database | done |
| G-7 | `include-types` `ENUM` and `JSON` (the extra spellings, incl. MariaDB's LONGTEXT JSON) | PG, MySQL, MariaDB | done |
| G-8 | `exclude-expression` takes a column back | all | done |
| G-9 | `exclude-types` takes a column back | all | done |
| G-10 | `nullability` ALL / NULL / NOT_NULL on a nullable and a NOT NULL column | all | done |
| G-11 | both includes must match | all | done |
| G-12 | first match wins among the user's rules | all | done |
| G-13 | the user's rules before the defaults (a TIMESTAMP forced to `Text`) | all | done |
| G-14 | default forced types off: each default's column is the type table's | all | done |
| G-15 | target: helper names (`uuid_column`, `json_column`, `timestamp_column`) - create_all and round trip | all | done |
| G-16 | target: SQLAlchemy type with args (`String(36)`, `Numeric(19, 4)`, keyword args) - create_all builds that type | all | done |
| G-17 | target: dialect type `mysql.TINYINT(unsigned=True)` (MySQL/MariaDB), `postgresql.INET` (PG) - create_all builds it, a value round-trips | per database | done |
| G-18 | target: converter, no args / args / keyword args / `module:Class` / an instance / a factory | all | done |
| G-19 | target: sqlalchemy-type function / TypeDecorator / TypeEngine instance / `module:Class` | all | done |
| G-20 | converter first then a name: the converter wraps the name; name first: no converter | all | done |
| G-21 | two forced types importing the same user name from two modules: both aliased apart, module imports | unit | done |
| G-22 | defaults are per database (`JSONB` only on PG; `JSON` not forced on PG) | unit (exists) + integration | done |

## (c) Config file validation

| # | case | state |
| --- | --- | --- |
| C-1 | a typo in every top-level key | done |
| C-2 | a typo in every forced-type key (incl. `dialects`, which only the built-ins use) | done |
| C-3 | every string key given a number, a bool, a list | done |
| C-4 | `views` and `default-forced-types` given strings and numbers | done |
| C-5 | a bad regex in `includes`, `excludes`, and each of the four forced-type patterns | done |
| C-6 | no include; two targets; three targets; no target | done |
| C-7 | the old `user-type`, also alongside a new key | done |
| C-8 | `nullability` lower case or unknown | done |
| C-9 | `forced-types` a table, a list of strings | done |
| C-10 | `schemas` a bare string (accepted), a list of numbers, a number | done |
| C-11 | a missing file when named; a missing default `pyproject.toml` (quiet) | done |
| C-12 | invalid TOML; a directory; non-UTF-8 (P9) | done; **bug** P9 fixed |
| C-13 | `[tool.ekbatan.schemagen]` not a table (P10) | done; **bug** P10 fixed |
| C-14 | `url` of the wrong type never prints the password (P6) | done; **bug** P6 fixed |
| C-15 | paths relative to the file, absolute paths kept | done |
| C-16 | an error names the file and the forced type's index | done |
| C-17 | empty `image` / `includes` fall back to the default silently | done; **open** (pinned as is) |

## (d) The command end to end

| # | case | state |
| --- | --- | --- |
| D-1 | `python -m ekbatan.schemagen --image sqlite`: exit 0, `wrote <path>`, module imports | done |
| D-2 | `--image postgres:17-alpine`, `mysql:8.4`, `mariadb:11.4` in a subprocess: the container is started, migrated, stopped and removed; MariaDB is generated as MariaDB | done |
| D-3 | exit 1 and `error: ...` on stderr, nothing on stdout, no traceback: no settings, missing `--config`, bad TOML, bad forced type, no migration directory, no tables | done |
| D-4 | notes are printed as `note: ...` on stderr | done |
| D-5 | each flag overriding the file: `--migrations`, `--out`, `--url`, `--image`, `--schema` (replaces the list), `--include`, `--exclude`, `--no-views`, `--no-default-forced-types` | done |
| D-6 | `--config` in another directory: its paths are relative to it, not the cwd | done |
| D-7 | the `url` from the file is used when no flag is given | done |
| D-8 | `--help` exits 0 and lists every flag | done |
| D-9 | an unknown flag exits 2 (argparse) | done |

## (e) The generated module

| # | case | state |
| --- | --- | --- |
| E-1 | imports exactly what it uses: no unused import, no undefined name (AST check) - on the type matrix, the shape schema and the forced-type grid, per database | done |
| E-2 | imports cleanly in a fresh interpreter (subprocess) | done |
| E-3 | `create_all` rebuilds the migrated schema (exists for SHAPE; add identity, serials, named single PK, MariaDB checks, framework indexes) | done |
| E-4 | staleness: stale -> regenerate -> imports again | done |
| E-5 | same input -> byte-identical output, twice in-process and in two processes, per database | done |
| E-6 | the written module equals what `generate` returns | done |
| E-7 | `__all__` lists every table and view constant, sorted, and `metadata`/`view_metadata` | done |

## (f) Found reading the code

| # | case | state |
| --- | --- | --- |
| F-1 | control characters in comments, defaults, checks (P1) | done; **bug** P1 fixed |
| F-2 | a table whose constant shadows an import (P2) | **open** - strict xfail tests |
| F-3 | a named single-column integer key (P4) | done; **bug** P4 fixed |
| F-4 | registry ports and digests in `image` (P5) | done; **bug** P5 fixed |
| F-5 | a foreign key into the connection's own schema from a listed one (P7) | **open** - fix tried and reverted; pinned per database |
| F-6 | quotes and backslashes in names, comments, defaults, enum values | done |
| F-7 | a table name with a space or hyphen; a keyword name (`class`) | done |
| F-8 | `_relative`: exactly `MAX_CLIMB` levels up is kept, one more is not | done |
| F-9 | `check_fingerprint`'s error names the fingerprints and the regenerate command | done |
| F-10 | a non-key `SERIAL` in a listed schema (P8) | done; **bug** P8 fixed |
| F-11 | an old-style `nextval(('s'::text)::regclass)` default: kept as text, no `Sequence` guessed | done |
| F-12 | `python -m` with the command's containers labelled so the removal check sees only its own (another run shares this Docker) | done |

## Results (2026-10-18)

**Full run**, once at the end: `uv run --with pytest-cov pytest --dialects=all
tests/unit/test_schemagen_config.py tests/unit/test_schemagen_hardening.py tests/integration/ -k
schemagen -p no:randomly --cov=ekbatan.schemagen --cov-branch` - **1179 passed, 27 skipped, 4
xfailed, 0 failed** in 113 s. Every skip is a database without the feature (SQLite has no schemas,
identity is PostgreSQL's, AUTO_INCREMENT is MySQL's, ...). The 4 xfails are F-2, strict, so they
fail the run the day F-2 is fixed. `uv run ruff check src tests` and `uv run ruff format --check
src tests`: clean. Docker answered throughout; no timeouts. After the full run, one message was changed (a section that is not a table now
names its kind, not its value); its unit and CLI tests were rerun and pass, lint clean.

**Coverage**, branch, same run (baseline in brackets):

| file | now | baseline |
| --- | --- | --- |
| `__init__.py` | 100% | 65% |
| `__main__.py` | 100% | 0% |
| `_config.py` | 100% | 90% |
| `_reflect.py` | 98% | 96% |
| `_render.py` | 100% | 89% |
| `_types.py` | 100% | 93% |
| total | 99% | 88% |

The one line and two branches left, both in `_reflect.py`, are not reachable from a supported
database:

- line 104, `if table.schema != schema: continue` - `metadata.reflect(..., resolve_fks=False)`
  reads one schema and does not follow a foreign key into another, so every table it returns is
  of the schema asked for. A guard, kept.
- branch 159->167, a dialect that is none of PostgreSQL, MySQL, MariaDB, SQLite - schemagen
  supports those four; another would get no raw spellings and still be typed through SQLAlchemy.

**Tests added** (cases as collected with `--dialects=all`):

| area | where | functions | cases |
| --- | --- | --- | --- |
| (a)+(c)+(f) config file, matching, targets, type table, writer, image choice, `_relative`, notes | `tests/unit/test_schemagen_hardening.py` | 56 | 189 |
| (b)+(e)+(f) forced-type grid, targets, create_all fidelity, reflection edges, regeneration, staleness | `tests/integration/test_schemagen_hardening.py` | 19 | 76 |
| (d) the command: in-process `main` and `python -m` with real containers | `tests/integration/test_schemagen_cli.py` | 16 | 36 |
| (a)+(e) type matrix: `XML[]`, `TIMESTAMPTZ(3)`, `DECIMAL UNSIGNED`, `FLOAT(M,D) UNSIGNED`; imports-exactly on the matrix and the shape module | `test_schemagen_types.py`, `test_schemagen.py` | 2 + 4 cases | 32 |

Also changed: `tests/schemagen_support.py` gained `unused_and_undefined` (E-1).

**Docs gaps** (not edited, listed in DECISIONS): `schema.table.column` works for the own schema
too; expression indexes are not generated on SQLite (SQLAlchemy skips them); list-valued index
options are written in single quotes.
