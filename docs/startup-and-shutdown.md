# Startup and shutdown

## Why read this page

Your app works on your laptop. Then it meets the real world, and one of these happens:

- **A deploy cuts a job off halfway.** A new version is rolled out, the old process is stopped
  while a job is sending emails, and later another instance runs the whole job again - so some
  people get the email twice. *Closing the job registry properly prevents it; see the shutdown
  steps below, and "A worker without a web server" for programs that have no web server.*
- **The database says "too many connections".** It ran fine with one process; then the server was
  set to four workers, or the app to three replicas, and each copy opened its own connections.
  *See "When your server runs several processes".*
- **ekbatan raises `ForkedProcessError`.** Your server copies your app into several workers after
  it has started (gunicorn with `--preload`, uwsgi, Celery), and an object made before the copy
  is being used in a worker. Shared that way, its database connections would mix two workers'
  queries, so ekbatan stops it. *See "Why at startup, and not at import time".*
- **ekbatan raises `BlockingCallError`.** An `async def` endpoint called the sync API, which
  would have frozen every other request on the server while it waited on the database. *See
  "Two mistakes ekbatan refuses".*

All of these come from the same place: a few objects your app creates hold open database
connections or background threads, and they have to be created, and closed, in the right place.
This page shows that place and explains each step. Read it before your app goes to production,
when you move to a server with several workers, or when you meet one of those two errors.

## The idea

Most of what you write with ekbatan - models, repositories, actions, the executor - are plain
objects. You create them and use them, and when your program ends they simply go away.

A few objects are different: they hold things that are open while your program runs. The
database registry holds open connections to your database. The job registry runs your scheduled
jobs on background threads. These need two moments of care: they are created when your program
starts, and closed when it stops. Closing them is what lets running jobs finish and connections
end cleanly instead of being cut off.

The rest of this page shows where those two moments are in a FastAPI app and in a plain worker,
and why each step is where it is. After that come the two mistakes ekbatan refuses for you, what
changes when your server runs several processes, and notes for specific servers.

## What needs a start and a stop

There are three kinds of object to look after.

**The database registry** (`DatabaseRegistry`, or `AsyncDatabaseRegistry` for async code) holds
a pool of database connections for each database in `ekbatan.yaml`. You create one, and every
repository and the executor are built on it. It opens connections as they are needed, and
`close()` closes them all.

**The job registry** (`JobRegistry`) runs your distributed jobs - the event jobs and any
scheduled jobs of your own - on background threads. After creating it you call `start()`. At
shutdown you call `stop()`, which waits for running jobs to finish (up to a time limit you set),
so a deploy does not cut a job off halfway.

**The extra connection pools.** The job registry polls the database, and a keyed lock provider
holds a connection for each lock it has taken. Each of them gets its own small pool, made with
`engine_for(...)` from a named entry in `ekbatan.yaml`, so they never take connections away from
your requests. You create these pools, so you close them, with `dispose()`.

In short:

| object | created at startup | closed at shutdown |
| --- | --- | --- |
| database registry | `DatabaseRegistry.from_config(layout)` | `registry.close()` |
| job registry | `job_registry()...build()`, then `jobs.start()` | `jobs.stop()` |
| a pool for the jobs or the locks | `engine_for(member.config_for("jobsConfig"))` | `engine.dispose()` |
| lock provider | `PostgresKeyedLockProvider.builder().engine(lock_engine).build()` | `locks.close()`, then dispose its pool |
| repositories, executor, actions | create them | nothing to do |

A fourth object appears only if an event handler is `async def`: the `HandlerLoop` its async
work runs on. It is covered below with the async app.

### Why "at startup", and not at import time

It is tempting to create the registry at the top of a module, next to the imports. Don't: a
module is imported in places where your app is not running - a test, a script, a tool that only
reads your code - and some servers import your app once and then copy the whole process into
several workers. An object created at import time would then exist, and later connect, in the
wrong place - or be copied into a worker together with connections that belong to another process.

Your app's startup code runs exactly once, in the process that serves the requests. That is where
these objects belong. In a FastAPI app it is the lifespan; in a plain worker, the top of
`main()`.

