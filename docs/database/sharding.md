# Sharding

The reference. For *why* there are two axes and when to reach for each strategy, read
[Concepts → Sharding](../concepts/sharding.md) first; this page is the configuration and the
wire format.

## Two axes: group + member

A `ShardIdentifier` is a numeric `(group, member)` pair, and the two axes answer different
questions:

- **Group** is the **policy** axis - boundaries forced on you from outside. Data residency
  ("Mexico data stays in Mexico"), a tenant isolation contract, a business-domain separation.
  How many groups you have is decided by those constraints, not by you. **8 bits, up to 256.**
- **Member** is the **capacity** axis - horizontal scale inside one policy boundary. You add a
  member when one database cannot take the write throughput. How many is capacity planning.
  **6 bits, up to 64 per group.**

```python
from ekbatan.sharding import DEFAULT_SHARD, ShardIdentifier

GLOBAL = ShardIdentifier(0, 0)      # prints as "0:0"; DEFAULT_SHARD is this
MEXICO = ShardIdentifier(1, 0)
MEXICO_2 = ShardIdentifier(1, 1)    # a second database for the same policy boundary
```

16,384 addressable shards, and the numbers travel **inside the id** rather than in a lookup
table - see [Self-describing IDs](#self-describing-ids-shardedid).

Members of one group are assumed to share network locality and a failure domain: same region,
same VPC, same availability zone. A predicate read inside a group scatters across every member
of it, so intra-group latency is what that read costs. Names like `global-eu-1`, `global-eu-2`
reflect the convention. Nothing enforces it - a connection url is opaque to the framework - but
it is the shape the design assumes, and ignoring it makes scatter reads slower than they look.

A shard this deployment does not have routes to the default, in both ports, and **logs a warning
saying so** - see [Unregistered shards](#unregistered-shards-fall-back-to-default-and-say-so)
below, and `unknown_shard_policy="fail"` if you want it to be an error instead.

## Declarative configuration

Java's layout, level for level: **groups** of **members**, each member one shard and a named map
of datasources.

```python
layout = ShardingConfig(
    default_shard=GLOBAL,
    groups=(
        ShardGroupConfig(group=0, name="global", members=(
            ShardMemberConfig(member=0, name="global-eu-1", configs={
                "primaryConfig":   DataSourceConfig(url=global_url, pool_size=20),
                "secondaryConfig": DataSourceConfig(url=global_replica_url),
                "lockConfig":      DataSourceConfig(url=global_url, pool_size=50),
            }),
        )),
        ShardGroupConfig(group=1, name="mexico", members=(
            ShardMemberConfig(member=0, name="mexico-1", configs={
                "primaryConfig": DataSourceConfig(url=mexico_url),
            }),
        )),
    ),
)
registry = DatabaseRegistry.from_config(layout)
```

A member's `(group, member)` is its `ShardIdentifier`. Its `configs` map has two reserved names
and room for any other:

| key | |
| --- | --- |
| `"primaryConfig"` | **required.** The pool for writes and transactional reads |
| `"secondaryConfig"` | optional. A read replica for non-transactional reads; omit it and both go to the primary |
| anything else | yours - `"jobsConfig"`, `"lockConfig"`, `"analyticsConfig"`. Read back with `member.config_for(name)` and build with `engine_for(...)`; the registry builds only the two above |

The key names are Java's, spelling included, so one layout describes both ports. A group needs a
`name`; a member's `name` is optional and labels its spans and log lines.

`DataSourceConfig` is the pool:

| | default | |
| --- | --- | --- |
| `url` | - | a SQLAlchemy url; the dialect is read from it. No login in it |
| `username` | None | the user to log in as; never blank |
| `password` | None | the login password; the empty string is valid |
| `pool_size` | 10 | connections kept open - the pool fills to this many in the background as soon as it is built |
| `max_overflow` | 0 | connections beyond `pool_size`. Zero is deliberate - see below |
| `pool_timeout` | 30.0 | seconds a caller waits for a connection, free or still being opened |
| `pool_recycle` | 1800 | retire a connection after this many seconds |
| `pool_pre_ping` | True | check a connection is alive before handing it out |
| `data_source_properties` | `{}` | extra driver settings, handed over as given |
| `echo` | False | log every statement |

**`max_overflow=0` by default**, where SQLAlchemy's own default is 10. An overflow connection is
opened on demand and closed on return, so a burst that exceeds `pool_size` silently becomes a
burst of connection *establishment* against the database - which is the expensive part, and
exactly when you can least afford it. A fixed pool makes the limit visible as waiting rather than
invisible as latency.

**The pool runs as Java's Hikari pool does with Ekbatan's settings.** It fills to `pool_size` in
the background as soon as it is built - Hikari's `minimumIdle` defaults to the maximum - and
refills when a connection is thrown away; a pool of 3 holds 3 open connections a moment after
start, query or not. A connection that cannot be opened - a wrong password, a database not up
yet - is retried with Hikari's backoff (10 ms, doubling up to 5 s), and the caller fails with the
driver's error only when `pool_timeout` is up: 30 s by default, Hikari's `connectionTimeout`. The
pool keeps trying in the background until it is disposed. The password is never logged while it
retries.

The same layout can be read from a YAML, TOML, JSON or INI file: see [Config files](config-files.md).

**The login lives in `username` and `password`, never in the url** - as in the Java port. A url
is logged and repeated, so one that carries a user name or a secret is refused, and the message
says where each goes instead, without repeating the url:

```python
DataSourceConfig(
    url="postgresql+psycopg://db:5432/wallet",
    username="app",
    password=os.environ["DB_PASSWORD"],
)
```

Both are optional: leave `username` out only when the driver supplies the user, and `password` for
a login that has none. A url SQLAlchemy cannot parse - two hosts written `h1:3306,h2:3306` - is
read from its text instead, the way Java reads every url. `split_login(url)` takes the login out of
a url that arrives with one, such as a test container's.

**`data_source_properties` are handed to the driver as given** - a number stays a number, a group
stays a group, an object stays an object:

```python
DataSourceConfig(
    url="mariadb+pymysql://db:3306/wallet",
    username="app",
    password=password,
    data_source_properties={
        "connect_timeout": 10,
        "ssl": {"capath": "/etc/ssl/certs"},
    },
)
```

`user`, `password` and PyMySQL's `passwd` are refused there, in any letter case, because they would
silently win over `username` and `password`. Other secrets that have no field of their own - a
client key's `sslpassword`, PyMySQL's `ssl_key_password` - belong here. An empty value (`None`) is
refused. Which setting names hold a login or a secret, for every setting of psycopg, PyMySQL and
sqlite3, is decided in the ledger `tests/driver_settings.tsv`. The pool and the migrator both
connect with the same login and the same settings.

**It never prints the password**, nor any `data_source_properties` value - every one of those is
treated as a secret. Printing a `DataSourceConfig`, or anything that holds one - a layout, a member,
a migration result - shows `***` in their place. `log.info(layout)` is safe to write.

There is no setting for Hikari's `idleTimeout` or `minimumIdle`: the pool behaves as Hikari's
defaults (`minimumIdle` equal to the maximum, so `idleTimeout` never applies), and retirement is
by age. See [SHORTCOMINGS.md](../SHORTCOMINGS.md).

## Sharded models and repositories

A model that participates in sharding declares its id type as `ShardedId` instead of `Id`:

```python

@dataclass(frozen=True, kw_only=True)
class Wallet(Model[ShardedId["Wallet"], WalletState, WalletBuilder]):
    owner_id: UUID
    currency: str
    balance: Decimal

    @staticmethod
    def open(*, shard: ShardIdentifier, owner_id: UUID, currency: str,
             opening_balance: Decimal) -> "Wallet":
        wallet_id = ShardedId[Wallet].generate(shard)        # the shard bits go into the UUID here
        return (
            WalletBuilder()
            .id(wallet_id)
            .state(WalletState.OPENED)
            .owner_id(owner_id)
            .currency(currency)
            .balance(opening_balance)
            .with_event(WalletOpenedEvent(model_id=str(wallet_id), owner_id=owner_id,
                                          currency=currency, opening_balance=opening_balance))
            .build()
        )
```

**That is the whole of the model's participation.** One base class on the id, and one argument at
the single point where a row is created. Nothing else on the model mentions a shard, because
after `generate(shard)` the id carries it — the row is bonded to that database for its lifetime,
and `ShardedId[Wallet].generate` is the only place that decision can be made.

No field holds the shard, and deliberately so: a column would be a second copy of something the
primary key already says, free to disagree with it after a bad backfill. Reading it out of the id
cannot drift.

The framework's own tables need no change at all. Each shard carries its own `events` and
`event_notifications`, and an event is written to the database whose row it describes — so the
outbox is sharded by construction rather than by configuration.

### Reaching a specific shard

```python
registry.manager(MEXICO)          # the transaction manager for one shard
registry.default                  # the default shard's
registry.effective_shard(key)     # where this key lives
registry.shards                   # every configured identifier
```

You need these rarely. A repository routes for you; these are for the admin script, the
reconciliation job, and the test that asserts placement.

## Sharded repositories

Opt a repository in by passing a `ShardingStrategy` to `super().__init__`. The bundled
`EmbeddedBitsShardingStrategy` decodes the shard from the id's embedded bits:

```python
class WalletRepository(ModelRepository[Wallet, ShardedId[Wallet]]):
    def __init__(self, registry: DatabaseRegistry) -> None:
        super().__init__(
            Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy()
        )
```

That one keyword is the entire difference from an unsharded repository. There is no second line
to change, because the mapping is by field name and the id type already carries the shard —
where the Java port also has to widen `fromRecord` to rebuild a `ShardedId`.

What it changes about the inherited reads and writes:

| | routes by |
| --- | --- |
| `get`, `find`, `find_many`, `exists`, `update`, `delete` | the shard packed into the id |
| `add` | the shard packed into the id the model was created with |
| `find_where`, `find_one_where`, `exists_where`, `count` | **every shard** — a predicate says nothing about placement |
| anything passed `shard=` | that shard, and only that one |

`find_many(ids)` is the one worth calling out: it **groups the ids by shard and queries each
shard once**, rather than one query per id. Duplicates are collapsed first, and an empty input
returns an empty list without going near a database — never `IN ()`, and never an accidental
full-table read.

### Shard-aware accessors for custom queries

When an inherited method does not cover the question, the accessors take the same routing
information the inherited ones use — so a hand-written query lands on the right database without
you working out which one that is:

```python
def find_all_by_owner_beside(self, wallet_id: ShardedId[Wallet], owner_id: UUID) -> list[Wallet]:
    with self.readonly_db(key=wallet_id) as conn:          # that wallet's shard
        return conn.rows(
            select(WALLETS).where(WALLETS.c.owner_id == owner_id)
        ).fetch(self.from_row)
```

| accessor | connection |
|---|---|
| `db(key=…)` / `db(model=…)` / `db(shard=…)` | the writer for that shard, outside any transaction |
| `readonly_db(key=…)` / `readonly_db(shard=…)` | the **replica**, for a read that tolerates lag |
| `tx_db(key=…)` | the transaction already open on that shard, or `None` |
| `tx_db_else_db(key=…)` | that transaction, or no transaction at all |
| `dbs()` / `readonly_dbs()` | **every** shard's, one at a time, as `(shard, conn)` pairs |

All three are **keyword-only**, so a call always says which one it is routing by rather than
leaving it to argument position. Passing more than one is not an error — `shard=` wins, then
`model=`, then `key=` — and passing none routes to the default shard. Pass one.

*Which accessor* you reach for is the explicit decision here. There is no implicit "join the
transaction if there is one" default: `db()` is outside a transaction and `tx_db_else_db()` joins
one, and you say which you meant. See [Repositories](../database/repositories.md).

An **unsharded** repository's `dbs()` visits only the default shard, even when the registry holds
several. A repository that opted out of routing should not silently acquire a scatter.

## Self-describing IDs: `ShardedId`

The shard is packed into the `rand_b` region of a UUIDv7:

```
┌────────────────────────────────────────────────────────────────┐
│ MSB: [48-bit timestamp][4-bit version=7][12-bit rand_a]         │
│ LSB: [2-bit variant][8-bit GROUP][6-bit MEMBER][48-bit rand]    │
└────────────────────────────────────────────────────────────────┘
```

**This layout is a cross-language contract.** An id minted by a Java service and one minted here
carry the shard in the same bits, so either port can route the other's rows without a lookup.

Minting and reading:

```python
wallet_id = ShardedId[Wallet].generate(MEXICO)      # bonded to that shard for the row's life
wallet_id.shard                            # -> ShardIdentifier(1, 0)
wallet_id.minted_at                        # -> datetime, from the v7 timestamp
```

`ShardedId[Wallet].random()` uses the default shard. Because the timestamp is the high bits, ids sort
chronologically - which is what keeps a primary key index from fragmenting the way a v4 does.
Comparing two ids (`<`, `sorted`) follows Java's `UUID.compareTo`, which reads each half of the
uuid as a signed number: version 7 ids sort by time either way, but a random `Id[...]` whose
first digit is `8`-`f` sorts before one starting `0`-`7`, as in Java (the database's own order
is unsigned).

## Custom sharding strategies

When the shard is not derivable from the id — you need to move rows between shards, or the key
is something the id does not carry — implement `ShardingStrategy` and override `shard_for` on
the repository.

The cost is that the id stops being self-describing: every read that starts from an id alone
needs the strategy to locate the row first, which is a round trip you pay implicitly on each
lookup. A strategy that cannot resolve a shard from an id is fine in itself - it just has to
route from something else - but the first id-only call on such a repository raises
`UnsupportedError`, naming the strategy and telling you to override `shard_for()`. Refused rather
than guessed at, because guessing here means reading the right row from the wrong database and
finding nothing.


The interface is small. A strategy answers one question — which shard does this key live on —
and declares whether it can answer it from an id alone:

```python
class ShardingStrategy(Protocol):
    @property
    def uses_shard_aware_id(self) -> bool: ...

    def shard_for_id(self, key: object) -> ShardIdentifier | None: ...
```

`shard_for_id` returns `None` to mean *use the default shard*, rather than guessing.

`uses_shard_aware_id` is what the repository checks at construction. A strategy that cannot
resolve a shard from an id, used by a repository whose reads start from ids, raises there
rather than on the first lookup — the failure lands where somebody can fix it.

Two cases justify writing one. **Re-sharding live data**, because embedded bits are bonded to
the row for its lifetime and a move would invalidate the id. And **a key the id does not
carry**, where placement follows something decided after creation.

Both pay the same price: the id stops being self-describing, so a read starting from an id
needs a lookup before it can route. That is a round trip on every such read, and it is why the
embedded-bits strategy is the default despite being the less flexible one.

## Unregistered shards fall back to default, and say so

An id naming a shard this deployment has not configured **lands on the default**, in both ports.
That is what lets a staging database with one shard read ids minted where there are four.

It is also exactly what a mistyped shard identifier does, and that one writes to the wrong
database. Nothing at the call site can tell the two apart, so neither port tries to. **What both
now do instead is say it happened:**

```
WARNING  ekbatan.persistence  shard 9:9 is not registered, routing to default 0:0 [registered=['0:0', '1:0']]
```

Until `EKB-0200` the fallback was silent, which is the part that made a typo expensive: the
write succeeded, the row was in the wrong database, and nothing surfaced until somebody counted.
The warning is unconditional — it fires under either policy, because it is the diagnosis, not the
enforcement.

A deployment that holds the **whole** layout can go further, because there an unregistered shard
can only be a bug:

```python
ShardingConfig(
    default_shard=GLOBAL,
    groups=(...),
    unknown_shard_policy="fail",   # omitted -> "use_default_shard": fall back, and warn
)
```

```
ValidationError: shard 9:9 is not registered and unknown_shard_policy is 'fail'; registered: ['0:0', '1:0']
```

The value is read as Java reads it: case and separators do not matter, so `fail`, `FAIL`,
`useDefaultShard`, `use_default_shard` and `USE-DEFAULT-SHARD` all work, in code and in a
config file; an empty value, or `None`, is the default.

It is applied where routing resolves, so it covers **reads and writes alike** — `find(id)` on an
unregistered shard raises just as `add(model)` does. Leave it alone if your deployment is a
**subset** of the layout: turning it on there refuses exactly the foreign-id reads the fallback
exists for.

`None` is a different thing and warns about nothing: it means no strategy proposed a shard, which
is the ordinary unsharded path.

Running unsharded on purpose is `NoShardingStrategy`: every key maps to the default, and its
scatter accessors visit only that shard.

## Cross-shard actions

Refused by default. `CrossShardError` names the two shards involved.

```python
executor.execute(TransferAction, params,
                 config=ExecutionConfiguration(allow_cross_shard=True))
```

With it on: one transaction per shard, each committing independently, and the action's outbox row
written to **every** involved shard so each holds the full context. A failure rolls back only its
own shard and the framework logs the partial commit, naming what stayed.

The outbox row is duplicated to every involved shard **under the same id**, so a consumer
reading any one of them sees the whole action, and a consumer reading all of them can tell it was
one action rather than several.

**This is not two-phase commit.** There is no such thing to have across separate databases. Each
shard commits on its own, so a partial commit is not an edge case the framework failed to handle
— it is the actual semantics, and the flag exists to make you say you accept them. The shards
that already committed stay committed.

Per call rather than per executor, so the risk is visible where the risk is taken. An executor
default is for a dedicated executor whose actions are *all* designed for per-shard commits, and
nothing else.

Reach for it only when the operation is genuinely cross-region or cross-tenant and the domain is
designed to be eventually consistent. If a partial commit would need compensating, you want a
[saga](../concepts/sagas.md): one action per shard, chained through the outbox, with a compensating
action when a later step fails. Slower to write, and the only version that recovers from a
partial failure without a human.

## Migrating every shard

```python
migrate(layout, MIGRATIONS)                        # all of them
migrate(layout, MIGRATIONS, shards=[MEXICO])       # one
```

Ordered by the configuration rather than by the argument, and an unknown identifier is refused
rather than skipped. See [Migrations](migrations.md).

## What is sequential here

A scatter read and an event fan-out round visit shards **one at a time**; Java forks a virtual
thread per shard. On eight shards a round takes eight batches' time rather than one. Recorded in
[SHORTCOMINGS.md](../SHORTCOMINGS.md); the fix is a pool around the per-shard drain.

## What sharding does not provide

A scatter read and an event fan-out round visit shards **one at a time**; Java forks a virtual
thread per shard. On eight shards a round takes eight batches' time rather than one. Recorded in
[SHORTCOMINGS.md](../SHORTCOMINGS.md); the fix is a pool around the per-shard drain.

## See also

- [Concepts → Sharding](../concepts/sharding.md) - the reasoning
- [Migrations](migrations.md) - applying migrations across shards
- [`fastapi-wallet-rest-uv-sharded-pg`](../../ekbatan-py-examples/fastapi-wallet-rest-uv-sharded-pg)
