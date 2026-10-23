# Deploying

One rule covers every server: **create ekbatan when your app starts, and stop it when it stops.**
In a FastAPI app that is the lifespan; in a plain worker, the top of `main()`. Normal code uses
the sync API; `async def` code uses `ekbatan.aio`.

Two mistakes are refused with a clear error rather than left to fail later in a strange way:

| error | when | the fix |
| --- | --- | --- |
| `ForkedProcessError` | something ekbatan built - a registry, a lock provider, a job registry, a handler loop - is used in a process that `fork()` copied it into | build it in the process that uses it: in the lifespan, or at the top of the worker's `main()`, never at import time |
| `BlockingCallError` | the sync API - a transaction, a connection, a repository, the executor, a migration run - is called on an event loop's thread, inside `async def` | use `ekbatan.aio`; or make the endpoint a plain `def`; or call it with `await asyncio.to_thread(...)` |

Both only refuse. Nothing is reconnected, reset or moved to a thread behind your back.

## The two shapes

**A FastAPI app.** The lifespan is `async def`, so the sync work in it - building the service
runs the migrations, starting registers the jobs - goes through `asyncio.to_thread`:

**Sync**

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    service = await asyncio.to_thread(build)
    await asyncio.to_thread(service.start)
    app.state.service = service
    try:
        yield
    finally:
        await asyncio.to_thread(service.close)
```

**Async**

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    service = await asyncio.to_thread(build)
    await asyncio.to_thread(service.start)
    app.state.service = service
    try:
        yield
    finally:
        await service.aclose()
```

Every REST and saga example is written this way; [Wiring with FastAPI](wiring-fastapi.md) walks
through it.

**A plain worker** - a job worker, a consumer, a script:

```python
def main() -> None:
    service = build()      # here, at the top of main() - not at import time
    service.start()
    try:
        service.wait()     # whatever your worker does until it is told to stop
    finally:
        service.close()
```

[`job-worker-uv-pg`](../ekbatan-py-examples/job-worker-uv-pg) is that shape, and
[`job-worker-uv-async-pg`](../ekbatan-py-examples/job-worker-uv-async-pg) the same with its work
awaited on a loop.

## Three things true on every server

**Pools are per process.** Four worker processes hold four of every pool, so the database sees
`4 x (pool_size + job pool + lock pool)` connections. Size per process, then multiply by
processes and by replicas before comparing with `max_connections`. Every process also runs the
scheduler; that is fine, since a job runs on exactly one of them.

**SIGTERM.** A container stop sends SIGTERM. An ASGI server - uvicorn, hypercorn, granian,
gunicorn's UvicornWorker - turns it into a lifespan shutdown, which stops the jobs; gunicorn's
other workers run `worker_exit`. A plain worker has no server to do that: call
`register_shutdown_hook()` on the job registry builder, or stop it from your own handler. A
registry started where nothing handles SIGTERM logs one warning saying so - see
[Draining at exit](jobs/distributed-jobs.md#draining-at-exit-on-by-default-and-the-sigterm-half-opt-in).

**Migrations** run once per deploy, ideally as their own step - see
[Migrations](database/migrations.md). At startup they run in `build()`, on a worker thread.

## What is tested

| setup | status |
| --- | --- |
| uvicorn | **tested**: every FastAPI example's tests run its lifespan, and a test serves an app from a real uvicorn process |
| the fork refusal | **tested**: real `os.fork()` children, sync and async, on all four databases - built before the fork is refused, built in the child works, the parent is untouched |
| the sync API under gevent's monkey-patching | **tested**: the whole suite runs patched (`make test-gevent`), and the event-loop refusal stays silent there |
| everything else below | **expected**, from how each server starts its workers - not run here |

## Per server

### uvicorn, and `fastapi run`

The golden path as it is. `--workers N` starts N processes, each running the lifespan, so each
builds its own pools. uvicorn starts its workers fresh rather than by copying the parent, so
nothing is shared. Sync endpoints as plain `def`, async ones on `ekbatan.aio`. `fastapi run`
is uvicorn.

### gunicorn with `uvicorn.workers.UvicornWorker`

An ASGI app, so the lifespan runs in every worker - build there and nothing is copied. gunicorn
starts workers with `fork()`, and **`--preload`** imports your app in the parent before forking:
anything ekbatan built at import time is then copied into every worker, and refused there with
`ForkedProcessError`. With the service built in the lifespan, `--preload` is fine.

### gunicorn with `gthread` or `gevent` workers (Flask, Django, any WSGI app)

WSGI has no lifespan. Build the service in gunicorn's `post_fork` hook, which runs in each worker
after the fork, and close it in `worker_exit`:

```python
# gunicorn.conf.py
def post_fork(server, worker):
    import myapp
    myapp.service = myapp.build()
    myapp.service.start()

def worker_exit(server, worker):
    import myapp
    myapp.service.close()
```

Use the sync API. Under `-k gevent` every blocking call yields, with nothing to change; the
event-loop refusal stays silent because no asyncio loop runs there. `ekbatan.aio` does not run
under gevent's patching. `--preload` is fine as long as nothing is built at import time.

### uwsgi

uwsgi loads the app once and then forks its workers, so a service built at import time is
copied. Either set `lazy-apps = true`, so each worker loads the app itself, or build it in a
`uwsgidecorators.postfork` function. Sync API, as for gunicorn's WSGI workers.

### hypercorn (asyncio workers)

An ASGI server with a lifespan: the same as uvicorn. `ekbatan.aio` in `async def` endpoints, a
plain `def` endpoint for the sync API.

### granian

With `--interface asgi` it runs the lifespan in each worker: the same as uvicorn.

### waitress

One process, a pool of threads, WSGI. Build the service at the top of your `main()`, before
`serve(app, ...)`, and close it after. Sync API.

### Celery

The default **prefork** pool forks its child processes from the worker. Build ekbatan in each
child with the `worker_process_init` signal and close it with `worker_process_shutdown`:

```python
from celery.signals import worker_process_init, worker_process_shutdown

@worker_process_init.connect
def start_ekbatan(**_):
    tasks.service = build()

@worker_process_shutdown.connect
def stop_ekbatan(**_):
    tasks.service.close()
```

With `--pool threads` or `--pool gevent` there is one process: build it in `worker_init` and
close it in `worker_shutdown`. Tasks use the sync API.

### RQ

`rq worker` forks a new process for every job, so a service built in the worker is copied into
each job and refused. Run a `SimpleWorker`, which runs jobs in its own process, from your own
`main()` that builds the service first; or build it inside the job, at the price of new pools per
job.

### Kubernetes replicas

Each pod runs one of the setups above, so everything per process holds per pod: multiply the
pools by replicas too. Jobs need no change - the scheduler picks one runner across all pods. Give
`terminationGracePeriodSeconds` more time than the job registry's `shutdown_max_wait`, so a
stop can let running jobs finish, and run migrations as their own step (a Job or an init
container) rather than in every pod at once.

## See also

- [Wiring with FastAPI](wiring-fastapi.md) - the lifespan, the dependency, the endpoints
- [Concurrency](runtime/concurrency.md) - threads, asyncio, gevent, and where the pools live
- [Distributed jobs](jobs/distributed-jobs.md) - draining at exit, and SIGTERM