## A FastAPI app

FastAPI runs a function called the *lifespan* when the app starts and again when it stops. The
code before its `yield` runs at startup, and the code after it at shutdown - exactly the two
moments we need.

Here is a complete lifespan for an app with jobs and a keyed lock provider, first for the sync
API and then for `ekbatan.aio`. Read it once; the steps below explain it. `your_jobs` stands for
your own function that returns the jobs to run.

**Sync**

```python
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from ekbatan import DatabaseRegistry, engine_for
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import job_registry
from ekbatan.locks import PostgresKeyedLockProvider
from ekbatan.migrations import migrate

CONFIG_FILE = Path("ekbatan.yaml")
MIGRATIONS = Path("migrations")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = load_ekbatan_config(CONFIG_FILE)
    layout = config.sharding
    member = layout.groups[0].members[0]

    # Startup.
    await asyncio.to_thread(migrate, layout, MIGRATIONS)
    registry = DatabaseRegistry.from_config(layout)  # opens no connection yet
    lock_engine = engine_for(member.config_for("lockConfig"))
    locks = PostgresKeyedLockProvider.builder().engine(lock_engine).build()
    jobs_engine = engine_for(member.config_for("jobsConfig"))
    jobs = (
        job_registry()
        .engine(jobs_engine)
        .with_jobs(your_jobs(registry))
        .with_config(config.jobs)
        .build()
    )
    await asyncio.to_thread(jobs.start)
    # The repositories and the ActionExecutor are built on `registry` and hold nothing.
    app.state.registry = registry
    app.state.locks = locks
    try:
        yield
    finally:
        # Shutdown: stop taking work first, then close what the work runs on.
        await asyncio.to_thread(jobs.stop)
        locks.close()
        lock_engine.dispose()
        jobs_engine.dispose()
        await asyncio.to_thread(registry.close)
```

**Async**

If your endpoints are `async def` and use `ekbatan.aio`, the shape is the same, with two
differences. The endpoints get an `AsyncDatabaseRegistry`; it runs on the app's own event loop, and
is closed there with `await registry.aclose()`. The jobs still run on background threads, so they
keep a small sync registry of their own, `jobs_registry`, built over the jobs' pool - closing it
also disposes that pool:

```python
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from ekbatan import DatabaseRegistry, TransactionManager, engine_for
from ekbatan.aio import AsyncDatabaseRegistry, async_engine_for
from ekbatan.aio.locks import AsyncPostgresKeyedLockProvider
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import job_registry
from ekbatan.migrations import migrate

CONFIG_FILE = Path("ekbatan.yaml")
MIGRATIONS = Path("migrations")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = load_ekbatan_config(CONFIG_FILE)
    layout = config.sharding
    member = layout.groups[0].members[0]

    # Startup.
    await asyncio.to_thread(migrate, layout, MIGRATIONS)
    registry = AsyncDatabaseRegistry.from_config(layout)  # for the endpoints
    lock_engine = async_engine_for(member.config_for("lockConfig"))
    locks = AsyncPostgresKeyedLockProvider.builder().engine(lock_engine).build()
    jobs_engine = engine_for(member.config_for("jobsConfig"))
    jobs_registry = (
        DatabaseRegistry.builder()
        .with_default_database(TransactionManager(layout.default_shard, jobs_engine))
        .build()
    )
    jobs = (
        job_registry()
        .engine(jobs_engine)
        .with_jobs(your_jobs(jobs_registry))
        .with_config(config.jobs)
        .build()
    )
    await asyncio.to_thread(jobs.start)
    app.state.registry = registry
    app.state.locks = locks
    try:
        yield
    finally:
        # Shutdown: stop taking work first, then close what the work runs on.
        await asyncio.to_thread(jobs.stop)
        locks.close()
        await lock_engine.dispose()
        await asyncio.to_thread(jobs_registry.close)  # disposes jobs_engine too
        await registry.aclose()
```

**Step by step:**

1. **Read the settings.** `load_ekbatan_config` reads `ekbatan.yaml`: which databases there are,
   and the named pools (`lockConfig`, `jobsConfig`) that `member.config_for(...)` picks out.
