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


Four things that contract fixes, and each of them is a decision somebody would otherwise make
wrongly under pressure:

- **A lease always expires.** `max_hold` is mandatory, not optional. It is not a timeout on your
  code — it is the promise that the lock is released even if this process dies holding it. Set
  it above the worst case for the work inside.
- **Contention is not failure.** `try_acquire` returns `None` when the lease cannot be had
  inside `max_wait`; `acquire` raises `LockAcquisitionError`. Keeping those distinguishable is
  what lets an HTTP handler answer 503 with a `Retry-After` instead of 500.
- **The key is a string you choose**, and its granularity is the whole design. `wallet:{id}`
  serialises one wallet; `wallets` serialises every wallet, which is a different product.
- **Releasing is not your job.** The lease is a context manager, so the release happens on the
  way out of the block whichever way you leave it — including an exception, which is the path
  that gets forgotten when release is a method call.

## Where to acquire the lock

### ✅ At the caller, around `executor.execute(...)` — the default

```
├─ acquire lock       ←─┐
├─ executor.execute()   │
│   ├─ Action.perform() │ lock held
│   │   ├─ read state   │
│   │   └─ plan.update  │
│   └─ commit           │
└─ release lock       ←─┘
```

```python
with service.locks.acquire(f"wallet:{wallet_id}", LOCK_HOLD):
    service.executor.execute(DepositMoneyAction, params)
```

The lease wraps **both** `perform()` and the executor's commit. A concurrent acquirer cannot
enter until the previous commit is fully visible — so there is no race window, and no
optimistic-locking conflict caused by writes that were supposed to be coordinated.

**This is what most applications want.** Whenever the goal is "concurrent writes on the same key
must produce a consistent result", the lock has to span the commit phase, and only the caller
holds a scope that contains it.

Size `LOCK_HOLD` as an honest upper bound on `perform()` plus the commit, not as a guess: it is
the watchdog's deadline, and a lease that expires mid-commit hands the key to someone else while
you are still writing.

### ⚠️ Inside `Action.perform()` — narrower use case only

Defensible only for serialising a read-and-compute that has no write of its own.

```
└─ executor.execute()
    ├─ Action.perform()
    │   ├─ acquire lock   ←─┐
    │   ├─ read state       │ lock held
    │   ├─ plan.update      │
    │   └─ release lock   ←─┘  (the `with` block exits)
    └─ commit                  ← race window: another acquirer reads pre-commit state
```

The lease closes *before* the framework's commit runs. Another acquirer takes the lock
immediately, reads the still-pre-commit state, computes a stale-version update, and collides at
its own commit. Optimistic locking catches it — `StaleRecordError`, and the executor re-runs
phase one — so **no data is lost**. But calls fail and retry under contention that a
caller-side lease would have prevented entirely. The Java port measured its own version of this:
ten concurrent deposits, about six committing first time.

This placement is for **side-effect coordination, not commit serialisation**:

- An outbound webhook where you want at-most-one in flight per resource. The lock is held across
  the external call; the database write is thin record-keeping.
- A long external compute — PDF generation, an inference call — where you want to deduplicate
  in-flight work per key.
- Anywhere the write is idempotent (an upsert) and serialising it was never the goal.

For **write-path serialisation**, which is the common case, acquire at the caller.

### Why not just always lock inside `perform()`?

Because `perform()` is the body of the action, not the transaction. The change plan is built
there, but nothing is open yet: the executor opens the transaction *after* `perform()` returns
the plan, and commits it before `execute(...)` returns to the caller. The transaction lifecycle
belongs to the executor.

So the action body has no way to "hold the lock until the executor commits" — the lease closes
when the `with` block exits, and that is before the transaction it was meant to protect even
begins. A lease taken and released inside `perform()` is gone exactly when it was needed.

The caller-side pattern works for the mirror-image reason: the caller owns a boundary that
*contains* the executor's whole lifecycle. Its lease closes after `execute(...)` returns, which
is after the commit.

## Reentrancy

**The same key, taken again in the same context, re-enters.** It does not deadlock and it does
not take a second connection. The backend lock is released only when the **outermost** lease
closes — or when its watchdog fires.

