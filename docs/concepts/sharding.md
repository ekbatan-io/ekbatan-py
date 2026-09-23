# Sharding

Opt-in, with zero overhead when it is off. When you do need it, this framework separates two
reasons to shard - policy and capacity - and gives each its own axis. Your actions and models
stay sharding-agnostic; routing happens at the edges.

## Two reasons to shard, two axes

Most sharding designs use one number for "which database does this row live on" and let it
conflate everything. A `ShardIdentifier` here is a `(group, member)` pair, and the two answer
different questions:

| axis | the question | cardinality driven by | range |
| --- | --- | --- | --- |
| **group** (policy) | where is this data *allowed* to live? | data residency law, contractual tenant isolation, business-domain separation. You do not pick how many you have; the world picks | 8 bits → 256 groups |
| **member** (capacity) | how many writes per second can one database take? | capacity planning. You add members when one database starts to fall over | 6 bits → 64 members per group |

The split matters because the two have completely different operational profiles. A new **group**
- "we signed a bank that requires onshore data" - is a major event: new infrastructure, new
region, new compliance posture, a separate failure domain. A new **member** is routine: same
region, same VPC, same playbook, more compute.

Conflating them into "shard #47" forces every routing decision through one lens. Routing for
compliance is deterministic from the customer's properties; routing for capacity is whatever
distributes load, usually a hash. Different concerns, different logic, different change cadences.

## Three strategies, in order of weight

### Single-database (no sharding)

Everything at `(group=0, member=0)` - `DEFAULT_SHARD`, which prints as `0:0`. You write actions
exactly as you would if sharding did not exist: `Id[Wallet]` rather than `ShardedId[Wallet]`, no
shard parameter anywhere. The registry and transaction manager are still in the path; every call
resolves to the same database.

> Use it when you have no write-throughput problem and no policy forcing data location - which is
> almost everyone. Stay here as long as you can.

The day you outgrow it, the action code does not change. The model's id type and the
configuration do.

### Embedded-bits UUID (`ShardedId`)

The `(group, member)` pair is encoded into 14 bits of the UUID itself, inside the `rand_b` field
of a UUIDv7. The crucial property: **the shard is recoverable from the id, with no lookup.**

```
┌────────────────────────────────────────────────────────────────┐
│ MSB: [48-bit timestamp][4-bit version=7][12-bit rand_a]         │
│ LSB: [2-bit variant][8-bit GROUP][6-bit MEMBER][48-bit rand]    │
└────────────────────────────────────────────────────────────────┘
```

A request arrives with a wallet id; the framework reads the shard bits out of it, finds the
database, opens the transaction. No directory service, no sticky sessions at the load balancer.
The id describes itself.

The shard is chosen **at creation**: `ShardedId[Wallet].generate(shard)`, and from then on the id and its
shard are bonded for the row's life. Choosing it is your call - typically a small selector
mapping tenant, region or regulatory class to a shard, inside the create action.

This layout is a **cross-language contract**. An id minted by a Java service and one minted here
have their shard in the same bits, so either can route the other's rows.

> Use it when each row's shard is decidable from what you know at creation time and you will
> never move rows between shards. That covers most cases.

### Custom sharding strategy (full control)

If embedded bits do not fit - you need to re-shard live data, or the shard is not knowable at
creation but is derivable from the row - supply a `ShardingStrategy`. It takes a model or a query
parameter and returns a `ShardIdentifier`, and is called once per operation, before the
transaction opens.

The cost is that the id is no longer self-describing. Any read that starts from an id alone needs
the strategy to find the row first, which is a round trip you pay implicitly. Use embedded bits
if you can; reach for this when you cannot.

## Cross-shard actions: one transaction per shard, never atomic across

By default, an action's staged changes must all route to one shard. One transaction, everything
written, commit. That is the only way the outbox guarantee survives, because atomic-across-
databases does not exist.

With `allow_cross_shard=True`, the executor groups changes by shard and opens **one transaction
per shard**, each committing independently. A failure rolls back only its own shard; shards that
already committed stay committed, and the framework logs that loudly, naming them.

```python
executor.execute(TransferAction, params,
                 config=ExecutionConfiguration(allow_cross_shard=True))
```

Prefer opting in **per call**, so the risk is visible at the call site. An executor-wide default
should be reserved for a dedicated executor whose actions are all designed for per-shard commits
and eventual consistency.

What it buys you, and what it costs:

- **Per-shard atomicity, not global.** Shard A can commit while shard B rolls back. You are back
  in [dual-write](the-dual-write-trap.md) territory for that action - except the two systems are
  now two shards of your own database rather than a database and a broker. The mitigation is a
  [saga](sagas.md): one action per shard, chained by outbox events, with compensation.
- **No cross-shard reads in one transaction.** Joins across shards happen in application code,
  after each shard's transaction has committed.

The good news is that most actions never need it. Routing customers to the same shard as their
wallets, and orders to the same shard as their customer, removes most cross-shard pressure for
free.

## Fan-out is sequential here

Worth knowing before you plan a large deployment, and recorded in
[SHORTCOMINGS.md](../SHORTCOMINGS.md) rather than left to be discovered: a scatter read across N
shards, and the event fan-out round, visit shards **one at a time**. Java forks a virtual thread
per shard and does them at once.

On eight shards a fan-out round therefore takes eight batches' time rather than one. Rounds are
short - the work per shard is one bounded batch - so it has not bitten, and the fix is a pool
around the existing per-shard drain rather than a redesign. But it is a real difference from
Java, and eight shards is where you would first feel it.

## What sharding doesn't solve

It is a write-throughput-and-policy tool. It does not:

- **give you cross-shard transactions** - no framework can; that is a property of distributed
  systems, not of this one;
- **speed up scatter-gather reads** - a predicate across N members hits N databases, and the
  latency is the max, not the mean (and here they are sequential, so it is the sum);
- **remove hot shards** - if 80% of traffic targets one customer, that customer's shard is hot
  however many others exist. Pre-splitting a hot tenant across members of its own group is a
  separate technique.

If you are reaching for sharding to fix a *read latency* problem, it is probably the wrong tool.
Caching, read replicas and a read model are usually the answer.

## See also

- [Sagas](sagas.md) - the right shape for multi-shard work
- [The dual-write trap](the-dual-write-trap.md) - why cross-shard is conceptually a dual write
- [Actions](actions.md) - where changes get grouped by shard
- [`fastapi_wallet_rest_sharded_pg`](../../ekbatan-py-examples/fastapi_wallet_rest_sharded_pg) -
  a runnable two-database wallet routed by the shard bits in each id
