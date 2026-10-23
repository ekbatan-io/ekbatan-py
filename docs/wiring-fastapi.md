# Wiring with FastAPI

Where a service meets the outside world, and where the two sharpest mistakes are available:
building the service per request instead of once, and calling the sync API from an `async def`
endpoint.

[Wiring](wiring.md) is the framework half and has nothing to do with HTTP. This page is the
seam between the two.

> From [`ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/api.py`](../ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/api.py).

## Build once, in a lifespan

**Sync**

```python
def create_app(settings: Settings | None = None) -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Built and started on a worker thread: `build` runs the migrations and starting
        # registers the jobs, both sync and both waiting on the database - and the sync API
        # is refused on the event loop's own thread.
        service = await asyncio.to_thread(build, settings)
        await asyncio.to_thread(service.start)
        app.state.service = service
        try:
            yield
        finally:
            await asyncio.to_thread(service.close)

    app = FastAPI(title="Wallet", lifespan=lifespan)
```

**Async**

```python
def create_app(settings: Settings | None = None) -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # `build` is sync - it runs the migrations - and so is starting the jobs: both on a
        # worker thread, since the sync API is refused on the event loop's own thread.
        service = await asyncio.to_thread(build, settings)
        await asyncio.to_thread(service.start)
        app.state.service = service
        try:
            yield
        finally:
            await service.aclose()     # the async pools close with `aclose()`

    app = FastAPI(title="Wallet", lifespan=lifespan)
```

Pools, the scheduler and the migration run belong to the process, not to a request. `lifespan`
is where a process-scoped thing is born and buried, and the `try/finally` is what closes pools
when the server shuts down rather than leaking them.

The lifespan is an `async def`, so it runs on the event loop's thread, where the sync API is
refused with `BlockingCallError`: building (the migrations) and starting and stopping the jobs
go through `asyncio.to_thread`, as above. Never build the service at import time - a module-level
`service = build()` - either: a server that forks its workers would copy it into each one, and
ekbatan refuses to work in a copy (`ForkedProcessError`). See [Deploying](deploying.md).

A **factory** rather than a module-level `app = FastAPI()`, so a test can build one against a
throwaway database with settings of its own - the url and login set with `monkeypatch.setenv`
for that test only. Every example's test does exactly that.

## The dependency, and the trap in it

```python
def service_of(request: Request) -> WalletService:
    service: WalletService = request.app.state.service
    return service


Service = Annotated[WalletService, Depends(service_of)]
```

Then every endpoint takes `service: Service`.

**`Service` must be at module level.** This looks like a style point and is not. With
`from __future__ import annotations` - which every module in this repository has - FastAPI
resolves the string annotations against the **module** namespace. An `Annotated[...]` alias
defined inside `create_app` is invisible to that lookup, so FastAPI cannot tell the parameter is
a dependency and reads it as an unfilled *query parameter* instead.

The symptom is a 422 on every endpoint, naming a field nobody declared. It cost an afternoon.

### Never open a transaction in a dependency

A sync `yield` dependency looks like the place to open one transaction per request:

```python
def in_transaction(service: Service) -> Iterator[None]:     # wrong
    with service.registry.default.transaction():
        yield
```

It does not cover the endpoint. FastAPI runs a sync dependency and a sync endpoint in separate
copies of the request's context, each on its threadpool, so the endpoint finds no transaction:
each of its statements commits on its own, and a failure rolls nothing back. When the
dependency's block ends, in yet another context, the framework raises `TransactionContextError`
saying so - Python's own error would only say a token "was created in a different Context".

Open the transaction where the work runs. Usually that is the action: `executor.execute(...)`
opens and commits its own. Work outside an action opens it in the endpoint:

```python
@app.post("/wallets/{wallet_id}/notes", status_code=204)
def add_note(wallet_id: str, body: NoteRequest, service: Service) -> None:
    with service.registry.default.transaction():
        ...   # every write here is in this one transaction
```

