# Other ways to log in: PostgreSQL, MySQL and MariaDB

Part of the [Python driver survey](../DRIVER-SURVEY.md). Checked on 2026-10-11 against Python 3.14.7
and SQLAlchemy 2.0.52, on macOS (Apple silicon).

**Scope: PostgreSQL, MySQL and MariaDB.** SQLite has no login at all; see [sqlite.md](sqlite.md).
Ekbatan-py's drivers today are psycopg 3 (PostgreSQL) and PyMySQL (MySQL and MariaDB).

How this was checked: a scratch virtual environment, the installed source, and small scripts. No
cloud account and no real database were used, and no credentials were entered or created. AWS,
Google and Azure credential lookups were switched off or found nothing. Every "password" and "token"
was a fixed fake string. Connections went to a closed port (127.0.0.1 port 1), or to a small fake
MySQL server written for the test, which never offers TLS.

| package | version | note |
|---|---|---|
| psycopg + psycopg-binary | 3.3.5 | bundles libpq 18.6 |
| PyMySQL | 1.2.0 | the `uv.lock` version; with cryptography, PyNaCl |
| aws-advanced-python-wrapper | 3.1.0 | |
| boto3 / botocore | 1.43.111 | |
| aws-secretsmanager-caching | 1.2.0 | |
| cloud-sql-python-connector | 1.22.0 | with pg8000 1.31.5, asyncpg 0.32.0 |
| google-auth | 2.61.0 | |
| azure-identity | 1.26.0 | |
| mysql-connector-python | 26.7.0 | |
| mariadb (Connector/Python) | 1.1.14 | needs a C library; read in source |

Every package installed on Python 3.14; no 3.13 fallback was needed. `mariadb` cannot build on macOS
without MariaDB Connector/C, so its source was read from the downloaded package instead.

The "how known" column:

- `ran`: proved by running.
- `src file:line`: read in the installed source. Paths are relative to `site-packages/`.
- `docs`: from the page named in the text.

## Answers

### How a login reaches the driver

SQLAlchemy offers four routes. The route a login method needs decides what Ekbatan-py must build.

1. **A plain user name and password.** They reach the driver as `user` and `password`.
2. **A token used as the password.** Cloud logins (AWS IAM, Azure Entra ID) hand out a short-lived
   token instead of a password. It expires, so it cannot sit in a config file. It must be made fresh
   for each new connection. SQLAlchemy's `do_connect` event runs before every new connection and can
   put a fresh token into `password` (ran).
3. **`creator=`.** SQLAlchemy hands the whole job of connecting to a function. It then **ignores**
   the URL's user name and password, `connect_args` and the `do_connect` event (ran). For Ekbatan-py,
   `username`, `password` and `data_source_properties` would vanish without a word, unless Ekbatan-py
   passes them to that function itself.
4. **A wrapper with its own SQLAlchemy URL name.** The AWS wrapper registers
   `postgresql+aws_wrapper_psycopg://` and `mysql+aws_wrapper_mysqlconnector://`. Its settings travel
   like any driver setting, in the URL or in `connect_args`.

Ekbatan-py's migrator connects without the engine, and always with plain psycopg or PyMySQL
(`src/ekbatan/_yoyo.py:562-614` in this repository). **`do_connect` and `creator=` never reach
migrations**, and wrapper settings make the migrator fail (replicated run:
`invalid connection option "wrapper_dialect"`).

### Each mechanism

| mechanism | databases | drivers | plugs in through |
|---|---|---|---|
| AWS Advanced Python Wrapper | PostgreSQL, MySQL | psycopg; mysql-connector | own URL name, or `creator=` |
| RDS IAM token, plain driver | PostgreSQL, MySQL, MariaDB | psycopg, PyMySQL | token as password, `do_connect` |
| Secrets Manager, plain boto3 | PostgreSQL, MySQL, MariaDB | psycopg, PyMySQL | user + password |
| Google Cloud SQL Connector | PostgreSQL, MySQL | psycopg, pg8000; PyMySQL | **`creator=` only** |
| Azure Entra ID | PostgreSQL, MySQL | psycopg, PyMySQL | token as password, `do_connect` |
| Kerberos | all three | psycopg; mysql-connector; mariadb | flat settings |
| Client certificates | all three | psycopg, PyMySQL, others | flat file settings |
| MariaDB PAM, ed25519 | MariaDB | PyMySQL, mariadb | plain password |

**1. AWS Advanced Python Wrapper 3.1.0.**

- PostgreSQL through psycopg (sync and async). MySQL through **mysql-connector-python**, or aiomysql
  for async, **not PyMySQL**. **No MariaDB.** Built for Aurora and RDS.
- In 3.1.0 it **needs no `creator=`**: its own URL name plus `connect_args` is enough (ran).
- All 116 settings are flat: text, numbers, true/false. IAM, Secrets Manager, ADFS and Okta login are
  plugins, named in the `plugins` text.
- `username` becomes `user`, which IAM needs. Secrets Manager, ADFS and Okta **overwrite** `user`.
- `password`: every login plugin overwrites it, so leave it empty.
- `data_source_properties`: works; everything is flat. Put `plugins` there, not in the URL:
  SQLAlchemy silently removes `plugins` from a URL (ran).
- It writes the IAM token into its DEBUG log (ran).

**2. RDS IAM token with a plain driver (boto3)** - **needs `do_connect` or another code hook.**

- RDS and Aurora PostgreSQL, MySQL and MariaDB (docs). psycopg and PyMySQL work as they are.
- boto3 makes the token on the machine from AWS credentials, with no call to AWS. It lasts 15 minutes
  and is checked only at login (source, docs).
- `username`: the database user; the token is made for it. `password`: empty; a hook fills it for each
  new connection. `data_source_properties`: only TLS settings, all flat.

**3. AWS Secrets Manager without the wrapper (boto3).**

- All three databases, any driver. The secret is JSON with `username` and `password` in it (docs).
- Read once at start, it is just a plain user name and password. To survive a password change
  ("rotation"), it must be read again in `do_connect`: a code hook again.
- It fills `username` and `password`. Nothing goes in `data_source_properties`.

**4. Google Cloud SQL Python Connector 1.22.0** - **`creator=` only, and its `Connector` takes
objects with no flat form.**

- Cloud SQL for PostgreSQL (psycopg, pg8000, asyncpg) and MySQL (PyMySQL). Cloud SQL has no MariaDB.
- No SQLAlchemy URL name (ran). Ekbatan-py would have to call
  `connector.connect(instance, driver, user=..., password=..., db=..., **settings)` inside its own
  `creator=` function.
- `Connector(credentials=..., loop=..., resolver=...)` takes a credentials object, an event loop and
  a class. Without them it finds Google credentials in the environment (ran).
- IAM login (`enable_iam_auth=True`): no password, and `username` is the IAM email (docs).

**5. Azure Entra ID (azure-identity)** - **needs `do_connect` or another code hook.**

- Azure Database for PostgreSQL and Azure Database for MySQL (flexible server). No MariaDB: Azure
  Database for MariaDB was retired in September 2025 (docs).
- A token is the password. It lasts 5 to 60 minutes (docs). azure-identity reuses it and renews it
  5 minutes before it ends (source).
- `username`: the Entra user, group or managed identity name. `password`: empty; a hook fills it.
  `data_source_properties`: TLS settings only.
- MySQL needs the token sent as clear text. PyMySQL does that by itself when asked, **even without
  TLS** (ran), so TLS must be switched on.

**6. Kerberos, client certificates, MariaDB PAM and ed25519, MySQL login plugins.**

- **Kerberos**: PostgreSQL through psycopg, with flat settings (`gssencmode`, `krbsrvname`, `gsslib`,
  `gssdelegation`); the bundled libpq has Kerberos built in (ran). MySQL Kerberos (MySQL Enterprise):
  mysql-connector-python. MariaDB Kerberos: drivers built on MariaDB's C library, such as `mariadb`.
  PyMySQL can do neither (ran). The login comes from the Kerberos ticket (`kinit`), so `password`
  stays empty.
- **Client certificates**: flat file paths. libpq `sslcert`, `sslkey`, `sslpassword`; PyMySQL
  `ssl_cert`, `ssl_key`, `ssl_key_password`. The key passwords are secrets under names the planned
  `password` refusal misses.
- **MariaDB PAM**: PyMySQL answers a `Password: ` question with `password` (ran). Any other question
  needs `auth_plugin_map`, a dictionary of Python classes, **with no flat form**. mysql-connector
  cannot answer PAM's questions (ran); it can log in only when the server sends PAM logins through
  `mysql_clear_password`, and only over TLS.
- **MariaDB ed25519**: PyMySQL, with PyNaCl installed (ran). Not mysql-connector (ran). MariaDB's newer
  `parsec`: neither (ran).
- **MySQL `caching_sha2_password` and `sha256_password`**: PyMySQL and mysql-connector; PyMySQL needs
  the `cryptography` package when TLS is off.
- **MySQL `mysql_clear_password`: PyMySQL sends the password as clear text even without TLS** (ran).
  mysql-connector refuses without TLS (ran).
- **OpenID Connect**: MySQL Enterprise's needs mysql-connector (`openid_token_file`). PostgreSQL 18's
  OAuth settings exist in psycopg's libpq (ran: listed) but were not tried.

### What it means for Ekbatan-py

