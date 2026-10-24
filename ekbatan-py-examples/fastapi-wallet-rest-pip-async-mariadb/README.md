# fastapi-wallet-rest-pip-async-mariadb

A wallet service over HTTP on **MariaDB** on native asyncio: every endpoint is `async def` and awaits `ekbatan.aio`. Two shards, `global` and `mexico`, each on
a server of its own; migrations from files; three pools per member; a keyed lock around the
deposit; and an in-process event handler that reacts to every deposit.

It is the Python counterpart of the Java wallet-rest example: the same model, actions, event
handler, endpoints and test cases, under the same names. What differs is what the language asks
for - FastAPI for the controller, plain classes and builders instead of DI annotations, pytest,
the yoyo migrator, a generated `schema.py` instead of jOOQ - and each is built three ways, with
uv, pip and Poetry. See [`../README.md`](../README.md).

<!-- build: written by scripts/sync-examples.py -->
```
make            # generate what is out of date, then test the built wheel, installed
make run        # start the databases, run it, stop the databases
```

`make run` needs only Docker: it starts the two databases `compose.yaml` describes, one server per shard - the image the tests use, with the login and database `ekbatan.yaml` defaults to - waits until it is ready, runs the app, and stops the databases when the app stops. `docker compose down` throws the databases away.

Built with **pip and setuptools**. The same project with [uv](../fastapi-wallet-rest-uv-async-mariadb) and [Poetry](../fastapi-wallet-rest-poetry-async-mariadb) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.

The venv is made with `python3`, which must be 3.13 or later - `make PYTHON=python3.13` if yours is older.
<!-- /build -->

## What it does

| Endpoint | What runs |
| --- | --- |
| `POST /wallets` `{countryCode, ownerId, currency, initialBalance}` | `WalletCreateAction` - "MX" puts the wallet on the Mexico shard, anything else on the global one; the id carries the shard from then on. 201 |
| `POST /wallets/{id}/deposits` `{amount, recipient}` | `WalletDepositMoneyAction`, under the keyed lock `wallet:{id}`; emits `WalletMoneyDepositedEvent` |
| `POST /wallets/{id}/close` | `WalletCloseAction`; emits `WalletClosedEvent` |
| `GET /wallets/{id}` | the wallet, or 404 |

JSON names are camelCase on the wire and snake_case in the code; `balance` is written as text,
which keeps every digit of the `Decimal`.

Each deposit's event reaches `WalletMoneyDepositedEventHandler` in the same process: fan-out turns
the committed event into a delivery and dispatch runs the handler, which starts
`CreateNotificationAction` - a row in `notifications`, on the default shard whichever shard the
wallet is on. The recipient is not part of the event; the handler reads it from the parameters
of the action that produced it.

## What to look at

- **`model.py`** - `Wallet` and its three events, and `Notification`, an entity: writing one
  emits nothing, and the unit of work that wrote it is still recorded.
- **`actions.py`** - each action's parameters are its nested `Params`. `WalletCreateAction` is
  the only place a shard is chosen; every other action is routed by the wallet's id.
- **`handlers.py`** - the listen-to-yourself handler.
- **`repository.py`** - the queries the app asks: `find_all_by_owner_id`, `exists_on_shard`,
  `find_open_with_balance_below`, and the typed mapping to and from the table's record.
- **`app.py`** - the wiring, and the only file that knows how many databases there are.
- **`api.py`** - the HTTP layer, and nothing else.
- **`ekbatan.yaml`** - the shard layout, in the same keys as Java's `application.yml`: per member,
  `primaryConfig` for requests, `jobsConfig` for the scheduler and `lockConfig` for the lock, and `handlersConfig` for the async handler. The
  urls and the login are `${NAME:default}` placeholders, so the file runs as it is against the
  servers in `compose.yaml`.

## What is async, and what is not

The requests run on `ekbatan.aio`: an `AsyncDatabaseRegistry` over both shards, async
repositories, an `AsyncActionExecutor`, an async keyed lock. The handler is `async def` and
awaits an executor of its own, on dispatch's handler loop - an async pool belongs to the loop it
is used on, so its registry is its own, over `handlersConfig`. What polls - db-scheduler, and the
fan-out and dispatch jobs it runs - stays on threads, through a sync registry over every shard's
`jobsConfig`. The urls name the sync driver; the async side reads the same urls and connects
through the database's async default.

## The `eventlog` database

On MariaDB a schema *is* a database, so the framework's two event tables live in a database of
their own, `eventlog`, beside `wallet` - one per server, shared by every database on it.
`V0000__create_eventlog_database.sql` creates it, from this database's connection, so the login
needs the right to create a database and use it: root here, in `compose.yaml` and the tests.
Each shard is its own server, so each has its own `eventlog`.

## How the schema gets there

`schema.py` is generated, not written: `make` runs `python -m ekbatan.schemagen`, which starts a
MariaDB container, applies the migrations in `migrations/` to it, and reflects the result - so the
Python and the database cannot drift. The module records a fingerprint of the migrations it came
from and checks it on import, so a stale one refuses to load rather than quietly disagreeing with
the database.

One forced type in `pyproject.toml`: every `CHAR(3)` column is a currency code, whatever it is
called, and reads as the app's own `Currency` - the wallet's `java.util.Currency`, which Python
does not have. `CurrencyConverter`, in `converters.py`, turns the text into a `Currency` and back,
so the repository never sees the text. Action parameters and events keep the plain code,
`"currency": "EUR"`, because they are written to the outbox as JSON for other services to read.

## Its test

```
make
```

Against two MariaDB servers in Testcontainers, one per shard, it asks what the Java example's
test asks: separate wallets route to their own shards, a deposit's event reaches the handler and
leaves a notification, closing a wallet closes it, and the driver settings in `ekbatan.yaml` reach
the database. Two checks are its own: no object of the service shows the database password, and
the migration files pass the checks a deploy runs.
