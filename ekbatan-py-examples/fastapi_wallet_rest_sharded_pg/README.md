# fastapi_wallet_rest_sharded_pg

The REST wallet service again, over **two databases**, with routing that needs no lookup table. The Python counterpart to Java's
`spring-boot-wallet-rest-gradle-sharded-pg`.

```
cd ekbatan-py-examples && uv run python -m fastapi_wallet_rest_sharded_pg
uv run pytest ekbatan-py-examples/fastapi_wallet_rest_sharded_pg      # needs Docker
```

## The claim, through HTTP

`api.py` is the same shape as `fastapi_wallet_rest_pg/api.py`. The only line in the whole
application that knows there is more than one database is the country code on `POST /wallets`:

```
POST /wallets            {"country": "MX", ...}   -> id carries shard 1:0
GET  /wallets/{id}                                -> routed by the id, no hint
POST /wallets/{id}/deposits                       -> names no database
POST /wallets/{id}/transfers  (same shard)        -> 200
POST /wallets/{id}/transfers  (across shards)     -> 422, and says to use a saga
GET  /shards                                      -> one query per database
```

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

## What to look at

| File | What it shows |
|---|---|
| `actions.py` | `OpenWalletAction` — the **one** place a shard is chosen, from a country code |
| `model.py` | `WalletId(ShardedId)`, and nothing else about sharding |
| `schema.py` | no shard column anywhere; every database holds the same tables |
| `repository.py` | one repository over every shard, and what a query without an id costs |
| `app.py` | the only file that knows how many databases there are |

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
[`../fastapi_wallet_saga_pg`](../fastapi_wallet_saga_pg).

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
