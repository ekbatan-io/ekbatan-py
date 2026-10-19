# wallet-service-pip-async-sqlite

The async twin of [`wallet-service-uv-sqlite`](../wallet-service-uv-sqlite): the same wallet, end to end,
with every unit of work awaited on `ekbatan.aio`. Runs on SQLite, through aiosqlite, so you can
try it now:

<!-- build: written by scripts/sync-examples.py -->
```
make run        # run it
make            # test the built wheel, installed
```

Built with **pip and setuptools**. The same project with [uv](../wallet-service-uv-async-sqlite) and [Poetry](../wallet-service-poetry-async-sqlite) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.

The venv is made with `python3`, which must be 3.13 or later - `make PYTHON=python3.13` if yours is older.
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

**`jobs.py`** - the daily stipend is still a sync job: db-scheduler claims its row and calls
`execute` on one of its threads. Its work is a coroutine, handed to the handler loop with
`loop.run(...)`, where it awaits the handler's executor and repository.

**`app.py`** - an `AsyncDatabaseRegistry` and an `AsyncActionExecutor` for the units of work; a
second async registry and executor for the handler and the stipend's work, used only on the
handler loop; and a sync `DatabaseRegistry` for what polls - db-scheduler, fan-out, dispatch and
the stipend - built from the same `ekbatan.yaml`. `start()` is awaited and returns once the
stipend's first run is over; `aclose()` closes everything in order.

**`__main__.py`** - the narration is a coroutine, run with `asyncio.run`.

## What it leaves out

Keyed locks: neither twin takes one. SQLite has no advisory locks, so there is no keyed lock
provider for it, sync or async; a service on SQLite that needs one uses the Redis provider
(`ekbatan.aio.locks.redis.AsyncRedisKeyedLockProvider` here). The server-database examples take
one around the deposit.

## What it does not show

That state and its events commit together even when the process dies mid-write. That is the
guarantee the whole design exists for, and no example can demonstrate it - only a test can, and
the framework has them.
