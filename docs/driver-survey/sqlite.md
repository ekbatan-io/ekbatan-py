# SQLite: what `sqlite3.connect()` accepts

Part of the [Python driver survey](../DRIVER-SURVEY.md). Checked on 2026-10-11 against Python 3.14.7
(SQLite library 3.53.1) and SQLAlchemy 2.0.52, on macOS (Apple silicon).

How this was checked: a scratch virtual environment, `sqlite3.connect.__text_signature__`, and small
scripts that call `sqlite3.connect()` and `create_engine()` with good and bad values. In this Python
build `sqlite3` is compiled in (`_sqlite3` has no file), so there is no C source to cite. The driver
facts below come from runs. The SQLAlchemy facts come from runs and from the installed source.

The "how known" column:

- `ran`: proved by running.
- `src file:line`: read in the installed source. Paths are relative to `site-packages/`.
- `docs`: from the page named next to it.

## Answers

- **SQLite has no login.** There is no user name and no password. `sqlite3.connect()` refuses
  `user=` and `password=` like any unknown word (ran). SQLAlchemy refuses a SQLite URL that holds a
  user name or a password, and its error shows the password as `***` (ran).
- `sqlite3.connect()` takes **nine settings**: `database`, `timeout`, `detect_types`,
  `isolation_level`, `check_same_thread`, `factory`, `cached_statements`, `uri` and `autocommit`.
- Eight of them are plain values: text, a number, or true/false. **`factory` is not.** It must be a
  class or a function, and no plain setting can stand in for it.
- **An unknown setting fails** when the connection is opened, with
  `TypeError: Connection() got an unexpected keyword argument 'foo'` (ran). The check is made by the
  `factory` class, so a custom `factory` can accept settings of its own (ran).
- **Two settings read any value as true or false**: `check_same_thread` and `uri`. The text
  `"false"` counts as true and switches them **on** (ran). `autocommit` is strict: only `True`,
  `False` or `-1`. `timeout`, `detect_types` and `cached_statements` refuse text.
- **No setting holds a user name or a secret.**
- **Through SQLAlchemy's URL, only six settings are read**: `uri`, `timeout`, `isolation_level`,
  `detect_types`, `check_same_thread` and `cached_statements`. Every other word in the URL is dropped
  without a warning. With `uri=true` in the URL, the leftover words become part of the SQLite file
  address instead. **`isolation_level` is dropped too**, although SQLAlchemy lists it (ran; a
  SQLAlchemy slip, see below).
- `connect_args` reach `sqlite3` unchanged and win over the URL (ran).
- SQLite's own file options (`mode=ro`, `cache=shared`, `immutable=1`, ...) live inside the file
  address. They need `uri=true` **in the URL**. With `uri` only in `connect_args`, the options are
  lost and a writable file is created (ran).
- PRAGMAs (`foreign_keys`, `journal_mode`, `busy_timeout`), `row_factory` and custom SQL functions
  are not `connect()` settings. They are set after connecting, so `data_source_properties` cannot
  carry them.

What it means for Ekbatan-py:

- **`username` and `password`: refuse both for SQLite.** They have nowhere to go.
- **`data_source_properties`** (passed as `connect_args`): the eight plain settings work as given.
  The "no objects or functions" rule refuses `factory`, and nothing useful is lost.
- **Passing values as given, not as text, is right here too.** Text fails for the number settings,
  and the text `"false"` silently means "on" for `check_same_thread` and `uri`. A real YAML boolean
  is safe.
- An unknown name fails at the first connection, not when the config is read.
- `uri` in `data_source_properties` does not do what a user expects (see above). It works only in the
  URL.

## `sqlite3.connect()` settings

The signature, from `sqlite3.connect.__text_signature__` (ran):

```
(database, timeout=5.0, detect_types=0, isolation_level='', check_same_thread=True,
 factory=ConnectionType, cached_statements=128, uri=False, *,
 autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL)
```

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `database` | text, bytes or path | scalar | n/a | no | no | ran |
| `timeout` | number (seconds) | scalar | n/a | no | no | ran |
| `detect_types` | integer (flags) | scalar | n/a | no | no | ran |
| `isolation_level` | text or `None` | scalar | n/a | no | no | ran |
| `check_same_thread` | any, read as true/false | scalar | n/a | no | no | ran |
| `factory` | class or function | function | none | no | no | ran |
| `cached_statements` | integer | scalar | n/a | no | no | ran |
| `uri` | any, read as true/false | scalar | n/a | no | no | ran |
| `autocommit` | `True`, `False` or `-1` | scalar | n/a | no | no | ran |

- `database`: a file path, or `":memory:"`. An integer or `None` fails with
  `TypeError: expected str, bytes or os.PathLike object, not int`.
