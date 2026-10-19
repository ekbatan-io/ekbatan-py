# fastapi-wallet-rest-poetry-sharded-pg

The REST wallet service again, over **two databases**, with routing that needs no lookup table. The Python counterpart to Java's
`spring-boot-wallet-rest-gradle-sharded-pg`. Its async twin, the same service and tests on
`ekbatan.aio`, is [`fastapi-wallet-rest-uv-async-sharded-pg`](../fastapi-wallet-rest-uv-async-sharded-pg).

<!-- build: written by scripts/sync-examples.py -->
```
make            # generate what is out of date, then test the built wheel, installed
make run        # run it; the database comes first, above
```

Built with **Poetry**. The same project with [uv](../fastapi-wallet-rest-uv-sharded-pg) and [pip and setuptools](../fastapi-wallet-rest-pip-sharded-pg) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.

Needs `poetry` 2 on the path.
<!-- /build -->

## The claim, through HTTP

`api.py` is the same shape as `fastapi_wallet_rest_pg/api.py`. The only line in the whole
application that knows there is more than one database is the country code on `POST /wallets`:

```
POST /wallets            {"country": "MX", ...}   -> id carries shard 1:0
GET  /wallets/{id}                                -> routed by the id, no hint
POST /wallets/{id}/deposits                       -> names no database; under a keyed lock
POST /wallets/{id}/transfers  (same shard)        -> 200
POST /wallets/{id}/transfers  (across shards)     -> 422, and says to use a saga
GET  /shards                                      -> one query per database
GET  /wallets/{id}/alerts                         -> what the deposit handler wrote
```

**A deposit on any shard reaches its handler**, as in Java's sharded example. The deposit
commits on its wallet's shard; one scheduler, on the global shard's `jobsConfig` pool, runs the
fan-out and handling jobs, which reach every shard through the registry; the handler writes an
alert. Alerts have plain ids, so they all live on the default shard, as Java's notifications
do. The tables for this arrive in `V0002__event_handler_and_alerts.sql` - a second migration,
because an applied one is never edited.

A **422** for the cross-shard transfer, not a 500: the framework refused the request rather than
failing at it, so it is the caller's request that is wrong. The reply names both databases and
points at `fastapi_wallet_saga_pg`.

## The whole idea, in one line

The shard is packed into the identifier. `WalletId.generate(MEXICO)` mints an id that carries
its coordinate, and every read and write afterwards follows it — no tenant column, no lookup
table, no argument saying where to look.

Which means `DepositMoneyAction` mentions no shard at all:

```python
def perform(self, params: DepositParams) -> Wallet:
    wallet = self.wallets.get(params.wallet_id)  # routed by the id
    return self.plan.update(wallet.deposit(params.amount))
```

That is the property worth protecting. Business logic that named a database would have to be
edited every time the layout changed.

## Two forced types

`pyproject.toml` tells schemagen to turn two plain text columns into small classes of this
app's own:

```toml
# Every CHAR(3) column is a currency code, so the rule picks columns by their type.
[[tool.ekbatan.schemagen.forced-types]]
converter = "fastapi_wallet_rest_sharded_pg.converters.CurrencyConverter"
include-types = "CHAR\\(3\\)"

# One column, picked by its name.
[[tool.ekbatan.schemagen.forced-types]]
converter = "fastapi_wallet_rest_sharded_pg.converters.EmailAddressConverter"
include-expression = "alerts\\.recipient"
```

and schemagen writes these two lines into `schema.py`:

```python
Column("currency", converted(CHAR(length=3), CurrencyConverter()), nullable=False)
Column("recipient", converted(VARCHAR(length=255), EmailAddressConverter()), nullable=False)
```

- **Picked by type.** Every `CHAR(3)` column is a currency code, whatever it is called. A
  wallet's `currency` is a `Currency`, which takes only three capital letters: `"eur"` is
  refused.
- **Picked by name.** `alerts.recipient` is an `EmailAddress`. A bad address is refused when
  the deposit is made (a 400), rather than later in the handler that writes the alert, where it
  would fail on every retry.

The two classes are in `model.py` and the converters in `converters.py`; neither knows
SQLAlchemy. The repository hands the table a `Currency` and gets one back, and the column holds
`EUR`. Action parameters and events keep the plain text, `"currency": "EUR"`, because they are
written to the outbox as JSON for other services to read. The test checks each value in Python,
in the table, and through the API.

## What to look at

| File | What it shows |
|---|---|
| `actions.py` | `OpenWalletAction` — the **one** place a shard is chosen, from a country code |
| `model.py` | `WalletId(ShardedId)`, and nothing else about sharding |
| `schema.py` | no shard column anywhere; every database holds the same tables |
| `repository.py` | one repository over every shard, and what a query without an id costs |
| `ekbatan.yaml` | the only file that knows how many databases there are, in the keys of Java's `application.yml`; every pool on UTC (`options: -c TimeZone=UTC`) |
| `app.py` | the wiring, reading the layout from `ekbatan.yaml` |
| `api.py` | the deposit under a keyed lock - one database, the global member, holds every key, whichever shard the wallet is on; a request that waits out the pool is a 503 |

## Three things the tests pin

**Isolation is checked from the outside.** A Mexican wallet is on the Mexican database and not
on the other one, asserted by asking each database directly — going through the repository would
hide a routing failure behind the thing that failed.

**A query without an id asks every shard.** `find_all_by_owner` is one query per database, and
the test says so. A question you ask often should be answerable from one shard, which usually
means choosing the shard key so that it is.

**Reaching a second shard is refused, not half-committed.** There is no two-phase commit here.
Committing each shard separately would turn "all or nothing" into a lie the first time an action
touched two databases by accident, so the framework raises `CrossShardError` and names the two
that disagreed.

Moving money *between* shards is therefore a saga, not a transaction — see
[`../fastapi-wallet-saga-uv-pg`](../fastapi-wallet-saga-uv-pg).

## The pools

Every pool puts its sessions on UTC with `options: -c TimeZone=UTC`.

| pool | member | used by |
| --- | --- | --- |
| `primaryConfig` | each shard | the requests, the handler, fan-out and dispatch on every shard; past `poolSize` a request waits at most `poolTimeout`, then gets a 503 |
| `lockConfig` | global | the deposit's keyed lock - one connection per held lease. A lock is about a wallet's key, not its rows, so one database holds every key |
| `jobsConfig` | global | db-scheduler: one scheduler for every shard |

## Its test

```
make
```

Twenty-two cases against two databases on one PostgreSQL in Testcontainers. One says what the
keyed lock is for: eight deposits at once on a Mexican wallet all land, none a 409, and the
balance is exact.

## Two databases, one server

The tests create `shard_global` and `shard_mexico` on one Postgres container. The framework
opens two pools against two urls and cannot tell that one process is behind both, so the routing
is genuinely exercised; two containers would prove nothing further and cost a second startup.

What matters is that it is **not** two schemas or one database pretending. A test sharing a
database would pass with the routing removed, which would make it worse than no test at all.

## Ids that name a shard you have not configured

`default_shard` is where those land, rather than being refused. That is what lets one
environment read another's ids — a staging deployment with one database can read a production
id minted for shard 3 instead of throwing at it.
