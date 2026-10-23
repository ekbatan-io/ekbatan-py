# job-worker-uv-async-pg

A worker process: jobs, no HTTP - with the jobs' work on **native asyncio**, `ekbatan.aio`.
The same worker as [`job-worker-uv-pg`](../job-worker-uv-pg): the same two jobs, the same
handler, model, converters and migration, the same tests.

```
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=ekbatan -e POSTGRES_USER=ekbatan -e POSTGRES_DB=wallet postgres:17-alpine
```

<!-- build: written by scripts/sync-examples.py -->
```
make            # generate what is out of date, then test the built wheel, installed
make run        # run it; the database comes first, above
```

Built with **uv**. The same project with [pip and setuptools](../job-worker-pip-async-pg) and [Poetry](../job-worker-poetry-async-pg) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.
<!-- /build -->

The settings are in `ekbatan.yaml`, in the same keys as Java's `application.yml`: the layout
with the scheduler's own `jobsConfig` pool, the namespace, `jobs.pollingInterval: PT10S` and
`local-event-handler.handling.enabled`. Durations are ISO-8601, as Java's `Duration.parse` reads
them. Each pool puts its sessions on UTC with `options: -c TimeZone=UTC`, whatever the server's
default.

## What "async" means for a job

**A distributed job is sync, always.** db-scheduler claims the job's row in `scheduled_tasks`
and calls `execute` on one of its worker threads, and writes the heartbeat from another. That
is the same in every app that uses ekbatan, sync or async, and this example does not change it.

**The job's work is async.** Each job's `execute` hands a coroutine to the **worker loop** -
a `HandlerLoop`, one event loop on a thread of its own, the same kind dispatch runs async
handlers on - with `loop.run(...)`, and waits for it. The coroutine awaits an
`AsyncActionExecutor` and async repositories, on asyncpg:

```python
def execute(self, ctx: ExecutionContext) -> None:
    self.loop.run(self.pay())  # on db-scheduler's thread: run it there, wait


async def pay(self) -> None:
    low = await self.wallets.find_open_below(self.threshold)
    for wallet in low:
        await self.executor.execute(DepositMoneyAction, DepositParams(...))
```

Because the job's thread waits, whatever the coroutine raises, `execute` raises: a failure and
its retry are db-scheduler's, exactly as for a sync job. The async event handler runs on the
same loop - dispatch is handed it - so everything that awaits was built for one loop, and is
closed on it: `close()` stops the jobs, then closes the async registry on the loop, then the loop.

| | here |
| --- | --- |
| the jobs | sync, on db-scheduler's threads: the claim, the heartbeat, the retry |
| the jobs' work | async, on the worker loop: `PayStipendJob.pay` and `WalletReportJob.report` await an `AsyncActionExecutor` and async repositories |
| the event handler | async, on the same worker loop |
| fan-out and dispatch | sync jobs, on threads |
| migrations, `schemagen` | sync, through psycopg |

Two pools, from one url that names psycopg:

| pool | used by | driver |
| --- | --- | --- |
| `primaryConfig` | the jobs' work and the handler, on the worker loop | asyncpg |
| `jobsConfig` | db-scheduler, and fan-out and dispatch | psycopg |

When is this worth it? When the work itself waits on many things at once - a job that fans out
to other services or to many shards and can await them together. A job whose work is a few
statements in a row gains nothing from it: [`job-worker-uv-pg`](../job-worker-uv-pg) is the
simpler choice, and an async web service can run it beside itself unchanged.

## What it shows

**Two jobs, and the two shapes a job comes in.** `PayStipendJob` writes, through the same
actions an HTTP request would use. `WalletReportJob` only reads — a scheduled query, which is
the more common kind: ops dashboards, metrics exporters, freshness probes. It invokes no action
and so produces no outbox row, which is correct — nothing happened.

**Once per slot, across the cluster.** Three replicas of this worker share one row per job in
`scheduled_tasks`, and the row is what makes that true. Nothing in either job class arranges it.

**A job is an ordinary caller.** The stipend goes through `DepositMoneyAction`, so the outbox row
it writes is indistinguishable from one a request produced — `action_name` says
`DepositMoneyAction` either way. A consumer downstream cannot tell, and does not need to.

**The whole chain in one process.** Job → action → outbox → fan-out → handler → another action,
with no HTTP anywhere and no second runtime. The fan-out and handling jobs are `DistributedJob`s
themselves, claimed from the same table as the stipend — so a cluster fans out once rather than
once per replica.

## The two things worth copying

