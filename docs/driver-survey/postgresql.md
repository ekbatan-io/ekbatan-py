# PostgreSQL drivers: what each `connect()` accepts

Part of the [Python driver survey](../DRIVER-SURVEY.md). Checked 2026-10-11 against Python 3.14.7 and
SQLAlchemy 2.0.52, on macOS (Apple silicon).

How this was checked: a scratch virtual environment with all four drivers (each had a wheel for
Python 3.14, so no fallback to 3.13 was needed), `inspect.signature`, the installed source, and small
scripts run against a closed port (127.0.0.1 port 1) and a throwaway PostgreSQL 18.4 container.

The "how known" column in the tables:

- `ran`: proved by running.
- `ran + docs`: the setting's presence and its `*`/`D` mark come from a run; its value format comes from
  the PostgreSQL 18 docs (https://www.postgresql.org/docs/18/libpq-connect.html).
- `source file:line`: read in the installed source. Paths are relative to `site-packages/`, and `SA/`
  stands for `sqlalchemy/dialects/postgresql/`.

## Answers

### 1. The drivers

| driver | version surveyed | sync or async | SQLAlchemy URL name |
|---|---|---|---|
| psycopg 3 | 3.3.5 + psycopg-binary 3.3.5 (bundles libpq 18.6) | both | `postgresql+psycopg` |
| psycopg2 | psycopg2-binary 2.9.13 (bundles libpq 17.11) | sync | `postgresql+psycopg2` |
| pg8000 | 1.31.5 (pure Python) | sync | `postgresql+pg8000` |
| asyncpg | 0.32.0 | async only | `postgresql+asyncpg` |

- psycopg 3 with `create_async_engine` keeps the same URL name and switches to its async side by
  itself. `postgresql+psycopg_async` also works (ran).
- A plain `postgresql://` URL picks psycopg2, not psycopg 3 (ran; `SA/__init__.py:97`).
- asyncpg only works with `create_async_engine`. With `create_engine`, the first connect fails with
  `MissingGreenlet: greenlet_spawn has not been called; can't call await_only() here.` (ran). Async
  engines also need the `greenlet` package installed.
- Newer releases exist (psycopg 3.3.6, SQLAlchemy 2.1.4). They were not surveyed.

### 2. Every setting, with its type

- **psycopg 3**: 6 settings of its own, plus every libpq setting: 50 in its bundled libpq 18.6. The list
  comes from `psycopg.pq.Conninfo.get_defaults()` (ran). Its `dispchar` field marks password-like
  settings with `*` and debug settings with `D`.
- **psycopg2**: 5 settings of its own, `database` as a second name for `dbname`, plus every libpq
  setting: 41 in its bundled libpq 17.11. That list comes from calling libpq's `PQconndefaults` on
  psycopg2's own copy of libpq (ran). It lacks nine libpq 18 settings: `min_protocol_version`,
  `max_protocol_version`, `scram_client_key`, `scram_server_key`, the four `oauth_*` settings and
  `sslkeylogfile`.
- **pg8000**: 13 named settings. Nothing else.
- **asyncpg**: 23 named settings. Ten more (TLS files, `sslpassword`, ...) can only be given inside the
  `dsn` text, a service file or environment variables.
- **SQLAlchemy** adds a few keys of its own for the async drivers and takes them out before the driver
  sees them: `async_creator_fn` and `async_fallback` for psycopg 3 async; those two plus
  `prepared_statement_cache_size` and `prepared_statement_name_func` for asyncpg.
- libpq takes every value as text. psycopg 3 and psycopg2 turn any Python value into text with `str()`,
  so `True` becomes `"True"`, which libpq refuses for its 0/1 settings (ran). pg8000 and asyncpg want
  real Python types: numbers, booleans, objects.

The full tables are in each driver's section below.

### 3. Dictionary, list, object and function settings

**Yes: some settings work only as a dictionary, list, object or function, with no flat alternative.**

- **pg8000 `startup_params` (dictionary)**: the only way to send `options` or any other server setting
  (such as `search_path`) when connecting. Only `application_name` and `replication` have their own
  flat settings.
- **pg8000 `ssl_context` (object)**: the only way to check the server's certificate or to send a client
  certificate. The flat values only give "TLS without any check" (`True`, or the default) or "no TLS"
  (`False`).
- **pg8000 `source_address` (tuple)**: the only way to pick the local address to connect from. A list is
  refused; it must be a tuple (ran).
- **asyncpg `prepared_statement_name_func` (function; a SQLAlchemy key)**: SQLAlchemy's documented way to
  run asyncpg behind PgBouncer (`SA/asyncpg.py:120-156`).
- **asyncpg `password` as a function**: the only driver setting that can produce a fresh password for
  each new connection (for example a short-lived cloud token). Plain text works for a fixed password.
  Outside the driver settings, SQLAlchemy's `do_connect` event can do the same for any driver.
- **psycopg 3 `cursor_factory` (class)**: the only way to make psycopg 3 put the parameters into the SQL
  on the client side, as psycopg2 does. SQLAlchemy documents `cursor_factory=ClientCursor` for this
  (`SA/psycopg.py:138-164`).

Settings that are not flat, but where a flat setting can do the same job:

- asyncpg `server_settings` (dictionary): the same settings can go in the query part of a `dsn` text,
  for example `dsn="postgresql:///?search_path=s&application_name=a"` (ran). But `dsn` can also carry a
  user name and a password (ran).
- asyncpg `ssl` as an `SSLContext` object: the mode text (`"require"`, `"verify-full"`, ...) is flat. The
  certificate files then come from the `dsn` text, environment variables or `~/.postgresql/` files.
- asyncpg `host` and `port` lists (several servers): text with a comma is NOT split (ran). The flat way
  is SQLAlchemy's multi-host URL, which SQLAlchemy itself turns into lists (ran), or a `dsn` text.
- psycopg 3 and psycopg2 never take a dictionary or a list. Several hosts are one comma text
  (`host="h1,h2"`, `port="5432,5433"`). Several server settings are one `options` text, for example
  `"-c search_path=s -c statement_timeout=5000"` (ran).

Code hooks with no flat form, and no use as configuration under SQLAlchemy: psycopg 3 `row_factory`
(breaks SQLAlchemy, ran) and `context` (SQLAlchemy passes its own; replacing it silently dropped
SQLAlchemy's JSON serializer, ran); psycopg2 `connection_factory` and `cursor_factory`; asyncpg
`connection_class`, `record_class` and `loop`; SQLAlchemy's `async_creator_fn`.

### 4. User names and secrets

Settings that carry a login or a secret:

- **psycopg 3 and psycopg2 (libpq)**: `user` holds the user name. libpq marks `password`, `sslpassword`
  (the client key's password) and `oauth_client_secret` (libpq 18 only) with `*`. **Two password-like
  settings are NOT marked `*`**: `scram_client_key` and `scram_server_key` (libpq 18 only, marked `D`).
  The docs say they enable "pass-through SCRAM authentication". `passfile`, `sslkey` and `service` point
  to secrets; a service file can hold both the user and the password (ran). `sslkeylogfile` (libpq 18)
  writes the TLS session keys to a file.
- **Whole connection strings that can hold a user and a password**: psycopg 3 `conninfo`, psycopg2 `dsn`
  and asyncpg `dsn`. All three logged in by run with no `user` or `password` key.
- **pg8000**: `user` (required) and `password` (text or bytes). An `ssl_context` object can hold a client
  key.
- **asyncpg**: `user`, `password` (text or function), `passfile`, `service`/`servicefile` and `dsn`.
  **A `user` entry inside `server_settings` replaced the login user** (ran: the server checked the
  password for `nobody_role`). asyncpg sends these entries after `user`, and the server keeps the last
  one.

Where a login can come from without being passed in:

- **psycopg 3 and psycopg2 (libpq)**: `PGUSER`, `PGPASSWORD`, `PGPASSFILE` or `~/.pgpass`, and `PGSERVICE`
  with `PGSERVICEFILE` or `~/.pg_service.conf` (or the system file under `PGSYSCONFDIR`). Client
  certificates come from `~/.postgresql/postgresql.crt` and `.key`, or from `PGSSLCERT` and `PGSSLKEY`.
  With no user given anywhere, libpq uses the operating-system user name. For both drivers, a run logged
  in through each of these: a service file, a password file, `PGUSER` + `PGPASSWORD`, and `PGPASSFILE`.
- **asyncpg**: `PGUSER`, `PGPASSWORD`, `PGPASSFILE` or `~/.pgpass`, client certificate files found the same
  way as libpq, and the operating-system user name. A service file is read only when a `dsn` is given
  (ran). `PGSERVICE` is read but never used (ran; `asyncpg/connect_utils.py:560-561`).
- **pg8000**: nowhere. No environment variables, no password file, no service file (ran: `PGPASSWORD` was
  ignored).

Where drivers print secrets:

- psycopg 3: `conn.info.dsn` and `conn.info.get_parameters()` leave out only `password`. They printed
  `sslpassword` in clear (ran; `psycopg/_connection_info.py:102`). `repr(conn)` shows only host, port and
  database.
- psycopg2: `repr(conn)` and `conn.dsn` show `password=xxx`, but printed `sslpassword` in clear. So did
  `conn.info.dsn_parameters` (ran).
- Driver errors quote bad values: `invalid integer value "abc" for connection option "keepalives"`,
  `bad value for connect_timeout: 'abc'`, `invalid sslmode value: "nonsense"` (ran).

### 5. A setting the driver does not know

All four refuse it with an error, before trying to connect:

| driver | exact error (ran: `connect(host="127.0.0.1", port=1, ..., not_a_setting=1)`) |
|---|---|
| psycopg 3 | `psycopg.ProgrammingError: invalid connection option "not_a_setting"` (ends with a newline) |
| psycopg2 | `psycopg2.ProgrammingError: invalid dsn: invalid connection option "not_a_setting"` (ends with a newline) |
| pg8000 | `TypeError: connect() got an unexpected keyword argument 'not_a_setting'` |
| asyncpg | `TypeError: connect() got an unexpected keyword argument 'not_a_setting'` |

- **Exception**: psycopg 3 and psycopg2 silently drop any setting whose value is `None`, even an unknown
  one (ran). pg8000 and asyncpg refuse an unknown name even when its value is `None` (ran).
- psycopg 3 looks up the host name first. If the name has several addresses, the message repeats the
  error once per address under "Multiple connection attempts failed" (ran with `localhost`). If the
  lookup fails, the lookup error hides the unknown name (ran).
- What counts as unknown depends on the libpq copy: `oauth_client_id` works in psycopg 3 (libpq 18) and
  is an unknown option in psycopg2 (libpq 17) (ran).
- Names in other letter cases (`USER`, `Password`) are unknown to all four (ran).

### 6. How SQLAlchemy 2.0.52 builds the connect arguments

| driver | URL parts renamed | converted to another type | everything else |
|---|---|---|---|
| psycopg 3, psycopg2 | user name to `user`, database to `dbname` | `port` to int; several hosts to comma text | query values stay text |
| pg8000 | user name to `user` | `port` to int | query values stay text; several hosts not handled |
| asyncpg | user name to `user` | `port` to int; several hosts to lists; `prepared_statement_cache_size` to int | query values stay text |

- Sources: `SA/_psycopg_common.py:132-153` (shared by psycopg 3 and psycopg2), `SA/psycopg.py:452-460`,
  `SA/pg8000.py:503-508`, `SA/asyncpg.py:1133-1161`, and multi-host parsing in `SA/base.py:3493-3560`.
  All were confirmed by running with a `do_connect` listener that captured the final arguments.
- No PostgreSQL dialect turns a URL value into a boolean. Text from the URL broke these, by run:
  psycopg 3 `?prepare_threshold=0` (fails at the first query), pg8000 `?timeout=10`, and asyncpg
  `?timeout=10` and `?statement_cache_size=0`. asyncpg also refuses any query key that is not one of its
  own names, such as `?application_name=x` (ran).
- For psycopg 3, SQLAlchemy adds a setting of its own: `context`, an object holding its type adapters
  (`SA/psycopg.py:456-457`).
- **`connect_args` win.** `sqlalchemy/engine/create.py:633-634` lays them over the URL's arguments:
  `immutabledict(_cparams).union(connect_args)`. By run, `connect_args` replaced the URL's `user`,
  `application_name`, `connect_timeout` and `command_timeout`. `sqlalchemy/engine/default.py:628-630`
  then calls `dbapi.connect(*cargs, **cparams)`.

### 7. Replacing psycopg 3

- **psycopg2: nearly the same settings.** Same libpq names, except the nine libpq 18 settings that its
  libpq 17 lacks. `connect_timeout` must be a whole number (`2.5` fails, ran). `autocommit` and
  `prepare_threshold` do not exist (unknown option, ran); psycopg2 never prepares statements on the
  server. `async_=True` breaks SQLAlchemy loudly (ran). Sync only.
- **pg8000: different names and value types.** It does not use libpq: no environment variables, password
  file, service file or several hosts. TLS is set only through `ssl_context` (see answer 3). Server
  settings go in the `startup_params` dictionary. `user` is required.
- **asyncpg: different names and value types, async only.** Server settings go in the `server_settings`
  dictionary or the `dsn` query. TLS is `ssl`. No TCP keepalive settings, no `load_balance_hosts`.

| psycopg 3 (libpq) | psycopg2 | pg8000 | asyncpg |
|---|---|---|---|
| `dbname` | `dbname` or `database` | `database` | `database` |
| `connect_timeout` | same, whole number | `timeout` (number) | `timeout` (number) |
| `options`, server settings | same | `startup_params` dict | `server_settings` dict or `dsn` query |
| `application_name` | same | same | `server_settings` entry |
| `sslmode` | same | `ssl_context` True/False | `ssl` mode text |
| `sslrootcert`, `sslcert`, `sslkey`, `sslpassword` | same | inside an `SSLContext` | `dsn` query, env vars or `SSLContext` |
| `keepalives` (0/1), `keepalives_*` | same | `tcp_keepalive` (bool) | none |
| `host="h1,h2"`, `load_balance_hosts` | same | one host only | `host` list; no load balancing |
| `target_session_attrs` | same | none | same name |
| `passfile`, `service`, `PG*` env vars | same | none | `passfile`, env vars; `service` only with `dsn` |
| `prepare_threshold` | none | none | `statement_cache_size` (roughly) |
| `autocommit` | none | none | none |

### What this means for the planned rules

- **"No lists, no nested blocks"** keeps every real connection setting of psycopg 3 and psycopg2. It
  loses psycopg 3's client-side binding (`cursor_factory`). It would block pg8000's server settings and
  certificate checks completely, and asyncpg's server settings (except through `dsn`) and SQLAlchemy's
  PgBouncer setup.
- **"Refuse `None`"** loses psycopg 3 `prepare_threshold=None`, its documented way to turn off prepared
  statements (needed behind older connection poolers). A very large whole number does the same (ran:
  no prepared statements after 7 queries, as with `None`). Refusing `None` also helps: the libpq drivers would otherwise drop it silently, even
  under a misspelled name.
- **Refusing only `user` and `password` leaves other ways to supply a login**, all proved by run:
  `conninfo` (psycopg 3), `dsn` (psycopg2, asyncpg), `service` and `passfile` (both libpq drivers;
  asyncpg needs a `dsn` for `service`), and a `user` entry in asyncpg's `server_settings`.
- **"Values reach the driver as given" is right, and numbers must stay numbers.** libpq refuses `True`
  for 0/1 settings, so write `1`/`0`. psycopg 3 `autocommit` and pg8000 `tcp_keepalive` read the text
  `"false"` as true. psycopg 3 `prepare_threshold`, pg8000 `timeout` and asyncpg
  `timeout`/`statement_cache_size` fail on text.
- **psycopg 3 `autocommit=True` silently turns off SQLAlchemy transactions.** A row that was inserted and
  then rolled back was still there (ran), and `autocommit="false"` did the same. It is a plain boolean,
  so the shape rules let it through.
- **"Every value is a secret" is justified.** Driver errors quote bad values, and both libpq drivers print
  `sslpassword` in clear (see answer 4).

## psycopg 3

- **Version**: psycopg 3.3.5 with psycopg-binary 3.3.5. `psycopg.pq.__impl__` is `binary`, and the
  bundled libpq is 18.6 (`pq.version()` returns `180006`) (ran).
- **Sync or async**: both. `psycopg.connect` is `Connection.connect` (`psycopg/connection.py:81-92`).
  `AsyncConnection.connect` (`psycopg/connection_async.py:85`) takes the same settings (ran).
- **SQLAlchemy URL name**: `postgresql+psycopg`. It also serves async engines, through
  `SA/psycopg.py:500`. `postgresql+psycopg_async` is an alias (`SA/__init__.py:92-95`).
- **How it reads settings**: six named keyword arguments (`conninfo`, `autocommit`, `prepare_threshold`,
  `context`, `row_factory`, `cursor_factory`). Every other keyword lands in `**kwargs` and is merged with
  `conninfo` into one libpq connection string:
  - `None` values are dropped (`psycopg/conninfo.py:52`).
  - Every value goes through `str()` (`:59`).
  - libpq's parser checks the names before connecting (`:62`).

  psycopg itself also:
  - splits comma lists of hosts (`split_attempts` in `psycopg/_conninfo_utils.py`) and looks up host
    names;
  - handles `load_balance_hosts=random` (`psycopg/_conninfo_attempts.py:57`) and
    `target_session_attrs=prefer-standby` (`:38-41`);
  - enforces `connect_timeout` with `int(float(value))` (`psycopg/conninfo.py:123-140`); the default is
    130 seconds when it is unset (`:23`).

  The six named settings are applied after the connection is made (`psycopg/connection.py:142-149`).
- **Unknown setting**: `psycopg.ProgrammingError: invalid connection option "not_a_setting"`, followed by
  a newline. `AsyncConnection.connect` gives the same error. With the value `None`, the setting is
  silently dropped (ran).
- **SQLAlchemy**:
  - The URL's `port` becomes an int (`SA/_psycopg_common.py:140-141`) and query values stay text (`:142`).
  - Several hosts become comma text (`:144-148`).
  - An empty URL passes `""` as the first positional argument (`:153`). That then clashes with a
    `conninfo` key: `TypeError: Connection.connect() got multiple values for argument 'conninfo'` (ran).
  - SQLAlchemy adds `context` and, if it is set on `create_engine`, `client_encoding`
    (`SA/psycopg.py:456-459`).

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `conninfo` | libpq string or URI | scalar | - | can hold one | can hold one | ran |
| `autocommit` | bool | scalar | - | no | no | ran |
| `prepare_threshold` | int or None | scalar | huge int for None | no | no | ran |
| `context` | AdaptContext object | object | none | no | no | ran |
| `row_factory` | callable | function | none | no | no | ran |
| `cursor_factory` | Cursor class | object | **none** | no | no | ran |
| `async_creator_fn` (SQLAlchemy, async) | async callable | function | none | via code | via code | source SA/psycopg.py:787-789 |
| `async_fallback` (SQLAlchemy, async) | bool | scalar | - | no | no | source SA/psycopg.py:786 |
| `service` | text (name) | scalar | - | via file | via file | ran |
| `user` | text | scalar | - | yes | no | ran |
| `password` | text | scalar | - | no | yes, `*` | ran |
| `passfile` | file path | scalar | - | no | points to one | ran |
| `channel_binding` | disable/prefer/require | scalar | - | no | no | ran + docs |
| `connect_timeout` | int seconds | scalar | - | no | no | ran |
| `dbname` | text | scalar | - | no | no | ran |
| `host` | text, comma list | scalar | - | no | no | ran |
| `hostaddr` | IP, comma list | scalar | - | no | no | ran + docs |
| `port` | int, comma list | scalar | - | no | no | ran |
| `client_encoding` | text | scalar | - | no | no | ran + docs |
| `options` | text `-c k=v ...` | scalar | - | no | no | ran |
| `application_name` | text | scalar | - | no | no | ran |
| `fallback_application_name` | text | scalar | - | no | no | ran + docs |
| `keepalives` | 0/1 | scalar | - | no | no | ran |
| `keepalives_idle` | int seconds | scalar | - | no | no | ran |
| `keepalives_interval` | int seconds | scalar | - | no | no | ran + docs |
| `keepalives_count` | int | scalar | - | no | no | ran + docs |
| `tcp_user_timeout` | int ms | scalar | - | no | no | ran |
| `sslmode` | disable ... verify-full | scalar | - | no | no | ran |
| `sslnegotiation` | postgres/direct | scalar | - | no | no | ran + docs |
| `sslcompression` | 0/1 | scalar | - | no | no | ran + docs |
| `sslcert` | file path | scalar | - | no | no | ran + docs |
| `sslkey` | file path | scalar | - | no | points to one | ran + docs |
| `sslcertmode` | disable/allow/require | scalar | - | no | no | ran + docs |
| `sslpassword` | text | scalar | - | no | yes, `*` | ran |
| `sslrootcert` | file path or `system` | scalar | - | no | no | ran + docs |
| `sslcrl` | file path | scalar | - | no | no | ran + docs |
| `sslcrldir` | dir path | scalar | - | no | no | ran + docs |
| `sslsni` | 0/1 | scalar | - | no | no | ran + docs |
| `requirepeer` | OS user name | scalar | - | no | no | ran + docs |
| `require_auth` | methods, comma list | scalar | - | no | no | ran + docs |
| `min_protocol_version` | 3.0/3.2/latest | scalar | - | no | no | ran + docs |
| `max_protocol_version` | 3.0/3.2/latest | scalar | - | no | no | ran + docs |
| `ssl_min_protocol_version` | TLSv1 ... TLSv1.3 | scalar | - | no | no | ran + docs |
| `ssl_max_protocol_version` | TLSv1 ... TLSv1.3 | scalar | - | no | no | ran + docs |
| `gssencmode` | disable/prefer/require | scalar | - | no | no | ran + docs |
| `krbsrvname` | text | scalar | - | no | no | ran + docs |
| `gsslib` | gssapi/sspi | scalar | - | no | no | ran + docs |
| `gssdelegation` | 0/1 | scalar | - | no | no | ran + docs |
| `replication` | true/false/database (`D`) | scalar | - | no | no | ran + docs |
| `target_session_attrs` | any/read-write/... | scalar | - | no | no | ran + docs |
| `load_balance_hosts` | disable/random | scalar | - | no | no | ran + docs |
| `scram_client_key` | base64 text | scalar | - | no | yes, `D` | ran + docs |
| `scram_server_key` | base64 text | scalar | - | no | yes, `D` | ran + docs |
| `oauth_issuer` | HTTPS URL | scalar | - | no | no | ran + docs |
| `oauth_client_id` | text | scalar | - | no | no | ran |
| `oauth_client_secret` | text | scalar | - | no | yes, `*` | ran + docs |
| `oauth_scope` | text, space list | scalar | - | no | no | ran + docs |
| `sslkeylogfile` | file path (`D`) | scalar | - | no | writes TLS keys | ran + docs |

- A `-` under "flat alternative" means the setting is already flat.
- `*` and `D` are libpq's own marks from `dispchar`: `*` means "Password field - hide value"; `D` means
  "Debug option - don't show by default" (docs). The marks do not cover every secret: the two SCRAM keys
  carry `D`.
- Value checks, by run:
  - `keepalives=True` gives `invalid integer value "True" for connection option "keepalives"`.
  - `connect_timeout=True` gives `ProgrammingError: bad value for connect_timeout: 'True'`.
  - `connect_timeout=2.5` is accepted.
  - `port=99999` gives `invalid port number: "99999"`.
  - `sslmode="nonsense"` gives `invalid sslmode value: "nonsense"`.
- A Python list or dictionary is not refused; it is turned into text (ran). `host=["a", "b"]` became
  `"['a', 'b']"` and failed the name lookup. `options={"search_path": "x"}` reached the server and failed
  with `FATAL: invalid command-line argument for server process`.
- `options` is the flat way to set server settings: `"-c search_path=myschema -c statement_timeout=5000"`
  gave `myschema` and `5s` (ran).
- `autocommit` goes through `bool()` (`psycopg/connection.py:142`), so the text `"false"` turns it on
  (ran). Under SQLAlchemy, both `True` and `"false"` made a rollback do nothing (ran).
- `prepare_threshold` must be a whole number or `None` (`psycopg/_preparing.py:64,73`).
  - Text failed at the first query (ran, also when it came from the URL):
    `TypeError: '>=' not supported between instances of 'int' and 'str'`.
  - `None` and `2**62` both left 0 prepared statements after 7 queries; the default `5` left 1 (ran).
  - psycopg's docs give `None` as the way to turn prepared statements off behind connection poolers.
    They also say psycopg 3.2+ with libpq 17+ can keep prepared statements through PgBouncer 1.22+
    (https://www.psycopg.org/psycopg3/docs/advanced/prepare.html).
- `context`: SQLAlchemy sets its own (see above). Passing a different one made SQLAlchemy's
  `json_serializer` silently unused (ran).
- `row_factory=dict_row` broke SQLAlchemy on the first connect:
  `AssertionError: Could not determine version from string 'version'` (ran).
- `cursor_factory=psycopg.ClientCursor` worked under SQLAlchemy (ran).
- `conninfo` holding `user=... password=...` logged in with no `user` or `password` key (ran).
- `dbname` is not opened up as a connection string. A run with `dbname="host=127.0.0.1 port=1"` gave
  `FATAL: database "host=127.0.0.1 port=1" does not exist`. libpq only does that in
  `PQconnectdbParams` and `PQsetdbLogin` (docs).
- Logins by run, with no user or password key:
  - `service=` plus a service file holding `user` and `password` (found through `PGSERVICEFILE`);
  - `passfile=`;
  - `PGUSER` + `PGPASSWORD`;
  - `PGPASSFILE`.

  The control run with none of these failed with `fe_sendauth: no password supplied`.
- `conn.info.dsn` printed `sslpassword` and `oauth_client_id` in clear, because only `password` is left
  out (`psycopg/_connection_info.py:98-104`). `repr(conn)` printed only host, port and database (ran).
- `requirepeer` holds the server's operating-system user name, not a login. `oauth_client_id` names the
  application, not the database user (docs).
- The list depends on which libpq is in use. psycopg-binary bundles libpq 18.6. psycopg with the
  system's libpq (installed without `[binary]`) gets that libpq's list. psycopg-binary's libpq also
  defaults `gssencmode` to `disable`, where psycopg2-binary's defaults it to `prefer` (ran).

## psycopg2

- **Version**: psycopg2-binary 2.9.13 (`psycopg2.__version__` is `2.9.13 (dt dec pq3 ext lo64)`). It
  bundles libpq 17.11 (`psycopg2.extensions.libpq_version()` returns `170011`) (ran).
- **Sync or async**: sync. `async_=True` makes a non-blocking connection that you poll by hand; it is not
  asyncio.
- **SQLAlchemy URL name**: `postgresql+psycopg2`. A plain `postgresql://` also picks it
  (`SA/__init__.py:97`, ran).
- **How it reads settings**: `connect(dsn=None, connection_factory=None, cursor_factory=None, **kwargs)`
  (`psycopg2/__init__.py:80`). It first takes `async`/`async_` out of `**kwargs` (`:115-119`). Then
  `make_dsn` (`psycopg2/extensions.py:138-169`) turns the rest into one libpq string:
  - `database` becomes `dbname` (`:149-153`). Giving both raises
    `TypeError: you can't specify both 'database' and 'dbname' arguments` (ran).
  - `None` values are dropped (`:155-156`).
  - Every value goes through `str()` (`:163`).
  - libpq checks the names before connecting (`:167`).
- **Unknown setting**: refused before connecting, with
  `psycopg2.ProgrammingError: invalid dsn: invalid connection option "not_a_setting"` followed by a
  newline. With the value `None`, the setting is silently dropped (ran).
- **SQLAlchemy**: the same `create_connect_args` as psycopg 3 (`SA/_psycopg_common.py:132-153`), but
  without the added `context` (ran).

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `dsn` | libpq string or URI | scalar | - | can hold one | can hold one | ran |
| `connection_factory` | callable or class | function | none | no | receives it | ran |
| `cursor_factory` | cursor class | object | none | no | no | source psycopg2/__init__.py:123-124 |
| `async`, `async_` | bool | scalar | - | no | no | ran |
| `database` | text, same as `dbname` | scalar | - | no | no | ran |
| `service` | text (name) | scalar | - | via file | via file | ran |
| `user` | text | scalar | - | yes | no | ran |
| `password` | text | scalar | - | no | yes, `*` | ran |
| `passfile` | file path | scalar | - | no | points to one | ran |
| `channel_binding` | disable/prefer/require | scalar | - | no | no | ran + docs |
| `connect_timeout` | int seconds | scalar | - | no | no | ran |
| `dbname` | text | scalar | - | no | no | ran |
| `host` | text, comma list | scalar | - | no | no | ran |
| `hostaddr` | IP, comma list | scalar | - | no | no | ran + docs |
| `port` | int, comma list | scalar | - | no | no | ran |
| `client_encoding` | text | scalar | - | no | no | ran + docs |
| `options` | text `-c k=v ...` | scalar | - | no | no | ran + docs |
| `application_name` | text | scalar | - | no | no | ran |
| `fallback_application_name` | text | scalar | - | no | no | ran + docs |
| `keepalives` | 0/1 | scalar | - | no | no | ran |
| `keepalives_idle` | int seconds | scalar | - | no | no | ran + docs |
| `keepalives_interval` | int seconds | scalar | - | no | no | ran + docs |
| `keepalives_count` | int | scalar | - | no | no | ran + docs |
| `tcp_user_timeout` | int ms | scalar | - | no | no | ran + docs |
| `sslmode` | disable ... verify-full | scalar | - | no | no | ran |
| `sslnegotiation` | postgres/direct | scalar | - | no | no | ran + docs |
| `sslcompression` | 0/1 | scalar | - | no | no | ran + docs |
| `sslcert` | file path | scalar | - | no | no | ran + docs |
| `sslkey` | file path | scalar | - | no | points to one | ran + docs |
| `sslcertmode` | disable/allow/require | scalar | - | no | no | ran + docs |
| `sslpassword` | text | scalar | - | no | yes, `*` | ran |
| `sslrootcert` | file path or `system` | scalar | - | no | no | ran + docs |
| `sslcrl` | file path | scalar | - | no | no | ran + docs |
| `sslcrldir` | dir path | scalar | - | no | no | ran + docs |
| `sslsni` | 0/1 | scalar | - | no | no | ran + docs |
| `requirepeer` | OS user name | scalar | - | no | no | ran + docs |
| `require_auth` | methods, comma list | scalar | - | no | no | ran + docs |
| `ssl_min_protocol_version` | TLSv1 ... TLSv1.3 | scalar | - | no | no | ran + docs |
| `ssl_max_protocol_version` | TLSv1 ... TLSv1.3 | scalar | - | no | no | ran + docs |
| `gssencmode` | disable/prefer/require | scalar | - | no | no | ran + docs |
| `krbsrvname` | text | scalar | - | no | no | ran + docs |
| `gsslib` | gssapi/sspi | scalar | - | no | no | ran + docs |
| `gssdelegation` | 0/1 | scalar | - | no | no | ran + docs |
| `replication` | true/false/database (`D`) | scalar | - | no | no | ran + docs |
| `target_session_attrs` | any/read-write/... | scalar | - | no | no | ran + docs |
| `load_balance_hosts` | disable/random | scalar | - | no | no | ran + docs |

- Values follow the same rules as psycopg 3, through libpq 17. `keepalives=True` fails the same way (ran).
  One difference: here libpq itself checks `connect_timeout` and wants a whole number, so `2.5` fails
  (ran): `invalid integer value "2.5" for connection option "connect_timeout"`.
- These are not settings in psycopg2: `autocommit=True` and `prepare_threshold=5` give
  `invalid dsn: invalid connection option ...` (ran). libpq 18-only names fail the same way (ran with
  `oauth_client_id` and `min_protocol_version`).
- `async_=True` returned a connection without any error outside SQLAlchemy. Under SQLAlchemy it failed
  with `OperationalError: asynchronous connection attempt underway`. `async_="false"` failed with
  `TypeError: 'str' object cannot be interpreted as an integer` (ran).
- `connection_factory` is called with the full connection string, password included (ran:
  `connection_factory=str` returned that string).
- `dsn` holding `user=... password=...` logged in with no `user` or `password` key (ran). `dbname` is not
  opened up as a connection string (ran).
- Logins by run: service file, `passfile`, `PGUSER` + `PGPASSWORD` and `PGPASSFILE` all worked, as with
  psycopg 3.
- `repr(conn)` and `conn.dsn` show `password=xxx`, but they printed the `sslpassword` test value in clear;
  so did `conn.info.dsn_parameters` (ran).

## pg8000

- **Version**: 1.31.5. A pure Python wheel (`py3-none-any`).
- **Sync or async**: sync.
- **SQLAlchemy URL name**: `postgresql+pg8000`.
- **How it reads settings**: fixed named arguments only.
  - SQLAlchemy imports the `pg8000` module and calls `pg8000.connect` (`pg8000/__init__.py:97-126`), which
    has 13 parameters. `user` is required.
  - `pg8000.connect` hands them to `CoreConnection.__init__` (`pg8000/core.py:258-400`), which also takes
    `sock`. `pg8000.connect` itself does not take `sock` (ran: `TypeError` for `sock`).
- **Unknown setting**: `TypeError: connect() got an unexpected keyword argument 'not_a_setting'`, also
  when the value is `None` (ran).
- **SQLAlchemy** (`SA/pg8000.py:503-508`):
  - The URL's user name becomes `user`, the database stays `database`, `port` becomes an int, and query
    values stay text.
  - Several hosts are not handled: SQLAlchemy passed the tuple `host=('h1:5432', 'h2:5433')` (ran).
  - A URL without a user name fails with
    `TypeError: connect() missing 1 required positional argument: 'user'` (ran).

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `user` | text or bytes | scalar | - | yes | no | ran |
| `host` | text, one host | scalar | - | no | no | ran |
| `database` | text or bytes | scalar | - | no | no | ran |
| `port` | int | scalar | - | no | no | ran |
| `password` | text or bytes | scalar | - | no | yes | ran |
| `source_address` | (host, port) tuple | list | **none** | no | no | ran |
| `unix_sock` | socket file path | scalar | - | no | no | source pg8000/core.py:180-197 |
| `ssl_context` | None, bool or SSLContext | object | **none for checks** | no | can hold key | ran |
| `timeout` | number (seconds) | scalar | - | no | no | ran |
| `tcp_keepalive` | bool | scalar | - | no | no | ran |
| `application_name` | text or bytes | scalar | - | no | no | ran |
| `replication` | text, e.g. `database` | scalar | - | no | no | source pg8000/core.py:292-297 |
| `startup_params` | dict of text | dict | **none** | refused | no | ran |
| `sock` | socket object | object | - | no | no | ran (not reachable) |

- `user` is required. `None` gives `InterfaceError: The 'user' connection parameter cannot be None`
  (`pg8000/core.py:289-290`).
- `user`, `database`, `application_name`, `replication` and every `startup_params` value must be text or
  bytes. `None` drops the value (except for `user`, see above); anything else is refused, for example
  `InterfaceError: The parameter application_name can't be of type <class 'int'>.`
  (`pg8000/core.py:308-314`, ran).
- `startup_params` adds names to the startup message. It worked with `options` and with plain server
  setting names such as `search_path` and `statement_timeout` (ran).
  - It refuses `user`, `database`, `application_name` and `replication` (`pg8000/core.py:299-305`, ran).
    Because of a pg8000 bug, the message prints `{common_params}` literally.
  - Other names go to the server: `USER` gave `unrecognized configuration parameter "USER"` (ran).
- `timeout` goes to `socket.create_connection` (`pg8000/core.py:204`). Text fails with
  `TypeError: 'str' object cannot be interpreted as an integer or float` (ran), and that is exactly what
  `?timeout=10` in the URL produces.
- `tcp_keepalive` is only tested for truth, so `"false"` turns it on (ran). It is on by default.
- `ssl_context` (`pg8000/core.py:217-241`):
  - `None` (the default) tries TLS without checking anything and falls back to plain.
  - `True` requires TLS but still checks nothing.
  - `False` turns TLS off.
  - An `SSLContext` is used as given.
  - The text `"false"` is treated as a context object (ran).
  - pg8000's `True` is much weaker than asyncpg's `True`, which checks everything.
- `source_address` goes to `socket.create_connection`. Text or a list fails with
  `TypeError: bind(): AF_INET address must be tuple, not list` (ran).
- With no password, a SCRAM login crashes (`pg8000/core.py:595-602`, ran):
  `AttributeError: 'NoneType' object has no attribute 'decode'`.
- pg8000 reads no environment variables, password file or service file. The package has no `environ`,
  `getenv` or `pgpass` in it, and `PGPASSWORD` was ignored in a run.

## asyncpg

- **Version**: 0.32.0.
- **Sync or async**: async only. `asyncpg.connect` is a coroutine function (ran). Use
  `create_async_engine`.
- **SQLAlchemy URL name**: `postgresql+asyncpg`.
- **How it reads settings**: 23 named keyword-only arguments (`asyncpg/connection.py:2116-2135`). Their
  types are documented in the docstring (`:2147-2463`). `dsn` is a URL text:
  - Its query part may carry `sslmode`, `sslcert` and similar names.
  - Any other query name becomes a server setting (`asyncpg/connect_utils.py:294-453`).
  - Named keyword arguments win over values in `dsn` (ran).
- **Unknown setting**: `TypeError: connect() got an unexpected keyword argument 'not_a_setting'`. This
  also happens with the value `None`, and for libpq names such as `sslcert` (ran).
- **SQLAlchemy** (`SA/asyncpg.py:1133-1161`, ran):
  - The URL's user name becomes `user` and the database stays `database`.
  - Query values stay text (`:1137`).
  - Several hosts become `host` and `port` lists (`:1139-1157`).
  - `port` becomes an int (`:1159`), and so does `prepared_statement_cache_size` (`:1160`).
  - SQLAlchemy's adapter then takes out `async_fallback`, `async_creator_fn`,
    `prepared_statement_cache_size` and `prepared_statement_name_func` (`SA/asyncpg.py:935-943`).

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `dsn` | URL text | scalar | - | can hold one | can hold one | ran |
| `host` | text or list | scalar or list | URL multi-host | no | no | ran |
| `port` | int or list | scalar or list | URL multi-host | no | no | ran |
| `user` | text | scalar | - | yes | no | ran |
| `password` | text or callable | scalar or function | text (fixed only) | no | yes | ran |
| `passfile` | file path | scalar | - | no | points to one | ran |
| `service` | text (name) | scalar | - | via file | via file | ran |
| `servicefile` | file path | scalar | - | via file | via file | ran |
| `database` | text | scalar | - | no | no | ran |
| `loop` | event loop | object | none | no | no | source asyncpg/connection.py:2238 |
| `timeout` | number (seconds) | scalar | - | no | no | ran |
| `statement_cache_size` | int, 0 or more | scalar | - | no | no | ran |
| `max_cached_statement_lifetime` | int, 0 or more | scalar | - | no | no | source asyncpg/connect_utils.py:888-895 |
| `max_cacheable_statement_size` | int, 0 or more | scalar | - | no | no | source asyncpg/connect_utils.py:888-895 |
| `command_timeout` | number above 0 | scalar | - | no | no | ran |
| `ssl` | mode text, bool or SSLContext | scalar or object | mode text | no | can hold key | ran |
| `direct_tls` | bool | scalar | - | no | no | source asyncpg/connect_utils.py:664-667 |
| `connection_class` | Connection subclass | object | none | no | no | ran |
| `record_class` | Record subclass | object | none | no | no | source asyncpg/connection.py:2491-2492 |
| `server_settings` | dict of text | dict | `dsn` query | **can replace** | no | ran |
| `target_session_attrs` | any/primary/standby/... | scalar | - | no | no | ran |
| `krbsrvname` | text | scalar | - | no | no | source asyncpg/connect_utils.py:857-858 |
| `gsslib` | gssapi/sspi | scalar | - | no | no | source asyncpg/connect_utils.py:860-867 |
| `prepared_statement_cache_size` (SQLAlchemy) | int | scalar | - | no | no | ran |
| `prepared_statement_name_func` (SQLAlchemy) | callable | function | **none** | no | no | source SA/asyncpg.py:941-943 |
| `async_creator_fn` (SQLAlchemy) | async callable | function | none | via code | via code | source SA/asyncpg.py:937 |
| `async_fallback` (SQLAlchemy) | bool | scalar | - | no | no | source SA/asyncpg.py:936 |

The settings below are not keyword arguments. They can only be given in the query part of `dsn`, in a
service file (read only when `dsn` is given) or through environment variables
(`asyncpg/connect_utils.py:394-453` and `:495-559`):

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `sslmode` | disable ... verify-full | scalar (in `dsn`) | - | no | no | source asyncpg/connect_utils.py:394-397 |
| `sslcert` | file path | scalar (in `dsn`) | - | no | no | source asyncpg/connect_utils.py:399-400 |
| `sslkey` | file path | scalar (in `dsn`) | - | no | points to one | source asyncpg/connect_utils.py:402-403 |
| `sslrootcert` | file path | scalar (in `dsn`) | - | no | no | source asyncpg/connect_utils.py:405-406 |
| `sslnegotiation` | postgres/direct | scalar (in `dsn`) | - | no | no | source asyncpg/connect_utils.py:408-409 |
| `sslcrl` | file path | scalar (in `dsn`) | - | no | no | source asyncpg/connect_utils.py:411-412 |
| `sslpassword` | text | scalar (in `dsn`) | - | no | yes | source asyncpg/connect_utils.py:414-415 |
| `ssl_min_protocol_version` | TLSv1 ... TLSv1.3 | scalar (in `dsn`) | - | no | no | source asyncpg/connect_utils.py:417-420 |
| `ssl_max_protocol_version` | TLSv1 ... TLSv1.3 | scalar (in `dsn`) | - | no | no | source asyncpg/connect_utils.py:422-425 |
| `dbname` | text, same as `database` | scalar (in `dsn`) | - | no | no | source asyncpg/connect_utils.py:369-372 |
| any other name | text | scalar (in `dsn`) | - | no | no | ran (becomes a server setting) |

- Text against numbers, by run:
  - `timeout="10"` gives `TypeError: unsupported operand type(s) for +: 'float' and 'str'`.
  - `statement_cache_size="0"` gives `TypeError: '<' not supported between instances of 'str' and 'int'`.
  - `command_timeout="5"` and `port="5432"` are converted and work.
  - `None` is refused for the three cache sizes, for example
    `ValueError: statement_cache_size is expected to be greater or equal to 0, got None`
    (`asyncpg/connect_utils.py:888-895`).
- `host="h1,h2"` (text with a comma) is one host name, not two (`asyncpg/connect_utils.py:576-577`; ran:
  the name lookup failed). `port="1,2"` gives `ValueError: invalid literal for int() with base 10: '1,2'`.
  A list of hosts with a list of ports worked (ran).
- `server_settings` must be a dict of text. An int value or a list gives
  `ClientConfigurationError: server_settings is expected to be None or a Dict[str, str]`
  (`asyncpg/connect_utils.py:834-840`, ran).
  - It worked with `search_path`, `statement_timeout`, `application_name` and `options` (ran).
  - **A `user` entry replaced the login user.** The entries are sent after `user`
    (`asyncpg/protocol/coreproto.pyx:952-961`). A run with `server_settings={"user": "nobody_role"}`
    gave `InvalidPasswordError: password authentication failed for user "nobody_role"`.
- The `dsn` query is the flat way to give server settings.
  `dsn="postgresql:///?search_path=dsnschema&application_name=dsnapp&statement_timeout=4000"` set all
  three (ran, also through SQLAlchemy `connect_args`). A full `dsn` with user and password logged in on
  its own (ran), and a `user` keyword overrode the `dsn` user (ran).
- `ssl` (`asyncpg/connect_utils.py:686-828`):
  - Mode text: `disable`, `allow`, `prefer` (the default), `require`, `verify-ca`, `verify-full`.
  - `True` means `verify-full` with the system's certificate store (`:820-824`).
  - `False` means off.
  - An `SSLContext` is used as given, and counts as `require`.
  - The text `"false"` fails (ran):
    ``ClientConfigurationError: `sslmode` parameter must be one of: disable, allow, prefer, require, verify-ca, verify-full``.
- TLS files are not keyword arguments. They come from the `dsn` query, a service file, `PGSSLROOTCERT`,
  `PGSSLCRL`, `PGSSLKEY`, `PGSSLCERT`, or files under `~/.postgresql/`. No environment variable carries
  `sslpassword`. The `SSLKEYLOGFILE` environment variable makes asyncpg write its TLS keys to a file
  (`:796-799`).
- `password` may be a function, plain or async. It is called for each new connection
  (`asyncpg/connect_utils.py:1070-1075`; both kinds ran).
- The service file is read only inside the `dsn` branch (`asyncpg/connect_utils.py:294-559`).
  - Without a `dsn`, `service="ekbsurvey", servicefile=...` silently did nothing and tried
    localhost:5432 (ran).
  - With `dsn="postgresql://"`, it logged in (ran).
  - `PGSERVICE` is read at `:560-561`, after the file has already been read, and is never used. It was
    ignored even with a `dsn` (ran).
- Environment variables read (from the source): `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`,
  `PGDATABASE`, `PGPASSFILE`, `PGSSLMODE`, `PGSSLNEGOTIATION`, `PGSSLROOTCERT`, `PGSSLCRL`, `PGSSLKEY`,
  `PGSSLCERT`, `PGSSLMINPROTOCOLVERSION`, `PGSSLMAXPROTOCOLVERSION`, `PGTARGETSESSIONATTRS`,
  `PGKRBSRVNAME`, `PGGSSLIB`, `PGSERVICEFILE` (only with a `dsn`) and `SSLKEYLOGFILE`. With no user given,
  asyncpg uses the operating-system user name (`:600-603`). Logins by run: `passfile` and `PGPASSWORD`.
- `target_session_attrs="nope"` gives an error that prints a Python method object instead of the allowed
  values (an asyncpg bug; ran).
- Through SQLAlchemy, by run:
  - `?application_name=u` fails:
    `TypeError: connect() got an unexpected keyword argument 'application_name'`.
  - `?timeout=10` and `?statement_cache_size=0` fail as above.
  - `?command_timeout=10` and `?prepared_statement_cache_size=0` work.
  - `connect_args={"server_settings": {...}}` works.
- SQLAlchemy's documented setups use values that are not flat: `prepared_statement_name_func` for
  PgBouncer (`SA/asyncpg.py:120-156`), and `server_settings={"jit": "off"}` against slow ENUM lookups
  (`SA/asyncpg.py:158-171`).
- asyncpg has no TCP keepalive settings and no host load balancing. Hosts are tried in order; a random
  pick happens only for `prefer-standby` (`asyncpg/connect_utils.py:1294-1296`).

## Open points

- TLS was not tested end to end, because the test server had no TLS. Client certificates, `sslpassword`
  in real use, the certificate checks of pg8000 and asyncpg, and key logging (`sslkeylogfile`,
  `SSLKEYLOGFILE`) are described from the source and the docs.
- Kerberos/GSS settings (`krbsrvname`, `gsslib`, `gssencmode`, `gssdelegation`) and libpq 18's OAuth
  settings were not tested; they need matching servers. Whether psycopg-binary's libpq is built with
  OAuth support was not checked.
- psycopg 3 with the system's libpq (no `[binary]`, or psycopg-c) gets that libpq's setting list. This was
  not surveyed; it depends on the machine.
- Both binary wheels bundle libldap, so LDAP lookups in service files may work. Not tested.
- Windows differences (asyncpg's `gsslib` default `sspi`, the password file location) were not checked.
- pg8000's `replication` values come from reading the source only.
- Newer releases (psycopg 3.3.6, SQLAlchemy 2.1.4) were not surveyed. SQLAlchemy 2.1 may build the
  connect arguments differently.
