# MySQL and MariaDB drivers: what `connect()` accepts

Part of the [driver survey](../DRIVER-SURVEY.md). Checked on 2026-10-11 against Python 3.14.7 and
SQLAlchemy 2.0.52. Almost every fact below was run, not just read. "How known" in each table says
which.

## Answers

**1. The drivers.** Through SQLAlchemy 2.0.52, all four are sync only.

| Driver | Version | Built how | SQLAlchemy URL names |
|---|---|---|---|
| PyMySQL | 1.2.0 | pure Python | `mysql+pymysql`, `mariadb+pymysql` |
| mysqlclient (`MySQLdb`) | 2.3.0 | C, needs a client library | `mysql+mysqldb`, `mariadb+mysqldb`, plain `mysql://`, plain `mariadb://` |
| mysql-connector-python | 26.7.0 | C, with a pure Python fallback | `mysql+mysqlconnector`, `mariadb+mysqlconnector` |
| mariadb (Connector/Python) | 1.1.14 | C, needs MariaDB Connector/C | `mariadb+mariadbconnector`, `mysql+mariadbconnector` |

- **Version mix-up:** the brief and `DRIVER-SURVEY.md` say "PyMySQL 2.2.8". No such release exists on
  PyPI. The newest is 1.2.3. ekbatan-py's `uv.lock` pins 1.2.0, so this survey covers 1.2.0.
  Version 1.2.3 accepts the same 40 settings. The only change: `binary_prefix` now does nothing.