**Idempotency by construction, not by promise.** A slot *can* run twice: if an instance stops
writing heartbeats — because it died, stalled, or briefly lost the database — another treats the
execution as dead and runs it again, while the first may still be part-way through. The claim
protects the row, not your method.

`PayStipendJob` handles that by selecting wallets *below* a threshold, so a repeat finds the ones
it already topped up are no longer below it. A job that deposited unconditionally would
double-pay on the replay that at-least-once delivery makes inevitable.

**SIGTERM is yours to arrange, or the framework's if you ask.** A started registry drains on a
normal exit and on Ctrl-C by default, through `atexit`. SIGTERM does not run `atexit` in
CPython, and by default no signal handler is installed, so a container stop would end the
process with executions still picked and their rows waiting out the dead-execution window - 60
seconds on framework defaults. `__main__.py` catches
the signal and calls `close()`, which is what makes a rolling deploy clean rather than merely
survivable.

`register_shutdown_hook()` on the builder does the same thing for you. It is off by default
because CPython has **one** handler slot per signal, and a library that installed one uninvited
would be taking the application's SIGTERM away from it - where a JVM shutdown hook is additive,
which is why Java's `JobRegistry` can turn its own on without asking. Either way, keep whatever
handler was already there and call it: dropping it is harmless in a `__main__` and silently
wrong inside a gunicorn or uvicorn worker.

## Two forced types

`pyproject.toml` tells schemagen to turn two plain text columns into small classes of this
app's own:

```toml
# Every CHAR(3) column is a currency code, so the rule picks columns by their type.
[[tool.ekbatan.schemagen.forced-types]]
converter = "job_worker_async_pg.converters.CurrencyConverter"
include-types = "CHAR\\(3\\)"

# One column, picked by its name.
[[tool.ekbatan.schemagen.forced-types]]
converter = "job_worker_async_pg.converters.EmailAddressConverter"
include-expression = "alerts\\.recipient"
```

and schemagen writes these two lines into `schema.py`:

```python
Column("currency", converted(CHAR(length=3), CurrencyConverter()), nullable=False)
Column("recipient", converted(VARCHAR(length=255), EmailAddressConverter()), nullable=False)
```

and, in the records beside the tables, the converters' own types - so a repository's
`to_record` that hands the table a plain string where a `Currency` belongs is a mypy error:

```python
class AlertsRecord(dict[str, Any]):
    ...

    def column_recipient(self) -> EmailAddress: ...


class WalletsRecord(dict[str, Any]):
    ...

    def column_currency(self) -> Currency: ...
```

- **Picked by type.** Every `CHAR(3)` column is a currency code, whatever it is called. A
  wallet's `currency` is a `Currency`, which takes only three capital letters: `"eur"` is
  refused.
- **Picked by name.** `alerts.recipient` is an `EmailAddress`. The stipend job's alerts go to
  `stipend@example.com`; `DepositMoneyAction` checks the address before it moves any money, so
  a bad one never reaches the handler.

The two classes are in `model.py` and the converters in `converters.py`; neither knows
SQLAlchemy. The repository hands the table a `Currency` and gets one back, and the column holds
`EUR`. Action parameters and events keep the plain text, `"currency": "EUR"`, because they are
written to the outbox as JSON for other services to read. The test checks each value in Python
and in the table.

## The pools

The scheduler gets **its own engine**, sized above the worker count. db-scheduler writes
heartbeats from a housekeeping thread; a job body holding the last connection starves it, and an
execution whose heartbeat stops is declared dead and run again somewhere else. The framework
warns at startup when the two are mismatched — listen to it.

`workers(n)` sets how many threads run job bodies - each of which, here, waits on the worker
loop while its work runs there. Java needs no such setting: it hands
db-scheduler an unbounded virtual-thread executor. Here they are real OS threads, so the number
is chosen rather than assumed.

## Seeing the cluster behaviour

Start two of these against the same database and watch each slot run once rather than twice:

```
WORKER_DATABASE_URL=postgresql+psycopg://localhost:5432/wallet \
WORKER_DATABASE_USERNAME=ekbatan WORKER_DATABASE_PASSWORD=ekbatan \
  make run
```

## Its test

```
make
```

Twelve cases against a real PostgreSQL in Testcontainers, the scheduler's clock in the test's
hands - the sync twin's ten, and two more: the stipend's work really runs on the worker loop's
thread, and what the work raises reaches the job. The settings test also checks the work's pool
is asyncpg and the scheduler's psycopg, both on UTC.
