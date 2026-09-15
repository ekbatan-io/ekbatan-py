# Keyed locks

Optimistic locking is the framework's default and is in every write. A keyed lock is the other
tool: **pessimistic**, named by a string you choose, and held across whatever work you wrap in
it.

The difference is what happens when two callers race. Optimistic locking is correct by
*failing* - the loser's version check misses and it raises. A keyed lock makes the second caller
*wait and then succeed*, which is what anyone depositing into a busy wallet actually wants.

## The contract

```python
lease = locks.acquire(key, max_hold)                     # waits as long as it takes
lease = locks.try_acquire(key, max_wait, max_hold)       # None if it could not be had
with lease:
    ...
```

**`max_hold` is not a timeout on your code.** Nothing interrupts the block. It is the promise
that the lock is released even if this process dies holding it - so set it above the worst case
for the work inside.

**`try_acquire` returning `None` is contention, not failure.** The two are kept distinguishable
on purpose: contention is a "come back shortly", a failure is a bug or an outage. In an HTTP
handler that distinction is a 503 with a `Retry-After` rather than a 500.

```python
lease = service.locks.try_acquire(f"wallet:{wallet_id}", LOCK_WAIT, LOCK_HOLD)
if lease is None:
    raise HTTPException(503, "that wallet is busy; retry", headers={"Retry-After": "1"})
with lease:
    ...
```

## Where to take it

**At the caller, around `executor.execute(...)`.** This is the default and almost always right.
The lease has to span **both** `perform()` and the executor's commit, or the race it exists to
prevent reopens in the gap between them - and only the caller can wrap both.

```python
with service.locks.acquire(f"wallet:{wallet_id}", LOCK_HOLD):
    service.executor.execute(DepositMoneyAction, params)
```

**Inside `perform()`** is narrower and usually wrong for exactly that reason: `perform()` returns
before anything is written, so a lease taken and released inside it is gone before the commit.
It is defensible only for serialising a read-and-compute that has no write of its own.

## Reentrancy

Uniform across every backend: **the same key, taken again in the same context, re-enters.** It
does not deadlock and it does not take a second connection. The lock is released when the
outermost lease closes.

Re-entry is per context, the same way the ambient transaction is - a thread you hand work to does
not inherit your hold.

## The backends

| backend | mechanism | scope |
| --- | --- | --- |
| `PostgresKeyedLockProvider` | `pg_advisory_lock` | the cluster |
| `MySQLKeyedLockProvider` | `GET_LOCK` | the server |
| `MariaDBKeyedLockProvider` | `GET_LOCK` | the server |
| `RedisKeyedLockProvider` | Redis | the Redis deployment |
| `InProcessKeyedLockProvider` | `threading.Lock` | **this process only** |

The four SQL and in-process providers come from `ekbatan.locks`. The Redis one is imported from
`ekbatan.locks.redis` and needs the `redis` extra - it is deliberately *not* re-exported from the
package, because importing it eagerly would make `ekbatan.locks` fail for everyone who has not
installed redis-py.

The in-process one is for tests and single-process deployments. It is not a distributed lock and
will not tell you so at runtime - two instances each get their own.

Each key is hashed to a 64-bit integer, and the hash matches Java's exactly, **so a Python
service and a Java service contending for one key actually contend.** The MySQL lock name is
`ekbatan:<unsigned hex>`, byte for byte what Java produces.

### Wiring one

```python
locks = PostgresKeyedLockProvider(
    create_engine(url, pool_size=5, max_overflow=0)
)
```

**Give it its own pool.** A SQL keyed lock is *session*-scoped: each hold keeps a connection for
as long as the lease lasts, because that is what the lock physically is. A lease taken during a
request would otherwise pin a request connection for its duration.

Size that pool for the **maximum number of leases held at once**, not for traffic. See
[Wiring](../wiring.md).

That is also why `max_hold` is mandatory rather than optional: a connection held forever is a
pool exhausted forever.

### How the SQL backends wait

Not in one unbounded call. An unbounded `pg_advisory_lock` or `GET_LOCK` parks inside a socket
read that nothing can interrupt, so a shutdown would hang on it. Both ports wait in **5-second
segments** instead.

Three consequences:

- **Shutdown latency is up to 5 seconds.** Well inside a typical 30-second budget, not instant.
- **Fairness is best-effort.** Within a segment the waiter is genuinely queued inside the
  database, so hand-off from a releasing holder is immediate - this is not client-side spinning.
  But each new segment re-enters that queue at the *back*, so a long waiter yields position to
  newer arrivals. If that matters, you are contending hard enough that you want optimistic
  locking rather than a bigger lock.
- **A useful side effect:** the connection sends traffic every five seconds, so NAT gateways,
  load balancers and connection poolers stop reaping a long wait as idle.

A network partition landing *inside* a segment still parks the caller in a blocking socket read,
and no client-side loop can bound that. Set a socket timeout on the lock pool if you need a hard
one; it is not lock-specific, any long-running query has the same exposure.

## A sub-second wait is not a wait on MySQL

`GET_LOCK` takes a `DOUBLE`, and the two servers have diverged on what they do with the
fraction. Measured against the containers this suite runs, holding the lock and timing a second
session:

| timeout asked | MySQL 8.4.11 | MariaDB 11.4 |
| --- | --- | --- |
| 0.25s | **0.00s** | 0.26s |
| 0.5s | **0.00s** | 0.51s |
| 1s | 1.01s | 1.01s |
| 1.5s | **2.01s** | 1.51s |

MySQL truncates a sub-second timeout to zero and rounds a fractional one above a second *up*;
MariaDB honours the fraction. So `try_acquire(key, timedelta(milliseconds=500), ...)` on MySQL is
a **poll, not a wait** - it returns almost immediately.

The value is passed through as-is, as Java passes it. Rounding up here would make a 50 ms wait
take a second, a surprise in the other direction; polling in a loop would trade a bounded
imprecision for load on the database. What is worth having is that it is written down and tested
rather than discovered.

Java's own comment says MySQL "rounds up to the next whole second". That holds above one second
and not below it: a 500 ms wait does not become 1 s, it becomes none.

## The watchdog costs a thread here

Each outermost hold gets a watchdog that force-releases at `max_hold`. Java uses
`Thread.ofVirtual()`, which is effectively free. Here it is a `threading.Timer` - a real OS
thread.

Same behaviour, and fine at the scale a keyed lock is for. But **a service holding a thousand
locks at once holds a thousand threads.** If that becomes the constraint the fix is one timer
thread with a heap of deadlines rather than a thread per hold - contained, not a redesign. It is
recorded in [SHORTCOMINGS.md](../SHORTCOMINGS.md).

## Optimistic or pessimistic?

| | |
| --- | --- |
| **optimistic** (the default, always on) | no lock, no waiting, no connection held. The loser raises and the action retries. Right when conflicts are rare |
| **pessimistic** (a keyed lock) | the second caller waits and succeeds. Right when conflicts are expected and failing is the wrong answer |

They compose: a keyed lock does not turn optimistic locking off, and a write inside a lease still
carries its version check. That is a belt-and-braces you get for free, and it is what catches a
writer that did not take the lock.

**Not the right primitive at very high concurrency.** A thousand callers queued on one key is a
thousand held connections on the SQL backends and a queue with no fairness promise. At that point
the answer is to shard the key, or to serialise the work through a job rather than through a
lock.

## See also

- [Models and entities](../concepts/models-and-entities.md) - optimistic locking, which is always on
- [Wiring](../wiring.md) - the lock pool, and how to size it
- [Wiring with FastAPI](../wiring-fastapi.md) - a lease spanning a request