**The first acquire's `max_hold` governs the watchdog; a re-entry's `max_hold` is ignored.** That
is stricter than the last-call-wins convention some lock libraries use, and it is the only rule
under which the outer region's reasoning stays true: it committed to a worst case, and an inner
call cannot pull that commitment out from under it. The same is true in the Java port, and for
the same reason.

Re-entry is **per thread, not per call stack** — as in Java, where the holder is the thread too.
A thread you hand work to is a different identity and will block on a key you hold. That is not a
bug to work around: if the child could inherit the hold, "this key is held by one holder" would
stop being true.

### Not on an event loop

**Taking a lock on an asyncio event loop's own thread is refused** with a `ValidationError`.
There, every request shares the one thread, so a second request would count as the same holder
and walk straight into the first one's lock - and a request that did have to wait would freeze
the loop, the holder included. Ekbatan is synchronous throughout; call it from a worker thread,
where each request is its own holder again: a plain `def` endpoint, which FastAPI and Starlette
already run on one, or `await asyncio.to_thread(...)` from an `async def`. Threaded servers and
gevent are unaffected.

One shared helper owns the per-`(thread, key)` counter, the watchdog timer and the
release arbitration, so each backend implements only a low-level acquire and release. That is
what makes "uniform across all five" checkable rather than aspirational.

## Five backends

