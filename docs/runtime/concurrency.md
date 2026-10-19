# Concurrency

The sync API blocks, and every place it could have run something concurrently is answered here -
five of them - along with the one way to use it wrongly. For an asyncio service there is also a
native async API, `ekbatan.aio`: see [Native asyncio](async.md).

If you read one section, read [Blocking is not free](#blocking-is-not-free-under-asyncio).

## The short version

| you are running | is it safe | what to do |
| --- | --- | --- |
| a thread per request (gunicorn sync, waitress, a worker loop) | yes | nothing |
| FastAPI or Starlette with plain `def` endpoints | yes | nothing - they run in a threadpool |
| FastAPI with `async def` endpoints calling the sync API | **no** | `await run_in_threadpool(...)` around each call - or use `ekbatan.aio` |
| FastAPI with `async def` endpoints on `ekbatan.aio` | yes | `await` every call; see Native asyncio |
| a process per worker (gunicorn, uvicorn `--workers`) | yes | nothing; pools are per process, so size them per process |
| a thread pool you own | yes | see [Work forked from inside an action](#2-work-forked-from-inside-an-action) |

## Five places threads could have been used

A CPython thread is an operating-system thread - about a megabyte of stack - and the GIL means
more of them buy concurrency while waiting on I/O, but not on CPU work. Threads are not free, so
each place where the framework could run things at the same time was decided on its own. There
are five, and they went five different ways.

### 1. The unit of work - no threads at all

The one that matters most needs none.

The open transaction is held in a **`ContextVar`**: a mapping from shard to connection. Nothing
is started; it answers one question - *is this execution context already inside a transaction
on this shard?*

A `ContextVar` is per-thread **and** per-async-task, so the same mechanism is correct under a
threaded server and an asyncio one. The threading model above the framework is the host's to
choose and the framework works under any of them, because the state is per-context rather than
global.

Pinned rather than assumed, in
[`tests/integration/test_concurrency.py`](../../tests/integration/test_concurrency.py): eight
concurrent units of work take eight distinct connections, cannot see each other's uncommitted
rows, and a pool worker does not carry a transaction into its next task.

### 2. Work forked from inside an action

Fan out, **join**, and only then touch the plan. A new thread starts with its own `ContextVar`
values, so work handed to a pool from inside a unit of work does **not** inherit the ambient
transaction.

**Sync**

```python
def perform(self, principal: str | None, params: Params) -> Result:
    with ThreadPoolExecutor() as pool:
        prices = list(pool.map(self.fetch_price, params.symbols))  # no plan, no transaction
    return self.plan.update(portfolio.priced(prices))              # back on this thread
```

**Async**

```python
async def perform(self, principal: str | None, params: Params) -> Result:
    # Gathered reads run at the same time, each on a connection of its own.
    prices = await asyncio.gather(*(self.fetch_price(s) for s in params.symbols))
    return self.plan.update(portfolio.priced(prices))              # after the gather
```

Read and compute in the pool; stage on the thread that owns the plan. In async mode the same
rule reads: gather the reads, then stage, once they are all back. `current_plan()` raises
rather than returning `None`, so getting it wrong is loud rather than subtle.

That a worker does not inherit the transaction is correct, not a limitation - a pool thread
outliving the unit of work and holding its connection is precisely the bug the design avoids.

### 3. The keyed-lock watchdog - one thread for all of them

Each outermost hold needs a watchdog that force-releases it at `max_hold`. The obvious shape is
a `threading.Timer` per hold, and each of those is an operating-system thread. This framework
used one per hold until it was measured, and it was worse in every direction - not only heavier
but **later**, because starting a thousand threads takes 70 ms before any of them can fire:

| 1,000 held locks | register | median late | threads | memory |
| --- | --- | --- | --- | --- |
| a thread per hold | 70 ms | 48 ms | 1000 | 40 MB |
| one watcher and a heap | 3 ms | 6 ms | 1 | ~0 |

There is now one watchdog thread per lock provider, holding a heap of deadlines and waiting
until the earliest is due - not polling on a cycle, which would have to pick an interval that is
either burning a core or arriving late. On the real holder: 1,000 held locks register in 2 ms,
10,000 in 18 ms, one thread either way.

Size your lock pool for the maximum number of leases held at once, not for traffic: a lease holds
its connection for as long as it is held, because that is what the lock *is*.

### 4. Job workers - a ceiling, with a knob

Job bodies run on daemon threads, and threads cost memory, so there is a ceiling - defaulting to
one per registered job.
That default is right: a recurring task is a single row, so it has at most one execution in
flight.

**A ceiling, not a reservation.** Workers start as work arrives and only when none is already
waiting, so twenty registered jobs with nothing due hold **no** worker threads. A thread, once
started, is kept rather than retired when idle; the standard library has no pool that retires
idle threads.

```python
job_registry().engine(engine).with_jobs(jobs).workers(3).build()
```

Two sizing rules, and the second has teeth:

- Workers run job **bodies**. Size for how many jobs can be due at once.
- **The jobs pool needs `workers + 3` connections**, and this page said "twice the worker
  count" until it was checked against the code. Three is not a margin: the scheduler's poll,
  heartbeat and dead-execution loops each take a connection when they run, on top of the
  workers. Twice is *more* than needed above three workers and **less** than needed below -
  at two workers it gives four where five are wanted. A job body holding the last connection
  starves the heartbeat, and an execution whose heartbeat stops is declared dead and **run
  again somewhere else**. db-scheduler warns at startup with the number it wants.

Give jobs their own pool, separate from the request pool, for the same reason: a traffic burst
must not be able to starve that heartbeat.

### 5. Fan-out and dispatch - shards and handlers side by side

The two event jobs - fan-out and dispatch - could run work side by side in two places: one per
shard in a round, and one per due notification in a batch. They do both.

**Per shard.** Both jobs drain their shards through a `ThreadPoolExecutor`, one thread
per shard. This is one of the cases where threads in CPython buy real wall-clock, because a
batch against a shard is a round trip to a *different* database - I/O the whole way, with the
GIL released for all of it. Measured, one 40 ms round trip per shard:

| shards | sequential | concurrent | speed-up |
| --- | --- | --- | --- |
| 1 | 44 ms | 44 ms | 1.0x |
| 4 | 177 ms | 46 ms | 3.9x |
| 8 | 354 ms | 48 ms | 7.3x |
| 16 | 694 ms | 48 ms | 14.6x |
| 32 | 1405 ms | 47 ms | 29.6x |

Flat: a round costs one shard's time however many shards there are.

**There is no setting for it.** A deployment with many shards
already holds a connection pool per shard, so a thread per shard for the length of a round is in
proportion to what it already has. A single shard builds no pool and runs inline.

The whole round is submitted before any failure is raised, so one
unreachable shard does not hold back its siblings, and results come back in shard order.

**Per notification.** A batch's handlers run at once, one thread each, in a pool per shard
bounded by the batch size. Measured at 20 ms per handler, a batch of 100 goes from 2376 ms one
after another to 28 ms. **Your handler code therefore runs at the same time as its siblings** -
write it so that is safe.

Outcomes are written one `UPDATE` per bucket, so a batch of 100 that all succeed is 1 statement
and a realistic mix is 4.

## Blocking is not free under asyncio

This is the one that will actually hurt you.

The sync API blocks, and under a **threaded** server that is exactly right - a blocking call
holds one worker thread and the GIL is released for the duration of the I/O.

Under an **asyncio** server it is not right, because a blocking call there stops the entire event
loop rather than one thread.

Measured: a 0.5s framework call from inside an `async def` handler let a background heartbeat
tick **5 times where a free loop manages about 55**. Every other request on that loop stalled for
the duration.

```python
# wrong, in an async def
wallet = wallets.get(wallet_id)

# right
wallet = await asyncio.to_thread(wallets.get, wallet_id)

# right, in Starlette or FastAPI
from starlette.concurrency import run_in_threadpool
wallet = await run_in_threadpool(wallets.get, wallet_id)

# right, with ekbatan.aio: an async repository, awaited
wallet = await wallets.get(wallet_id)
```

Two things make this less painful than it sounds.

**A plain `def` endpoint in FastAPI is already run in a threadpool** by the framework, so the
ordinary way of writing one is safe with none of the above.
[`fastapi-wallet-rest-uv-pg`](../../ekbatan-py-examples/fastapi-wallet-rest-uv-pg) is written that way
throughout, which is why it contains no `to_thread` at all.

**Or await the database natively, with `ekbatan.aio`.** The ambient transaction is a
`ContextVar`, per task as well as per thread, and `ekbatan.aio` is the same framework with every
database wait awaited: `async def perform`, async repositories, an executor you `await`, on
asyncpg, aiomysql or aiosqlite. Measured on one PostgreSQL with a pool of 40, it does about one
and a half times the work of the sync API in a 40-thread pool once 50 or more requests are in
flight, with a lone request a little slower. An `async def` handler calling the *sync* API
straight is still a real mistake with a real cost, and nothing in the API stops you making it.

## gevent: cheap blocking, with nothing to change

Everything above is about a framework that blocks. **gevent removes the cost of blocking** - for
*every* blocking call, not only the ones a library knows about.

```python
from gevent import monkey; monkey.patch_all()   # the very first thing, before any other import
import myapp
```

Then run it as you would any WSGI app - `gunicorn -k gevent myapp:app`.

**Measured on this stack:**

```
50 x time.sleep(0.5)   ->  0.51s   (one after another would be 25s)
20 x getaddrinfo       ->  0.47s
patched: time, socket, select, threading, ssl
```

A plain `time.sleep` yielded. So did DNS. That is the property that matters: a `requests.get()`
inside an action yields, and nothing has to be rewritten to make it so.

**psycopg supports it deliberately.** From psycopg 3's own source:

> Detect if some greenlet library has patched the select library. If this is the case, avoid to
> use the wait_c function as it doesn't behave in a collaborative way. **Currently supported:
> gevent.**

Under gevent it switches from `wait_c`, which blocks inside C, to `wait_poll`, which yields.

**This framework needs no change, and has none.** There is no gevent import in `src/`, no flag
and no mode. The two mechanisms that could have made it unsafe were checked by running them:

| | under gevent |
| --- | --- |
| the open transaction, held in a `ContextVar` | each greenlet keeps its own - no unit of work sees another's |
| the keyed lock's holder key, `threading.get_ident()` | 20 greenlets, 20 distinct idents - the exclusion holds |

And the whole test suite passes unchanged: **631 passed under gevent, 631 without** - container
tests included. `make test-gevent` is a CI job, so that stays a measurement rather than becoming
another sentence nobody rechecked.

**What it does not cover.** gevent serves **WSGI**. FastAPI, Litestar and anything else on an
asyncio loop cannot use it - for those, `ekbatan.aio` or the `to_thread` above is the answer,
and every example in this repository is FastAPI, so none of them demonstrates this. Nor do the
two mix: `ekbatan.aio` does not run under gevent's monkey-patching (its async runs are skipped
by `make test-gevent`), so a service picks one. SQLite is local file
I/O and will not yield, which costs nothing because it is local. And `patch_all()` must run
before any other import, which is an application discipline nothing can check for you.

**Sizing changes under it.** The advice on this page assumes an OS thread per worker. A greenlet
is not one, so `workers(n)` and the pool guidance are conservative under gevent rather than
wrong - the binding constraint becomes the database's connection limit, not the thread count.

## Processes, and where the pools live

A pool belongs to a process. Under `uvicorn --workers 4` or gunicorn with four workers there are
four of every pool, so the database sees `4 x (pool_size + job_pool_size + lock_pool_size)`
connections, not one set.

Two consequences:

- **Size per process, then multiply before comparing with `max_connections`.** This is the usual
  way a Python deployment runs out of connections.
- **Every worker process runs the scheduler.** That is fine and is the point - db-scheduler is
  cluster-exclusive, so four processes contend for the same rows and exactly one wins each
  execution. It is the same code path as four machines.

If you would rather not have every web worker also be a job worker, run the jobs in their own
process: build the service with the jobs registered and the HTTP app absent.
[`job-worker-uv-pg`](../../ekbatan-py-examples/job-worker-uv-pg) is that shape.

## The GIL, briefly

The framework's own work is I/O: queries, waits, commits. The GIL is released during those, so
threads give real concurrency for this workload and the usual Python caveat does not apply here.

Where it does apply is inside **your** action or handler bodies. A handler doing something
genuinely CPU-bound - parsing a large payload, computing a report - holds the GIL and blocks
every other thread in the process, job workers included. That work belongs in a process pool, or
in a different service.

Free-threaded CPython changes this, and nothing in this framework prevents it - there is no
global mutable state, and the ambient transaction is per-context by construction. It has not been
tested under a free-threaded build, so that is a reasonable expectation rather than a claim.

## See also

- [Wiring](../wiring.md) - the three pools, and what each is sized for
- [Wiring with FastAPI](../wiring-fastapi.md) - `def` versus `async def` at the endpoint
- [Native asyncio](async.md) - `ekbatan.aio`, when it helps, and what stays sync