- **Flat values cover almost everything.** Only three things need a dictionary, object, class or
  function with no flat form: the Cloud SQL Connector (`creator=` plus its `Connector` objects),
  PyMySQL `auth_plugin_map` (PAM questions other than `Password: `), and the AWS wrapper's code-only
  hooks (its own pools, profiles and custom plugins).
- **Tokens need code, not config.** RDS IAM, Azure Entra ID and rotating secrets need something that
  runs before each new connection. A config file cannot hold it. Ekbatan-py would need a code hook,
  for example a password function on the builder, and the migrator must call it too.
- **If Ekbatan-py ever accepts `creator=`**, it must refuse `username`, `password` and
  `data_source_properties` next to it, or hand them to it. Otherwise they vanish silently.
- **More names carry a login than `user` and `password`.** PyMySQL `passwd` works as the password when
  `password` is empty (ran). Others: libpq `sslpassword`, `oauth_client_secret`, `scram_client_key`,
  `scram_server_key`; PyMySQL `ssl_key_password`; the AWS wrapper's `db_user`, `idp_username`,
  `idp_password` and its `monitoring-` copies (ran). mysql-connector's are listed in
  [mysql-mariadb.md](mysql-mariadb.md).
- **Some flat settings fetch a login from elsewhere**: libpq `service` and `passfile` (and libpq reads
  `PGUSER`, `PGPASSWORD` and `~/.pgpass` by itself), and PyMySQL `read_default_file` (ran). Empty
  `username` and `password` do not mean "no login".

## How SQLAlchemy hands a login to the driver

- **The URL's login**: the psycopg and PyMySQL dialects turn the URL's user name and password into
  `user` and `password` (ran for the user name: `do_connect` saw `user='app_user'` for both).
- **`connect_args`** are laid over the URL's arguments and win
  (`src sqlalchemy/engine/create.py:633-634`).
- **`do_connect`**: SQLAlchemy's default connect function copies the arguments for each new
  connection, runs every `do_connect` listener on the copy, then calls the driver
  (`create.py:647-667`). A listener may change the copy, or return a finished connection
  (`src sqlalchemy/engine/events.py:833-869`).
  - Ran with SQLite: two new connections gave two calls, each starting from the original settings. A
    pooled connection that was reused gave no call. After `engine.dispose()`, a new connection gave a
    call again.
  - Ran with psycopg and PyMySQL against a closed port: each new connection got a new fake token as
    `password`, and no error message contained the token.
- **`creator=`**: `creator = pop_kwarg("creator", connect)` (`create.py:669`) replaces that whole
  function: URL arguments, `connect_args` and `do_connect` together. SQLAlchemy's own docs say "Usage
  of this function causes connection parameters specified in the URL argument to be bypassed"
  (`create.py:179-183`). The pool then calls the creator with or without its pool record
  (`src sqlalchemy/pool/base.py:330-361`).
  - Ran: URL `sqlite:///should-not-exist.db?timeout=9`, `connect_args={"bogus": 1, "password": ...}`
    and a `do_connect` listener, with a creator that returns an in-memory connection. The query
    worked. The file was never created, `bogus` raised nothing, and the listener never ran.
  - The dialect's own setup still ran (its `regexp` SQL function was there). A bad URL value still
    failed at `create_engine`, because the URL is still parsed (`create.py:633`).
- **`async_creator=`** does the same for async engines: "the parameters specified in the URL are not
  applied here" (`src sqlalchemy/ext/asyncio/engine.py:80-92`, `103-119`).
- **What `creator=` means for Ekbatan-py**: `username`, `password` and `data_source_properties` all
  travel through the URL and `connect_args`, so a creator drops all three silently. Either refuse them
  next to a creator, or pass them into it. The engine's pool settings still apply.
- **URL words that SQLAlchemy takes for itself**: `plugin` and `plugins` are removed from the URL;
  SQLAlchemy loads its own engine plugins from `plugin` (`src sqlalchemy/engine/url.py:737-759`).
  Ran: `?plugins=iam` vanished without an error. Empty URL values are dropped too (ran: `?a=&b=1` kept
  only `b`).
- **Ekbatan-py's migrator** always uses psycopg, PyMySQL or sqlite3, whatever driver the URL names
  (`src/ekbatan/_yoyo.py:562-567`). It builds the arguments with the dialect's `create_connect_args`
  and lays `connect_args` over them, with no engine (`_yoyo.py:597-608`). So it gets no `do_connect`,
  no `creator=` and no wrapper.

## 1. AWS Advanced Python Wrapper 3.1.0

A layer over a normal driver that adds Aurora and RDS features (fast failover, health monitoring,
read/write splitting) and logins: IAM, Secrets Manager, ADFS and Okta. On PyPI as
`aws-advanced-python-wrapper`; it pulls in boto3, OpenTelemetry, the AWS X-Ray SDK and requests.

Source paths in this section are inside `aws_advanced_python_wrapper/`, unless they name another
package.

### Databases and drivers

- Target drivers: psycopg 3 and mysql-connector-python, plus a "generic" fallback
  (`src aws_advanced_python_wrapper/driver_dialect_manager.py:48-52`, `driver_dialect_codes.py:15-18`).
  For async: psycopg's async connection and aiomysql, an async driver built on PyMySQL; the wrapper
  notes that "aiomysql raises pymysql's PEP-249 error classes" (`aio/driver_dialect/psycopg.py`,
  `aio/driver_dialect/aiomysql.py`, `sqlalchemy_dialects/mysql_async.py:212-216`).
- Databases: only MySQL and PostgreSQL kinds: `aurora-pg`, `rds-pg`, `multi-az-pg`,
  `global-aurora-pg`, `pg`, the same five for MySQL, plus `custom` and `unknown`
  (`database_dialect.py:60-75`). **Nothing for MariaDB.**
- **PyMySQL is not a target driver.** The wrapper picks the driver by looking for "mysql" in the name
  of the connect function (`mysql_driver_dialect.py:84-86`). So `pymysql.connect` is taken for
  mysql-connector-python (ran), whose code paths expect mysql-connector's connection classes.
- The wheel's README lists Aurora MySQL 3.07+, Aurora PostgreSQL 13.21/14.18/15.13/16.9/17.5+ and RDS
  PostgreSQL with rds_tools 1.7+ (`aws_advanced_python_wrapper-3.1.0.dist-info/METADATA:189-191`).

### How it plugs into SQLAlchemy

- Three SQLAlchemy URL names, registered as entry points
  (`aws_advanced_python_wrapper-3.1.0.dist-info/entry_points.txt`): `postgresql+aws_wrapper_psycopg`
  (sync and async), `mysql+aws_wrapper_mysqlconnector` and `mysql+aws_wrapper_aiomysql`. The first
  one ran (below).
- Each is SQLAlchemy's own dialect with the driver module swapped for the wrapper's
  (`sqlalchemy_dialects/pg.py:36-60`, `sqlalchemy_dialects/mysql.py:33-43`).
- Ran: `create_engine("postgresql+aws_wrapper_psycopg://appuser@127.0.0.1:1/appdb?wrapper_dialect=pg&connect_timeout=2", connect_args={"failover_timeout_sec": 5})`
  reached the wrapper with `wrapper_dialect` from the URL and `failover_timeout_sec` from
  `connect_args`. No `creator=` was needed.
- SQLAlchemy removes `plugins` from a URL, so the dialects accept `wrapper_plugins=` in the URL and
  rename it (`pg.py:77-87`, `mysql.py:45-52`). In `connect_args`, `plugins` works as it is (ran:
  `connect_args={"plugins": "iam"}` ran the IAM plugin).
