# Ledger: login fields and `data_source_properties` (Track 1)

Bringing Java's `DataSourceConfig` login rules to this port, as decided on 2026-10-11 (see
[DRIVER-SURVEY.md](DRIVER-SURVEY.md) for the survey and every decision). Config files and
`${...}` placeholders are Track 2, later.

## What gets built

| Piece | Java | Here |
|---|---|---|
| Optional login fields | `username`, `password` | `username`, `password` |
| A blank username | refused | refused |
| An empty password | a valid password | a valid password |
| A URL carrying a login or a secret | refused (`JdbcUrlCredentials`) | refused, read through SQLAlchemy's own URL parser |
| Driver settings bag | `dataSourceProperties` | `data_source_properties`, replacing `connect_args` |
| `user` / `password` in the bag, any case | refused | refused, plus PyMySQL's `passwd` |
| Empty value in the bag | refused | refused (`None`) |
| Group or list in the bag | refused | **allowed** (option B) |
| Values | turned into text | kept as given |
| Every bag value | never printed | never printed |
| Which names hold a login or a secret | `driver-settings.tsv` + a test | `tests/driver_settings.tsv` + a test |
| Pool and migrations log in the same way | yes | yes: one URL object, login set on it |

## Tasks

| # | Task | State |
|---|---|---|
| 1 | `DataSourceConfig`: `username`, `password`, `data_source_properties`, their checks, builder setters | done |
| 2 | URL check: refuse a login before `@` and refused query settings; messages say where each goes, never repeat the URL or a value | done |
| 3 | Ledger `tests/driver_settings.tsv`: every setting of psycopg (libpq + its own), PyMySQL and sqlite3, with its kind | done |
| 4 | Tests: the code's refused names equal the ledger's; the ledger covers every setting the installed drivers accept | done |
| 5 | Printing: the password field and every bag value hidden | done |
| 6 | Pool (`engine_for`) and migrator (`open_backend`) take the login from the fields and the bag | done |
| 7 | Unit tests mirroring Java's `DataSourceConfigCredentialsTest` and `JdbcUrlCredentialsTest` | done |
| 8 | Test helpers: split a container's URL into url + username + password; every test moved over | done |
| 9 | Real-database tests on PostgreSQL, MySQL and MariaDB: login through the fields, and bag settings reaching the driver, for the pool and the migrator | done |
| 10 | Prove the new tests can fail (reinstate each bug, watch it go red) | done |
| 11 | Examples: login out of the URL | done |
| 12 | Docs and website: every URL with a login, the connecting/dialect pages, sharding config table | done |
| 13 | `DIVERGENCE.md`, `JAVA-PARITY-LEDGER.md`, `DECISIONS-TO-REVIEW.md`, `SHORTCOMINGS.md` | done |
| 14 | `make lint`, `make test`, `make test-all`, `make examples` | done - 2,229 passed on four databases, 85 example tests, website built |

## Judgement calls (also logged in DECISIONS-TO-REVIEW.md)

- A url SQLAlchemy cannot parse (two hosts, `h1:3306,h2:3306`) is read from its text, not refused.
- A login on a SQLite config is refused: SQLite has none.
- Every `data_source_properties` value prints as `***`; the names are shown.
- `split_login(url)` is public; `schemagen --url` still takes a url with a login and splits it.

## Proof

- Ten bugs reinstated one at a time; each turned its test red (migrator or pool ignoring the bag,
  pool ignoring the login fields, `passwd` allowed, `sslpassword` not refused, bag values or the
  password printed, a url login accepted, a blank username accepted, `None` accepted).
- Real logins through `username` / `password`, and bag settings reaching the driver, proved on
  PostgreSQL, MySQL and MariaDB for the pool and the migrator.