- `timeout`: default 5.0 seconds. Takes an int, a float, or a bool (a bool counts as a number). Text
  fails with `TypeError: must be real number, not str`, and so does `None`.
- `detect_types`: default 0. The flags are `PARSE_DECLTYPES` (1) and `PARSE_COLNAMES` (2). Any
  integer is accepted, even 99 or -1. Text and `None` fail. With `PARSE_DECLTYPES`, reading a `date`
  column printed a DeprecationWarning: the built-in date converter is deprecated since Python 3.12.
- `isolation_level`: default `""`. Takes `""`, `"DEFERRED"`, `"IMMEDIATE"` or `"EXCLUSIVE"` in any
  letter case (`"deferred"` became `"DEFERRED"`), or `None`. Other text fails with
  `ValueError: isolation_level string must be '', 'DEFERRED', 'IMMEDIATE', or 'EXCLUSIVE'`. A number
  fails with `TypeError: isolation_level must be str or None`.
- `check_same_thread`: default `True`. Any value is accepted and read the Python way. `"false"` turned
  the check on: a second thread was blocked. `None`, `0` and `""` turned it off.
- `factory`: default `sqlite3.Connection`. It is called with all the other settings. The text
  `"MyConn"` fails with `TypeError: 'str' object is not callable`. A plain function that returns a
  connection also works.
- `cached_statements`: default 128. An integer only: 0 and -1 were accepted, `1.5`, `"128"` and
  `None` were refused.
- `uri`: default `False`. Any value is accepted and read the Python way. `"false"` and `"no"` turned
  URI mode on: `file:t.db?mode=memory` opened an in-memory database instead of creating a file with
  that name. `False`, `None`, `0` and `""` created the file.
- `autocommit`: new in Python 3.12, default `sqlite3.LEGACY_TRANSACTION_CONTROL` (-1). Only `True`,
  `False` and `-1` are accepted. `0`, `1`, `"false"`, `"True"` and `None` fail with
  `ValueError: autocommit must be True, False, or sqlite3.LEGACY_TRANSACTION_CONTROL`. With `False`,
  a transaction is already open right after connecting (`in_transaction` was `True`).
- Passing more than `database` by position prints a DeprecationWarning: the others become
  keyword-only in Python 3.15. SQLAlchemy passes them by name, so it is not affected.

### A setting `sqlite3` does not know

- `sqlite3.connect(":memory:", foo=1)` fails with
  `TypeError: Connection() got an unexpected keyword argument 'foo'` (ran). `user=` and `password=`
  fail the same way.
- The error names `Connection()`, not `connect()`. `connect()` hands every setting to `factory`, and
  the factory checks them. A `factory` subclass whose `__init__` takes `extra=None` accepted
  `extra="hello"` (ran).
- The error comes when the connection is opened. Through SQLAlchemy that is the first
  `engine.connect()`, and SQLAlchemy passes the `TypeError` through unchanged (ran).

## SQLite's own file options (inside the file address)

With `uri=True`, the file name is a SQLite address such as `file:app.db?mode=ro`. Its options are
plain text inside that one setting.

| setting | type | shape | flat alternative | user name | secret | how known |
|---|---|---|---|---|---|---|
| `mode` | `ro`, `rw`, `rwc`, `memory` | scalar | n/a | no | no | ran |
| `cache` | `shared`, `private` | scalar | n/a | no | no | ran |
| `vfs` | text (a VFS name) | scalar | n/a | no | no | ran |
| `immutable` | `0` or `1` | scalar | n/a | no | no | ran |
| `nolock` | `0` or `1` | scalar | n/a | no | no | ran |
| `psow` | `0` or `1` | scalar | n/a | no | no | ran |

- `mode=ro` and `immutable=1` refused writes: `attempt to write a readonly database` (ran). The other
  options opened the file and allowed writes.
- Bad values fail when connecting: `no such access mode: bogus`, `no such vfs: bogus`,
  `no such cache mode: bogus` (ran).
- An unknown option (`?bogus=1`) is ignored without a word (ran).
- The option list is from https://www.sqlite.org/uri.html (docs).

## SQLAlchemy 2.0.52: URL query and `connect_args`

`create_connect_args` turns the URL into the arguments for `sqlite3.connect()`
(`src sqlalchemy/dialects/sqlite/pysqlite.py:642-702`).

| URL word | turned into | reaches `sqlite3` | how known |
|---|---|---|---|
| `uri` | true/false | yes | ran |
| `timeout` | float | yes | ran |
| `isolation_level` | nothing | no, dropped | ran |
| `detect_types` | int | yes | ran |
| `check_same_thread` | true/false | yes | ran |
| `cached_statements` | int | yes | ran |
| `autocommit`, `factory` | nothing | no, dropped | ran |
| any other word | nothing | dropped, or put in the address | ran |