- mysql-connector also has an async API (`mysql.connector.aio`), but SQLAlchemy 2.0.52 has no
  dialect for it. mariadb 2.0.0rc2 is a pure Python preview with async support (see the
  [preview notes](#preview-mariadb-200rc2)).
- mysqlclient and mariadb 1.1.14 ship install files (wheels) for Windows only. On the Mac, both
  failed to build because the MySQL/MariaDB client library is missing. So I built and ran them in a
  Linux container with Python 3.14.7. No driver needed the 3.13 fallback.

**2. Every setting.** PyMySQL takes 40 settings, mysqlclient 33, mysql-connector 64 and
mariadb 1.1.14 41. These counts include aliases (`passwd`, `db`, ...) and names a driver lists but
refuses, like `dsn`. I passed each setting to `connect()` on its own, and each driver accepted every
one (tables below). Two are accepted only next to another setting: mysql-connector `option_groups`
needs `option_files`, and mariadb `pool_validation_interval` needs `pool_name`.

**3. Settings that take a dictionary, list, object or function.** THE KEY QUESTION: yes. Some
settings have **no flat replacement** among the connect arguments:

- **mysqlclient `ssl` (dictionary) - DICTIONARY ONLY.** It is the only connect setting that carries
  the CA file, client certificate, client key, CA folder and cipher list. There is no `ssl_ca` name:
  passing one fails (ran). The only flat routes go around the connect arguments: an option file
  (`read_default_file` with `ssl-ca=...`), or the SQLAlchemy URL query (`?ssl_ca=...`, which
  SQLAlchemy folds into the dictionary). `data_source_properties` goes in as connect arguments, so
  it skips that fold.
- **PyMySQL `ssl` (dictionary or `ssl.SSLContext`) - DICTIONARY ONLY for the CA folder (`capath`) and
  the cipher list (`cipher`).** Its other keys have flat names (`ssl_ca`, `ssl_cert`, `ssl_key`,
  `ssl_key_password`, `ssl_verify_cert`, `ssl_verify_identity`). Some things can only be done with an
  `SSLContext` object and have no setting at all, for example the lowest TLS version allowed.
- **mysql-connector `conn_attrs` (dictionary), `failover` (list of dictionaries, and it carries user
  and password), and `tls_ciphersuites` (list - its text form is broken in 26.7.0): ALL THREE ONLY
  as a dictionary or list.**
- **Code hooks, OBJECT/FUNCTION ONLY**, which can never come from a YAML file anyway: PyMySQL `conv`,
  `cursorclass`, `auth_plugin_map`; mysqlclient `conv`, `cursorclass`; mysql-connector
  `converter_class`, `_span`, `_tracer`; mariadb `converter`, `status_callback`, `connectionclass`.
- These have a flat replacement: the mariadb `ssl` dictionary (every key has an `ssl_*` name),
  mysql-connector `client_flags` (a number instead of a list), `tls_versions` (text
  `"[TLSv1.2,TLSv1.3]"`), `option_files` and `option_groups` (text), and `webauthn_callback` (text
  naming a function).
- What "no nested blocks" costs: with PyMySQL (today's driver), only the CA folder and the cipher
  list. With mysqlclient, every TLS file setting. With mysql-connector, connection attributes,
  failover and TLS cipher choice. With mariadb 1.1.14, nothing.

**4. User names and secrets.**

- User name: `user` (all four drivers), `username` (mysql-connector and mariadb), and `failover[].user`
  (mysql-connector). SQLAlchemy's mariadb dialect itself sends `username`, not `user`.
- Secret: `password` (all four drivers), `passwd` (all four), `password1`/`password2`/`password3`
  (mysql-connector; `password1` even overrides `password`), `ssl_key_password` plus the `password`
  key of the `ssl` dictionary (PyMySQL), and `failover[].password` (mysql-connector).
- Settings that make the driver read a login from a file: `read_default_file` and
  `read_default_group` (PyMySQL, mysqlclient), `option_files`, `read_default_file` and
  `option_groups` (mysql-connector), and `default_file` and `default_group` (mariadb).
  mysql-connector's `openid_token_file` and `oci_config_file` point at credential files.
- Logins that arrive without being passed in (ran against a real MariaDB 11.4):
  - The `MYSQL_PWD` environment variable gives the password to mysqlclient and mariadb 1.1.14
    when none is passed. PyMySQL, mysql-connector and mariadb 2.0 ignore it.
  - `~/.my.cnf` is read only when asked for. mysqlclient reads it with `read_default_group="client"`.
    mariadb reads it with `default_group=...` or `default_file=""`. PyMySQL needs
    `read_default_file`, and mysql-connector needs `option_files`.
  - With no user given, PyMySQL sends the OS login name (the `USER`/`LOGNAME` environment variables
    come first). The two C drivers send the OS account name. mysql-connector sends an empty name
    (pure Python) or `root` (C extension).
- So refusing `user` and `password` alone is not enough. Also refuse `username`, `passwd`,
  `password1` to `password3`, `ssl_key_password` and `failover`, and decide about the option-file
  settings.

**5. A setting the driver does not know.** All four stable drivers refuse it before any connection
attempt. The message names the key but never the value:

- PyMySQL: `TypeError: Connection.__init__() got an unexpected keyword argument 'not_a_setting'`
- mysqlclient: `TypeError: connect() got an unexpected keyword argument 'not_a_setting'`
- mysql-connector: `AttributeError: Unsupported argument 'not_a_setting'`
- mariadb 1.1.14: `TypeError: connect() got an unexpected keyword argument 'not_a_setting'`
- mariadb 2.0.0rc2 (preview): ignores it without a word, and connects.

Names are case-sensitive. Python 3.14 adds `Did you mean 'connect_timeout'?` for near misses (not
mysql-connector). Some
known settings are accepted but do nothing: mysql-connector `ssl_cipher` (ran), extra keys in
mysqlclient's `ssl` dictionary (ran), and PyMySQL 1.2.3 `binary_prefix`. Some value checks do print
the value: mysql-connector `conn_attrs`, `tls_versions`, `openid_token_file` and `oci_config_file`,
and SQLAlchemy's true/false parser.

**6. SQLAlchemy URL query versus `connect_args`.**

- `connect_args` are laid over the URL's own arguments, key by key, and they win
  (`sqlalchemy/engine/create.py:633-634`). Ran on all ten URL names: URL `connect_timeout=5` plus
  `connect_args` `connect_timeout=7` gave 7. A dictionary in `connect_args` replaces the URL's whole
  dictionary. It is not merged.
- A URL query value stays text unless the dialect converts it. Each dialect converts its own short
  list (see the [SQLAlchemy section](#sqlalchemy-2052-url-query-and-connect_args)). Query keys also
  beat the URL's own user, password and host (ran).
- **Trap (ran):** every dialect adds a "found rows" flag to `client_flag` (or `client_flags`), so that
  an UPDATE counts the rows it matched. A `client_flag` given in `connect_args` replaces that value
  and drops the flag. A no-change UPDATE then reports 0 rows instead of 1, which breaks row-count
  checks. Ekbatan's migration path merges the same way (`src/ekbatan/_yoyo.py:605-606`).

**7. Replacing PyMySQL.** I ran one SQLAlchemy round trip with each driver against MySQL 8.4.11 and
MariaDB 11.4.13: insert, then read back int, decimal, `DATETIME(6)` and accented text, then a
no-change UPDATE count. All four stable drivers passed on both databases. mariadb 2.0.0rc2 could
not log in to MySQL 8.4.

- mysqlclient is the closest, since PyMySQL copied its names. It needs a compiler and a client
  library. TLS files go only through the `ssl` dictionary. It has no flat `ssl_ca`/`ssl_cert`/`ssl_key`
  names (only `ssl_mode`), no `bind_address`, `program_name`, `max_allowed_packet`, `defer_connect` or `auth_plugin_map`. Types
  are stricter: `None` is refused for text. `compress=False` still turns compression on. It runs two
  SQL statements in one call by default, and it reads `MYSQL_PWD`.
- mysql-connector uses different names (`connection_timeout`, `client_flags`, `allow_local_infile`,
  `option_files`). It has no `ssl_key_password` or `ssl_capath`, and `ssl_cipher` does nothing.
  SQLAlchemy warns about its regressions and forces `buffered=True`. It also runs two statements per
  call.
- mariadb 1.1.14 uses `default_file`/`default_group` and `converter`. It has no `charset`,
  `collation` or `sql_mode` (the connection is always utf8mb4, so use `init_command`). It has full
  flat TLS names, plus CRL files and certificate fingerprints. It reads `MYSQL_PWD`.
- For all drivers, values reach the driver unchanged. The text `"false"` counts as true wherever the
  driver does not check the type. `autocommit="false"` turned autocommit ON on the server for
  PyMySQL, mysqlclient, mariadb and pure-Python mysql-connector. mysql-connector's C extension
  refuses the text. Numbers given as text fail in PyMySQL, mysqlclient and mariadb 1.1.14.

## How this was checked

- On the Mac (scratch virtualenv, Python 3.14.7): PyMySQL 1.2.0 (1.2.3 compared by source diff),
  mysql-connector-python 26.7.0 (C extension and `use_pure=True`), mariadb 2.0.0rc2.
- In a Linux container (`python:3.14.7-slim-trixie`, built from source against Debian's MariaDB
  Connector/C 3.4.9): mysqlclient 2.3.0 and mariadb 1.1.14.
- Mac build failures, as printed:
  - mysqlclient: `Exception: Can not find valid pkg-config name. Specify MYSQLCLIENT_CFLAGS and MYSQLCLIENT_LDFLAGS env vars manually`
  - mariadb 1.1.14: `OSError: mariadb_config not found.`
- Each setting was passed alone to `connect()` against a closed port (127.0.0.1 port 1). For
  PyMySQL I used `defer_connect=True`. "Accepted" means the driver got past its argument check.
- Login sources, `autocommit` text values, multi-statement calls, the found-rows trap and the round
  trips ran against Docker `mariadb:11.4` (11.4.13) and `mysql:8.4` (8.4.11), using random passwords
  that were never printed. All containers were removed afterwards.
- Source citations point into the installed package (PyMySQL, mysql-connector, SQLAlchemy) or the
  sdist (mysqlclient `src/MySQLdb/...`, mariadb `mariadb/...`).

## PyMySQL 1.2.0

- Pure Python and sync only. One wheel (`py3-none-any`) works everywhere.
- SQLAlchemy names: `mysql+pymysql` and `mariadb+pymysql` (ran: `MySQLDialect_pymysql`,
  `MariaDBDialect_pymysql`).
- **How it reads settings.** `pymysql.connect` is the `Connection` class
  (`pymysql/__init__.py:136`). It takes 40 named, keyword-only settings
  (`pymysql/connections.py:175-218`), with no `**kwargs` and no positional settings. A positional
  host fails with `TypeError: Connection.__init__() takes 1 positional argument but 2 were given`.
- **Option file.** It reads one only when `read_default_file` or `read_default_group` is given
  (`connections.py:237-272`). A group alone means `/etc/my.cnf` (Windows: `c:\my.ini`). From the
  file's group it reads `user`, `password`, `host`, `database`, `socket`, `port`, `bind-address`,
  `default-character-set` and `ssl-ca/capath/cert/key/password/cipher`. A passed value wins, but
  only when it is not empty (`connections.py:250-256`). It never reads `~/.my.cnf` unless that file
  is named (ran).
- **Environment.** No variable is read for the login itself (ran: `MYSQL_PWD` ignored). The default
  user is `getpass.getuser()`, taken at import (`connections.py:38-47`), and that looks at the
  `LOGNAME`, `USER`, `LNAME` and `USERNAME` variables first. Ran: with `USER=envuser` the default
  became `envuser`. With no user, the server saw the OS login `mike`.
- **TLS when nothing is set.** Since 1.2.0, PyMySQL tries TLS if the server offers it, without
  checking the certificate (`connections.py:298-303`). Ran: `ssl=True`, `_ssl_required=False`.
- **Unknown setting:**
  `TypeError: Connection.__init__() got an unexpected keyword argument 'not_a_setting'`. It is the
  same with `defer_connect=True`, so it fails before any network use.
- **Closed port:**
  `pymysql.err.OperationalError: (2003, "Can't connect to MySQL server on '127.0.0.1' ([Errno 61] Connection refused)")`.

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `user` | str | scalar | - | yes | - | ran |
| `password` | str or bytes | scalar | - | - | yes | ran |
| `passwd` | str | scalar | alias of `password` | - | yes | ran |
| `host` | str | scalar | - | - | - | ran |
| `port` | int only | scalar | - | - | - | ran |
| `database` | str | scalar | - | - | - | ran |
| `db` | str | scalar | alias of `database` | - | - | ran |
| `unix_socket` | str (path) | scalar | - | - | - | ran |
| `bind_address` | str | scalar | - | - | - | ran |
| `charset` | str | scalar | - | - | - | ran |
| `collation` | str | scalar | - | - | - | ran |
| `sql_mode` | str | scalar | - | - | - | ran |
| `init_command` | str (SQL) | scalar | - | - | - | ran |
| `connect_timeout` | int/float, 1 to 31536000 | scalar | - | - | - | ran |
| `read_timeout` | number above 0 | scalar | - | - | - | ran |
| `write_timeout` | number above 0 | scalar | - | - | - | ran |
| `autocommit` | bool or None | scalar | - | - | - | ran |
| `local_infile` | bool | scalar | - | - | - | ran |
| `max_allowed_packet` | int | scalar | - | - | - | ran |
| `client_flag` | int (bit flags) | scalar | - | - | - | ran |
| `use_unicode` | bool | scalar | - | - | - | ran |
| `binary_prefix` | bool | scalar | - | - | - | ran |
| `program_name` | str | scalar | - | - | - | ran |
| `defer_connect` | bool | scalar | - | - | - | ran |
| `read_default_file` | str (path) | scalar | - | via file | via file | ran |
| `read_default_group` | str | scalar | - | via file | via file | ran |
| `server_public_key` | str/bytes (PEM) | scalar | - | - | - (public) | ran |
| `ssl` | dict or `SSLContext` | dict / object | `ssl_*`, not capath/cipher | - | `password` key | ran |
| `ssl_ca` | str (path) | scalar | - | - | - | ran |
| `ssl_cert` | str (path) | scalar | - | - | - | ran |
| `ssl_key` | str (path) | scalar | - | - | - | ran |
| `ssl_key_password` | str | scalar | - | - | yes | ran |
| `ssl_verify_cert` | bool or text | scalar | - | - | - | ran |
| `ssl_verify_identity` | bool | scalar | - | - | - | ran |
| `ssl_disabled` | bool | scalar | - | - | - | ran |
| `conv` | dict: type to function | dict | **none** | - | - | ran |
| `cursorclass` | class | object | **none** | - | - | ran |
| `auth_plugin_map` | dict: name to class | dict | **none** | - | - | ran |
| `compress` | must be false | scalar | - | - | - | ran |
| `named_pipe` | must be false | scalar | - | - | - | ran |

- **`ssl` dictionary keys** (`connections.py:381-419`): `ca`, `capath`, `cert`, `key`, `password`,
  `cipher`, `check_hostname`, `verify_mode`. Their flat names, in order: `ssl_ca`, none, `ssl_cert`,
  `ssl_key`, `ssl_key_password`, none, `ssl_verify_identity`, `ssl_verify_cert`. If any of `ssl_ca`,
  `ssl_cert`, `ssl_key`, `ssl_verify_cert` or `ssl_verify_identity` is given, the `ssl` value is
  thrown away and rebuilt from the flat names (`connections.py:276-290`). The 1.2.0 docstring calls
  the dictionary deprecated, but no warning is raised (ran).
- **`capath` and `cipher`** can only come from the `ssl` dictionary, an option file (`ssl-capath`,
  `ssl-cipher`) or the SQLAlchemy URL query (`ssl_capath`, `ssl_cipher`). They cannot come from flat
  connect arguments.
- **An `SSLContext` object** is used as-is (`connections.py:382-383`). It is the only way to set,
  for example, the lowest TLS version.
- **`ssl_verify_cert`** also takes the text `none`/`0`/`false`/`no`, `optional`, or
  `required`/`1`/`true`/`yes` (`connections.py:400-410`). Ran: `"optional"` gave optional checking.
- **`conv`, `cursorclass`, `auth_plugin_map`** are code hooks. SQLAlchemy needs the default
  converters and cursor (`sqlalchemy/dialects/mysql/mysqldb.py:224-225`: "Rich values
  'cursorclass' and 'conv' are not supported via query string").
- **`auth_plugin_map`** is needed only for a login plugin PyMySQL does not handle itself. Built in:
  `mysql_native_password`, `caching_sha2_password`, `sha256_password`, `client_ed25519`,
  `mysql_old_password`, `mysql_clear_password`, and `dialog`, the last only for a `"Password: "`
  prompt (`connections.py:1033-1107`).
- **Types (ran):**
  - `port="3306"` gives `ValueError: port should be of type int`.
  - `connect_timeout="10"` gives `TypeError: '<' not supported between instances of 'int' and 'str'`.
  - `read_timeout="5"` gives a `TypeError`.
  - `client_flag="2"` gives `TypeError: unsupported operand type(s) for |=: 'str' and 'int'`.
  - Unchecked booleans keep the text: `autocommit="false"` turned autocommit ON on the server, and
    `local_infile="false"` and `ssl_disabled="false"` both count as true.
  - An unknown `charset` gives `AttributeError: 'NoneType' object has no attribute 'encoding'`.
- **`compress` / `named_pipe`:** any true value gives
  `NotImplementedError: compress and named_pipe arguments are not supported`.
- **`passwd` / `db`** raise a `DeprecationWarning` (ran). `username` is not a setting (ran:
  `TypeError`).
- **Sent to the server:** `program_name` and the built-in connection attributes. The server shows
  them in `performance_schema.session_connect_attrs`.
- **Multi-statements:** two statements in one call are refused (ran: error 1064).

## mysqlclient 2.3.0 (`MySQLdb`)

- C extension, sync only. Released 2026-09-14. Ran in Linux with MariaDB Connector/C 3.4.9.
  Oracle's own `libmysqlclient` changes a few things, which I read in the source but did not run:
  `ssl_mode` becomes native, and `local_infile_dir` works (`src/MySQLdb/_mysql.c:552-564`,
  `648-689`).
- SQLAlchemy names: `mysql+mysqldb`, `mariadb+mysqldb`, plus plain `mysql://` and `mariadb://`,
  which both default to this driver (ran).
- **How it reads settings.** `MySQLdb.connect(*args, **kwargs)` (`src/MySQLdb/__init__.py:118-125`)
  goes to `Connection.__init__`. That handles 12 names in Python
  (`src/MySQLdb/connections.py:177-216`) and passes the rest to C, which takes a fixed list of 23
  names (`_mysql.c:500-509`, value types at `_mysql.c:529`). Positional settings are allowed (host,
  user, password, database, port, ...).
- **Option files.** Read only when `read_default_file` or `read_default_group` is given
  (`_mysql.c:628-631`). The C library reads them. A group alone also reads `~/.my.cnf`. Ran: it
  logged in with the user and password stored there. With neither setting, `~/.my.cnf` was ignored
  (ran).
- **Environment.** With no password passed, `MYSQL_PWD` was used (ran). With no user, the OS account
  was sent (ran: `root` in the container, even with `USER`/`LOGNAME` set to another name).
  `MYSQL_HOST` was ignored (ran).
- **Unknown setting:** `TypeError: connect() got an unexpected keyword argument 'not_a_setting'`.
- **Closed port:** `MySQLdb.OperationalError: (2002, "Can't connect to server on '127.0.0.1' (115)")`.

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `host` | str, not None | scalar | - | - | - | ran |
| `user` | str, not None | scalar | - | yes | - | ran |
| `password` | str, not None | scalar | - | - | yes | ran |
| `passwd` | str | scalar | alias of `password` | - | yes | ran |
| `database` | str | scalar | - | - | - | ran |
| `db` | str | scalar | alias of `database` | - | - | ran |
| `port` | int | scalar | - | - | - | ran |
| `unix_socket` | str (path) | scalar | - | - | - | ran |
| `named_pipe` | int (Windows only) | scalar | - | - | - | ran |
| `connect_timeout` | int | scalar | - | - | - | ran |
| `read_timeout` | int | scalar | - | - | - | ran |
| `write_timeout` | int | scalar | - | - | - | ran |
| `compress` | any value turns it on | scalar | - | - | - | ran |
| `init_command` | str (SQL) | scalar | - | - | - | ran |
| `charset` | str | scalar | - | - | - | ran |
| `collation` | str | scalar | - | - | - | ran |
| `sql_mode` | str | scalar | - | - | - | ran |
| `use_unicode` | bool | scalar | - | - | - | ran |
| `autocommit` | bool or None | scalar | - | - | - | ran |
| `client_flag` | int (bit flags) | scalar | - | - | - | ran |
| `multi_statements` | bool, default True | scalar | - | - | - | ran |
| `local_infile` | int/bool | scalar | - | - | - | ran |
| `local_infile_dir` | str (path) | scalar | - | - | - | ran |
| `auth_plugin` | str | scalar | - | - | - | ran |
| `server_public_key_path` | str (path) | scalar | - | - | - | ran |
| `read_default_file` | str (path) | scalar | - | via file | via file | ran |
| `read_default_group` | str | scalar | - | via file | via file | ran |
| `ssl` | dict, or bool | dict | **none in `connect()`** | - | - | ran |
| `ssl_mode` | str, 5 fixed names | scalar | - | - | - | ran |
| `binary_prefix` | bool | scalar | - | - | - | ran |
| `executemany_fallback` | `"loop"` or `"multi"` | scalar | - | - | - | ran |
| `conv` | dict: type to function | dict | **none** | - | - | ran |
| `cursorclass` | class | object | **none** | - | - | ran |

- **`ssl` - DICTIONARY ONLY.**
  - The C code reads only the keys `ca`, `capath`, `cert`, `key` and `cipher` (`_mysql.c:572-579`).
    Any other key is ignored: ran with `{'check_hostname': False}`, which was accepted and did
    nothing.
  - `ssl=True` means `ssl_mode=REQUIRED`. A false value that is not a dictionary means `DISABLED`
    (`_mysql.c:580-588`).
  - There is no flat `ssl_ca`, `ssl_cert`, `ssl_key` or `ssl_key_password`. Ran: each one gave
    `TypeError: ... unexpected keyword argument`.
  - Flat routes exist only outside the connect arguments: an option file (`ssl-ca=...` under
    `[client]`), or the SQLAlchemy URL query, which builds this dictionary
    (`sqlalchemy/dialects/mysql/mysqldb.py:227-242`).
- **`ssl_mode`** must be exactly `DISABLED`, `PREFERRED`, `REQUIRED`, `VERIFY_CA` or
  `VERIFY_IDENTITY` (`_mysql.c:470-479`). Ran: `"required"` gave
  `MySQLdb.NotSupportedError: Unknown ssl_mode specification`. With MariaDB Connector/C it is
  imitated with two other options (`_mysql.c:652-671`).
- **Types (ran):**
  - A number given as text gives `TypeError: 'str' object cannot be interpreted as an integer`.
  - `None` for text gives `TypeError: connect() argument 1 must be str, not None` (argument 2 is
    `user`, argument 3 is `password`).
  - `client_flag="2"` gives a `TypeError` (`|=`).
  - `conv="x"` gives `AttributeError: 'str' object has no attribute 'items'`.
  - `executemany_fallback="bad"` gives
    `ValueError: executemany_fallback must be either 'loop' or 'multi'`.
- **`compress`:** any value, even `False`, turns compression on. Only leaving it out keeps it off
  (`_mysql.c:620-623`).
- **`local_infile_dir`** gives `MySQLdb.NotSupportedError: local_infile_dir is not supported` with
  MariaDB Connector/C (ran).
- **`autocommit`** is applied as `bool(value)`, so `"false"` turned autocommit ON on the server
  (ran; `connections.py:250-252`, `284-286`).
- **`multi_statements`** is on by default (`connections.py:209-212`). Ran: two statements in one
  call ran. With `multi_statements=False` they were refused.
- `username` is not a setting (ran: `TypeError`).

## mysql-connector-python 26.7.0

- Released 2026-07-29 (Oracle moved from 9.x to 26.x numbering). Wheels include macOS arm64 for
  Python 3.14 with the C extension, plus a pure Python wheel. Ran both modes on the Mac.
- Sync. The driver's async API (`mysql.connector.aio`) has no SQLAlchemy 2.0.52 dialect. The only
  async MySQL dialects there are `aiomysql` and `asyncmy`.
- SQLAlchemy names: `mysql+mysqlconnector` and `mariadb+mysqlconnector` (ran; the second uses
  `MariaDBDialect_mysqlconnector`, `sqlalchemy/dialects/mysql/mysqlconnector.py:294-303`).
  SQLAlchemy warns that this driver "is subject to frequent, major regressions"
  (`mysqlconnector.py:24-29`).
- **How it reads settings.** `connect(*args, **kwargs)` (`mysql/connector/pooling.py:202-323`) first
  handles these itself:
  - `dns_srv` (243-291)
  - `read_default_file`, `option_files` (294-300)
  - `failover` (303-304)
  - `pool_*` (307-312)
  - `use_pure` (315-323)

  It then builds a connection whose `config()` (`mysql/connector/abstracts.py:559-887`) checks every
  remaining key against `DEFAULT_CONFIGURATION`, which has 54 names (`mysql/connector/constants.py:54-109`).
  Aliases are mapped first: `db`, `username`, `passwd`, `connect_timeout`, `read_default_file`
  (`abstracts.py:669-683`).
- **Option files.** Read only with `option_files` or its alias `read_default_file`. The default groups
  are `[client]` and `[connector_python]`, and `option_groups` picks others
  (`optionfiles.py:57-64`).
  - Values are parsed as Python literals, so a file can hold a dictionary or list. Ran: `conn_attrs`
    and `tls_versions` read from a file. The password stays text (`optionfiles.py:109-115`).
  - An unknown name is skipped in `[client]`. In `[connector_python]` it gives
    `AttributeError: Unsupported argument 'not_an_option'` (ran).
  - Passed values win (ran).
- **Environment.**
  - `MYSQL_PWD` was ignored in both modes (ran).
  - The Kerberos plugin reads `KRB5CCNAME` (`plugins/authentication_kerberos_client.py:203-210`).
  - The OCI plugin reads the OCI SDK's default config file.
  - With no user, pure Python sends an empty name, and the C extension sent `root` while I ran as a
    normal user (ran; cause not traced).
  - For the OCI plugin, and for Kerberos on Windows, the OS user name fills in for a missing user
    (`connection.py:281-293`).
- **Unknown setting:** `AttributeError: Unsupported argument 'not_a_setting'` in both modes.
- **Closed port:**
  - C extension: `mysql.connector.errors.DatabaseError: 2003 (HY000): Can't connect to MySQL server on '127.0.0.1:1' (61)`
  - Pure Python: `mysql.connector.errors.InterfaceError: 2003: Can't connect to MySQL server on '127.0.0.1:1' (Errno 61: Connection refused)`

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `user` | str | scalar | - | yes | - | ran |
| `username` | str | scalar | alias of `user` | yes | - | ran |
| `password` | str | scalar | - | - | yes | ran |
| `passwd` | str | scalar | alias of `password` | - | yes | ran |
| `password1` | str | scalar | overrides `password` | - | yes | ran |
| `password2` | str | scalar | - | - | yes | ran |
| `password3` | str | scalar | - | - | yes | ran |
| `host` | str | scalar | - | - | - | ran |
| `port` | int or digits text | scalar | - | - | - | ran |
| `unix_socket` | str (path) | scalar | - | - | - | ran |
| `database` | str | scalar | - | - | - | ran |
| `db` | str | scalar | alias of `database` | - | - | ran |
| `force_ipv6` | bool | scalar | - | - | - | ran |
| `dns_srv` | bool only | scalar | - | - | - | ran |
| `failover` | list of dicts | list | `dns_srv` (DNS) | inside | inside | ran |
| `connection_timeout` | int | scalar | - | - | - | ran |
| `connect_timeout` | int | scalar | alias | - | - | ran |
| `read_timeout` | int, 0 or more | scalar | - | - | - | ran |
| `write_timeout` | int, 0 or more | scalar | - | - | - | ran |
| `charset` | str | scalar | - | - | - | ran |
| `collation` | str | scalar | - | - | - | ran |
| `autocommit` | bool | scalar | - | - | - | ran |
| `time_zone` | str | scalar | - | - | - | ran |
| `sql_mode` | str | scalar | - | - | - | ran |
| `init_command` | str (SQL) | scalar | - | - | - | ran |
| `use_unicode` | bool | scalar | - | - | - | ran |
| `get_warnings` | bool only | scalar | - | - | - | ran |
| `raise_on_warnings` | bool only | scalar | - | - | - | ran |
| `buffered` | bool | scalar | - | - | - | ran |
| `raw` | bool | scalar | - | - | - | ran |
| `consume_results` | bool | scalar | - | - | - | ran |
| `compress` | bool | scalar | - | - | - | ran |
| `client_flags` | int or list of int | scalar / list | int | - | - | ran |
| `converter_class` | class | object | **none** | - | - | ran |
| `converter_str_fallback` | bool | scalar | - | - | - | ran |
| `allow_local_infile` | bool | scalar | - | - | - | ran |
| `allow_local_infile_in_path` | str (folder) | scalar | - | - | - | ran |
| `use_pure` | bool | scalar | - | - | - | ran |
| `ssl_ca` | str (path) | scalar | - | - | - | ran |
| `ssl_cert` | str (path) | scalar | - | - | - | ran |
| `ssl_key` | str (path) | scalar | - | - | - | ran |
| `ssl_verify_cert` | bool | scalar | - | - | - | ran |
| `ssl_verify_identity` | bool | scalar | - | - | - | ran |
| `ssl_cipher` | str | scalar | - (does nothing) | - | - | ran |
| `ssl_disabled` | bool | scalar | - | - | - | ran |
| `tls_versions` | list, or `"[...]"` text | list | `"[TLSv1.2]"` text | - | - | ran |
| `tls_ciphersuites` | list of str | list | **none** (text broken) | - | - | ran |
| `auth_plugin` | str | scalar | - | - | - | ran |
| `kerberos_auth_mode` | `"SSPI"` or `"GSSAPI"` | scalar | - | - | - | ran |
| `krb_service_principal` | str `a/b@REALM` | scalar | - | - | - | ran |
| `oci_config_file` | str (path) | scalar | - | - | via file | ran |
| `oci_config_profile` | str | scalar | - | - | - | ran |
| `webauthn_callback` | function or dotted text | function | dotted text | - | - | ran |
| `openid_token_file` | str (path) | scalar | - | - | via file | ran |
| `conn_attrs` | dict of str to str | dict | **none** | - | - | ran |
| `option_files` | str or list of str | scalar / list | str | via file | via file | ran |
| `read_default_file` | str or list | scalar / list | alias of `option_files` | via file | via file | ran |
| `option_groups` | str or list of str | scalar / list | str | - | - | ran |
| `pool_name` | str | scalar | - | - | - | ran |
| `pool_size` | int | scalar | - | - | - | ran |
| `pool_reset_session` | bool | scalar | - | - | - | ran |
| `dsn` | refused | scalar | - | - | - | ran |
| `_span` | tracing object | object | **none** | - | - | ran |
| `_tracer` | tracing object | object | **none** | - | - | ran |

- **`conn_attrs` - DICTIONARY ONLY** (`abstracts.py:783-819`). Names are text up to 32 characters and
  may not start with `_`. Values are text up to 1024 characters. A string value gives
  `InterfaceError: conn_attrs must be of type dict` (ran). Error messages print the value (ran:
  `Attribute 'app' value: '5' must be a string type`). The server shows these attributes to anyone
  who can read `performance_schema.session_connect_attrs`.
- **`failover` - LIST ONLY, and it carries a login.**
  - Each entry may hold only `user`, `password`, `host`, `port`, `unix_socket`, `database`,
    `pool_name`, `pool_size` and `priority` (`pooling.py:129-141`).
  - Ran: it connected with the user and password given only inside `failover`.
  - A bad key gives `ValueError: Unsupported connection argument  in failover: charset` (the double
    space is the driver's).
  - The nearest flat route is `dns_srv=True`, which takes the hosts from DNS and needs `dnspython`.
- **`tls_ciphersuites` - LIST ONLY in practice.** The `"[...]"` text form is broken in 26.7.0: the loop
  takes the whole string, not each item (`abstracts.py:348`). Ran: `"[TLS_AES_256_GCM_SHA384]"` gave
  `AttributeError: The value '[TLS_AES_256_GCM_SHA384]' in tls_ciphersuites is not a valid cipher suite`.
  The list form worked.
- **`tls_versions`** takes a list, or text in brackets. Ran: `"[TLSv1.2]"` worked, and bare `"TLSv1.2"`
  gave `AttributeError: tls_versions must be a list, found: 'TLSv1.2'`.
- **`ssl_cipher` is accepted but does nothing.** Neither the pure Python path nor the C path passes
  it on (`mysql/connector/authentication.py:130-137`, `connection_cext.py:287-348`). Ran with
  `ssl_cipher="NOT-A-CIPHER"`: TLS still came up (`Ssl_cipher` was `TLS_AES_128_GCM_SHA256` in C mode
  and `TLS_AES_256_GCM_SHA384` in pure mode). There is no `ssl_key_password`, `ssl_capath`,
  `ssl_crl` or `ssl_mode` (ran: `Unsupported argument`). `ssl_key` and `ssl_cert` must come together.
- **`client_flags`:** a number replaces all flags, and only if it is above 0. A list adds positive
  flags and removes negative ones (`abstracts.py:1081-1090`). Text gives
  `ProgrammingError: client_flags setter expect integer (>0) or set` (ran).
- **`converter_class`** is a class only. Ran: the dotted text path gave
  `AttributeError: Converter class should be a subclass of conversion.MySQLConverterBase`.
- **`webauthn_callback`** takes a function or a dotted name, which the driver imports
  (`abstracts.py:893-925`). Ran: `"os.path.exists"` was accepted, and `"os.getcwd"` gave
  `ProgrammingError: 'webauth_callback' requires 1 positional argument, but the callback provided has 0`.
  A dotted name lets a setting make the driver import any Python object.
- **`password1`** wins over `password` when they differ (`connection.py:295-296`). Ran: a wrong
  `password` plus the right `password1` connected.
- **Pool settings** turn on the driver's own pool, which then sits inside SQLAlchemy's pool
  (`pooling.py:307-312`). SQLAlchemy even converts `pool_size` and `pool_reset_session` from the URL
  (`mysqlconnector.py:187-188`).
- **`dsn`** gives `NotSupportedError: Data source name is not supported`. `_span` and `_tracer` are
  internal tracing hooks, removed first (`abstracts.py:576-577`).
- **Types (ran):**
  - `port="1"` works because the driver calls `int()`. `port="abc"` gives
    `InterfaceError: TCP/IP port number should be an integer`.
  - `connection_timeout="2"` gives `TypeError: argument 4 must be int, not str` (C) or
    `InterfaceError: 'str' object cannot be interpreted as an integer or float` (pure).
  - `read_timeout="5"` gives `InterfaceError: Option read_timeout must be a positive integer`.
  - `get_warnings` and `raise_on_warnings` give `ValueError: Expected a boolean type` for text.
  - `dns_srv="true"` gives `InterfaceError: The value of 'dns-srv' must be a boolean`.
  - Other booleans keep the text: `autocommit="false"` turned autocommit ON in pure mode, and in C
    mode gave `ValueError: mode must be boolean`.
- **Values printed in check errors (ran):** `tls_versions`, `tls_ciphersuites`, `conn_attrs`,
  `openid_token_file` (`The path '/nope' provided via 'openid_token_file' does not exist`) and
  `oci_config_file` (`Invalid oci-config-file: /x`).
- **Multi-statements:** two statements in one call ran in both modes.

## mariadb 1.1.14 (MariaDB Connector/Python)

- Latest stable release (2025-10-07). A 2.0.0rc2 preview exists; see below. It is a C extension
  over MariaDB Connector/C, version 3.3.1 or newer to build (`mariadb_posix.py:49`). Install files
  exist only for Windows. Ran in Linux with Connector/C 3.4.9. Sync only.
- SQLAlchemy names: `mariadb+mariadbconnector` and `mysql+mariadbconnector` (ran). SQLAlchemy notes
  that plain `mariadb://` still means mysqlclient (`mariadbconnector.py:24-25`).
- **How it reads settings.** `mariadb.connect(*args, connectionclass=..., **kwargs)`
  (`mariadb/__init__.py:43-98`) works like this:
  - `pool_name` sends the call to the driver's own pool.
  - `Connection.__init__` (`mariadb/connections.py:62-85`) takes `autocommit`, `reconnect` and
    `converter`, and turns an `ssl` dictionary into flat `ssl_*` names.
  - The rest goes to C, which takes a fixed list of 36 names (`mariadb/mariadb_connection.c:32-46`,
    value types at line 328).
  - The first positional argument is `dsn`, which is refused (ran).
- **Option files.** Read via `default_file` and `default_group` (`mariadb_connection.c:412-422`).
  `default_group=...` or `default_file=""` also reads `~/.my.cnf`. Ran: it logged in with the stored
  user and password. With neither setting, the file was ignored (ran).
- **Environment.** `MYSQL_PWD` gives the password when none is passed (ran). With no user, the OS
  account is sent (ran: `root` in the container). `MYSQL_HOST` was ignored (ran).
- **Unknown setting:** `TypeError: connect() got an unexpected keyword argument 'not_a_setting'`.
- **Closed port:** `mariadb.OperationalError: Can't connect to server on '127.0.0.1' (115)`.

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `user` | str or None | scalar | - | yes | - | ran |
| `username` | str or None | scalar | alias; wins over `user` | yes | - | ran |
| `password` | str or None | scalar | - | - | yes | ran |
| `passwd` | str or None | scalar | alias of `password` | - | yes | ran |
| `host` | str (comma list ok) | scalar | - | - | - | ran |
| `port` | int | scalar | - | - | - | ran |
| `unix_socket` | str (path) | scalar | - | - | - | ran |
| `database` | str | scalar | - | - | - | ran |
| `db` | str | scalar | alias of `database` | - | - | ran |
| `dsn` | refused | scalar | - | - | - | ran |
| `connect_timeout` | int | scalar | - | - | - | ran |
| `read_timeout` | int | scalar | - | - | - | ran |
| `write_timeout` | int | scalar | - | - | - | ran |
| `init_command` | str (SQL) | scalar | - | - | - | ran |
| `autocommit` | bool | scalar | - | - | - | ran |
| `reconnect` | bool | scalar | - | - | - | ran |
| `local_infile` | bool (0-255) | scalar | - | - | - | ran |
| `compress` | bool (0-255) | scalar | - | - | - | ran |
| `client_flag` | int (bit flags) | scalar | - | - | - | ran |
| `plugin_dir` | str (path) | scalar | - | - | - | ran |
| `default_file` | str (path), `""` | scalar | - | via file | via file | ran |
| `default_group` | str | scalar | - | via file | via file | ran |
| `ssl` | bool, or dict | scalar / dict | `ssl_*` names | - | - | ran |
| `ssl_ca` | str (path) | scalar | - | - | - | ran |
| `ssl_cert` | str (path) | scalar | - | - | - | ran |
| `ssl_key` | str (path) | scalar | - | - | - | ran |
| `ssl_capath` | str (folder) | scalar | - | - | - | ran |
| `ssl_cipher` | str | scalar | - | - | - | ran |
| `ssl_crl` | str (path) | scalar | - | - | - | ran |
| `ssl_crlpath` | str (folder) | scalar | - | - | - | ran |
| `ssl_verify_cert` | int/bool | scalar | - | - | - | ran |
| `tls_version` | str, comma list | scalar | - | - | - | ran |
| `tls_fp` | str (fingerprint) | scalar | - | - | - | ran |
| `tls_fp_list` | str (path) | scalar | - | - | - | ran |
| `converter` | dict: type to function | dict | **none** | - | - | ran |
| `status_callback` | function | function | **none** | - | - | ran |
| `connectionclass` | class | object | **none** | - | - | ran |
| `pool_name` | str | scalar | - | - | - | ran |
| `pool_size` | int | scalar | - | - | - | ran |
| `pool_reset_connection` | bool | scalar | - | - | - | ran |
| `pool_validation_interval` | int, pool only | scalar | - | - | - | ran |

- **The `ssl` dictionary** is a compatibility feature. Its keys `ca`, `cert`, `capath`, `key` and
  `cipher` become `ssl_ca` and so on, plus `ssl=True` (`connections.py:76-83`). Any value that is not
  a bool takes this path. Ran: `ssl="yes"` was treated as a dictionary and turned into `ssl=True`,
  and `ssl=1` gave `TypeError: argument of type 'int' is not a container or iterable`.
- **`converter`, `status_callback`, `connectionclass`** are code hooks. `status_callback` takes any
  object without a check (ran: text was accepted), so a mistake would only show up when the driver
  calls it.
- **`username` wins over `user`** when both are given (ran). SQLAlchemy's mariadb dialect sends
  `username` (`mariadbconnector.py:198`).
- **Not settings (ran: `TypeError`):** `charset`, `collation`, `sql_mode`, `ssl_key_password`,
  `ssl_mode`. The connection is always utf8mb4 (`mariadb_connection.c:48-49`, `381`). A common
  `?charset=utf8mb4` in the URL therefore breaks this driver.
- **Types (ran):**
  - A number or flag given as text gives `TypeError: 'str' object cannot be interpreted as an integer`.
    This covers `port`, `connect_timeout`, `local_infile`, `compress`, `ssl_verify_cert` and
    `client_flag`.
  - `local_infile=300` gives `OverflowError: unsigned byte integer is greater than maximum`.
  - Text settings accept `None`.
  - `autocommit="false"` turned autocommit ON on the server.
- **`dsn`** gives `mariadb.ProgrammingError: dsn keyword is not supported`.
  `pool_validation_interval` is accepted only next to `pool_name`. Without it, it is an unknown
  setting (ran).
- **`host`** may be a comma-separated list of hosts to try in turn (Connector/C 3.3 or newer,
  `connections.py:66-74`). `tls_fp` and `tls_fp_list` are missing from the docstring but accepted
  (ran).
- **Multi-statements:** two statements in one call are refused (ran).

### Preview: mariadb 2.0.0rc2

A release candidate (2026-03-04), shipped as one pure Python wheel. It is sync plus async
(`asyncConnect`), and it ships its own SQLAlchemy async module (`mariadb/sqlalchemy_mariadb_async.py`).
Ran on the Mac. It picked the pure Python implementation, and its version string is
`2.0.0rc2+native`.

- Settings are mapped in `mariadb/impl/configuration.py:113-240`:
  - Login and place: `host`, `port`, `user`/`username`, `password`, `database`/`db`, `unix_socket`.
  - Timeouts: `socket_timeout` (with `read_timeout` and `write_timeout` as aliases),
    `connect_timeout`, `query_timeout`.
  - TLS: `ssl`/`use_ssl`, `ssl_key`, `ssl_ca`, `ssl_cert`, `ssl_crl`, `ssl_cipher`, `ssl_capath`,
    `ssl_crlpath`, `ssl_verify_cert`, `tls_version`.
  - Behavior: `autocommit`, `read_only`, `compress`, `binary`, `local_infile`,
    `max_allowed_packet`, `character_encoding`/`charset`, `init_command`.
  - Results and caching: `named_tuple`, `dictionary`, `native_object`, `cache_prep_stmts`,
    `prep_stmt_cache_size`, `pipeline`, `client_flag`.
  - Code hooks: `converter` (dictionary) and `connectionclass` (class).
  - It also accepts an `ssl` dictionary, a `pool_name` (driver pool), and a URI as the first
    positional argument.
- **Unknown settings are silently ignored.** They are stored in `non_mapped_options`
  (`configuration.py:236-238`), which nothing ever reads. Ran: `not_a_setting=1`, `dsn` and
  `ssl_key_password` all went through without error.
- **`passwd` is documented as an alias but ignored** (ran: `using password: NO`). `default_file` is
  ignored too (ran). `hostname`, `server`, `schema`, `socket`, `named_pipe` and `pipe_name` are
  listed but never mapped.
- Types are converted with `int()` and `bool()`. Ran: `connect_timeout="5"` worked, `"x"` gave
  `ValueError: invalid literal for int() with base 10: 'x'`, `ssl_verify_cert="false"` became true,
  and `autocommit="false"` turned autocommit ON.
- `MYSQL_PWD` is ignored, and the default user is empty (ran).
- The SQLAlchemy round trip passed on MariaDB 11.4. Logging in to MySQL 8.4 failed with
  `Access denied ... (using password: YES)`, though the same login worked for every other driver.

## SQLAlchemy 2.0.52: URL query and `connect_args`

- **Overlay.** `cargs_tup, _cparams = dialect.create_connect_args(u)` is followed by
  `cparams = util.immutabledict(_cparams).union(pop_kwarg("connect_args", {}))`
  (`sqlalchemy/engine/create.py:633-634`). A `connect_args` key replaces the dialect's key. There is
  no deep merge.
- **Order inside each dialect.** URL user, password, host, database and port come first. Then the
  query string is laid on top (`opts.update(url.query)`), so `?user=` or `?password=` beat the URL's
  own (ran). Only after that are the conversions applied.
- **pymysql** (`dialects/mysql/pymysql.py:148-155`, which reuses `mysqldb.py:201-252`):
  - Sends `user`, `password`, `database`.
  - Turns into numbers: `connect_timeout`, `read_timeout`, `write_timeout`, `client_flag`
    (`mysqldb.py:212-222`).
  - Turns into true/false: `compress`, `local_infile`, `use_unicode`.
  - Folds into the `ssl` dictionary: `ssl_ca`, `ssl_key`, `ssl_cert`, `ssl_capath`, `ssl_cipher`
    (text) and `ssl_check_hostname` (true/false) (`mysqldb.py:227-242`).
  - Adds `FOUND_ROWS` to `client_flag` (`mysqldb.py:244-251`).
  - Everything else stays text. Ran: `port`, `autocommit` and `ssl_verify_cert` from the query stayed
    text.
- **mysqldb** works the same, but sends `db`, `user`, `passwd` (`mysqldb.py:205-207`). Its folded
  `check_hostname` key is ignored by mysqlclient.
- **mysqlconnector** (`dialects/mysql/mysqlconnector.py:172-219`):
  - Turns into true/false (`177-193`): `allow_local_infile`, `autocommit`, `buffered`, `compress`,
    `consume_results`, `force_ipv6`, `get_warnings`, `pool_reset_session`, `raise_on_warnings`,
    `raw`, `ssl_verify_cert`, `use_pure`, `use_unicode`.
  - Turns into numbers: `client_flag` (a name this driver does not have), `connection_timeout`,
    `connect_timeout`, `pool_size`.
  - Forces `buffered=True` (`201`).
  - Sets `client_flags` to the given value or the default, plus `FOUND_ROWS`, and swallows any error
    (`205-217`). Ran: `?client_flags=4` stayed the text `'4'`, which the driver then refuses.
  - `read_timeout`, `write_timeout` and `dns_srv` stay text, and the driver refuses them (ran).
- **mariadbconnector** (`dialects/mysql/mariadbconnector.py:197-234`):
  - Sends `username`, `password`, `database` (`198`).
  - Turns into numbers: `connect_timeout`, `read_timeout`, `write_timeout`, `client_flag`, `port`,
    `pool_size`.
  - Turns into true/false: `local_infile`, `ssl_verify_cert`, `ssl`, `pool_reset_connection`,
    `compress`.
  - Adds `FOUND_ROWS` to `client_flag` (`222-233`).
- **True/false parser** (`util/langhelpers.py:1316-1325`): it accepts
  `true yes on y t 1 / false no off n f 0`. Anything else gives
  `ValueError: String is not true/false: 'maybe-secret'`, which prints the value (ran).
- **Names.** `mariadb+<driver>` builds a MariaDB variant of any MySQL driver dialect
  (`dialects/mysql/mariadb.py:53-72`, `dialects/__init__.py:38-46`).
- **The found-rows trap (ran with PyMySQL on MariaDB 11.4):**

  | `connect_args` | flag sent | no-change UPDATE count |
  |---|---|---|
  | none | 2 (FOUND_ROWS) | 1 |
  | `client_flag=0` | 0 | 0 |
  | `client_flag=2` | 2 | 1 |

## Open points

- **The `DRIVER-SURVEY.md` runtime line says PyMySQL 2.2.8.** That version does not exist; the lock
  pins 1.2.0. I did not edit that file.
- mysqlclient ran only against MariaDB Connector/C. With Oracle's `libmysqlclient` I read but did
  not run `ssl_mode`, `local_infile_dir` and `server_public_key_path`. Whether that library still
  reads `MYSQL_PWD` was not checked either (Oracle has deprecated it).
- Why mysql-connector's C extension sends `root` when no user is given was not traced. The cause is
  inside its bundled C library.
- Why mariadb 2.0.0rc2 cannot log in to MySQL 8.4 was not traced. It is a release candidate.
- Default TLS behavior was not compared across drivers beyond PyMySQL's "try TLS, don't check"
  default. That includes which driver checks certificates by default.
- Not mapped: the full list of option-file names each C library reads, the default file search
  order, and `!include` lines.
- Windows-only settings (`named_pipe`, `kerberos_auth_mode="SSPI"`) were accepted but not run on
  Windows.
- Login plugins (PAM/dialog, GSSAPI, ed25519, parsec, OCI, OpenID) are left to
  `sqlite-and-logins.md`.
- The list of check errors that print a value covers only what I hit. It is not a full sweep.
- The async drivers `aiomysql` and `asyncmy` were not surveyed.
