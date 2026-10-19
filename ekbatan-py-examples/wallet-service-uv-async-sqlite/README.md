# wallet-service-uv-async-sqlite

The async twin of [`wallet-service-uv-sqlite`](../wallet-service-uv-sqlite): the same wallet, end to end,
with every unit of work awaited on `ekbatan.aio`. Runs on SQLite, through aiosqlite, so you can
try it now:

<!-- build: written by scripts/sync-examples.py -->
```
make run        # run it
make            # test the built wheel, installed
```

Built with **uv**. The same project with [pip and setuptools](../wallet-service-pip-async-sqlite) and [Poetry](../wallet-service-poetry-async-sqlite) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.
<!-- /build -->

It starts the jobs, creates a wallet, deposits into it, waits while the in-process handler
reacts to the deposit by writing a notification, and prints the outbox so you can see what a
consumer would read. Nobody calls the handler: the fan-out and handling jobs run in the
background, on threads, as in any service, switched on by `local-event-handler.handling.enabled`
in `ekbatan.yaml`.

## What is the same as the sync twin

`model.py` and `schema.py`, byte for byte. The rows each unit of work writes, the events in the
outbox, the optimistic lock, soft delete and the handler's at-least-once delivery mean the same
thing in both modes - they are the same code.

## What is different

**`actions.py`** - `AsyncAction`, and `perform` is `async def`: it awaits its reads. What it
stages is the same.

**`repository.py`** - the repositories extend `AsyncModelRepository` and
`AsyncEntityRepository`, and every query of their own is `async def`. `to_row` and `from_row`
stay plain functions, written in each repository as in the sync twin.

**`handlers.py`** - `MoneyDepositedEventHandler.handle` is `async def` and awaits an executor of
its own. It still runs after the deposit has committed, never inside it: dispatch runs it on
its handler loop, one event loop on a thread of its own.

**`app.py`** - an `AsyncDatabaseRegistry` and an `AsyncActionExecutor` for the units of work; a
second async registry and executor for the handler, used only on the handler loop; and a sync
`DatabaseRegistry` for what polls - db-scheduler, fan-out and dispatch - built from the same
`ekbatan.yaml`. `aclose()` closes them in order.

**`__main__.py`** - the narration is a coroutine, run with `asyncio.run`.

## What it leaves out

The sync twin's daily stipend job. Distributed jobs always run on threads, through the sync API,
so a job in an async service is written exactly as the sync twin's `jobs.py`, over a sync
executor of its own; repeating it here would show nothing async.

Keyed locks: neither twin takes one. SQLite has no advisory locks, so there is no keyed lock
provider for it, sync or async; a service on SQLite that needs one uses the Redis provider
(`ekbatan.aio.locks.redis.AsyncRedisKeyedLockProvider` here). The server-database examples take
one around the deposit.

## What it does not show

That state and its events commit together even when the process dies mid-write. That is the
guarantee the whole design exists for, and no example can demonstrate it - only a test can, and
the framework has them.