- A user name, password, host or port in the URL fails at `create_engine`
  (`pysqlite.py:643-650`). Ran: `sqlite://user:...@/a.db` raised
  `ArgumentError: Invalid SQLite URL: sqlite://user:***@/a.db`, with the password hidden.
- The six known words and their types are a fixed list (`pysqlite.py:658-665`). They are converted by
  `util.coerce_kw_type` (`pysqlite.py:666-669`).
- **Why `isolation_level` is dropped:** `coerce_kw_type` writes a value into the result only when it
  has to convert it (`src sqlalchemy/util/langhelpers.py:1366-1374`). A URL value is always text,
  and `isolation_level` is supposed to be text, so nothing is written. Ran:
  `sqlite:///a.db?isolation_level=IMMEDIATE` and `?isolation_level=bogus` both left the driver
  connection at `''`. The same value in `connect_args` worked (`'IMMEDIATE'`).
- True/false words go through `asbool` (`langhelpers.py:1316-1325`): `true`, `yes`, `on`, `y`, `t`, `1`
  and `false`, `no`, `off`, `n`, `f`, `0`. Anything else fails at `create_engine` with
  `ValueError: String is not true/false: 'maybe'` (ran).
- Other bad values also fail at `create_engine`, before any connection: `?timeout=abc` gave
  `ValueError: could not convert string to float: 'abc'`. A repeated word (`?timeout=1&timeout=2`)
  gave `TypeError: float() argument must be a string or a real number, not 'tuple'` (ran).
- With `uri=true`, the leftover words are added to the file address, sorted (`pysqlite.py:671-692`).
  Ran: `sqlite:///file:b.db?uri=true&mode=rwc&foo=1&timeout=3` gave the address
  `file:b.db?foo=1&mode=rwc`, with `uri=True` and `timeout=3.0` as settings.
- Without `uri=true`, the file name is made absolute and the leftover words are dropped
  (`pysqlite.py:693-696`). Ran: `?foo=bar`, `?autocommit=true`, `?factory=x` and `?password=...` all
  vanished.
- `check_same_thread` defaults to `False` for a file database and `True` for `:memory:`
  (`pysqlite.py:698-700`; ran).
- `uri=true` has to be in the URL query (SQLAlchemy's own note, `pysqlite.py:79-82`). Ran:
  `sqlite:///file:x.db?mode=ro` with `connect_args={"uri": True}` passed the absolute path
  `.../file:x.db` and `uri=True`. `mode=ro` was lost, and a writable file named `file:x.db` was
  created.
- `connect_args` are laid over the URL's settings and win (`src sqlalchemy/engine/create.py:633-634`).
  Ran: URL `timeout=7` with `connect_args` `timeout=1` gave 1. `autocommit=True` from `connect_args`
  reached the driver. `check_same_thread="false"` from `connect_args` reached it as the text `"false"`,
  which `sqlite3` reads as true.
- Ekbatan-py's migrator builds its arguments with the same `create_connect_args` and lays
  `connect_args` over them (`src/ekbatan/_yoyo.py:597-608` in this repository). SQLite behaves the
  same there.

## Settings that are not `connect()` settings

- After connecting, a connection has `row_factory`, `text_factory`, `isolation_level` and
  `autocommit` as attributes. It has methods such as `create_function`, `create_collation`,
  `set_authorizer`, `setlimit`, `setconfig` and `enable_load_extension` (ran: listed on a live
  connection).
- PRAGMAs (`PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout`) are SQL statements.
- All of these are set after connecting. Under SQLAlchemy, that is a `connect` event. SQLAlchemy
  itself adds the `regexp` and `floor` SQL functions that way (`pysqlite.py:605-640`).
- Encrypted SQLite (SQLCipher) is a different driver. SQLAlchemy's `sqlite+pysqlcipher://` dialect uses
  the URL password as the encryption key (`src sqlalchemy/dialects/sqlite/pysqlcipher.py:128-145`).
  Stock `sqlite3` has nothing like it, and Ekbatan-py does not use it.

## Open points

1. **`?isolation_level=` is dropped by SQLAlchemy 2.0.52.** It looks like a SQLAlchemy bug. It was not
   checked against SQLAlchemy's main branch or its issue tracker. The documented way is SQLAlchemy's
   own `create_engine(isolation_level=...)`, which is not a driver setting.
2. **Driver-level `autocommit` (Python 3.12+)** passed through `connect_args` was accepted. How it
   mixes with SQLAlchemy's own transaction handling, which works through `isolation_level`, was not
   tested.
3. **Should Ekbatan-py refuse `uri` in `data_source_properties`**, since it only works in the URL?
4. **Python 3.15** makes every setting but `database` keyword-only. No effect is expected, because
   SQLAlchemy passes them by name. Not run on 3.15.