| Provider | Scope | Mechanism | Hold | Notes |
|---|---|---|---|---|
| `PostgresKeyedLockProvider` | cross-process | `pg_advisory_lock` (session-scoped) | session + watchdog | Auto-released if the session ends — a crashed process or a dropped network connection releases the key without anyone intervening. One connection per lease |
| `MySQLKeyedLockProvider` | the server | `GET_LOCK(...)` | session + watchdog | A sub-second `max_wait` is **not a wait** — see [below](#a-sub-second-wait-is-not-a-wait-on-mysql) |
| `MariaDBKeyedLockProvider` | the server | `GET_LOCK(...)` | session + watchdog | Same statements as the MySQL one, separate because MariaDB honours sub-second timeouts |
| `RedisKeyedLockProvider` | the Redis deployment | `SET NX PX` | **TTL** + watchdog | Sub-millisecond hand-off. Single-master only — **not** Redlock |

There is **no in-process provider**, as there is none in Java. A lock held in one process's
memory excludes nothing in another, and a Python service in production is several worker
processes - so it would go on working and stop excluding anything, without saying so. Every
backend above excludes across processes and machines alike.

The "Hold" column is the difference that matters operationally. The SQL backends hold a *session*
— the lock exists because a connection is open, so losing the connection releases it. Redis holds
a **TTL** — the key expires whether or not the holder is alive, so `max_hold` there has to be an
honest upper bound on the work rather than a generous guess. A holder that dies releases nothing;
the key simply expires and the next caller takes it.

The Redis provider deliberately drives the expiry itself rather than letting a client library's
own watchdog extend the lease, so the framework's first-acquire-wins rule holds there too.

The four SQL and in-process providers come from `ekbatan.locks`. The Redis one is imported from
`ekbatan.locks.redis` and needs the `redis` extra - it is deliberately *not* re-exported from the
package, because importing it eagerly would make `ekbatan.locks` fail for everyone who has not
installed redis-py.

The in-process one is for tests and single-process deployments. It is not a distributed lock and
will not tell you so at runtime - two instances each get their own.

Each key is hashed to a 64-bit integer, and the hash matches Java's exactly, **so a Python
service and a Java service contending for one key actually contend.** The MySQL lock name is
the bare unsigned hex of that hash (`813e4046e5b48a46` for `wallet:abc`), byte for byte what
Java produces, with no prefix - a test holds a lock and asks the server for Java's name.

### Wiring up a backend

```python
locks = PostgresKeyedLockProvider(
    create_engine(url, pool_size=5, max_overflow=0)
)
```

A provider is a plain object you construct and hand to whatever needs it — there is no registry
to declare it in and nothing discovers it. Close it when the service closes: `locks.close()`
releases every lease still held, which for a SQL backend is what returns those connections. The
engine is yours — you created it, so you dispose it — and the order matters: release the leases
before disposing the pool they are holding connections from.

Redis takes a client rather than an engine:

```python
from ekbatan.locks.redis import RedisKeyedLockProvider

locks = RedisKeyedLockProvider(redis.Redis(host="localhost"))
```

Size the SQL pool by **concurrent leases**, not by request rate: a lease is a connection for as
long as it is held. If twelve requests can hold twelve different keys at once, the pool needs
twelve, and `max_overflow=0` so the thirteenth waits rather than opening a connection the
database has not budgeted for.

### Dedicated pool via its own engine

**Give it its own pool.** A SQL keyed lock is *session*-scoped: each hold keeps a connection for
as long as the lease lasts, because that is what the lock physically is. A lease taken during a
request would otherwise pin a request connection for its duration.

Size that pool for the **maximum number of leases held at once**, not for traffic. See
[Wiring](../wiring.md).

That is also why `max_hold` is mandatory rather than optional: a connection held forever is a
pool exhausted forever.


Sizing it is a different exercise from sizing a request pool, and the number is usually much
smaller than people expect. It is bounded by **how many leases are held at the same instant**,
not by request rate: a hundred requests a second each holding a lease for 20 ms need two or
three connections, while ten long imports holding one for a minute each need ten.

`max_overflow=0` is deliberate on that engine. Overflow exists to absorb bursts, and a burst of
lock holders is exactly the thing you want to refuse rather than absorb — a refused
`try_acquire` is a 503 the caller can retry, while an absorbed one is an unbounded number of
connections held for the length of a lease.

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

## The watchdog is one thread, not one per hold

Each outermost hold gets a watchdog that force-releases at `max_hold`. Java uses
`Thread.ofVirtual()` per hold, which is effectively free - a hundred thousand of them fire 48 ms
late. A `threading.Timer` is a real OS thread, so this port keeps **one** watchdog thread holding
a heap of deadlines, waiting until the earliest is due.

| 1,000 held locks | register | median late | threads | memory |
| --- | --- | --- | --- | --- |
| a thread per hold | 70 ms | 48 ms | 1000 | 40 MB |
| one watcher and a heap | 3 ms | 6 ms | 1 | ~0 |

Measured on the holder itself: 1,000 held locks register in 2 ms, 10,000 in 18 ms, one thread
either way. The thread is not started until the first hold is registered, so a process that
never takes a lock never pays for it.

## Optimistic vs pessimistic - picking one

| | |
| --- | --- |
| **optimistic** (the default, always on) | no lock, no waiting, no connection held. The loser raises and the action retries. Right when conflicts are rare |
| **pessimistic** (a keyed lock) | the second caller waits and succeeds. Right when conflicts are expected and failing is the wrong answer |

They compose: a keyed lock does not turn optimistic locking off, and a write inside a lease still
carries its version check. That is a belt-and-braces you get for free, and it is what catches a
writer that did not take the lock.

## Caveat: not the right primitive at very high concurrency

A keyed lock serialises everything contending for one key, and a SQL one costs a connection per
hold while it does. Both of those are fine at the scale this is meant for — a busy wallet, a
tenant-wide import, a job that must not overlap itself — and neither scales to thousands of
simultaneous holders of the same key.

Two symptoms tell you that you are past it. Waiters pile up faster than holders drain, so
`try_acquire` starts returning `None` under normal traffic rather than under incidents. Or the
lock pool saturates, and a request that could have proceeded blocks waiting for a connection
whose only job is to hold a lock.

At that point the answer is not a bigger pool. It is to stop serialising: shard the key so the
contention spreads, batch the work so one holder does what many were queueing to do, or move to
optimistic concurrency and let the version check reject the losers — which is what the framework
does by default, and the reason a keyed lock is the exception rather than the tool.

## See also

- [Models and entities](../concepts/models-and-entities.md) - optimistic locking, which is always on
- [Wiring](../wiring.md) - the lock pool, and how to size it
- [Wiring with FastAPI](../wiring-fastapi.md) - a lease spanning a request
