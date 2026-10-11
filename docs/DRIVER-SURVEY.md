# Python driver survey

Java's `data-source-properties` rules rest on a ledger of every setting of every JDBC driver
Ekbatan accepts (`ekbatan-core/src/test/resources/io/ekbatan/core/config/driver-settings.tsv` in
the Java repo). JDBC is one standard: every driver takes a URL plus settings as text. Python has
no such standard for connecting - DB-API (PEP 249) covers queries, not `connect()` - so every
driver takes its own setting names and value types. This survey builds the Python map before
`data_source_properties` is designed.

Runtime it is checked against: Python 3.14.7, SQLAlchemy 2.0.52, psycopg 3.3.5, PyMySQL 1.2.0.
(PyMySQL's `__version__` says 2.2.8: that is the mysqlclient version it pretends to be, for
libraries that check one; its own version is `VERSION_STRING` and the one in `uv.lock`.)

## Decided so far (2026-10-11)

- Optional `username` and `password` fields, as in Java.
- `data_source_properties` replaces `connect_args`, with Java's rules: no empty values, no lists,
  no nested blocks, `user` and `password` refused in any case, every value treated as a secret.
- Values reach the driver as given - a number stays a number - not turned into text as Java does:
  PyMySQL fails on `connect_timeout="10"` and reads `autocommit="false"` as true.

## Questions

1. Which drivers exist for each database Ekbatan speaks (PostgreSQL, MySQL, MariaDB, SQLite):
   sync or async, and the SQLAlchemy URL name for each.
2. Every setting each driver's `connect()` accepts, with its value type.
3. Settings that take a dictionary, a list, an object or a function - and whether a flat setting
   does the same job. Decides whether "no nested blocks" is safe.
4. Settings that hold a user name or a secret. Decides the URL check and the refused names.
5. What each driver does with a setting it does not know: an error, or silence.
6. Other ways to log in - the AWS Advanced Python Wrapper, IAM tokens, Secrets Manager, Google's
   Cloud SQL connector, Azure tokens, Kerberos, client certificates, MariaDB's PAM - how each
   plugs in, and what it means for `username`, `password` and `data_source_properties`.
7. Whether one driver can replace another for the same database, and what changes when it does.

## Status

| Part | File | State |
|---|---|---|
| PostgreSQL drivers | `driver-survey/postgresql.md` | done |
| MySQL and MariaDB drivers | `driver-survey/mysql-mariadb.md` | done |
| SQLite | `driver-survey/sqlite.md` | done |
| Other ways to log in (PostgreSQL, MySQL, MariaDB) | `driver-survey/logins.md` | done |
| Answers and recommendation | this file | done |

## Answers

Short answers; the evidence - runs, source lines, exact errors - is in the four parts. Most facts
were proved by running, against real PostgreSQL 18.4, MySQL 8.4 and MariaDB 11.4 containers where a
database was needed.

### 1. The drivers

| Database | Drivers | Ekbatan uses |
|---|---|---|
| PostgreSQL | psycopg 3, psycopg2, pg8000, asyncpg (async only) | psycopg 3 |
| MySQL, MariaDB | PyMySQL, mysqlclient, mysql-connector-python, mariadb | PyMySQL |
| SQLite | sqlite3 | sqlite3 |

- A plain `postgresql://` URL picks psycopg2, and a plain `mysql://` picks mysqlclient.
- PyMySQL is 1.2.0; its `__version__` (2.2.8) is the mysqlclient version it imitates.

### 2. How many settings

psycopg 3: 6 of its own plus 50 libpq settings. psycopg2: 5 plus 41 (its libpq is 17, so it lacks
nine libpq 18 settings). pg8000: 13. asyncpg: 23. PyMySQL: 40. mysqlclient: 33. mysql-connector:
64. mariadb: 41. sqlite3: 9. Every one is listed in the parts.

### 3. Settings that only work as a dictionary, list, object or function

| Driver | No flat alternative |
|---|---|
| psycopg 3 | `cursor_factory` (a class) |
| PyMySQL | `ssl` for `capath`/`cipher`; `auth_plugin_map`; `conv`, `cursorclass` |
| sqlite3 | `factory` (a class or function) |
| pg8000 | `startup_params`, `ssl_context`, `source_address` |
| asyncpg | `password` as a function; `prepared_statement_name_func` |
| mysqlclient | `ssl` (every TLS file); `conv`, `cursorclass` |
| mysql-connector | `conn_attrs`, `failover`, `tls_ciphersuites` |
| mariadb | none |

- For Ekbatan's three drivers, "no nested blocks" blocks only PyMySQL's CA folder and cipher list,
  PyMySQL's answers to MariaDB PAM questions other than "Password:", and code hooks (classes and
  functions) that a settings map should not carry anyway. Everyday TLS uses flat names
  (`sslmode`/`sslrootcert` in psycopg, `ssl_ca`/`ssl_cert`/`ssl_key` in PyMySQL).
- For the other drivers it would block real features: pg8000's certificate checks and server
  settings, and every TLS file in mysqlclient.

### 4. Settings that hold a user name or a secret

Refusing `user` and `password` alone, as Java does, is not enough in Python:

| Where | User name or secret besides `user`/`password` |
|---|---|
| psycopg 3 (libpq) | `sslpassword`, `oauth_client_secret`, `scram_client_key`, `scram_server_key` |
| psycopg 3 (libpq) | `conninfo` (a whole connection string), `service`, `passfile` |
| PyMySQL | `passwd`, `ssl_key_password`, `read_default_file`, `read_default_group` |
| other MySQL drivers | `username`, `password1`-`password3`, `failover` |
| asyncpg | `dsn`; a `user` entry in `server_settings` |
| AWS wrapper | `db_user`, `idp_username`, `idp_password`, `monitoring-*` |