2. **Run the migrations** first, so the tables exist before anything uses them. `migrate` waits
   on the database, and the lifespan is `async def` - code that waits inside `async def` stops
   the whole server from answering while it waits. `asyncio.to_thread(...)` runs it on a
   separate thread instead, and the lifespan waits for it without blocking anything. The same
   goes for every sync call below that waits on the database. (Forget it, and ekbatan stops you
   with a clear error - see *Two mistakes ekbatan refuses* below.)
3. **Create the database registry.** Creating it opens nothing; connections open when the first
   request needs one.
4. **Create the lock pool and the lock provider**, then **the jobs' pool and the job registry**,
   and start the jobs. From here on, jobs run in the background.
5. **`yield`**: the app now serves requests. Endpoints reach the registry and the lock provider
   through `app.state`.
6. **Shutdown, in reverse order.** The jobs stop first, so nothing is still working while its
   connections are being closed - and `stop()` waits for a running job to finish. Then the lock
   provider and the pools close, and the registry last.

**If an event handler is `async def`.** Event handlers run in the background, after an action
commits, on the job registry's threads. An `async def` handler needs an event loop of its own to
run on: a `HandlerLoop`. What the handler awaits - its own async registry - belongs to that loop,
and is closed on it. Add this to the lifespan above:

```python
    # Startup, before building the jobs:
    handler_loop = HandlerLoop()  # from ekbatan.events
    handler_registry = AsyncDatabaseRegistry.from_config(layout)  # used only on handler_loop
    # ... the handlers' executor on handler_registry, and
    # EventHandlingJob.builder()...handler_loop(handler_loop)...build() among your jobs

    # Shutdown, right after jobs.stop():
    await asyncio.to_thread(handler_loop.run, handler_registry.aclose())
    await asyncio.to_thread(handler_loop.close)
```

`member.config_for(...)` returns `None` when `ekbatan.yaml` has no entry of that name; check it
in your own code, as the examples do.