The same holds with `ekbatan.aio`: open it in the endpoint or the action, not in a dependency.
See [The transaction manager](database/transaction-manager.md#repository-writes-join-automatically).

## With the sync API, a plain `def` endpoint is the right endpoint

**Sync**

```python
@app.post("/wallets", response_model=WalletResponse, status_code=201)
def open_wallet(body: OpenWalletRequest, service: Service) -> WalletResponse:
    return WalletResponse.of(
        service.executor.execute(
            OpenWalletAction,
            OpenWalletParams(owner_id=body.owner_id, currency=body.currency,
                             opening_balance=body.opening_balance),
        )
    )
```

**Async**

```python
@app.post("/wallets", response_model=WalletResponse, status_code=201)
async def open_wallet(body: OpenWalletRequest, service: Service) -> WalletResponse:
    return WalletResponse.of(
        await service.executor.execute(
            OpenWalletAction,
            OpenWalletParams(owner_id=body.owner_id, currency=body.currency,
                             opening_balance=body.opening_balance),
        )
    )
```

`def`, not `async def`, and with the sync API that is the single most important line on this
page.

The sync API blocks. FastAPI runs a plain `def` endpoint **in a threadpool**, so a blocking database call there blocks one worker
thread and nothing else. Each request waits on its own thread, and the others carry on.

An `async def` endpoint runs **on the event loop**, and a blocking call there stops the entire
loop - every other request on that worker, not just this one. Measured: a 0.5s framework call
from inside an `async def` handler let a background heartbeat tick **5 times where a free loop
manages about 55**.

If you need an `async def` endpoint and keep the sync API - because something else in it
genuinely awaits - then hand the framework call to a thread, or build the service on
`ekbatan.aio` instead (next section):

```python
wallet = await run_in_threadpool(service.wallets.get, wallet_id)
```

The example service is written with plain `def` throughout, which is why it needs no
`to_thread` anywhere.

**None of this applies under gevent** - where a blocking call yields to other work and costs
nothing. But gevent serves **WSGI**, and FastAPI is ASGI, so a FastAPI service cannot have it. If
your front end is Flask or Django, see the gevent section of [Concurrency](runtime/concurrency.md).

See [Concurrency](runtime/concurrency.md).

## `async def` endpoints, on `ekbatan.aio`

The other way: build the request path on the async API, and every endpoint is an `async def` that
awaits - the Async samples on this page. Nothing blocks the event loop, and under load it does
more work than a thread pool does.

`service.executor` is an `AsyncActionExecutor` and `service.wallets` an async repository. Two
things change over the sync service:

- **A keyed lock around the unit of work**, from `ekbatan.aio.locks`: `async with
  locks.acquire(key, max_hold):` around `await executor.execute(...)`. Its holder is the task.
- **Handlers run after the action, on dispatch's threads.** A handler may be `async def` and await
  an executor of its own on dispatch's handler loop; fan-out, dispatch and the jobs stay on
  threads through a sync registry built from the same config file.

[Going async](going-async.md) walks through it, and
[`fastapi-wallet-rest-uv-async-pg`](../ekbatan-py-examples/fastapi-wallet-rest-uv-async-pg) runs
it.

## Cap the load, in both modes

Two caps, the same whichever API the endpoints use:

- **A 503 for a full pool.** The connection pool is the cap on requests waiting on the database:
  one that waits `poolTimeout` (`pool_timeout`) for a connection fails with SQLAlchemy's
  `TimeoutError`, in both modes. Map it, once, to a 503 with `Retry-After`. Without it the caller
  gets a 500, after the default 30 seconds.
- **A cap at the door.** `uvicorn --limit-concurrency 50` answers 503 itself past 50 open
  requests, before they reach the app. Keep it a small multiple of the pool.

## Map the framework's errors once

```python
@app.exception_handler(EntityNotFoundError)
async def _missing(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(error)})


@app.exception_handler(ValidationError)
async def _rejected(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(error)})


@app.exception_handler(StaleRecordError)
async def _lost_a_race(request: Request, error: Exception) -> JSONResponse:
    # 409, not 500. The caller did nothing wrong; the right response is to try again.
    return JSONResponse(status_code=409, content={"detail": "the wallet changed; retry"})
```

The error kinds exist precisely so a caller can tell a bad request from a lost race from an
unreachable database - three things that call for opposite responses, which is what a status
code is for. Mapping them once here beats catching them in every endpoint.

**A Starlette exception handler must `return` a response, not `raise` one.** An exception raised
from inside a handler is not caught again and becomes a 500, which is the exact opposite of what
these are for. The signature returning `JSONResponse` is the reminder.

`StaleRecordError` → **409** is the one worth arguing about and the answer is clear: two deposits
raced, the loser's version check failed, and the caller did nothing wrong. A 500 would page
somebody.

## Keyed locks across a request

**Sync**

```python
lease = service.locks.try_acquire(f"wallet:{wallet_id}", LOCK_WAIT, LOCK_HOLD)
if lease is None:
    raise HTTPException(status_code=503, detail="that wallet is busy; retry",
                        headers={"Retry-After": "1"})
with lease:
    return WalletResponse.of(service.executor.execute(DepositMoneyAction, params))
```

**Async**

```python
lease = await service.locks.try_acquire(f"wallet:{wallet_id}", LOCK_WAIT, LOCK_HOLD)
if lease is None:
    raise HTTPException(status_code=503, detail="that wallet is busy; retry",
                        headers={"Retry-After": "1"})
async with lease:
    return WalletResponse.of(await service.executor.execute(DepositMoneyAction, params))
```

Three things here.

**The lease spans `execute`, not just the read.** It has to cover `perform()` *and* the
executor's commit, or the race it exists to prevent reopens in the gap. The `with` block holds
the lease for exactly that long and lets go however the block ends. In async mode the lock is
from `ekbatan.aio.locks`, its holder is the request's task, and waiting for it does not block
the loop.

**`try_acquire`, not `acquire`.** An HTTP request must not block forever. A lease that cannot be
had within the wait comes back as `None` - which is *contention*, not a failure, and the two are
kept distinguishable on purpose. 503 with a `Retry-After` is a cue to come back, not a bug
report.

**Why lock at all, when optimistic locking already works?** It does work - a concurrent deposit
loses the version check and raises. But it is correct by *failing*. Taking the key first means
the second caller waits and then succeeds, which is what anyone depositing into a busy wallet
wants. Different wallets take different keys and never contend.

`max_hold` is not a timeout on your code. It is the promise that the lock is released even if
this process dies holding it - set it above the worst case for the work inside.

## Do not return the model

```python
class WalletResponse(BaseModel):
    id: UUID
    owner_id: UUID
    currency: str
    balance: Decimal
    state: str
```

`Wallet` carries a version, timestamps and an event list. A client needs none of them and should
not break when one changes.

One subtlety the example documents at length: **quantize money at the boundary.** What an action
*returns* is the model it staged, at whatever scale the caller passed in; what a later read
returns is the column's scale. So `POST /wallets` answers `100.00` and `GET /wallets/{id}`
answers `100.0000` for the same untouched wallet - true, harmless, and exactly the sort of thing
that becomes a bug report. Presentation scale is the API's business, so settle it once in the
response model rather than making the write path do a read it does not need.

## Testing

```python
with TestClient(create_app(settings)) as client:
    ...
```

The `with` matters: `TestClient` only runs the lifespan inside a context manager, so without it
`app.state.service` is never set and every request fails on the dependency. That is the third
trap and the least interesting one. The same in both modes; in async mode, a test that reaches
past HTTP into the service awaits on the app's own loop, through `client.portal`.

## See also

- [Wiring](wiring.md) - what `build()` contains
- [Concurrency](runtime/concurrency.md) - the `def` versus `async def` question in full
- [Native asyncio](runtime/async.md) - `ekbatan.aio` in full
- [`fastapi-wallet-rest-uv-pg`](../ekbatan-py-examples/fastapi-wallet-rest-uv-pg) - all of the above,
  running