- Logins also arrive without being passed in: `PGUSER`, `PGPASSWORD`, `~/.pgpass` and service files
  for libpq; `MYSQL_PWD` and `~/.my.cnf` for mysqlclient and mariadb; the operating-system user name
  when none is given.
- Secrets in printed output: psycopg 3's `conn.info.dsn` and psycopg2's `repr(conn)` print
  `sslpassword` in clear; drivers quote bad values in their errors; the AWS wrapper writes IAM tokens
  to its DEBUG log; PyMySQL sends the password as clear text without TLS when the server asks.

### 5. A setting the driver does not know

Every stable driver refuses an unknown name before connecting, naming the setting but not its value.
Two exceptions: psycopg 3 and psycopg2 silently drop any setting whose value is `None`, even an
unknown one; the mariadb 2.0 preview ignores unknown names. Setting names are case-sensitive in every
Python driver, so Java's "in any case" refusal costs nothing here.

### 6. Other ways to log in

| Method | Plugs in through | Works with |
|---|---|---|
| AWS Advanced Python Wrapper 3.1.0 | its own URL name; flat settings | psycopg; mysql-connector |
| RDS IAM token | a token as the password, fresh each connection | any |
| Secrets Manager (plain boto3) | user and password; a hook once they rotate | any |
| Google Cloud SQL connector | `creator=` only | psycopg, pg8000; PyMySQL |
| Azure Entra ID | a token as the password, fresh each connection | any |
| Kerberos, client certificates | flat settings | psycopg; PyMySQL (certificates) |
| MariaDB PAM, ed25519 | the password | PyMySQL, mariadb |

- A fresh token per connection needs SQLAlchemy's `do_connect` event, and the Cloud SQL connector
  needs `creator=`. **`creator=` makes SQLAlchemy ignore the URL's login, `connect_args` and
  `do_connect`** (ran), so `username`, `password` and `data_source_properties` would vanish without
  a word.
- **Ekbatan's migrator connects without the engine**, always with plain psycopg or PyMySQL. So
  `do_connect` and `creator=` never reach migrations, and AWS wrapper settings make the migrator fail.
- The AWS wrapper does not support PyMySQL or MariaDB.

### 7. Replacing one driver with another

SQLAlchemy keeps the queries working: one round trip passed on all four MySQL drivers against both
MySQL and MariaDB. The settings do not carry over - mysql-connector says `connection_timeout` where
PyMySQL says `connect_timeout` - so the ledger of setting names has to be per driver, not per
database.

### Traps the planned rules would let through

- **Settings that take over the framework's job:** psycopg 3 `autocommit=True` makes rollbacks do
  nothing; MySQL `client_flag` replaces the "found rows" flag SQLAlchemy sets, so an UPDATE that
  changes nothing reports 0 rows. Both should be refused (or, for `client_flag`, merged).
- **Text read as true:** the text `"false"` turns on `autocommit` (PyMySQL), `uri` and
  `check_same_thread` (sqlite3). Values kept as given, as decided, avoids this.
- **`None`:** refusing it, as Java does, also refuses psycopg 3's `prepare_threshold=None` ("never
  prepare").

## Recommendation

1. Keep Ekbatan's three drivers: psycopg 3, PyMySQL and sqlite3. The planned rules fit them.
2. Refused names: **decided 2026-10-11 - Java's rule only.** `data_source_properties` refuses the keys
   that set the login user or password - `user`, `password`, and PyMySQL's old name `passwd` - in any
   letter case, pointing to the `username` and `password` fields. Keys that fetch a login from
   somewhere else (`conninfo`, `service`, `passfile`, `read_default_file`, `read_default_group`) are
   **not** refused. Other secrets with no field of their own (`sslpassword`, `ssl_key_password`) stay
   allowed and are never printed. The rule may be relaxed one day, in both ports together.
3. `autocommit` and `client_flag`: **decided 2026-10-11 - not refused.** They pass to the driver like
   any other setting, even though they change how transactions and row counts behave.
4. Nested blocks: **decided 2026-10-11 - allowed in Python (option B).** Groups and objects pass to the
   driver as given, so PyMySQL's `ssl` group (`capath`, `cipher`) and `auth_plugin_map` work. Java
   refuses groups because Spring Boot splits dotted names into groups; Python's YAML reader keeps
   them whole (ran), so the guard is not needed here. A deliberate difference from Java.
5. Leave fresh tokens (`do_connect`) and `creator=` out of `data_source_properties`. They need code,
   not settings - the same ground as the credentials provider deferred in the Java port - and the
   migrator would have to connect the same way first.

## Later (decided 2026-10-11)

- **Config files.** If ekbatan-py ever reads config files: YAML, TOML, JSON and Python settings files
  keep real types and fit the plan as is (ran). TOML splits unquoted dotted names into groups; a
  quoted name stays whole, and a forgotten quote surfaces as an unknown-setting error from the
  driver. INI files and environment variables give everything as text, so they would need a
  conversion step first.
- **Placeholders like `${DB_PASSWORD}`.** Python's YAML and TOML readers keep them as literal text;
  Spring fills them in for Java. Filling them in is its own piece of work, to be done together with
  config-file support.