**In the examples.** The example apps put all of the above into a class of their own,
`WalletService`, with a `build()` function that creates everything and a `close()` that closes
it, and the lifespan calls those. That is the examples' choice, not something ekbatan provides -
you can do the same, or keep it in the lifespan as shown here. See
[`fastapi-wallet-rest-uv-pg`'s `app.py`](../ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/app.py)
and, async,
[`fastapi-wallet-rest-uv-async-pg`'s](../ekbatan-py-examples/fastapi-wallet-rest-uv-async-pg/src/fastapi_wallet_rest_async_pg/app.py);
[Wiring with FastAPI](wiring-fastapi.md) walks through them.

## A worker without a web server

Some programs have no web server: a process that only runs jobs, consumes messages, or runs a
script. The same two moments apply, at the top and the bottom of `main()`.

What is different is *when to stop*. A web server tells your lifespan when it is time. A plain
worker has to listen for that itself. When Docker or Kubernetes stops a container, it sends the
process a signal called **SIGTERM**, meaning "finish up and exit". The worker below waits for it,
then shuts down in the same order as before:

**Sync**

```python
import signal
import threading
from pathlib import Path

from ekbatan import DatabaseRegistry, engine_for
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import job_registry
from ekbatan.migrations import migrate

CONFIG_FILE = Path("ekbatan.yaml")
MIGRATIONS = Path("migrations")


def main() -> None:
    config = load_ekbatan_config(CONFIG_FILE)
    layout = config.sharding
    migrate(layout, MIGRATIONS)
    registry = DatabaseRegistry.from_config(layout)
    jobs_engine = engine_for(layout.groups[0].members[0].config_for("jobsConfig"))
    jobs = (
        job_registry()
        .engine(jobs_engine)
        .with_jobs(your_jobs(registry))
        .with_config(config.jobs)
        .build()
    )

    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())  # a container stop
    jobs.start()
    try:
        stopping.wait()  # until SIGTERM, or Ctrl-C
    finally:
        jobs.stop()  # running jobs finish first, up to shutdown_max_wait
        jobs_engine.dispose()
        registry.close()
```

There is no event loop here, so nothing needs `asyncio.to_thread`: `main()` calls everything
directly.

If you'd rather not handle SIGTERM yourself, call `.register_shutdown_hook()` on the job registry
builder instead: SIGTERM and Ctrl-C then stop the jobs for you, and the process exits as it would
have. If neither is done, the job registry logs one warning when it starts, saying that running
jobs would be cut off by a container stop.

**Async**

If the worker's own work is `async def`, it runs on a `HandlerLoop`, with an async registry used
only on that loop. The jobs still run on threads, over a sync registry of their own:

```python
import signal
import threading
from pathlib import Path

from ekbatan import DatabaseRegistry, TransactionManager, engine_for
from ekbatan.aio import AsyncDatabaseRegistry
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import job_registry
from ekbatan.events import HandlerLoop
from ekbatan.migrations import migrate

CONFIG_FILE = Path("ekbatan.yaml")
MIGRATIONS = Path("migrations")


def main() -> None:
    config = load_ekbatan_config(CONFIG_FILE)
    layout = config.sharding
    migrate(layout, MIGRATIONS)
    loop = HandlerLoop(name="worker-loop")
    registry = AsyncDatabaseRegistry.from_config(layout)  # used only on `loop`
    jobs_engine = engine_for(layout.groups[0].members[0].config_for("jobsConfig"))
    jobs_registry = (
        DatabaseRegistry.builder()
        .with_default_database(TransactionManager(layout.default_shard, jobs_engine))
        .build()
    )
    jobs = (
        job_registry()
        .engine(jobs_engine)
        .with_jobs(your_jobs(jobs_registry, registry, loop))
        .with_config(config.jobs)
        .build()
    )

    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())  # a container stop
    jobs.start()
    try:
        stopping.wait()  # until SIGTERM, or Ctrl-C
    finally:
        jobs.stop()  # waits for the jobs' work, which is waiting on the loop
        loop.run(registry.aclose())  # closed on the loop it was used on
        loop.close()
        jobs_registry.close()  # disposes jobs_engine too
```

[`job-worker-uv-pg`](../ekbatan-py-examples/job-worker-uv-pg) is a complete sync worker, and
[`job-worker-uv-async-pg`](../ekbatan-py-examples/job-worker-uv-async-pg) the async one - again
with everything gathered into the example's own `Worker` class and `build()` function.

## Two mistakes ekbatan refuses

Two mistakes are easy to make and, left alone, would fail later in confusing ways. ekbatan stops
both at once, with an error that says what to do. Neither fixes anything behind your back.

**Waiting on the database inside `async def`.** An async server answers all its requests on one
thread, switching between them whenever one is waiting. ekbatan's sync API *waits* - for a
connection, a query, a commit - and inside `async def` that wait would freeze every request on the
server until it is over. So a sync call made there raises `BlockingCallError`. The fix is one of
three: use `ekbatan.aio` in async code; make the endpoint a plain `def` (FastAPI then runs it on a
thread for you); or wrap the call in `await asyncio.to_thread(...)`, as the lifespan above does.

**Using an object in a process it was copied into.** Some servers start your app once and then
copy the whole process to make several workers - gunicorn with `--preload`, uwsgi, Celery's
default worker pool and RQ all do this. A copy keeps the objects, but not their background
threads, and its open connections are the very same ones the original process is using; two
processes talking on one connection corrupt each other's queries. So if a registry, a job
registry or a lock provider is used in a process other than the one that created it, ekbatan
raises `ForkedProcessError`. The fix is the rule from the top of this page: create them in your
startup code, which runs in each worker, not at import time.

## When your server runs several processes

To use several CPU cores, Python servers run several copies of your app as separate processes:
`uvicorn --workers 4`, gunicorn's workers, several Kubernetes replicas. Each process runs your
startup code, so each has its own registry, its own pools and its own job registry. Two things
follow:

- **Connections multiply.** Four processes each holding a pool of 10, plus a jobs pool and a lock
  pool, means four times that many connections at the database. Size the pools per process, then
  multiply by processes and replicas, and compare with the database's connection limit.
- **Jobs need no change.** Every process runs a job registry, and they coordinate through the
  database, so each job still runs exactly once.

## Migrations

Migrations change the database's tables, so they should run once per deploy. Running them at
startup, as above, is simplest and is safe - ekbatan takes a lock, so when several processes
start at once only one migrates and the others wait. Larger deployments often run them as a
separate step before the app starts instead; see [Migrations](database/migrations.md).

## Under each server

Everything above applies to every server. What differs from one server to the next is how it
runs your app: how many processes, how many threads in each, whether it starts each worker fresh
or copies one process into many, and where it lets you run code at startup and shutdown. Each
section below answers the same three questions: **how it runs**, **where it can bite you**, and
**what to do**.

A few words used throughout:

- A **process** is a running copy of Python with its own memory. Several processes are how a
  Python server uses several CPU cores, because inside one process only one thread runs Python
  code at a time.
- A **worker** is one of those processes, serving requests or running tasks.
- A server **copies** a process with `fork()`: the new worker starts as an exact copy of the
  original, with everything already in memory - including anything your app created at import.
  A server that **starts workers fresh** launches a new Python for each one, which imports your
  app on its own.

**What has been tested here:** uvicorn - every FastAPI example's tests run its lifespan, and a
test serves an app from a real uvicorn process; the copied-process refusal, with real copied
processes on all four databases; and the sync API under gevent. The other servers are described
from how each one starts and stops its workers, and have not been run here.

### uvicorn, and `fastapi run`

**How it runs.** By default, one process. Inside it, one thread runs an *event loop* that serves
every request: while one request waits on the database or the network, the loop switches to
another. `async def` endpoints run on that loop. Plain `def` endpoints are handed to a small pool
of threads (40 by default), so a slow one doesn't hold up the loop. With `--workers 4`, uvicorn
starts four processes **fresh**, and each imports your app and runs its own lifespan. `fastapi
run` is uvicorn, with the same options.

**Where it can bite.**

- An `async def` endpoint that calls ekbatan's sync API would stop the loop - and every other
  request - while it waits on the database. ekbatan raises `BlockingCallError` instead.
- With `--workers 4`, there are four of every pool: four registries, four job registries, four
  lock pools. The database sees four times the connections.
- With plain `def` endpoints, up to 40 requests can run at once in each process. If the
  registry's pool is smaller, the rest wait for a free connection, and a request that waits
  longer than `pool_timeout` fails.

**What to do.** Use the lifespan shown above, unchanged. Write `async def` endpoints with
`ekbatan.aio`, or plain `def` endpoints with the sync API. Size the pool for how many requests
really run at once in one process, then multiply by `--workers` when comparing with the
database's connection limit.

### gunicorn running FastAPI (`-k uvicorn.workers.UvicornWorker`)

**How it runs.** gunicorn starts a main process that does no request work itself: it creates the
workers, watches them, and replaces any that die. It makes each worker by **copying** itself. Each
worker then runs uvicorn's event loop and your lifespan, exactly as plain uvicorn would. With
`--preload`, gunicorn imports your app once in the main process, *before* copying, to save memory
and start faster.

**Where it can bite.**

- **`--preload` with objects created at import.** If your module creates a registry at import
  time, with `--preload` it is created once in the main process and copied into every worker,
  together with its connections. ekbatan raises `ForkedProcessError` when a worker uses it.
- **Shutdown time.** When gunicorn stops a worker, it waits `--graceful-timeout` (30 seconds by
  default) and then kills it. If the job registry's `shutdown_max_wait` is longer, a job that
  needs the full time is killed mid-way.
- **Worker restarts.** With `--max-requests`, gunicorn replaces each worker after that many
  requests. Every replacement runs your shutdown and startup again - which is fine, as long as
  they are in the lifespan.
- Pools multiply by the number of workers, as with uvicorn.

**What to do.** Keep everything in the lifespan; then `--preload` is harmless, because the
lifespan runs in each worker, after the copy. Set `--graceful-timeout` longer than the job
registry's `shutdown_max_wait`.

### gunicorn running Flask, Django or another WSGI app (`gthread` or `gevent` workers)

**How it runs.** The same main process, copying itself into workers. What runs inside each worker
depends on the worker type:

- `gthread`: a pool of threads (`--threads`), each handling one request at a time.
- `gevent`: one thread running many requests at once. gevent changes Python's waiting calls -
  for the database, the network, `sleep` - so that while one request waits, another runs.
  `--worker-connections` (1000 by default) caps how many.

WSGI apps have no lifespan. gunicorn's own hooks take its place: `post_fork` runs in each worker
just after it is copied, and `worker_exit` runs as the worker stops.

**Where it can bite.**

- **Creating objects at import time.** Flask and Django apps often set things up at module level.
  With `--preload`, that runs once in the main process and is copied into every worker:
  `ForkedProcessError`.
- **More requests than connections.** With `gthread`, up to `--threads` requests run at once per
  worker; with `gevent`, up to `--worker-connections`. If they all want the database and the pool
  is smaller, the rest wait, and fail after `pool_timeout`.
- **`ekbatan.aio` under gevent.** It does not run under gevent's changes; use the sync API there.

**What to do.** Create the objects in `post_fork` and close them in `worker_exit`:

```python
# gunicorn.conf.py
from ekbatan import DatabaseRegistry, engine_for
from ekbatan.distributedjobs import job_registry


def post_fork(server, worker):
    import myapp

    member = myapp.LAYOUT.groups[0].members[0]
    myapp.registry = DatabaseRegistry.from_config(myapp.LAYOUT)
    myapp.jobs_engine = engine_for(member.config_for("jobsConfig"))
    myapp.jobs = (
        job_registry()
        .engine(myapp.jobs_engine)
        .with_jobs(myapp.your_jobs(myapp.registry))
        .build()
    )
    myapp.jobs.start()


def worker_exit(server, worker):
    import myapp

    myapp.jobs.stop()
    myapp.jobs_engine.dispose()
    myapp.registry.close()
```

Use the sync API. Size the pool for `--threads` (gthread) or for how many requests really reach
the database at once (gevent), and set `--graceful-timeout` longer than `shutdown_max_wait`.

### uwsgi

**How it runs.** uwsgi starts a main process that imports your app and then **copies** itself
into the workers (`processes = 4`). Each worker can also run several threads (`threads = 2`) or
use gevent. This copy-after-import is uwsgi's default - like gunicorn with `--preload`, without
asking for it.

**Where it can bite.**

- **Everything created at import is copied.** A registry created when the app module is imported
  ends up in every worker: `ForkedProcessError`.
- **Background threads may not run at all.** uwsgi does not enable Python threads unless you ask
  for them. The job registry runs on background threads, so without `enable-threads` its jobs
  never run.
- **SIGTERM means "reload" by default.** A container stop sends SIGTERM; unless told otherwise,
  uwsgi treats it as a reload rather than a shutdown, so your app is not shut down cleanly.

**What to do.** In `uwsgi.ini`:

```ini
lazy-apps = true       ; each worker imports the app itself, after the copy
enable-threads = true  ; let the job registry's threads run
die-on-term = true     ; SIGTERM shuts down, as a container stop expects
```

Then create the objects where the app starts in each worker, or in a function decorated with
`uwsgidecorators.postfork`. Use the sync API.

### hypercorn, and granian

**How they run.** Both serve ASGI apps such as FastAPI, like uvicorn. With several workers each
starts separate processes, and each worker imports your app and runs the lifespan itself.
hypercorn can run its event loop with asyncio, uvloop or trio. granian is written in Rust and runs
your Python app inside each of its workers; with `--interface asgi` it runs the lifespan.

**Where it can bite.**

- **hypercorn with trio.** `ekbatan.aio` needs asyncio's event loop, and trio is a different one.
- Pools multiply by workers, as everywhere.

**What to do.** Use the lifespan above. With hypercorn, use the asyncio or uvloop worker class.
With granian, run FastAPI with `--interface asgi`.

### waitress

**How it runs.** One process, and a pool of threads (4 by default) that serve a WSGI app. Nothing
is copied. Your own `main()` calls `serve(app, ...)`, which runs until the process is stopped.

**Where it can bite.**

- **SIGTERM.** By default, a container stop ends the process at once, before your code after
  `serve()` runs - so jobs are cut off. (The job registry warns about this when it starts.)
- **More threads than connections.** With more threads than the pool has connections, requests
  wait for one.

**What to do.** Create the objects at the top of `main()`, before `serve(...)`, and stop and close
them after it returns. Call `.register_shutdown_hook()` on the job registry builder, so a
container stop lets running jobs finish. Make the pool at least as large as `threads`.

### Celery

**How it runs.** A Celery worker is a main process that takes tasks from the queue and hands them
to a pool, which runs them. The pool type decides how:

- `prefork`, the default: the main process **copies** itself into child processes (one per CPU
  core by default), and each child runs one task at a time.
- `threads`: one process, a pool of threads.
- `gevent`: one process, many tasks at once on gevent.
- `solo`: one process, one task at a time.

Celery imports your task modules in the main process, before it copies anything.

**Where it can bite.**

- **Objects created in your task modules.** With `prefork`, a registry created when a task module
  is imported is made in the main process and copied into every child: `ForkedProcessError`.
- **Connections per child.** Each child has its own registry, so connections multiply by the
  number of children.
- **Children are replaced.** With `--max-tasks-per-child`, Celery replaces a child after that many
  tasks; each new child creates its objects again.

**What to do.** With `prefork`, create the registry in each child with the `worker_process_init`
signal and close it with `worker_process_shutdown`:

```python
from celery.signals import worker_process_init, worker_process_shutdown

from ekbatan import DatabaseRegistry


@worker_process_init.connect
def create_registry(**_):
    tasks.registry = DatabaseRegistry.from_config(tasks.LAYOUT)


@worker_process_shutdown.connect
def close_registry(**_):
    tasks.registry.close()
```

Build the repositories and the executor your tasks use on that registry, in the same place. With
`threads`, `gevent` or `solo` there is only one process: use the `worker_init` and
`worker_shutdown` signals instead. Tasks use the sync API.

### RQ

**How it runs.** `rq worker` is a main process that takes jobs from Redis. For **every** job it
**copies** itself into a short-lived child, runs the job there, and lets the child exit. RQ's
`SimpleWorker` runs each job in the main process itself, without copying.

**Where it can bite.**

- **A registry created in the main process.** It is copied into every job's child:
  `ForkedProcessError`.
- **A registry created inside each job.** That works, but every job opens new connections and
  closes them again, which is slow for many small jobs.

**What to do.** For many jobs, run a `SimpleWorker` from your own `main()`, which creates the
registry first and closes it when the worker stops. For occasional jobs, creating and closing the
registry inside the job is fine.

### Kubernetes

**How it runs.** Kubernetes runs your app as **pods** - each pod one container, running one of the
setups above. Several **replicas** are several pods, often on different machines. A deploy starts
new pods and stops old ones; to stop one, Kubernetes sends its process SIGTERM, waits
`terminationGracePeriodSeconds` (30 seconds by default), and then kills it.

**Where it can bite.**

- **Connections add up across pods.** Each pod has its own pools, so the database sees every
  pod's connections together.
- **The grace period is shorter than a job.** If a running job needs longer than the grace period,
  the pod is killed mid-job, and another pod runs the job again later.
- **Migrations in every pod at once.** If each pod migrates at startup, all of them try together.
  ekbatan takes a lock so only one migrates while the others wait - correct, but every pod's start
  is held up.

**What to do.** Count the connections as pool size × processes per pod × replicas, and keep that
under the database's limit. Set `terminationGracePeriodSeconds` longer than the server's own
shutdown wait (for example gunicorn's `--graceful-timeout`) and the job registry's
`shutdown_max_wait`. Run migrations as their own step - a Kubernetes Job, or an init container -
rather than in every pod.

## See also

- [Wiring with FastAPI](wiring-fastapi.md) - the lifespan, the dependency, the endpoints
- [Concurrency](runtime/concurrency.md) - threads, asyncio, gevent, and where the pools live
- [Distributed jobs](jobs/distributed-jobs.md) - the job registry, draining at exit, and SIGTERM