- `creator=` still works, as in the wrapper's docs:
  `create_engine("postgresql+aws_wrapper_psycopg://", creator=lambda: connect("host=... dbname=db user=john password=pwd", wrapper_dialect="aurora-pg", plugins="failover,host_monitoring_v2"))`
  (docs: https://github.com/aws/aws-advanced-python-wrapper/blob/main/docs/using-the-python-wrapper/SqlAlchemySupport.md).
  Then the URL login and `connect_args` are ignored, as with any creator.
- The same page says to call `engine.dispose()` and then the wrapper's `release_resources()` at
  shutdown. It also says read/write splitting (`read_write_splitting`, `srw`) does not work under
  SQLAlchemy (docs).
- Without SQLAlchemy: `AwsWrapperConnection.connect(target, conninfo="", **kwargs)` takes the target
  driver's connect function, a libpq-style text and keyword arguments (`wrapper.py:157-187`).

### How its settings travel

- The connection text and the keyword arguments are merged into one flat dictionary
  (`utils/properties.py:752-760`).
- Before calling the driver, the wrapper removes its own settings and every key starting with
  `monitoring-` or `topology-monitoring-`. It keeps `user` and `password`, and passes every other key
  on (`properties.py:878-897`). Ran: from `user`, `password`, `plugins`, `iam_region`,
  `secrets_manager_secret_id`, `idp_password`, `sslmode`, `monitoring-connect_timeout` and
  `made_up_key`, the driver got `made_up_key`, `password`, `sslmode` and `user`.
- A few are renamed for the driver, such as `database` to `dbname` and `tcp_keepalive` to
  `keepalives` for psycopg (`pg_driver_dialect.py:181-213`).
- The wrapper converts values itself:
  - Numbers go through `int(...)` or `float(...)`. Ran: `iam_default_port="abc"` raised `ValueError`.
  - Most true/false settings use `get_bool`: a real bool is used as it is, but text counts as true
    **only** when it is `"true"` in any case. `"1"` and `"yes"` are false (`properties.py:74-77`; ran).
  - Four settings are read the plain Python way, so **any text, even `"false"`, is true**:
    `auto_sort_wrapper_plugin_order`, `limitless_wait_for_transaction_router_info`,
    `enable_telemetry` and `telemetry_submit_toplevel`. Ran: `enable_telemetry="false"` switched
    telemetry on, which then failed for lack of a backend. In a URL, every value is text.
- Keys with the prefix `monitoring-` or `topology-monitoring-` are copies for the wrapper's background
  connections. The prefix is stripped and the copy replaces the main value
  (`properties.py:748-749`, `933-950`). Ran: `monitoring-user` and `monitoring-password` replaced
  `user` and `password` for the monitoring connection. So a user name or a password can arrive under
  these names.

### Plugin codes (the values of `plugins`)

`plugins` is a comma list (`plugin_service.py:827-850`). The default is
`initial_connection,aurora_connection_tracker,failover_v2,host_monitoring_v2`; for mysql-connector it
leaves out `host_monitoring_v2` (`properties.py:94-95`, `plugin_service.py:925-930`). An unknown code
fails (`plugin_service.py:947-948`). Plugins are put in a fixed order unless
`auto_sort_wrapper_plugin_order` is off (`plugin_service.py:857-880`, `955-960`).

| code | what it does |
|---|---|
| `iam` | IAM token as the password |
| `aws_secrets_manager` | user and password from Secrets Manager |
| `federated_auth` | ADFS sign-in, then an IAM token |
| `okta` | Okta sign-in, then an IAM token |
| `failover`, `failover_v2` | Aurora failover |
| `gdb_failover` | failover for Aurora Global Database |
| `host_monitoring`, `host_monitoring_v2` | health monitoring |
| `read_write_splitting`, `srw`, `gdb_rw` | read/write splitting |
| `fastest_response_strategy` | pick the fastest reader |
| `stale_dns` | guard against stale DNS answers |
| `custom_endpoint` | Aurora custom endpoints |
| `initial_connection` | choose the host for the first connection |
| `aurora_connection_tracker` | track connections per instance |
| `limitless` | Aurora Limitless routers |
| `bg` | Blue/Green switchover |
| `connect_time`, `execute_time`, `dev` | timing and testing |

### The login plugins

- **`iam`** (`iam_plugin.py:80-160`): needs `user` (L81-83). The host must be an RDS or Aurora name;
  an IP address is refused before any AWS call (`utils/iam_utils.py:43-56`; ran:
  `Invalid IAM host 127.0.0.1`). `iam_host`, `iam_default_port` and `iam_region` override what it reads
  from the connection (`iam_utils.py:43-67`, `iam_plugin.py:93`). The token comes from boto3's `generate_db_auth_token`
  (`iam_utils.py:97-101`) and is reused for `iam_expiration` seconds (default 900). It becomes
  `password`; for mysql-connector the plugin also sets `auth_plugin=mysql_clear_password`
  (`mysql_driver_dialect.py:158-160`). After a failed login with a reused token, it makes a new token
  and tries once more (`iam_plugin.py:141-158`). Ran with an RDS-style host and no AWS credentials:
  `NoCredentialsError`, before any connection.
- **`aws_secrets_manager`** (`aws_secrets_manager_plugin.py`): needs `secrets_manager_secret_id`
  (L76-80). The region comes from `secrets_manager_region` or from the secret's ARN (L223-238). It
  calls `get_secret_value` (L194-207) and sets both `user` and `password` from the keys named by
  `secrets_manager_secret_username_key` and `secrets_manager_secret_password_key` (L209-221). The
  secret is reused for `secrets_manager_expiration` seconds (default one year) and fetched again
  after a failed login (L111-142).
- **`federated_auth`** (ADFS) (`federated_plugin.py`): signs in at
  `https://{idp_endpoint}:{idp_port}/adfs/ls/IdpInitiatedSignOn.aspx?loginToRp={rp_identifier}`
  (L253-263), filling the form with `idp_username` and `idp_password` (L295-316). The answer is traded
  with AWS STS for temporary AWS keys (`AssumeRoleWithSAML` with `iam_role_arn` and `iam_idp_arn`;
  `credentials_provider_factory.py:36-49`). Those make an IAM token for `db_user`, which also replaces
  `user` (L106, L124, L154-179). The token is reused for `iam_token_expiration` seconds (default 870).
- **`okta`** (`okta_plugin.py`): posts `idp_username` and `idp_password` to
  `https://{idp_endpoint}/api/v1/authn` (L189-214), then fetches the sign-in answer from the Okta app
  `app_id` (L216-242). The rest is as for ADFS.
- **Where the AWS keys come from**: a boto3 session, with `aws_profile` naming a profile if given
  (`aws_credentials_manager.py:34-49`). So boto3's usual places apply (see section 2). The wrapper has
  no way to take AWS keys from code.

### Every setting

`src :N` below means `aws_advanced_python_wrapper/utils/properties.py`, line N. "type" says how the
wrapper reads the value. No setting takes a dictionary, list, object or function.

**Basics and login**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `profile_name` | text | scalar | n/a | no | no | src :98 |
| `plugins` | text, comma list | scalar | n/a | no | no | ran, src :101 |
| `user` | text | scalar | n/a | yes | no | ran, src :104 |
| `password` | text | scalar | n/a | no | yes | ran, src :105 |
| `database` | text | scalar | n/a | no | no | src :106 |
| `connect_timeout` | number, to driver | scalar | n/a | no | no | src :108 |
| `socket_timeout` | number | scalar | n/a | no | no | src :112 |
| `tcp_keepalive` | number, to driver | scalar | n/a | no | no | src :116 |
| `tcp_keepalive_time` | number, to driver | scalar | n/a | no | no | src :119 |
| `tcp_keepalive_interval` | number, to driver | scalar | n/a | no | no | src :123 |
| `tcp_keepalive_probes` | number, to driver | scalar | n/a | no | no | src :127 |
| `transfer_session_state_on_switch` | bool (text: only "true") | scalar | n/a | no | no | src :131 |
| `reset_session_state_on_close` | bool (text: only "true") | scalar | n/a | no | no | src :136 |
| `rollback_on_switch` | bool (text: only "true") | scalar | n/a | no | no | src :141 |

**Cluster topology**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `topology_refresh_ms` | number | scalar | n/a | no | no | src :148 |
| `cluster_id` | text | scalar | n/a | no | no | src :154 |
| `cluster_instance_host_pattern` | text | scalar | n/a | no | no | src :160 |
| `global_cluster_instance_host_patterns` | text, comma list | scalar | n/a | no | no | src :167 |

**AWS login: IAM and Secrets Manager**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `aws_profile` | text | scalar | n/a | no | no | src :175 |
| `iam_host` | text | scalar | n/a | no | no | src :179 |
| `iam_default_port` | number | scalar | n/a | no | no | ran, src :182 |
| `iam_region` | text | scalar | n/a | no | no | ran, src :186 |
| `iam_expiration` | number | scalar | n/a | no | no | src :189 |
| `secrets_manager_secret_id` | text | scalar | n/a | no | no | ran, src :194 |
| `secrets_manager_secret_username_key` | text | scalar | n/a | no | no | src :197 |
| `secrets_manager_secret_password_key` | text | scalar | n/a | no | no | src :202 |
| `secrets_manager_region` | text | scalar | n/a | no | no | src :207 |
| `secrets_manager_endpoint` | text | scalar | n/a | no | no | src :210 |
| `secrets_manager_expiration` | number | scalar | n/a | no | no | src :213 |

**Database dialect**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `wrapper_dialect` | text | scalar | n/a | no | no | ran, src :219 |
| `auxiliary_query_timeout_sec` | number | scalar | n/a | no | no | src :222 |

**Host monitoring**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `failure_detection_enabled` | bool (text: only "true") | scalar | n/a | no | no | src :231 |
| `failure_detection_time_ms` | number | scalar | n/a | no | no | src :236 |
| `failure_detection_interval_ms` | number | scalar | n/a | no | no | src :241 |
| `failure_detection_count` | number | scalar | n/a | no | no | src :246 |
| `monitor_disposal_time_ms` | number | scalar | n/a | no | no | src :251 |

**Failover**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `enable_failover` | bool (text: only "true") | scalar | n/a | no | no | ran, src :258 |
| `failover_mode` | text | scalar | n/a | no | no | src :261 |
| `failover_timeout_sec` | number | scalar | n/a | no | no | ran, src :265 |
| `failover_cluster_topology_refresh_rate_sec` | number | scalar | n/a | no | no | src :270 |
| `failover_writer_reconnect_interval_sec` | number | scalar | n/a | no | no | src :277 |
| `failover_reader_connect_timeout_sec` | number | scalar | n/a | no | no | src :282 |
| `failover_reader_host_selector_strategy` | text | scalar | n/a | no | no | src :289 |
| `enable_connect_failover` | bool (text: only "true") | scalar | n/a | no | no | src :293 |
| `failover_home_region` | text | scalar | n/a | no | no | src :299 |
| `active_home_failover_mode` | text | scalar | n/a | no | no | src :305 |
| `inactive_home_failover_mode` | text | scalar | n/a | no | no | src :312 |
| `cluster_topology_high_refresh_rate_ms` | number | scalar | n/a | no | no | src :320 |

**Custom endpoints**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `custom_endpoint_info_refresh_rate_ms` | number | scalar | n/a | no | no | src :326 |
| `custom_endpoint_idle_monitor_expiration_ms` | number | scalar | n/a | no | no | src :331 |
| `wait_for_custom_endpoint_info` | bool (text: only "true") | scalar | n/a | no | no | src :336 |
| `wait_for_custom_endpoint_info_timeout_ms` | number | scalar | n/a | no | no | src :344 |

**Host availability**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `default_host_availability_strategy` | text | scalar | n/a | no | no | src :352 |
| `host_availability_strategy_max_retries` | number | scalar | n/a | no | no | src :358 |
| `host_availability_strategy_initial_backoff_time` | number | scalar | n/a | no | no | src :364 |

**Driver dialect**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `wrapper_driver_dialect` | text | scalar | n/a | no | no | src :371 |

**Host selection and plugin order**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `reader_host_selector_strategy` | text | scalar | n/a | no | no | src :376 |
| `auto_sort_wrapper_plugin_order` | bool (text "false" = on) | scalar | n/a | no | no | ran, src :383 |
| `round_robin_default_weight` | number | scalar | n/a | no | no | src :391 |
| `round_robin_host_weight_pairs` | text, comma list | scalar | n/a | no | no | src :398 |
| `weighted_random_default_weight` | number | scalar | n/a | no | no | src :404 |
| `weighted_random_host_weight_pairs` | text, comma list | scalar | n/a | no | no | src :411 |

**Federated login: ADFS and Okta**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `idp_endpoint` | text | scalar | n/a | no | no | src :418 |
| `idp_port` | number | scalar | n/a | no | no | src :422 |
| `rp_identifier` | text | scalar | n/a | no | no | src :426 |
| `iam_role_arn` | text | scalar | n/a | no | no | src :430 |
| `iam_idp_arn` | text | scalar | n/a | no | no | src :434 |
| `iam_token_expiration` | number | scalar | n/a | no | no | src :438 |
| `idp_username` | text | scalar | n/a | yes | no | ran, src :442 |
| `idp_password` | text | scalar | n/a | no | yes | ran, src :444 |
| `http_request_connect_timeout` | number | scalar | n/a | no | no | src :446 |
| `ssl_secure` | bool (text: only "true") | scalar | n/a | no | no | src :452 |
| `idp_name` | text | scalar | n/a | no | no | src :459 |
| `db_user` | text | scalar | n/a | yes | no | src :463 |
| `app_id` | text | scalar | n/a | no | no | src :469 |

**Fastest response**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `response_measurement_interval_ms` | number | scalar | n/a | no | no | src :474 |

**Limitless**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `limitless_transaction_router_monitor_disposal_time_ms` | number | scalar | n/a | no | no | src :481 |
| `limitless_transaction_router_monitor_interval_ms` | number | scalar | n/a | no | no | src :488 |
| `limitless_wait_for_transaction_router_info` | bool (text "false" = on) | scalar | n/a | no | no | src :494 |
| `limitless_get_transaction_router_retry_interval_ms` | number | scalar | n/a | no | no | src :503 |
| `limitless_get_transaction_router_max_retries` | number | scalar | n/a | no | no | src :509 |
| `limitless_max_retries_ms` | number | scalar | n/a | no | no | src :515 |

**Blue/Green**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `bg_connect_timeout_ms` | number | scalar | n/a | no | no | src :522 |
| `bg_id` | text | scalar | n/a | no | no | src :527 |
| `bg_interval_baseline_ms` | number | scalar | n/a | no | no | src :532 |
| `bg_interval_increased_ms` | number | scalar | n/a | no | no | src :537 |
| `bg_interval_high_ms` | number | scalar | n/a | no | no | src :542 |
| `bg_switchover_timeout_ms` | number | scalar | n/a | no | no | src :547 |
| `bg_suspend_new_blue_connections` | bool (text: only "true") | scalar | n/a | no | no | src :552 |

**Telemetry**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `enable_telemetry` | bool (text "false" = on) | scalar | n/a | no | no | ran, src :560 |
| `telemetry_submit_toplevel` | bool (text "false" = on) | scalar | n/a | no | no | src :564 |
| `telemetry_traces_backend` | text | scalar | n/a | no | no | src :570 |
| `telemetry_metrics_backend` | text | scalar | n/a | no | no | src :576 |
| `telemetry_failover_additional_top_trace` | bool (text: only "true") | scalar | n/a | no | no | src :582 |

**First connection and role checks**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `reader_initial_connection_host_selector_strategy` | text | scalar | n/a | no | no | src :589 |
| `initial_connection_host_selector_strategy` | text | scalar | n/a | no | no | src :595 |
| `endpoint_substitution_role` | text | scalar | n/a | no | no | src :601 |
| `inactive_cluster_writer_endpoint_substitution_role` | text | scalar | n/a | no | no | src :609 |
| `verify_opened_connection_type` | text | scalar | n/a | no | no | src :617 |
| `verify_inactive_cluster_writer_endpoint_connection_type` | text | scalar | n/a | no | no | src :624 |
| `open_connection_retry_timeout_ms` | number | scalar | n/a | no | no | src :631 |
| `open_connection_retry_interval_ms` | number | scalar | n/a | no | no | src :637 |
| `wait_for_initial_topology_ms` | number | scalar | n/a | no | no | src :643 |

**Simple read/write splitting**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `srw_read_endpoint` | text | scalar | n/a | no | no | src :657 |
| `srw_write_endpoint` | text | scalar | n/a | no | no | src :663 |
| `srw_verify_new_connections` | bool (text: only "true") | scalar | n/a | no | no | src :669 |
| `srw_verify_initial_connection_type` | text | scalar | n/a | no | no | src :675 |
| `srw_connect_retry_timeout_ms` | number | scalar | n/a | no | no | src :682 |
| `srw_connect_retry_interval_ms` | number | scalar | n/a | no | no | src :688 |

**Global database**

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `gdb_rw_home_region` | text | scalar | n/a | no | no | src :695 |
| `gdb_rw_restrict_writer_to_home_region` | bool (text: only "true") | scalar | n/a | no | no | src :701 |
| `gdb_rw_restrict_reader_to_home_region` | bool (text: only "true") | scalar | n/a | no | no | src :707 |
| `gdb_enable_global_write_forwarding` | bool (text: only "true") | scalar | n/a | no | no | src :713 |
| `gdb_accessible_regions` | text, comma list | scalar | n/a | no | no | src :720 |
| `monitoring_connection_priority` | text, comma list | scalar | n/a | no | no | src :728 |
| `gdb_monitoring_connection_priority` | text, comma list | scalar | n/a | no | no | src :737 |

- `user`, `password`, `database`, `connect_timeout` and the `tcp_keepalive*` settings are passed on to
  the driver, renamed where needed. Every unknown key is passed on unchanged.
- `profile_name` picks a set of plugins registered in code (see below). It is the flat way to reach
  code-only configuration.
- `secrets_manager_secret_username_key` and `secrets_manager_secret_password_key` hold key names, not
  secrets. `secrets_manager_secret_id` is a name or ARN, not a secret.
- `idp_password` is posted to the company sign-in page. `password` is replaced by a token or by the
  secret's password whenever a login plugin is on.
- `reader_initial_connection_host_selector_strategy` is marked deprecated in favour of
  `initial_connection_host_selector_strategy` (`properties.py:588`).

### Code-only hooks (not settings)

These take functions, classes or objects, and are set in Python, not through connection settings:

- the target driver's connect function, the first argument of `AwsWrapperConnection.connect`
  (`wrapper.py:157-167`). The wrapper's SQLAlchemy dialects fill it in.
- the wrapper's own connection pools:
  `ConnectionProviderManager.set_connection_provider(SqlAlchemyPooledConnectionProvider(pool_configurator=..., pool_mapping=..., accept_url_func=...))`,
  three functions (`connection_provider.py:158`, `sql_alchemy_connection_provider.py:60-66`).
- configuration profiles: `DriverConfigurationProfiles.add_or_replace_profile(name, [plugin factories])`
  (`driver_configuration_profiles.py:31-32`), chosen by the flat `profile_name`
  (`plugin_service.py:918-923`).
- custom plugins: `PluginManager.register_plugin(code, factory, weight)` (`plugin_service.py:909-912`).
- a custom driver dialect: `DriverDialectManager.set_custom_dialect(...)`
  (`driver_dialect_manager.py:63-64`).

### Surprises

- **It logs tokens.** At DEBUG level it writes `Generated new authentication token = <token>`
  (`iam_utils.py:105`; the text is in `resources/aws_advanced_python_wrapper_messages.properties:289`).
  Ran with a stand-in boto3 client that returned a fake token: the token appeared in the log line.
  Reused tokens are logged too (`iam_plugin.py:112`, `federated_plugin.py:119`, `okta_plugin.py:115`;
  message lines 286, 220 and 224).
- Its own log masking hides only keys that contain `password` or `passwd` (`properties.py:916-930`).
  Ran: `sslpassword` and `idp_password` were hidden; `oauth_client_secret` was not.
- **ADFS/Okta fallback bug**: when `idp_username` or `idp_password` is missing, it fills in the literal
  texts `"user"` and `"password"`, not the values of `user` and `password` (`utils/saml_utils.py:37-41`;
  ran).
- For IAM over aiomysql it builds a TLS connection **without checking the server's certificate**,
  unless `ssl` or `ssl_ca` is given (`aio/auth_plugins.py:270-302`).
- SQLAlchemy's own psycopg dialect adds an object, `context`, to the driver arguments
  (`src sqlalchemy/dialects/postgresql/psycopg.py:456-457`; ran: seen in `do_connect`). It reaches the
  wrapper too. So the final driver arguments are not all flat, even when the user's are.

### What it means for Ekbatan-py

- `postgresql+aws_wrapper_psycopg://...` passes Ekbatan-py's URL check today, which reads only the part
  before `+` (`src/ekbatan/config.py:104`; read, not run).
- `username` → `user`: IAM needs it; Secrets Manager, ADFS and Okta overwrite it.
- `password` → `password`: leave it empty with a login plugin, which replaces it.
- `data_source_properties` → `connect_args`: every wrapper setting is flat, so the planned rules hold.
  Put `plugins` here, not in the URL. Use real booleans: the text `"false"` means "on" for four
  settings.
- Names that carry a user name or a secret besides `user` and `password`: `db_user`, `idp_username`,
  `idp_password`, and `monitoring-` or `topology-monitoring-` copies of anything.
- MySQL through the wrapper means mysql-connector-python instead of PyMySQL. Its own dictionary and
  list settings are in [mysql-mariadb.md](mysql-mariadb.md).
- **The migrator breaks.** It would call plain psycopg with the wrapper's settings. A replica of
  `_yoyo.py:597-608` for `postgresql+aws_wrapper_psycopg://...?wrapper_dialect=aurora-pg` with
  `connect_args={"plugins": "iam", "iam_region": ...}` gave
  `ProgrammingError: invalid connection option "wrapper_dialect"`. And even then, migrations would not
  get the IAM or Secrets Manager login.

## 2. RDS IAM login with a plain driver (boto3)

RDS and Aurora accept a short-lived signed token instead of a password. From the AWS docs: "IAM
database authentication works with MariaDB, MySQL, and PostgreSQL", "Each token has a lifetime of
15 minutes", and "The token is only used for authentication and doesn't affect the session after it is
established". A token is "generally about 1 KB but can be larger"
(docs: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.IAMDBAuth.html).

**Databases and drivers**: PostgreSQL (psycopg), MySQL and MariaDB (PyMySQL), on RDS and Aurora.

### How the token is made

- `boto3.client("rds").generate_db_auth_token(DBHostname, Port, DBUsername, Region=None)` (ran:
  signature). botocore adds it to the RDS client (`src botocore/handlers.py:1732`,
  `botocore/signers.py:515-566`).
- It signs a `connect` request for that user with the caller's AWS keys. The signature is valid for
  `expires_in=900` seconds, fixed (`signers.py:563`), under the name `rds-db` (L564). The result is the
  signed link without `https://` (L566).
- **It runs on the machine; nothing is sent to AWS.** Ran with credential lookup switched off:
  `NoCredentialsError: Unable to locate credentials`, at once.
- Aurora DSQL (PostgreSQL-compatible) has its own two functions, `generate_db_connect_auth_token` and
  `generate_db_connect_admin_auth_token`, whose `ExpiresIn` defaults to 900 (`signers.py:569-659`; ran:
  both exist on a `dsql` client).
- **Where the AWS keys come from**, in order: environment variables, a role named in the config, a
  web-identity token file, SSO, `~/.aws/credentials`, a login cache, an external `credential_process`,
  `~/.aws/config`, two legacy files, the container's metadata (ECS/EKS) and the EC2 machine's metadata
  (`src botocore/credentials.py:138-152`, `205-216`). From the docs: temporary credentials "must still
  be valid when using the IAM database authentication token to make a connection request".

### How it plugs into SQLAlchemy

A token goes into `password` for each new connection, through `do_connect`:

```python
import boto3
from sqlalchemy import create_engine, event

rds = boto3.client("rds", region_name="eu-west-1")
engine = create_engine(
    "postgresql+psycopg://app_user@mydb.abc123.eu-west-1.rds.amazonaws.com:5432/app",
    connect_args={"sslmode": "verify-full", "sslrootcert": "/etc/ssl/rds-global-bundle.pem"},
)

@event.listens_for(engine, "do_connect")
def iam_token(dialect, conn_rec, cargs, cparams):
    cparams["password"] = rds.generate_db_auth_token(
        DBHostname=cparams["host"], Port=int(cparams.get("port", 5432)), DBUsername=cparams["user"]
    )
```

Not run against AWS. The same pattern ran with a fake token for psycopg and PyMySQL (see
[How SQLAlchemy hands a login to the driver](#how-sqlalchemy-hands-a-login-to-the-driver)).

- psycopg: TLS through `sslmode` and `sslrootcert`
  (docs: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.IAMDBAuth.Connecting.Python.html).
  For a PostgreSQL user with the `rds_iam` role, "IAM authentication takes precedence over password
  authentication" (docs, first page above).
- PyMySQL: the server asks for `mysql_clear_password`, and PyMySQL answers with the token
  (`src pymysql/connections.py:1058-1060`) **without checking for TLS** (ran with the fake server).
  Give `ssl_ca`, and `ssl_verify_identity=True`. PyMySQL 1.2.0 tries TLS by itself when no TLS
  setting is given, but falls back to plain text if the server does not offer it
  (`connections.py:298-303`).

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `password` (the token) | text, ~1 KB | scalar | n/a | no | yes | ran (fake) |
| `user` | text | scalar | n/a | yes | no | ran |
| `DBHostname` | text | scalar | n/a | no | no | src signers.py:515 |
| `Port` | integer | scalar | n/a | no | no | src signers.py:515 |
| `DBUsername` | text | scalar | n/a | yes | no | src signers.py:515 |
| `Region` | text | scalar | n/a | no | no | src signers.py:515 |
| `sslmode`, `sslrootcert` | text | scalar | n/a | no | no | docs |
| `ssl_ca`, `ssl_verify_identity` | text, bool | scalar | n/a | no | no | src connections.py:276-290 |
| `AWS_ACCESS_KEY_ID` (env) | text | scalar | n/a | yes (key ID) | no | src credentials.py:1188 |
| `AWS_SECRET_ACCESS_KEY` (env) | text | scalar | n/a | no | yes | src credentials.py:1189 |
| `AWS_SESSION_TOKEN` (env) | text | scalar | n/a | no | yes | src credentials.py:1192 |
| `AWS_PROFILE` (env) | text | scalar | n/a | no | no | src configprovider.py:73 |
| `AWS_DEFAULT_REGION` (env) | text | scalar | n/a | no | no | src configprovider.py:74 |

- `sslmode` and `sslrootcert` are psycopg's; `ssl_ca` and `ssl_verify_identity` are PyMySQL's.
- `DBUsername` must be the same name the driver logs in with (`user`).
- Nothing new reaches the driver: the token only fills `password`.

### What it means for Ekbatan-py

- `username`: the database user. It is also the `DBUsername` the token is made for.
- `password`: empty in the config. A hook fills it for each new connection. A fixed token would stop
  working for new connections after 15 minutes.
- `data_source_properties`: only TLS settings, all flat.
- A code hook is needed (`do_connect`, or a password function that Ekbatan-py calls). The migrator must
  use it too.

## 3. AWS Secrets Manager without the wrapper (boto3)

Secrets Manager stores the database user name and password, and can change the password on a schedule
("rotation").

**Databases and drivers**: all three databases, any driver.

- `boto3.client("secretsmanager").get_secret_value(SecretId=...)`. Ran, from botocore's service model:
  the input is `SecretId` (required), `VersionId` and `VersionStage`; the output is `ARN`, `Name`,
  `VersionId`, `SecretBinary`, `SecretString`, `VersionStages` and `CreatedDate`. `SecretString` is
  marked sensitive. Ran with no credentials: `NoCredentialsError`.
- An RDS secret is JSON, for example
  `{"engine": "postgres", "host": "...", "username": "...", "password": "...", "dbname": "...", "port": 5432}`,
  with the same keys for `mariadb` and `mysql`
  (docs: https://docs.aws.amazon.com/secretsmanager/latest/userguide/reference_secret_json_structure.html).
- Caching: `aws-secretsmanager-caching` offers `SecretCache(config, client)` with
  `get_secret_string`, `get_secret_binary` and `refresh_secret_now` (ran). It refreshes every 3600
  seconds by default (`src aws_secretsmanager_caching/config.py:56`).

### How it plugs into SQLAlchemy

- **Read once at start**: then it is a plain user name and password. After a rotation, new connections
  fail until the application restarts.
- **Read in `do_connect`** from the cache, setting both `cparams["user"]` and `cparams["password"]`, and
  force a fresh read after a failed login. That is what the wrapper's plugin does
  (`aws_secrets_manager_plugin.py:128-142`).

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `SecretId` | text (name or ARN) | scalar | n/a | no | no | ran |
| `VersionId`, `VersionStage` | text | scalar | n/a | no | no | ran |
| `SecretString` | text (JSON) | scalar | n/a | yes | yes | ran |
| `username` (in the JSON) | text | scalar | n/a | yes | no | docs |
| `password` (in the JSON) | text | scalar | n/a | no | yes | docs |
| `host`, `port`, `dbname`, `engine` | text, integer | scalar | n/a | no | no | docs |
| `secret_refresh_interval` | integer (seconds) | scalar | n/a | no | no | src config.py:56 |

- `SecretString` is one text that holds JSON, so it holds both the user name and the password.
- The AWS keys come from boto3's usual places (section 2).

### What it means for Ekbatan-py

- It fills `username` and `password`, nothing else. Nothing goes in `data_source_properties`.
- With rotation it needs the same kind of code hook as a token, applied to the migrator too.

## 4. Google Cloud SQL Python Connector 1.22.0

Google's library for Cloud SQL. It opens an encrypted connection to an instance by its name, using
short-lived certificates, and can log in with IAM.

### Databases and drivers

- `connector.connect(..., driver)` accepts `pymysql`, `pg8000`, `asyncpg`, `pytds` and `psycopg`
  (`src google/cloud/sql/connector/connector.py:361-373`). `asyncpg`, `pg8000` and `psycopg` must point
  at PostgreSQL, `pymysql` at MySQL, `pytds` at SQL Server (`enums.py:60-67`). Cloud SQL offers no
  MariaDB.
- psycopg cannot take a ready-made socket, so the connector opens a temporary Unix socket and relays
  the bytes to its encrypted connection (`psycopg.py:120-224`). The docstrings still list only
  pymysql, pg8000 and pytds (`connector.py:239`).

### How it plugs into SQLAlchemy: `creator=` only

- No SQLAlchemy URL name: the package registers no entry points (ran).
- The documented pattern (not run):

  ```python
  from google.cloud.sql.connector import Connector
  connector = Connector()
  def getconn():
      return connector.connect("project:region:instance", "pymysql",
                               user="app", password="...", db="app")
  engine = create_engine("mysql+pymysql://", creator=getconn)
  ```

- Async engines use `async_creator=` with `connector.connect_async(...)` (`connector.py:263`).
- So the URL login, `connect_args` and `do_connect` are all skipped (see
  [How SQLAlchemy hands a login to the driver](#how-sqlalchemy-hands-a-login-to-the-driver)).

### Settings

The `Connector(...)` constructor (`connector.py:63-77`) and `connect(instance_connection_string,
driver, **kwargs)` (`connector.py:222-261`). Per call, the connector takes `ip_type`, `enable_iam_auth`
and `timeout` out of `kwargs` (`connector.py:323`, `375-379`), drops `host`, `port` and `ssl`
(L383-385), and passes the rest to the driver. "ran (sig.)" means the setting and its default were
printed from the signature by a run.

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `ip_type` | text or enum | scalar | n/a | no | no | ran (sig.) |
| `enable_iam_auth` | bool | scalar | n/a | no | no | ran (sig.) |
| `timeout` | integer (seconds) | scalar | n/a | no | no | ran (sig.) |
| `credentials` | google-auth object | object | environment | no | yes | ran |
| `loop` | event loop | object | not needed | no | no | ran (sig.) |
| `quota_project` | text | scalar | n/a | no | no | ran (sig.) |
| `sqladmin_api_endpoint` | text | scalar | n/a | no | no | ran (sig.) |
| `user_agent` | text | scalar | n/a | no | no | ran (sig.) |
| `universe_domain` | text | scalar | n/a | no | no | ran (sig.) |
| `refresh_strategy` | text or enum | scalar | n/a | no | no | ran (sig.) |
| `resolver` | class | object | none | no | no | ran (sig.) |
| `failover_period` | integer (seconds) | scalar | n/a | no | no | ran (sig.) |
| `instance_connection_string` | text | scalar | n/a | no | no | src connector.py:222 |
| `driver` | text | scalar | n/a | no | no | src connector.py:361 |
| `user` | text | scalar | n/a | yes | no | src pg8000.py:50 |
| `password` | text | scalar | n/a | no | yes | src pymysql.py:50 |
| `db` | text | scalar | n/a | no | no | src pg8000.py:51 |

- `ip_type`: `PUBLIC` (default), `PRIVATE` or `PSC`. `refresh_strategy`: `BACKGROUND` (default) or
  `LAZY` (ran: the enum values).
- `credentials` must be a `google.auth.credentials.Credentials` object (`connector.py:164-172`).
  Without it, Google's default credentials are looked up (`connector.py:173-175`):
  `GOOGLE_APPLICATION_CREDENTIALS`, gcloud's files, App Engine, then the Compute Engine metadata server
  (`src google/auth/_default.py:704-712`). Ran with all of them empty:
  `DefaultCredentialsError: Your default credentials were not found`.
- `loop` is created by the connector when not given. `resolver` is `DefaultResolver` or `DnsResolver`.
- `quota_project` and `universe_domain` also come from `GOOGLE_CLOUD_QUOTA_PROJECT` and
  `GOOGLE_CLOUD_UNIVERSE_DOMAIN` (`connector.py:188-196`).
- Each driver shim renames a little: for pymysql, `db` becomes `database` and `timeout` becomes
  `connect_timeout` (`pymysql.py:49-60`); for psycopg, `db` becomes `dbname` and `sslmode` is dropped
  (`psycopg.py:189-209`).

### IAM login

- `enable_iam_auth=True`, on the `Connector` or per call. The connector puts the caller's OAuth 2.0
  token into its request for the short-lived certificate (`client.py:252-255`), and the certificate
  ends when the token ends (`client.py:283-290`). No password is sent. The connector also shortens the
  user name to fit the database (`connector.py:430-439`).
- The user name is "the full email address of the user. For a service account, this is the service
  account's email without the `.gserviceaccount.com` domain suffix"
  (docs: https://docs.cloud.google.com/sql/docs/postgres/iam-logins).
- Without the connector there is a manual IAM login: an OAuth 2.0 access token as the password (docs,
  same page). That again needs a `do_connect` hook. The page does not give the token's lifetime.
- Without `creator=`: the Cloud SQL Auth Proxy, a separate program next to the application, gives a
  local address for a plain URL with psycopg or PyMySQL, and can do the IAM login itself
  (docs: https://docs.cloud.google.com/sql/docs/postgres/sql-proxy). Not checked.

### What it means for Ekbatan-py

- **`creator=` only.** Ekbatan-py would have to build the function itself and call
  `connector.connect(instance, driver, user=username, password=password, db=..., **data_source_properties)`.
- `username` → `user` (with IAM: the email). `password` → `password` (empty with IAM).
- `data_source_properties` would share one keyword list with the connector's own `ip_type`,
  `enable_iam_auth` and `timeout`. Flat values work.
- The `Connector` itself takes a credentials object, an event loop and a resolver class: **settings
  with no flat form**. The credentials can come from the environment instead.
- Ekbatan-py's PostgreSQL driver (psycopg) is supported, through the local relay socket.

## 5. Azure Database for PostgreSQL and MySQL with Microsoft Entra ID

Azure's managed PostgreSQL and MySQL accept a Microsoft Entra ID access token as the password.

### Databases and drivers

- Azure Database for PostgreSQL flexible server, with psycopg. Microsoft names libpq-based clients as
  tested (docs: https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/how-to-configure-sign-in-azure-ad-authentication).
- Azure Database for MySQL flexible server, with PyMySQL (ran: PyMySQL sends the token as clear text
  when asked). Microsoft lists "Connector/Python: Supported" and says to send the password as clear
  text (docs: https://learn.microsoft.com/en-us/azure/mysql/flexible-server/how-to-azure-ad).
- MariaDB: none. Azure Database for MariaDB was retired on 19 September 2025
  (docs: https://learn.microsoft.com/en-us/lifecycle/products/azure-database-for-mariadb).

### How the token is made

- `DefaultAzureCredential().get_token("https://ossrdbms-aad.database.windows.net/.default").token`.
  Other Azure clouds use other domains (docs, MySQL page).
- "The access token is valid for 5 to 60 minutes" (docs, both pages).
- `get_token` reuses its cached token. It asks for a new one when the token has expired or is within 5
  minutes of expiring (`src azure/identity/_internal/utils.py:36-66`, `_constants.py:10-11`). So calling
  it for every new connection is cheap.
- `DefaultAzureCredential` tries, in order: environment variables, workload identity (a token file),
  managed identity (the Azure machine's metadata endpoint), the shared token cache, VS Code, Azure CLI,
  Azure PowerShell, Azure Developer CLI and a broker. Interactive browser login is off by default
  (`azure/identity/_credentials/default.py:264-313`; ran: the chain was listed without asking for a
  token). `AZURE_TOKEN_CREDENTIALS` can narrow the chain (`default.py:246-247`).

### How it plugs into SQLAlchemy

A token goes into `password` for each new connection, through `do_connect`:

```python
from azure.identity import DefaultAzureCredential
from sqlalchemy import event

credential = DefaultAzureCredential()

@event.listens_for(engine, "do_connect")
def entra_token(dialect, conn_rec, cargs, cparams):
    cparams["password"] = credential.get_token(
        "https://ossrdbms-aad.database.windows.net/.default"
    ).token
```

Not run against Azure; the same pattern ran with a fake token.

- The user name is "the userPrincipalName of the Microsoft Entra user" (for example
  `user@tenant.onmicrosoft.com`) or a group name, and it is case-sensitive (docs).
- PostgreSQL: the docs' example uses `sslmode=require`. MySQL: the token must reach the server as
  clear text. PyMySQL sends it that way when the server asks, even without TLS (ran), so give `ssl_ca`.

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `password` (the token) | text | scalar | n/a | no | yes | docs |
| `user` | text | scalar | n/a | yes | no | docs |
| scope | text | scalar | n/a | no | no | docs |
| `managed_identity_client_id` | text | scalar | n/a | no | no | ran |
| `exclude_..._credential` (10) | bool | scalar | n/a | no | no | ran |
| `AZURE_CLIENT_ID` (env) | text | scalar | n/a | yes (app ID) | no | src _constants.py:44 |
| `AZURE_TENANT_ID` (env) | text | scalar | n/a | no | no | src _constants.py:46 |
| `AZURE_CLIENT_SECRET` (env) | text | scalar | n/a | no | yes | src _constants.py:45 |
| `AZURE_CLIENT_CERTIFICATE_PATH` (env) | text | scalar | n/a | no | no | src _constants.py:49 |
| `AZURE_CLIENT_CERTIFICATE_PASSWORD` (env) | text | scalar | n/a | no | yes | src _constants.py:50 |
| `AZURE_USERNAME` (env) | text | scalar | n/a | yes | no | src _constants.py:54 |
| `AZURE_PASSWORD` (env) | text | scalar | n/a | no | yes | src _constants.py:55 |
| `AZURE_FEDERATED_TOKEN_FILE` (env) | text | scalar | n/a | no | no | src _constants.py:70 |
| `AZURE_TOKEN_CREDENTIALS` (env) | text | scalar | n/a | no | no | src _constants.py:72 |

- `managed_identity_client_id` and the ten `exclude_*_credential` flags are `DefaultAzureCredential`
  keyword arguments (ran: read from its docstring). `_constants.py` is `azure/identity/_constants.py`.
- Nothing new reaches the driver: the token only fills `password`.

### What it means for Ekbatan-py

- `username`: the Entra name. `password`: empty; a hook fills it. `data_source_properties`: TLS
  settings only.
- The same code hook as the RDS IAM token is needed, and the migrator must use it too.

## 6. Kerberos, client certificates, MariaDB PAM and ed25519, MySQL login plugins

### PostgreSQL through psycopg (libpq)

psycopg passes these settings to libpq as text. The list and libpq's `*` (password) and `D` (debug)
marks come from the bundled libpq 18.6 (ran: `psycopg.pq.Conninfo.get_defaults()`). Every libpq setting
is covered in [postgresql.md](postgresql.md); these are the ones about logging in.

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `user` | text | scalar | n/a | yes | no | ran |
| `password` | text (`*`) | scalar | n/a | no | yes | ran |
| `passfile` | text (path) | scalar | n/a | no | no | ran |
| `service` | text | scalar | n/a | no | no | ran |
| `require_auth` | text, comma list | scalar | n/a | no | no | ran |
| `channel_binding` | text | scalar | n/a | no | no | ran |
| `gssencmode` | text | scalar | n/a | no | no | ran |
| `krbsrvname` | text | scalar | n/a | no | no | ran |
| `gsslib` | text | scalar | n/a | no | no | ran |
| `gssdelegation` | `0` or `1` | scalar | n/a | no | no | ran |
| `sslmode` | text | scalar | n/a | no | no | ran |
| `sslcert` | text (path) | scalar | n/a | no | no | ran |
| `sslkey` | text (path) | scalar | n/a | no | no | ran |
| `sslpassword` | text (`*`) | scalar | n/a | no | yes | ran |
| `sslcertmode` | text | scalar | n/a | no | no | ran |
| `sslrootcert` | text (path) | scalar | n/a | no | no | ran |
| `sslcrl`, `sslcrldir` | text (path) | scalar | n/a | no | no | ran |
| `sslnegotiation` | text | scalar | n/a | no | no | ran |
| `requirepeer` | text | scalar | n/a | yes | no | ran |
| `oauth_issuer` | text (URL) | scalar | n/a | no | no | ran |
| `oauth_client_id` | text | scalar | n/a | no | no | ran |
| `oauth_client_secret` | text (`*`) | scalar | n/a | no | yes | ran |
| `oauth_scope` | text | scalar | n/a | no | no | ran |
| `scram_client_key` | text (`D`) | scalar | n/a | no | yes | ran |
| `scram_server_key` | text (`D`) | scalar | n/a | no | yes | ran |

- **Kerberos**: psycopg-binary 3.3.5 ships MIT Kerberos and OpenLDAP libraries next to libpq (ran:
  `psycopg_binary/.dylibs` holds `libgssapi_krb5`, `libkrb5` and `libldap`). `gssencmode=require` failed
  with `GSSAPI encryption required but no credential cache`, so Kerberos is built in (ran). The bundled
  default for `gssencmode` is `disable` (ran). The login comes from the Kerberos ticket cache (`kinit`,
  `KRB5CCNAME`), so there is no password. `gsslib` matters only on Windows
  (docs: https://www.postgresql.org/docs/18/libpq-connect.html).
- `require_auth` (for example `scram-sha-256` or `!password`) refuses login methods the client does not
  want. A bad value fails when connecting: `invalid require_auth value: "bogus"` (ran).
- Client certificates: `sslcert` and `sslkey` are file paths, and `sslpassword` unlocks the key. By
  default libpq looks for `~/.postgresql/postgresql.crt` and `.key` (docs, same page).
- `requirepeer` holds the operating-system user of the server process (for Unix sockets), not a login.
- OAuth (PostgreSQL 18): the four `oauth_*` settings are known to the bundled libpq (ran: listed, and
  accepted when connecting to a closed port). The PostgreSQL docs call libpq's built-in OAuth device
  flow "an optional module" that needs libcurl when libpq is built
  (docs: https://www.postgresql.org/docs/18/libpq-oauth.html). The wheel ships no `libcurl` or
  `libpq-oauth` library (ran: `.dylibs` listing), so the built-in flow is probably missing. Not
  tested.
- `scram_client_key` and `scram_server_key` are marked `D`, not `*`, but hold keys (see postgresql.md).
- `service` and `passfile` read a user name and password from files, and libpq also reads `PGUSER` and
  `PGPASSWORD` by itself. Ran: a `pg_service.conf` entry filled `user` and `password`, and so did the
  two environment variables, with nothing passed in.
- Other PostgreSQL drivers: pg8000 supports only clear-text, MD5 and SCRAM passwords. Kerberos and SSPI
  fail with "Authentication method ... not supported by pg8000" (`src pg8000/core.py:626-628`). asyncpg
  0.32.0 has `krbsrvname` and `gsslib` (ran: signature) and accepts a function as `password`
  (`src asyncpg/connect_utils.py:1070-1073`).

### PyMySQL 1.2.0 (Ekbatan-py's MySQL and MariaDB driver)

`src :N` below means `pymysql/connections.py`, line N.

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `user` | text | scalar | n/a | yes | no | ran |
| `password` | text or bytes | scalar | n/a | no | yes | ran |
| `passwd` | text or bytes | scalar | n/a (alias) | no | yes | ran |
| `auth_plugin_map` | dict: name → class | dict | none | no | no | src :153-157, 199 |
| `server_public_key` | text or bytes (PEM) | scalar | n/a | no | no | src :158, 205 |
| `ssl` | dict or SSLContext | dict | `ssl_*` (most keys) | no | yes | src :381-419 |
| `ssl_ca` | text (path) | scalar | n/a | no | no | src :207 |
| `ssl_cert` | text (path) | scalar | n/a | no | no | src :208 |
| `ssl_key` | text (path) | scalar | n/a | no | no | src :210 |
| `ssl_key_password` | text | scalar | n/a | no | yes | src :211 |
| `ssl_verify_cert` | bool or text | scalar | n/a | no | no | src :212 |
| `ssl_verify_identity` | bool | scalar | n/a | no | no | src :213 |
| `ssl_disabled` | bool | scalar | n/a | no | no | src :209 |
| `read_default_file` | text (path) | scalar | n/a | no | no | ran |
| `read_default_group` | text | scalar | n/a | no | no | src :194, 237-245 |

- Login plugins (`connections.py:1033-1102`): `mysql_native_password`, `caching_sha2_password`,
  `sha256_password`, `client_ed25519`, `mysql_old_password`, `mysql_clear_password` and `dialog`. Any
  other fails with `Authentication plugin '...' not configured` (ran with `parsec` and
  `auth_gssapi_client`).
- **`mysql_clear_password` and `dialog` send the password as clear text, with no TLS check**
  (`connections.py:1058-1070`). Ran against the fake server, which never offered TLS: PyMySQL sent the
  password in clear text for both.
- `dialog` (MariaDB PAM): only a prompt of exactly `Password: ` is answered with `password`. Any other
  prompt needs a handler class in `auth_plugin_map` (`connections.py:1061-1097`).
- `client_ed25519` needs PyNaCl (`pymysql/_auth.py:59-67`). Ran: PyMySQL answered with a 64-byte
  signature, not the password.
- `sha256_password` and `caching_sha2_password` need the `cryptography` package for their RSA step when
  TLS is off (`_auth.py:143-145`). `server_public_key` saves asking the server for its key
  (`_auth.py:171-178`).
- `passwd` is used when `password` is empty, with a DeprecationWarning. When both are given, `password`
  wins (ran).
- `read_default_file` (with `read_default_group`, default `client`) reads `user`, `password`, `host`,
  `database`, `ssl-key`, `ssl-password` and more from a `my.cnf` file (`connections.py:243-273`). Ran:
  the user name and password came from the file. Given only `read_default_group`, PyMySQL reads
  `/etc/my.cnf` (L237-241).
- The `ssl` dictionary also takes `capath` and `cipher`, which have no flat names
  (`connections.py:384-387`, `415-416`; see mysql-mariadb.md). Its `password` key is the client key's
  password.
- With no TLS setting at all, PyMySQL 1.2.0 tries TLS and falls back to plain text when the server
  does not offer it (`connections.py:298-303`).

### mysql-connector-python 26.7.0 (the AWS wrapper's MySQL driver)

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `user`, `username` | text | scalar | n/a | yes | no | ran |
| `password`, `passwd` | text | scalar | n/a | no | yes | ran |
| `password1` to `password3` | text | scalar | n/a | no | yes | ran |
| `auth_plugin` | text | scalar | n/a | no | no | ran |
| `krb_service_principal` | text `p/i@REALM` | scalar | n/a | no | no | ran |
| `kerberos_auth_mode` | `SSPI` or `GSSAPI` | scalar | n/a | no | no | ran |
| `oci_config_file` | text (path) | scalar | n/a | no | no | ran |
| `oci_config_profile` | text | scalar | n/a | no | no | ran |
| `webauthn_callback` | function or its name | function | the name as text | no | no | ran |
| `openid_token_file` | text (path) | scalar | n/a | no | no | ran |
| `ssl_ca`, `ssl_cert`, `ssl_key` | text (path) | scalar | n/a | no | no | src constants.py:81-83 |
| `ssl_verify_cert`, `ssl_verify_identity` | bool | scalar | n/a | no | no | src constants.py:84-85 |
| `ssl_disabled` | bool | scalar | n/a | no | no | ran |
| `option_files` | text or list | scalar or list | text | no | no | ran |

- Paths: `constants.py` is `mysql/connector/constants.py`, `abstracts.py` is
  `mysql/connector/abstracts.py`.
- Login plugins shipped (`mysql/connector/plugins/`): `mysql_native_password`,
  `caching_sha2_password`, `sha256_password`, `mysql_clear_password`,
  `authentication_kerberos_client`, `authentication_ldap_sasl_client`, `authentication_oci_client`,
  `authentication_webauthn_client` and `authentication_openid_connect_client`. There is no `dialog`,
  `client_ed25519` or `parsec`. Ran: "Authentication plugin 'dialog' is not supported", and the same
  for `client_ed25519`.
- `mysql_clear_password` and OpenID Connect require TLS (`plugins/mysql_clear_password.py:59-75`,
  `abstracts.py:745-753`). Ran: refused against the fake server, and refused up front with
  `ssl_disabled=True`.
- Kerberos: `kerberos_auth_mode=GSSAPI` (the default outside Windows) needs the `gssapi` package. The
  ticket comes from the cache (`KRB5CCNAME`) or is obtained with `password`
  (`plugins/authentication_kerberos_client.py:196-234`). Bad values fail before connecting (ran). The
  server side is in MySQL Enterprise Edition only; the client side is in every distribution
  (docs: https://dev.mysql.com/doc/refman/8.4/en/kerberos-pluggable-authentication.html).
- OCI (MySQL HeatWave on Oracle Cloud): signs in with an OCI config file and profile, which point at an
  API key on disk.
- `webauthn_callback`: a function with one argument, or the text name of one, imported when
  connecting (`abstracts.py:893-925`). Ran: a module that does not exist and a function with the wrong
  number of arguments both failed before connecting.
- `openid_token_file`: a file that holds the identity token. It must exist (ran). The server side is in
  MySQL Enterprise Edition only, from MySQL 9.1
  (docs: https://dev.mysql.com/doc/refman/9.6/en/openid-pluggable-authentication.html).
- `password1` even overrides `password`; see mysql-mariadb.md. `read_default_file` is turned into
  `option_files` (`mysql/connector/pooling.py:295-299`). Ran: the user name and password were read from
  a `my.cnf`, and `ssl-password` was ignored.
- There is no setting for a client key's password. Ran: `ssl_key_password` failed with
  `AttributeError: Unsupported argument 'ssl_key_password'`.

### MariaDB Connector/Python 1.1.14 (read in source)

It did not build here. Its `connect()` words are listed in `src/mariadb/mariadb_connection.c:32-46`
of the downloaded source package; `src :N` below means that file.

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `user`, `username` | text | scalar | n/a | yes | no | src :33, 42 |
| `password`, `passwd` | text | scalar | n/a | no | yes | src :33, 42 |
| `ssl_key`, `ssl_cert`, `ssl_ca` | text (path) | scalar | n/a | no | no | src :37 |
| `ssl_capath`, `ssl_crl`, `ssl_crlpath` | text (path) | scalar | n/a | no | no | src :37-38 |
| `ssl_cipher` | text | scalar | n/a | no | no | src :38 |
| `ssl_verify_cert`, `ssl` | bool | scalar | n/a | no | no | src :39 |
| `tls_version`, `tls_fp`, `tls_fp_list` | text | scalar | n/a | no | no | src :43-44 |
| `plugin_dir` | text (path) | scalar | n/a | no | no | src :41 |
| `default_file`, `default_group` | text | scalar | n/a | no | no | src :36 |
| `status_callback` | function | function | none | no | no | src :43 |

- Login plugins come from MariaDB Connector/C, which "automatically loads client-side authentication
  plugins from the library's plugin directory" (`plugin_dir`; `mariadb_connection.c:403-407`). MariaDB
  lists eight client plugins: `mysql_native_password`, `mysql_old_password`, `client_ed25519`,
  `auth_gssapi_client`, `dialog`, `mysql_clear_password`, `sha256_password` and
  `caching_sha2_password`
  (docs: https://mariadb.com/docs/server/reference/plugins/authentication-plugins/pluggable-authentication-overview).
  That page does not say which Connector/C version adds `parsec`.
- There is no setting for a client key's password.
- `default_file` and `default_group` read option files (`mariadb_connection.c:415-420`). The MySQL
  survey found it also reads `MYSQL_PWD` (see mysql-mariadb.md).

### Which driver supports which login plugin

| server plugin | PyMySQL | mysql-connector | mariadb (C) |
|---|---|---|---|
| `mysql_native_password` | yes (src) | yes (src) | yes (docs) |
| `caching_sha2_password` | yes (src) | yes (src) | yes (docs) |
| `sha256_password` | yes (src) | yes (src) | yes (docs) |
| `mysql_clear_password` | yes, even without TLS (ran) | only with TLS (ran) | yes (docs) |
| MariaDB PAM (`dialog`) | `Password:` only (ran) | no (ran) | yes (docs) |
| MariaDB `ed25519` | yes, with PyNaCl (ran) | no (ran) | yes (docs) |
| MariaDB `parsec` (11.6+) | no (ran) | no (src) | not checked |
| MariaDB Kerberos (`gssapi`) | no (ran) | no (src) | yes (docs) |
| MySQL Kerberos (Enterprise) | no (src) | yes (src) | no (docs) |
| MySQL LDAP SASL (Enterprise) | no (src) | yes (src) | no (docs) |
| MySQL OCI IAM (HeatWave) | no (src) | yes (src) | no (docs) |
| MySQL WebAuthn (Enterprise) | no (src) | yes (src) | no (docs) |
| MySQL OpenID Connect (Enterprise) | no (src) | yes (src) | no (docs) |

- "no (src)" for PyMySQL means its plugin list has no such entry (`connections.py:1045-1102`); for
  mysql-connector, its `plugins/` folder has no such module.
- MariaDB PAM "usually ... uses the `dialog` client authentication plugin". With
  `pam_use_cleartext_plugin` it uses `mysql_clear_password` instead. Both send the password
  unencrypted, so encrypting the connection with TLS is "incredibly important"
  (docs: https://mariadb.com/docs/server/reference/plugins/authentication-plugins/authentication-with-pluggable-authentication-modules-pam/authentication-plugin-pam).
- The fake-server runs: a small Python server on 127.0.0.1 sent a MySQL greeting without offering TLS,
  asked the client to switch to the plugin under test, and recorded the answer.

## Open points

1. **Nothing was tried against a real cloud service or a real server.** IAM tokens, Secrets Manager,
   Cloud SQL, Entra ID, Kerberos and PostgreSQL OAuth rest on the source, the docs, and runs that stop
   before the login itself.
2. **Cloud SQL's manual IAM token**: the docs page does not give its lifetime.
3. **PostgreSQL 18 OAuth in psycopg-binary**: the settings are known, but the wheel has no
   `libpq-oauth` library, so the built-in flow is probably missing. Not tested.
4. **MariaDB Connector/C and `parsec`**: which version supports it was not found.
5. **The AWS wrapper with the rest of Ekbatan-py** (beyond the migrator): whether Ekbatan-py's
   repositories, JSON handling and outbox work over `postgresql+aws_wrapper_psycopg` and
   `mysql+aws_wrapper_mysqlconnector` was not run. Most plugins need a real Aurora cluster.
6. **A design question for Ekbatan-py**: where a per-connection password function would live (the
   builder, the database registry, a provider object), and how the migrator would receive it.
7. **A rules question for Ekbatan-py**: whether `data_source_properties` may carry settings that fetch a
   login from a file: libpq `service` and `passfile`; PyMySQL `read_default_file` and
   `read_default_group`; mysql-connector `option_files`; mariadb `default_file` and `default_group`.
8. **The AWS wrapper's DEBUG token logging** could be reported upstream. Ekbatan-py cannot hide what a
   library writes to its own logger, short of keeping that logger above DEBUG.
