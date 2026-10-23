# Startup and shutdown

## Why read this page

Your app works on your laptop. Then it meets the real world, and one of these happens:

- **A deploy cuts a job off halfway.** A new version is rolled out, the old process is stopped
  while a job is sending emails, and a minute later another process runs the whole job again - so
  some people get the email twice. *See
  [Stopping has a time limit](#stopping-has-a-time-limit).*
- **The database says "too many connections".** It ran fine with one process; then the server was
  set to four workers, or the app to three replicas, and each copy opened its own connections.
  *See [When your server runs several processes](#when-your-server-runs-several-processes).*
- **ekbatan raises `ForkedProcessError`.** Your server copied your app into several workers after
  the app had already created its objects, and a worker used one of them. *See
  [Import time and startup](#import-time-and-startup).*
- **ekbatan raises `BlockingCallError`.** An `async def` endpoint called the sync API, which
  would have frozen every other request on the server while it waited on the database. *See
  [Two mistakes ekbatan refuses](#two-mistakes-ekbatan-refuses).*

All four come from the same place: a few objects your app creates hold open database
connections or background threads, and they have to be created, and closed, in the right place.
This page shows that place for each kind of program, and explains each step. Read it before your
app goes to production, when you move to a server with several workers, or when you meet one of
those two errors.

## How to read this page

The page has three parts.

1. **Part 1 - the idea, once.** Four short sections every reader needs, best read in order:
   [What needs a start and a stop](#what-needs-a-start-and-a-stop),
   [Import time and startup](#import-time-and-startup),
   [How a server runs your app](#how-a-server-runs-your-app) and
   [How a program is told to stop](#how-a-program-is-told-to-stop).
2. **Part 2 - your kind of program.** [Find your kind of program](#find-your-kind-of-program) in
   a table, then read only its section: the complete code, and what each server that runs it
   needs.
3. **Part 3 - for every kind of program.** Read these before going to production:
   [Two mistakes ekbatan refuses](#two-mistakes-ekbatan-refuses),
   [When your server runs several processes](#when-your-server-runs-several-processes),
   [Migrations](#migrations), [In your tests](#in-your-tests) and
   [Running on Kubernetes](#running-on-kubernetes).

## What needs a start and a stop

Most of what you write with ekbatan - models, repositories, actions, the executor - are plain
objects. You create them and use them, and when your program ends they simply go away.

A few objects are different: they hold something open for as long as your program runs -
connections to the database, or background threads. They need two moments of care: they are
**created when your program starts**, and **closed when it stops**. Closing them is what lets
running jobs finish and connections end cleanly, instead of being cut off.

There are three of them, and you may not need all three.

**The database registry** (`DatabaseRegistry`, or `AsyncDatabaseRegistry` for async code) -
every app has one. It holds a *pool* of connections for each database in `ekbatan.yaml`: a set of
connections kept open and lent to whichever piece of code needs one, so that every query does not
have to connect from scratch. Every repository and the executor are built on it. It opens
connections as they are needed, and `close()` closes them all.

**The job registry** (`JobRegistry`) - if you have event handlers or scheduled jobs. It runs them
on background threads; your event handlers run as jobs too, after the action that raised the
event has committed. You call `start()` after creating it, and `stop()` at shutdown, which waits
for running jobs to finish - up to a time limit, `shutdown_max_wait` - so that a deploy does not
cut a job off halfway.

**The lock provider** - if you use keyed locks. It holds a connection for each lock it has taken.

The job registry and the lock provider each get **a small pool of their own**, made with
`engine_for(...)` from a named entry in `ekbatan.yaml`, so they never take connections away from
your requests. You create these pools, so you close them, with `dispose()`.

In short:

| object | at startup | at shutdown |
| --- | --- | --- |
| database registry | `DatabaseRegistry.from_config(layout)` | `registry.close()` |
| job registry | `job_registry()...build()`, then `jobs.start()` | `jobs.stop()` |
| lock provider | `PostgresKeyedLockProvider.builder()...build()` | `locks.close()` |
| their small pools | `engine_for(...)` | `engine.dispose()` |
| repositories, executor | create them | nothing to do |

If an event handler is `async def`, there is one more: the `HandlerLoop` it runs on. It is covered
in the FastAPI section, under
[If an event handler is `async def`](#if-an-event-handler-is-async-def).

## Import time and startup

The table says *at startup*. That needs a word of explanation, because a Python program has two
different moments when code runs before any work is done, and only one of them is right for
these objects.

### Import time

When Python imports a module - `import myapp`, or `from myapp.api import app` - it runs every line
at the top level of that file, once, there and then. Defining a function, a class or a constant
at the top level is harmless. But a line like this runs too:

```python
# myapp/db.py
from ekbatan import DatabaseRegistry
from ekbatan.config_files import load_ekbatan_config

registry = DatabaseRegistry.from_config(load_ekbatan_config("ekbatan.yaml").sharding)  # at import
```

This is *creating the registry at import time*. It looks convenient: every other module can
write `from myapp.db import registry` and use it. The trouble is that you do not decide when a
module is imported, or how often, or in which process. A module is imported:

- when the server starts your app (`uvicorn myapp.api:app` imports `myapp.api`);
- when your test runner collects your tests, before a single test runs;
- by a script or a tool that only needs one function from your code;
- and, with some servers, once in a main process that is then **copied** into several workers.

### Startup

Startup is code your app runs on purpose, when it actually begins to serve: in a FastAPI app the
*lifespan* function, which the server calls once when it starts the app; in a plain worker, the
top of your own `main()` function. Nothing runs there just because a module was imported.

```python
# myapp/api.py
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ekbatan import DatabaseRegistry
from ekbatan.config_files import load_ekbatan_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    layout = load_ekbatan_config("ekbatan.yaml").sharding
    app.state.registry = DatabaseRegistry.from_config(layout)  # at startup
    yield
    app.state.registry.close()                                  # at shutdown


app = FastAPI(lifespan=lifespan)
```

### Side by side

| | import time | startup |
| --- | --- | --- |
| when it runs | the module is first imported | the app starts serving |
| who runs it | anything that imports it | the server, or your `main()` |
| in which process | maybe one copied into workers later | each process that serves |
| where it is closed | nowhere | the end of the lifespan or `main()` |

### What goes wrong at import time

Three things, each one real:

1. **Tests open connections they never asked for.** Collecting your tests imports your modules,
   so a registry is created - and if the job registry is started at import too, jobs begin
   running against whatever database the settings point at - before any test has said what it
   needs.
2. **A copied worker gets another process's connections.** A server that imports your app first
   and then copies the process (gunicorn with `--preload`, uwsgi, Celery) hands every worker a
   copy of the registry, holding the main process's connections. Two processes talking on one
   connection corrupt each other's queries. ekbatan raises `ForkedProcessError` instead. (The next
   section explains [copying](#started-fresh-or-copied).)
3. **Nothing closes it.** There is no "end of import" to put `close()` and `stop()` in, so at
   shutdown the job registry is never stopped - running jobs are cut off - and the connections are
   simply dropped.

So: **define** things at import time - functions, classes, constants, the path to
`ekbatan.yaml` - but **create the objects that hold connections or threads at startup**, and
close them at shutdown. The rest of this page does exactly that.

## How a server runs your app

Servers don't all run your app the same way, and that changes where your startup code should go.
Below are the three differences that matter. Later, each server's "How it runs" paragraph tells
you what that server does for each of them.

### Processes and workers

A *process* is a running copy of Python with its own memory. Inside one process, only one thread
runs Python code at a time - so to use several CPU cores, a server runs several processes, called
its *workers*. Every worker runs your startup code, so every worker has its own registry, its own
pools and its own job registry.

### Started fresh, or copied

A server makes its workers in one of two ways:

- It can **start each worker fresh**: launch a new Python, which imports your app on its own.
  uvicorn and hypercorn do this.
- It can **copy** one process, with `fork()`: the new worker starts as an exact copy of the
  original, with everything already in memory - including anything your app created when it was
  imported. gunicorn, uwsgi, Celery and RQ do this.

A copy keeps the objects but not their background threads, and its connections are the very same
ones the original process is still using. So an object created before the copy must not be used
after it - and ekbatan refuses with `ForkedProcessError` if it is. Creating the objects at
startup, in each worker, avoids the problem whichever way the server works.

### Threads, or an event loop

Inside a worker, requests run in one of two ways:

- On **threads**: each request gets a thread of its own, and a request that waits on the database
  simply makes its own thread wait. Flask and Django apps run this way, and so do plain `def`
  endpoints in FastAPI.
- On an **event loop**: one thread serves many requests together. Whenever one is waiting on the
  database or the network, the loop moves on to another. `async def` endpoints in FastAPI run
  this way.

An event loop only works if code running on it never waits in a way the loop can't see. The
FastAPI section shows what that means for ekbatan, under [Code that waits](#code-that-waits).

## How a program is told to stop

Startup is something your program does. Shutdown is something that is done *to* it: the program
is asked to stop, and gets a limited time to do so.

### Signals

A running program is asked to stop with a *signal* - a short message the operating system
delivers to it:

- **SIGTERM** means "finish up and exit". Docker, Kubernetes, systemd and other process managers
  send it to stop a program - which is what every deploy does to the old version.
- **SIGINT** is what Ctrl-C sends in a terminal. Python turns it into a `KeyboardInterrupt`.

### Who catches SIGTERM

**A server catches it for you.** uvicorn, gunicorn, Celery and RQ - and uwsgi, set up as its
section shows - catch SIGTERM, stop taking new work, let the work in progress finish, and then run
your shutdown code: the end of the lifespan, or the server's own shutdown hook.

**Your own `main()` has to catch it itself.** In a worker you run yourself, or a server you start
from your own code such as waitress, nothing catches SIGTERM unless you do. Python's default is
to end the process on the spot: your shutdown code never runs, and running jobs are cut off. (The
job registry does stop itself when Python exits normally or on Ctrl-C - but a SIGTERM that nothing
catches ends Python before that can happen.) The sections for those programs show how to catch
it.

### Stopping has a time limit

Whoever sends SIGTERM waits a while, and then kills the process outright, whatever it is doing:

| who stops your program | waits, by default | where to change it |
| --- | --- | --- |
| `docker stop`, Docker Compose | 10 seconds | `--time`; Compose: `stop_grace_period` |
| Kubernetes | 30 seconds | `terminationGracePeriodSeconds` |
| systemd | 90 seconds | `TimeoutStopSec` |
| supervisord | 10 seconds | `stopwaitsecs` |
| gunicorn, stopping one of its workers | 30 seconds | `--graceful-timeout` |

The job registry's `stop()` waits for running jobs for up to `shutdown_max_wait` - and that is
**30 minutes** unless you set it, far longer than any limit above. So set it below the shortest
limit that applies to you, leaving room for the rest of your shutdown. For example, to fit inside
Kubernetes' 30 seconds, in `ekbatan.yaml`:

```yaml
ekbatan:
  jobs:
    shutdown-max-wait: PT20S  # 20 seconds, written as an ISO-8601 duration
```

The code on this page hands the file's settings to the job registry with
`.with_config(config.jobs)`; `.shutdown_max_wait(timedelta(seconds=20))` on the builder does the
same. A job still running when `shutdown_max_wait` runs out is left unfinished, and another
process runs it again about a minute later - so write jobs that are safe to run twice.

## Find your kind of program

This is where Part 2 starts. Where "startup" and "shutdown" are depends on what kind of program
you are writing, and there are four kinds - read only the section for yours:

| kind of program | for example | run by |
| --- | --- | --- |
| [a web app with a lifespan](#a-web-app-with-a-lifespan-fastapi) | FastAPI, Starlette | uvicorn, gunicorn, hypercorn, granian |
| [a web app without a lifespan](#a-web-app-without-a-lifespan-flask-django) | Flask, Django | gunicorn, uwsgi, waitress |
| [a task-queue worker](#a-task-queue-worker-celery-rq) | Celery, RQ | Celery, RQ |
| [a worker you run yourself](#a-worker-you-run-yourself) | a job worker, a consumer | you, from your own `main()` |

Startup and shutdown are in a different place in each, in the order of the table: the app's
**lifespan**; the **server's own hooks**; the **queue's per-worker hooks**; and the top and the
bottom of **your `main()`**. A server you start from your own code - waitress, or RQ's
`SimpleWorker` - also runs inside your `main()`.

Each section starts with what is particular to that kind of program, then gives the complete
code. The first three then take the servers that run them one by one, each in the same three
parts:

- **How it runs:** how many processes, how many threads in each, and whether workers are copied
  or started fresh.
- **Where it can bite:** what goes wrong with that server if nothing is done about it.
- **What to do:** the code, and the server's settings that matter.

## A web app with a lifespan: FastAPI

### The lifespan

FastAPI calls the lifespan once when your app starts and resumes it once when the app stops. The
code before `yield` is startup, the code after it is shutdown, and while it waits at `yield` your
app serves requests:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: runs once, before the first request.
    ...
    try:
        yield            # the app serves requests while the lifespan waits here
    finally:
        # Shutdown: runs once, when the server stops the app - also if it stops with an error.
        ...


app = FastAPI(lifespan=lifespan)
```

Endpoints reach what the lifespan created through `app.state` (in an endpoint,
`request.app.state`). For example, an endpoint can build a repository on the registry:

```python
@app.get("/wallets/{wallet_id}")
def read_wallet(wallet_id: str, request: Request):
    wallets = WalletRepository(request.app.state.registry)  # your repository class
    ...
```

### Code that waits

The lifespan is `async def`, so it runs on the server's
[event loop](#threads-or-an-event-loop). ekbatan's sync API waits plainly - for a connection, a
query, a commit - so called from `async def` code it would stop the loop, and with it every other
request, until it finished.

`asyncio.to_thread(...)` solves that: it runs a plain, waiting call on a separate thread, and the
`async def` code waits for it in a way the loop understands. The lifespan below uses it for every
sync call that waits on the database. (Forget it, and ekbatan stops you with `BlockingCallError` -
see [Two mistakes ekbatan refuses](#two-mistakes-ekbatan-refuses).)

### The complete lifespan

Here is a complete lifespan with every object from the table - the registry, the job registry
and a keyed lock provider - first for the sync API and then for `ekbatan.aio`. If your app has no
event handlers or scheduled jobs, leave out the jobs lines; if it uses no keyed locks, leave out
the lock lines. Read it once; the steps below explain it. `your_jobs` stands for your own function
that returns the jobs to run.

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
   and the named pools (`lockConfig`, `jobsConfig`) that `member.config_for(...)` picks out. It
   returns `None` when the file has no entry of that name; check for that in your own code, as the
   examples do.
2. **Run the migrations** first, so the tables exist before anything uses them - on a separate
   thread, through `asyncio.to_thread`, because `migrate` waits on the database. (Larger
   deployments often run them as a step of their own instead; see [Migrations](#migrations).)
3. **Create the database registry.** Creating it opens nothing; connections open when the first
   request needs one.
4. **Create the lock pool and the lock provider**, then **the jobs' pool and the job registry**,
   and start the jobs. From here on, jobs run in the background.
5. **`yield`**: the app now serves requests. Endpoints reach the registry and the lock provider
   through `app.state`.
6. **Shutdown, in reverse order.** The jobs stop first, so nothing is still working while its
   connections are being closed - and `stop()` waits for a running job to finish, up to
   `shutdown_max_wait`. Then the lock provider and the pools close, and the registry last.

**In the examples.** The example apps put all of the above into a class of their own,
`WalletService`, with a `build()` function that creates everything and a `close()` that closes
it, and the lifespan calls those. That is the examples' choice, not something ekbatan provides -
you can do the same, or keep it in the lifespan as shown here. See
[`fastapi-wallet-rest-uv-pg`'s `app.py`](../ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/app.py)
and, async,
[`fastapi-wallet-rest-uv-async-pg`'s](../ekbatan-py-examples/fastapi-wallet-rest-uv-async-pg/src/fastapi_wallet_rest_async_pg/app.py);
[Wiring with FastAPI](wiring-fastapi.md) walks through them.

### If an event handler is `async def`

Event handlers run in the background, after an action commits, on the job registry's threads. An
`async def` handler needs an event loop of its own to run on: a `HandlerLoop`. What the handler
awaits - its own async registry - belongs to that loop, and is closed on it. Add this to the
lifespan above:

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

### With uvicorn, or `fastapi run`

**How it runs.** By default, one process. Inside it, one thread runs the event loop that serves
every request. `async def` endpoints run on that loop. Plain `def` endpoints are handed to a small
pool of threads (40 by default), so a slow one doesn't hold up the loop. With `--workers 4`,
uvicorn starts four processes **fresh**, and each imports your app and runs its own lifespan.
`fastapi run` is uvicorn, with the same options.

**Where it can bite.**

- **Sync calls in `async def` endpoints.** They would stop the loop - and every other request -
  while they wait on the database. ekbatan raises `BlockingCallError` instead.
- **More requests than connections.** With plain `def` endpoints, up to 40 requests can run at
  once in each process. If the registry's pool is smaller, the rest wait for a free connection,
  and a request that waits longer than `pool_timeout` fails.
- **Pools multiply.** With `--workers 4` there are four of every pool, and the database sees four
  times the connections.
- **Shutdown time.** On SIGTERM, uvicorn first lets the requests in progress finish, and then
  runs the end of your lifespan. Both together have to fit in the
  [time limit](#stopping-has-a-time-limit) of whatever stopped it. `--timeout-graceful-shutdown`
  caps the first part; by default it has no cap.

**What to do.** Use the lifespan above, unchanged:

```bash
uvicorn myapp.api:app --workers 4
```

Write `async def` endpoints with `ekbatan.aio`, or plain `def` endpoints with the sync API. Size
the pool for how many requests really run at once in one process, then multiply by `--workers`
when comparing with the database's connection limit (see
[When your server runs several processes](#when-your-server-runs-several-processes)).

### With gunicorn and uvicorn workers

**How it runs.** gunicorn starts a main process that does no request work itself: it creates the
workers, watches them, and replaces any that die. It makes each worker by **copying** itself. With
`-k uvicorn_worker.UvicornWorker`, each worker then runs uvicorn's event loop and your lifespan,
exactly as plain uvicorn would. (The worker comes from the `uvicorn-worker` package; older
setups write `-k uvicorn.workers.UvicornWorker`, which recent uvicorn versions mark as deprecated.)
With `--preload`, gunicorn imports your app once in the main process, *before* copying, to save
memory and start faster.

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
- **Pools multiply** by the number of workers, as with uvicorn.

**What to do.** Keep everything in the lifespan; then `--preload` is harmless, because the
lifespan runs in each worker, after the copy. Set `--graceful-timeout` longer than the job
registry's `shutdown_max_wait`:

```bash
gunicorn myapp.api:app -k uvicorn_worker.UvicornWorker --workers 4 --graceful-timeout 60
```

### With hypercorn or granian

**How they run.** Both serve ASGI apps such as FastAPI, like uvicorn. Given several workers, both
start separate processes, and each worker imports your app and runs the lifespan itself.
hypercorn starts its workers fresh, and can run its event loop with asyncio, uvloop or trio.
granian is written in Rust and runs your Python app inside each of its workers.

**Where it can bite.**

- **hypercorn with trio.** `ekbatan.aio` needs asyncio's event loop, and trio is a different one.
- **granian's interfaces.** Only `--interface asgi` runs the lifespan. Its default interface,
  `rsgi`, is a different protocol, and `asginl` is ASGI with no lifespan, so your startup and
  shutdown code would not run.
- **Pools multiply** by workers, as everywhere.

**What to do.** Use the lifespan above. With hypercorn, use the asyncio or uvloop worker class.
With granian, run FastAPI with `--interface asgi`.

## A web app without a lifespan: Flask, Django

### What is different

Flask and Django apps are *WSGI* apps - the older, simpler way Python web servers talk to web
apps. Three things follow for ekbatan:

- **There is no lifespan.** A WSGI app is just a function the server calls for each request; it
  has no "the app is starting" or "the app is stopping" step of its own. The *server* has them
  instead, as hooks you can fill in - or, with a server you start from your own code, your
  `main()` is the startup and shutdown.
- **Requests run on threads** (or on gevent, which behaves like threads), never on an event loop.
  So you use ekbatan's **sync API**, and nothing needs `asyncio.to_thread`.
- **The usual setup places run at import time.** Django's `AppConfig.ready()` runs while Django
  sets itself up - when `wsgi.py` is imported, and for every `manage.py` command. A Flask
  `create_app()` called from `wsgi.py` runs when that file is imported. Both are import time in
  this page's sense, so they are not the place to create these objects.

### One module for startup and shutdown

Without a lifespan there is no `app.state` to keep the objects on. Keep them in a module of your
own instead - the `myapp/db.py` from [Import time](#import-time), done right. It defines the names
at import time, as `None`, and two functions of your own, `start()` and `stop()`, create and close
the objects. Each server below then only has to call
`start()` when a worker is ready to serve and `stop()` when it stops. (These are your functions,
not something ekbatan provides.) `your_jobs` stands for your own function that returns the jobs
to run:

```python
# myapp/db.py
from pathlib import Path

from ekbatan import DatabaseRegistry, engine_for
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import JobRegistry, job_registry

CONFIG_FILE = Path("ekbatan.yaml")

# None at import time; start() fills them in, in each process that serves.
registry: DatabaseRegistry | None = None
_jobs: JobRegistry | None = None
_jobs_engine = None


def start() -> None:
    global registry, _jobs, _jobs_engine
    config = load_ekbatan_config(CONFIG_FILE)
    layout = config.sharding
    registry = DatabaseRegistry.from_config(layout)  # opens no connection yet
    _jobs_engine = engine_for(layout.groups[0].members[0].config_for("jobsConfig"))
    _jobs = (
        job_registry()
        .engine(_jobs_engine)
        .with_jobs(your_jobs(registry))
        .with_config(config.jobs)
        .build()
    )
    _jobs.start()


def stop() -> None:
    if registry is None:  # start() never ran in this process
        return
    _jobs.stop()  # running jobs finish first, up to shutdown_max_wait
    _jobs_engine.dispose()
    registry.close()
```

The order is the same as in the lifespan: create the objects, then start the jobs; at shutdown,
stop the jobs first, then close the pools. With no event handlers or scheduled jobs, `start()` is
only the registry line and `stop()` only `registry.close()`. Migrations are best run as a step of
their own before the server starts (see [Migrations](#migrations)), so `start()` does not run
them.

Your views read the registry from the module, at the moment they run:

```python
# Flask
from myapp import db


@app.get("/wallets/<wallet_id>")
def read_wallet(wallet_id: str):
    wallets = WalletRepository(db.registry)  # your repository class
    ...


# Django
from myapp import db


def read_wallet(request, wallet_id: str):
    wallets = WalletRepository(db.registry)
    ...
```

Write `db.registry`, not `from myapp.db import registry`. The second copies the value the name
had when that line ran - `None`, at import time - and never sees the registry `start()` creates
later.

### With gunicorn (`gthread` or `gevent` workers)

**How it runs.** gunicorn starts a main process that does no request work itself: it creates the
workers, watches them, and replaces any that die. It makes each worker by **copying** itself. What
runs inside each worker depends on the worker type:

- `gthread`: a pool of threads (`--threads`), each handling one request at a time.
- `gevent`: one thread running many requests at once. gevent changes Python's waiting calls -
  for the database, the network, `sleep` - so that while one request waits, another runs.
  `--worker-connections` (1000 by default) caps how many.

With `--preload`, gunicorn imports your app once in the main process, *before* copying.

**Where it can bite.**

- **Creating objects at import time.** With `--preload`, module-level setup runs once in the main
  process and is copied into every worker: `ForkedProcessError`.
- **The `post_fork` hook with gevent.** gevent makes its changes to Python only once the worker
  starts, and gunicorn's `post_fork` hook runs before that. Objects created there are built
  without gevent's changes, and under load one request waiting for a pool connection can freeze
  the whole worker. The hook below runs after.
- **More requests than connections.** With `gthread`, up to `--threads` requests run at once per
  worker; with `gevent`, up to `--worker-connections`. If they all want the database and the pool
  is smaller, the rest wait, and fail after `pool_timeout`.
- **`ekbatan.aio` under gevent.** It does not run under gevent's changes; use the sync API there.
- **Shutdown time.** When gunicorn stops a worker, it waits `--graceful-timeout` (30 seconds by
  default) and then kills it. If the job registry's `shutdown_max_wait` is longer, a job that
  needs the full time is killed mid-way.

**What to do.** Call `start()` and `stop()` from two of gunicorn's hooks, in a
`gunicorn.conf.py` file:

```python
# gunicorn.conf.py
def post_worker_init(worker):
    from myapp import db

    db.start()


def worker_exit(server, worker):
    from myapp import db

    db.stop()
```

`post_worker_init` runs in each worker once it is ready to serve: after the copy, after gevent's
changes, and after your app has been imported. `worker_exit` runs as the worker stops. Because
the objects are created after the copy, `--preload` is harmless, and so is `--max-requests`,
which replaces each worker after that many requests - each new worker simply runs `start()`
again. Size the pool for `--threads` (gthread) or for how many requests really reach the
database at once (gevent), and set `--graceful-timeout` longer than `shutdown_max_wait`:

```bash
gunicorn myapp.wsgi:app -c gunicorn.conf.py --workers 4 --threads 8 --graceful-timeout 60
```

### With uwsgi

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
- **The shutdown function is skipped in some cases.** uwsgi does not call it (`uwsgi.atexit`,
  below) in a worker that is still busy with a request when it stops, nor in gevent mode. A job
  cut off that way is run again by another process about a minute later.

**What to do.** First, `uwsgi.ini`, with the two settings that matter here:

```ini
[uwsgi]
module = myapp.wsgi:app
processes = 4
enable-threads = true  ; let the job registry's threads run
die-on-term = true     ; SIGTERM shuts down, as a container stop expects
```

Then call `start()` and `stop()` from uwsgi's two moments:

```python
# myapp/uwsgi_hooks.py - import it from the module uwsgi loads; `import uwsgi` works only there
import uwsgi
from uwsgidecorators import postfork

from myapp import db


@postfork
def start_worker() -> None:
    db.start()


uwsgi.atexit = db.stop
```

A function marked `@postfork` runs in each worker after the copy - its startup. uwsgi calls the
function in `uwsgi.atexit` as a process stops - its shutdown. It calls it in the main process too,
where nothing was started, which is why `stop()` returns at once when `start()` never ran. uwsgi's
default copy is then harmless: the objects are created after it, in each worker.

### With waitress

**How it runs.** One process, and a pool of threads (4 by default) that serve a WSGI app. Nothing
is copied. Your own `main()` calls `serve(app, ...)`, which runs until the process is stopped.

**Where it can bite.**

- **SIGTERM.** Nothing catches it by default, so a container stop ends the process at once,
  before your code after `serve()` runs - and running jobs are cut off. (The job registry logs a
  warning about this when it starts.)
- **More threads than connections.** With more threads than the pool has connections, requests
  wait for one.

**What to do.** As in [a worker you run yourself](#a-worker-you-run-yourself), startup is the top
of your `main()` and shutdown is after `serve()` returns. waitress returns from `serve()` when it
is interrupted by Ctrl-C (`KeyboardInterrupt`) or by `sys.exit()` (`SystemExit`), so one line
turns SIGTERM into the second:

```python
# myapp/serve.py
import signal
import sys

from waitress import serve

from myapp import db
from myapp.wsgi import app


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # SIGTERM ends serve(), as Ctrl-C does
    db.start()
    try:
        serve(app, listen="0.0.0.0:8080", threads=8)
    finally:
        db.stop()


if __name__ == "__main__":
    main()
```

Make the pool at least as large as `threads`, since each thread can hold a connection.

## A task-queue worker: Celery, RQ

### What is different

A task queue runs functions - *tasks* - that other parts of your system put on a queue, in
separate worker processes. Your task functions use ekbatan's sync API, so the worker needs a
registry. The question is where to create it, and that depends on how the queue starts its
processes: both Celery and RQ, by default, **copy** a main process to run tasks. Each gives you a
place that runs in the process that really runs the tasks.

You usually do not start ekbatan's job registry in a task-queue worker: the queue already
schedules the work. If your app has event handlers, run the job registry in your web app, or in
[a worker of its own](#a-worker-you-run-yourself).

### With Celery

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
- **A slow start is taken for a crash.** Celery kills a child whose start-up hook takes longer
  than 4 seconds, assuming it failed to start. Creating a registry opens no connection, so it is
  quick - keep anything slow out of that hook.
- **Connections per child.** Each child has its own registry, so connections multiply by the
  number of children.
- **Children are replaced.** With `--max-tasks-per-child`, Celery replaces a child after that many
  tasks; each new child creates its objects again.

**What to do.** Celery sends a signal in each child as it starts and as it stops -
`worker_process_init` and `worker_process_shutdown`. Create the registry in the first and close it
in the second:

```python
# tasks.py
from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

from ekbatan import DatabaseRegistry
from ekbatan.config_files import load_ekbatan_config

app = Celery("myapp", broker="redis://localhost")
registry: DatabaseRegistry | None = None  # set in each child, never at import


@worker_process_init.connect
def create_registry(**_):
    global registry
    registry = DatabaseRegistry.from_config(load_ekbatan_config("ekbatan.yaml").sharding)


@worker_process_shutdown.connect
def close_registry(**_):
    registry.close()


@app.task
def credit_wallet(wallet_id: str, amount: str) -> None:
    ...  # build the repository and the executor on `registry`, and run the action
```

With `--pool threads`, `--pool gevent` or `--pool solo` there is only one process and nothing is
copied: use the `worker_init` and `worker_shutdown` signals instead, the same way. With `threads`
or `gevent`, make the pool as large as `--concurrency`, since each task can hold a connection.

### With RQ

**How it runs.** `rq worker` is a main process that takes jobs from Redis. For **every** job it
**copies** itself into a short-lived child, runs the job there, and lets the child exit. Your task
module is normally imported inside that child, after the copy. RQ's `SimpleWorker` runs each job
in the main process itself, without copying.

**Where it can bite.**

- **A registry created when your task module is imported.** With `rq worker`, the module is
  imported again in every job's child - so a new registry is created for every job, and nothing
  ever closes it. If you import the module in the main process first (RQ suggests this to make
  jobs start faster), the registry is copied into every child instead: `ForkedProcessError`.
- **`SimpleWorker`'s limits.** It runs jobs without copying, but RQ notes that it sends no
  heartbeats during a job - so a long job can look stuck to monitoring - and does not recommend it
  for production unless you accept that.

**What to do.** With `rq worker`, create the registry inside the job and close it at the end.
Each job then opens its own connections, which is fine unless you run very many small jobs:

```python
# tasks.py
from ekbatan import DatabaseRegistry
from ekbatan.config_files import load_ekbatan_config


def credit_wallet(wallet_id: str, amount: str) -> None:
    registry = DatabaseRegistry.from_config(load_ekbatan_config("ekbatan.yaml").sharding)
    try:
        ...  # build the repository and the executor on `registry`, and run the action
    finally:
        registry.close()
```

For very many small jobs, where opening connections every time is too slow, run a `SimpleWorker`
from your own `main()` instead, with one registry for all jobs. Your task functions then use a
module-level `registry`, set here before the first job runs:

```python
# worker.py
from redis import Redis
from rq import Queue, SimpleWorker

from ekbatan import DatabaseRegistry
from ekbatan.config_files import load_ekbatan_config

import tasks


def main() -> None:
    tasks.registry = DatabaseRegistry.from_config(load_ekbatan_config("ekbatan.yaml").sharding)
    try:
        redis = Redis()
        SimpleWorker([Queue(connection=redis)], connection=redis).work()
    finally:
        tasks.registry.close()


if __name__ == "__main__":
    main()
```

On SIGTERM or Ctrl-C, `work()` returns once the current job has finished, so the `finally` runs.

## A worker you run yourself

### What is different

Some programs have no web server and no task queue: a process that only runs jobs, consumes
messages, or runs a script. Nothing calls your startup and shutdown code for you, so **your
`main()` is both**: startup is its top, shutdown its end. And because no server catches SIGTERM,
your `main()` [catches it itself](#who-catches-sigterm).

### The complete worker

The worker below catches SIGTERM with a `threading.Event`: `main()` waits on it, and the signal
handler sets it. Ctrl-C ends the wait too, with a `KeyboardInterrupt`. Either way, the `finally`
runs.

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


if __name__ == "__main__":
    main()
```

There is no event loop here, so nothing needs `asyncio.to_thread`: `main()` calls everything
directly.

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


if __name__ == "__main__":
    main()
```

[`job-worker-uv-pg`](../ekbatan-py-examples/job-worker-uv-pg) is a complete sync worker, and
[`job-worker-uv-async-pg`](../ekbatan-py-examples/job-worker-uv-async-pg) the async one - again
with everything gathered into the example's own `Worker` class and `build()` function.

If you'd rather not handle SIGTERM yourself, call `.register_shutdown_hook()` on the job registry
builder instead of the `signal.signal(...)` line: SIGTERM and Ctrl-C then stop the jobs for you.
After a SIGTERM the process then ends as it would have, without the rest of your shutdown code -
the connections are simply dropped. If neither is done, the job registry logs one warning when it
starts, saying that running jobs would be cut off by a container stop.

### Where it can bite

- **The stop time limit.** `docker stop` waits only 10 seconds before killing the process, and
  `shutdown_max_wait` is 30 minutes unless you set it. See
  [Stopping has a time limit](#stopping-has-a-time-limit).
- **Processes of your own.** If your worker starts more processes itself - with `multiprocessing`
  or a `ProcessPoolExecutor` - create what each child needs inside the child, with the pool's
  `initializer=` function. A child that uses the parent's registry is refused with
  `ForkedProcessError` when it was made by copying (the default on Linux before Python 3.14). A
  child started fresh imports your module again - one more reason the code above creates
  everything inside `main()`, behind `if __name__ == "__main__":`.

## Two mistakes ekbatan refuses

This is where Part 3 starts: from here on, everything applies to every kind of program.

Two mistakes are easy to make and, left alone, would fail later in confusing ways. ekbatan stops
both at once, with an error that says what to do. Neither fixes anything behind your back.

### Waiting on the database inside `async def`: `BlockingCallError`

As explained under [Code that waits](#code-that-waits), `async def` code shares one thread with
every other request, and ekbatan's sync API waits plainly. So this endpoint would freeze the whole
server while its query runs, and ekbatan raises `BlockingCallError` instead:

```python
@app.get("/wallets/{wallet_id}")
async def read_wallet(wallet_id: str, request: Request):        # async def ...
    return WalletRepository(request.app.state.registry).get(...)  # ... calling the sync API
```

Any one of three fixes works:

```python
# 1. In async code, use ekbatan.aio: an async repository on an AsyncDatabaseRegistry.
async def read_wallet(wallet_id: str, request: Request):
    return await AsyncWalletRepository(request.app.state.registry).get(...)

# 2. Make the endpoint a plain def: FastAPI runs it on a thread for you.
def read_wallet(wallet_id: str, request: Request):
    return WalletRepository(request.app.state.registry).get(...)

# 3. Keep async def, and send the sync call to a thread yourself.
async def read_wallet(wallet_id: str, request: Request):
    wallets = WalletRepository(request.app.state.registry)
    return await asyncio.to_thread(wallets.get, ...)
```

### Using an object in a process it was copied into: `ForkedProcessError`

As explained under [Started fresh, or copied](#started-fresh-or-copied), some servers import your
app once and then copy the whole process to make several workers - gunicorn with `--preload`,
uwsgi, Celery's default worker pool and RQ all do this. A copy keeps the objects, but not their
background threads, and its open connections are the very same ones the original process is
using. So if a registry, a job registry or a lock provider is used in a process other than the
one that created it, ekbatan raises `ForkedProcessError`.

The fix is always the same: move the line that creates the object from import time into startup:
the lifespan, the top of `main()`, or the server's own per-worker hook (the section for your kind
of program shows which).

## When your server runs several processes

Every worker runs your startup code (see [Processes and workers](#processes-and-workers)), so
each has its own registry, its own pools and its own job registry - with `uvicorn --workers 4`,
gunicorn's workers, Celery's children, and again in every Kubernetes replica. Two things follow.

**Connections multiply.** Count them before the database does. Say each process has a registry
pool of 10 connections (with up to 5 extra under load), a jobs pool of 4 and a lock pool of 2:

| | per process | 4 workers | 4 workers × 3 replicas |
| --- | --- | --- | --- |
| registry pool (10, up to 15 under load) | 15 | 60 | 180 |
| jobs pool | 4 | 16 | 48 |
| lock pool | 2 | 8 | 24 |
| **total, at most** | **21** | **84** | **252** |

Compare the last column with the database's connection limit (PostgreSQL's default
`max_connections` is 100), and size the pools per process with that in mind.

**Jobs need no change.** Every process runs a job registry, and they coordinate through the
database, so each job still runs exactly once.

## Migrations

Migrations change the database's tables, so they run once per deploy, before the new code uses
them. There are two places to run them:

- **At startup**, as the FastAPI lifespan and the worker above do. It is the simplest, and safe
  with several processes: ekbatan takes a lock, so one process migrates and the others wait, then
  find nothing left to do.
- **As a step of its own**, before the app starts: a short script your deploy runs once - a
  release step, or a Kubernetes Job. It is the better choice when many processes start at once,
  since they would all wait for the one that migrates; the Flask, Django and task-queue code
  above leaves migrations to this step:

```python
# migrate.py - run once per deploy, before the app starts
from ekbatan.config_files import load_ekbatan_config
from ekbatan.migrations import migrate

if __name__ == "__main__":
    migrate(load_ekbatan_config("ekbatan.yaml").sharding, "migrations")
```

See [Migrations](database/migrations.md) for the rest.

## In your tests

Tests start and stop the objects the same way your program does - which is one more reason not
to create them at import time.

- **FastAPI:** `with TestClient(app) as client:` runs the lifespan - startup as the `with` begins,
  shutdown as it ends. A `TestClient(app)` used without `with` does not run it, and
  `app.state.registry` is missing. The examples' tests use `with`.
- **Everything else:** call your own startup and shutdown from a pytest fixture - for the module
  in [One module for startup and shutdown](#one-module-for-startup-and-shutdown), `db.start()`
  before `yield` and `db.stop()` after.

```python
# FastAPI
@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as client:  # startup runs here ...
        yield client                 # ... and shutdown once the test is done


# Flask, Django, or anything else using myapp/db.py
@pytest.fixture
def started() -> Iterator[None]:
    db.start()
    yield
    db.stop()
```

## Running on Kubernetes

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
under the database's limit (see
[When your server runs several processes](#when-your-server-runs-several-processes)). Set
`terminationGracePeriodSeconds` longer than the server's own shutdown wait (for example
gunicorn's `--graceful-timeout`) and the job registry's `shutdown_max_wait`. Run migrations as
their own step - a Kubernetes Job, or an init container - rather than in every pod.

## What has been tested

**Run here:** uvicorn - every FastAPI example's tests run its lifespan, and a test serves an app
from a real uvicorn process; the copied-process refusal, with real copied processes on all four
databases; and the sync API under gevent.

**Checked against each server's own source code or documentation, but not run here:** the hooks
and shutdown behaviour described for gunicorn, uwsgi, waitress, hypercorn, granian, Celery and RQ.
The code shown for those servers has not been run.

## See also

- [Wiring with FastAPI](wiring-fastapi.md) - the lifespan, the dependency, the endpoints
- [Concurrency](runtime/concurrency.md) - threads, asyncio, gevent, and where the pools live
- [Distributed jobs](jobs/distributed-jobs.md) - the job registry, draining at exit, and SIGTERM
