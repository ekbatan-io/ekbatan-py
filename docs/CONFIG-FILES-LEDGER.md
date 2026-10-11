# Ledger: reading the layout from config files (Track 2)

Track 1 ([DATASOURCE-LOGIN-LEDGER.md](DATASOURCE-LOGIN-LEDGER.md)) built the rules; this track lets a
config file produce the same `ShardingConfig` that code builds. The file format is only the
outside: every check stays in `DataSourceConfig` and its siblings.

## Decided with the user (2026-10-11)

| Part | Decision |
|---|---|
| Libraries | none; Python's own readers plus PyYAML (a normal dependency) and a small glue of our own. Compared by running them against pydantic-settings, Dynaconf, OmegaConf and Hydra: only this covers every format and fills placeholders (Dynaconf returned nothing for an INI file without its extra; OmegaConf turned a TOML file into one garbage key, without an error) |
| Formats | YAML (`yaml.safe_load`), TOML (`tomllib`), JSON (`json`), INI (`configparser`), all in step 1 |
| Keys | Java's layout under `ekbatan.sharding`; kebab-case and camelCase both accepted, as Java's `PropertyKeyNormalizer` does; names inside `data-source-properties` never changed |
| Env vars in YAML, TOML, JSON | `${DB_PASSWORD}`, default `${DB_PORT:5432}` - Spring's syntax |
| Env vars in INI | `%(ENV_DB_PASSWORD)s` - Supervisor's convention; `%(name)s` stays "another key in this file"; `%%` is a literal `%`; never `ExtendedInterpolation` |
| Missing variable, no default | startup fails, naming the variable - as Spring does |
| Variable set but empty | used as given |
| Types in YAML, TOML, JSON | as the file says |
| Types in INI | Ekbatan's fields by their declared type; in `data-source-properties`, quoted is text, an unquoted number or `true`/`false` is typed, anything else text |
| INI shape | keys keep their case; groups as dotted section names; list items as numbered sections |
| Errors | name the key or the variable, never a value; Python's INI errors print the raw value, so they are always caught and rephrased |
| Tests | real files in every format, against real databases |
| Out of scope | config made only of environment variables (Spring's `EKBATAN_SHARDING_...`); building objects from config (`"()"`, class import paths) |

## Tasks

| # | Task | State |
|---|---|---|
| 1 | Public loader: `load_sharding_config(path)` picks the reader by file extension | done |
| 2 | Map the layout into `ShardingConfig`: key forms, `configs` names, unknown keys refused with their path | done |
| 3 | Types: Ekbatan's fields checked against their declared types; INI text turned into them strictly | done |
| 4 | Placeholders: `${NAME}` / `${NAME:default}` for YAML, TOML, JSON; `%(ENV_NAME)s` and `%(key)s` for INI; fail early; values inserted as given | done |
| 5 | A placeholder of the other format's style refused with a hint (`${...}` in INI, `%(ENV_...)s` elsewhere) - it would otherwise be taken as a literal password | done |
| 6 | Errors: every message names a path or a variable and no value; Python's INI errors rephrased | done |
| 7 | PyYAML as a dependency; `uv.lock` | done |
| 8 | Unit tests: each format builds the same layout as code; every rule above, case by case | done |
| 9 | Integration tests: a file in each format, real PostgreSQL, MySQL and MariaDB, logging in through a placeholder password, migrating and connecting | done |
| 10 | Prove the tests can fail | done |
| 11 | Docs and website: a config-files page in both; DIVERGENCE, parity ledger, decisions | done |
| 12 | `make lint`, `make test`, `make test-all`, `make examples` | done - 2,311 passed on four databases (tracing tests included), 85 example tests, website built |

## Judgement calls (also logged in DECISIONS-TO-REVIEW.md)

- A value filled from an environment variable is text in `data-source-properties`.
- `$${` is a literal `${` in YAML, TOML and JSON.
- The other format's placeholder style is refused with a hint.
- Unknown keys are refused, in INI too - a `%(name)s` helper must be an existing key.
- INI's `[DEFAULT]` is not copied into other sections.
- A text field refuses a number from YAML instead of converting it.
- Everything outside `ekbatan.sharding` is ignored.

## Proof

- Nine bugs reinstated one at a time, each turning a test red - including a YAML error quoting
  the secret, which first survived because the test's broken YAML did not make PyYAML quote it.
- Every format against SQLite, PostgreSQL, MySQL and MariaDB: password from an environment
  variable, a driver setting reaching the database, migrating and querying.
- Every example on the docs page loads.
