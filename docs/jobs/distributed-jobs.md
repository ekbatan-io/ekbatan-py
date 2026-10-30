# Distributed jobs

Background work that runs on a schedule, on one instance of your service rather than on all of them.
Built on [db-scheduler](https://github.com/kagkarlsson/db-scheduler), which keeps the schedule in a
database table.

## Defining a job

**Sync**

```python
class WalletStipendJob(DistributedJob):
    def __init__(self, executor: ActionExecutor, wallet_repository: WalletRepository) -> None:
        self.executor = executor
        self.wallet_repository = wallet_repository

    def name(self) -> str:
        return "wallet-stipend"

    def schedule(self) -> Schedule:
        return FixedDelay.of_seconds(2)

    def execute(self, context: ExecutionContext) -> None:
        for wallet in self.wallet_repository.find_open_with_balance_below(THRESHOLD):
            self.executor.execute(None, WalletDepositMoneyAction, WalletDepositMoneyAction.Params(...))
```

**Async**

```python
class WalletStipendJob(DistributedJob):
    def __init__(
        self, loop: HandlerLoop, executor: AsyncActionExecutor, wallet_repository: WalletRepository
    ) -> None:
        self.loop = loop                  # the loop `executor` and `wallet_repository` were built for
        self.executor = executor
        self.wallet_repository = wallet_repository

    def name(self) -> str:
        return "wallet-stipend"

    def schedule(self) -> Schedule:
        return FixedDelay.of_seconds(2)

    def execute(self, context: ExecutionContext) -> None:
        self.loop.run(self.pay())         # still on the scheduler's thread: run it there, wait

    async def pay(self) -> None:
        for wallet in await self.wallet_repository.find_open_with_balance_below(THRESHOLD):
            await self.executor.execute(None, WalletDepositMoneyAction, WalletDepositMoneyAction.Params(...))
```

A job is a class with three methods:

- `name()` is the job's id across the cluster: the key of its row in the `scheduled_tasks`
  table. Keep it the same on every instance and across restarts.
- `schedule()` says when it runs. `FixedDelay.of_seconds(2)` runs it again two seconds after the
  previous run finished.
- `execute(context)` is the work.

`DistributedJob` is ekbatan's; the schedule types and the context are db-scheduler's, which is
installed with ekbatan:

```python
from db_scheduler.task import ExecutionContext
from db_scheduler.task.schedule import FixedDelay, Schedule
from ekbatan.distributedjobs import DistributedJob
```

Nothing about being a job makes the work special. It goes through an ordinary action, so the
outbox row it writes looks exactly like one written during a request: `action_name` says
`WalletDepositMoneyAction` either way, and nothing downstream can tell or needs to.

**In an async service the job is still sync.** `execute` is a plain `def`, run on the
scheduler's threads, in both modes. That leaves two choices:

- The simplest: give the job a sync executor and sync repositories, built from the same config
  file as the async ones.
- Or make its *work* async, as in the Async block. `execute` hands a coroutine to a `HandlerLoop`
  with `loop.run(...)` and waits for it. A `HandlerLoop` is an event loop on a thread of its own,
  the same kind that runs async event handlers. The claim, the heartbeat and a retry then behave
  exactly as for a sync job. What the work awaits must be built for that loop, and closed on it
  after the jobs stop.

See [a job whose work is async](../runtime/async.md#a-job-whose-work-is-async) and the
[`job-worker-uv-async-pg`](../../ekbatan-py-examples/job-worker-uv-async-pg)
example.

## How instances share the work

db-scheduler does the coordination, through the shared `scheduled_tasks` table:

- Every instance polls the table.
- When a scheduled run (a *slot*) comes due, **exactly one instance wins the claim** and runs it.
- The running instance heartbeats: it stamps the row regularly to show it is alive. If the
  heartbeat goes stale, another instance reclaims the row **and runs the job again**, while the
  first may still be part-way through `execute()`.
- A job that raises is retried per its schedule, and the row's `consecutive_failures` goes up.
  That is the column to alert on.

**The claim is per slot.** A job due daily at 02:00 is claimed by one instance at 02:00, across
the whole cluster. If 02:00 passes while every instance is down, the missed slot is run by the
next instance that polls.

### The rule every job body has to obey

**A slot can run twice.** The claim protects the row, not your method. db-scheduler stops a stale
instance from recording a *result*; it cannot undo the side effects that instance already caused.

Write `execute()` so that running the same slot twice is harmless, with the same techniques as
for [event handlers](../events/local-event-handler.md#handlers-must-be-idempotent): an
insert with a conflict clause, an update whose `WHERE` checks the state it expects, an
idempotency key on an external call.

### Bailing out before you are taken over

An execution is declared dead after **60 seconds** without a successful heartbeat
(`heartbeat_interval x missed_heartbeats_limit`, 10s x 6). The instance being replaced is **not
told and not interrupted**. Heartbeats are written by a separate housekeeping thread, so a job
whose work does not touch the scheduler's database carries on without noticing.

That is the usual way a slot runs twice. The first instance did not die; it just could not reach
the database for a while to say that it was alive.

A job that must not overlap itself can check its own heartbeat health, which the
`ExecutionContext` passed to `execute` carries:

```python
def execute(self, context: ExecutionContext) -> None:
    health = context.currently_executing.heartbeat_state
    for invoice in invoices:
        # 0.0 right after a good heartbeat, 1.0 at the moment another instance may claim the
        # row. Above ~0.5, contact has been lost long enough to be worth stopping over.
        if health.fraction_dead > 0.5:
            LOG.warning("heartbeats failing; stopping before another instance takes over")
            return
        send(invoice)
```

Returning early counts as a normal, successful run. The row is rescheduled, and the rest of the
work is picked up on the next run, or straight away by whichever instance reclaims the row, since
the reclaim happens anyway.

This **only works if the job checks often.** It suits a job looping over work items, and cannot
help one stuck inside a single long call. It makes the overlap shorter rather than impossible: an
`execute()` that is safe to run twice is the only complete answer.

## Wiring

```python
from ekbatan.distributedjobs import job_registry

jobs_data_source = DataSource.from_config(DataSourceConfig(url=url, pool_size=8))
jobs = (
    job_registry()
    .data_source(jobs_data_source)
    .with_jobs([WalletStipendJob(executor, wallet_repository), WalletReportJob(wallet_repository)])
    .workers(3)
    .build()
)
jobs.start()
...
jobs.stop()
jobs_data_source.close()  # the registry never closes the pool it polls
```

If you use in-process event handlers, their two jobs (fan-out and dispatch) are registered here
too: they *are* distributed jobs. See
[In-process event handlers](../events/local-event-handler.md#wiring).

### `workers(n)`, and sizing the pool

**Give the jobs a pool of their own**, as `jobs_data_source` above. db-scheduler polls all the
time and heartbeats from its own thread. On the request pool, a burst of traffic could starve the
heartbeat, and a starved heartbeat means an execution runs twice.

`workers(n)` is the most job bodies that run at once, each on a **real OS thread**. It defaults
to one per registered job.

That default is right: a recurring job is a single row, so it has at most one execution in
flight. Raise it if you schedule the same job to overlap itself, or use
`enable_immediate_execution` through `customize_scheduler`. Below the job count, a slow job
delays the others rather than only itself.

**A ceiling, not a reservation.** Threads start as work arrives, and only when no worker is
already waiting, so twenty registered jobs with nothing due hold no worker threads at all. Once
started, a worker is not retired: an idle one stays until the registry stops.

**A running job holds no connection by itself.** db-scheduler takes a connection to claim the
row, gives it back, runs your body with none open, then takes another to write the result. Only
your body's own database work holds one, for as long as that work takes. So a job waiting on an
HTTP call needs no connection while it waits.

**The jobs pool needs `workers + 3` connections** when your job bodies use the database, which
most do. The three are for the scheduler's own loops (poll, heartbeat, and spotting dead
executions), each of which takes a connection when it runs. If a job body holds the last
connection, the heartbeat cannot write, and an execution whose heartbeat stops is declared dead
and run again somewhere else. If the pool is no bigger than `workers`, db-scheduler logs a
warning at startup with the number it wants.

### What the three knobs do

| | default | what it controls |
| --- | --- | --- |
| `poll_interval` | 10s | how often each instance asks for due work. Sets start latency: a job due at 12:00:00 may begin at 12:00:07 |
| `heartbeat_interval` | **10s** | how often a running execution stamps `last_heartbeat` |
| `shutdown_max_wait` | 30 min | how long `stop()` lets in-flight executions finish |

The `heartbeat_interval` default is **this framework's own**; db-scheduler's would be five
minutes. It matters beyond the heartbeat, because an execution is declared dead after
`heartbeat_interval x missed_heartbeats_limit`, and that limit defaults to 6:

```
this framework   10s   x 6 = 60 seconds    before another instance revives the job
db-scheduler     5 min x 6 = 30 minutes
```

The short window is on purpose. In-process event handlers run on two jobs of their own (fan-out
and dispatch), so the scheduler carries all in-process event delivery. And a recurring job is a
**single row**, so a crashed instance freezes that job until the row is revived. Half an hour of
stalled event delivery is worse than an occasional early revival.

Lengthen it if your jobs run long and a duplicate run costs more than a delay. The miss count has
no builder method of its own. Set it through `customize_scheduler`, which hands you db-scheduler's
own builder:

```python
jobs = (
    job_registry()
    .heartbeat_interval(timedelta(seconds=30))
    .customize_scheduler(lambda b: b.missed_heartbeats_limit(8))  # 30s x 8 = 4 min; minimum 4
    .build()
)
```

Because the heartbeat is written by the housekeeping thread, not by the thread running your job,
a long `execute()` is not at risk by itself. What puts it at risk is that thread stalling: a pool
with no free connection, or a database that will not take the write.

**`stop()` waits up to `shutdown_max_wait`, once, then returns.** Python cannot interrupt a
thread, so nothing can end a job stuck in a `sleep` or a blocking call. `stop()` logs that it
cannot interrupt, names the executions still running, and returns. Such an execution runs on to
its end on a daemon thread, or is cut off when the process exits. If it is cut off, its row stays
claimed with a frozen heartbeat, and is recovered later the same way as after a crash.

A job that must stop cleanly checks `context.scheduler_state.is_shutting_down`. In tests, the
30-minute default is usually worth lowering.

### Database dialect

**There is nothing to set.** The scheduler's poll query differs by database: MySQL and MariaDB
cannot parse the generic form, `OFFSET 0 ROWS FETCH FIRST n ROWS ONLY`. The scheduler picks the
right one from the data source's URL, without connecting. So a scheduler started before its
database is reachable still sends the right query once the database is up.

## Letting running jobs finish when the process stops: on by default

A process that stops while a job is running should let the job finish first, rather than cut it
off. `register_shutdown_hook` does that, and it is on by default.

A process can end in two ways, and Python has a separate mechanism for each. The registry uses
both, from `start()`:

- **The program ends by itself**: it returns, raises, or gets Ctrl-C. The registry is on
  `atexit`, which adds to whatever else is registered and replaces nothing. `stop()` takes it
  off.
- **It gets SIGTERM**, from `docker stop` or a Kubernetes rollout. `atexit` does not run then:
  CPython kills the process on the spot. So if nothing else in the process handles SIGTERM, the
  registry does, and says so in one line:

```text
INFO io.ekbatan.distributedjobs.JobRegistry Nothing else in this process handles SIGTERM, so the
JobRegistry does: on SIGTERM, running jobs finish, then the process stops. A SIGTERM handler set
after this replaces it; register_shutdown_hook(False) turns it off.
```

On SIGTERM it stops every registry that asked for this, and their running jobs finish, each
within `shutdown_max_wait`. Then the process dies of the signal, as it would have anyway:
`finally` blocks and `atexit` do not run. A second SIGTERM stops it at once.

Measured, with a job two seconds into its body when the process was stopped:

| how the process stops | default | `.register_shutdown_hook(False)` |
| --- | --- | --- |
| it returns, or raises | **the job finishes** | cut off |
| Ctrl-C | **the job finishes**, still exits 130 | cut off |
| SIGTERM - a container stop | **the job finishes**, still exits 143 | cut off, exits 143 |
| SIGTERM, with your own handler set before `start()` | your handler decides | your handler decides |

**It leaves SIGTERM to whoever already has it.** CPython has **one handler per signal**, so
taking it would silently drop theirs. uvicorn and gunicorn set their handler before your
application starts, with one worker or several, and then they own shutdown: the end of the
FastAPI lifespan calls `jobs.stop()`, as the REST examples do. The same goes for an asyncio
`loop.add_signal_handler`, a SIGTERM ignored on purpose, and a handler of your own set before
`start()`. A handler set after `start()` replaces the registry's.

**It needs the main thread** to take SIGTERM, because CPython lets no other thread set a signal
handler. A registry started elsewhere, such as in a lifespan run in a thread pool, is on
`atexit` alone.

**It tells you when nothing catches SIGTERM.** If nothing handles SIGTERM once `start()` is done,
because the hook is off or could not take the signal off the main thread, `start()` logs one
warning, once per process:

```text
WARNING io.ekbatan.distributedjobs.JobRegistry Nothing in this process handles SIGTERM, and the
JobRegistry cannot: it was started off the main thread, and only the main thread can set a
signal handler. Jobs still running when the process gets SIGTERM (a container stop or a deploy)
are cut off, and another instance runs them again after the dead-execution window (60 seconds).
To let them finish first, start the JobRegistry on the main thread, or call its stop() from a
SIGTERM handler of your own.
```

With the hook off, the warning says that instead, and tells you how to turn the hook back on. The
check works from any thread. A `build_manual()` registry, the one for tests, never warns and
never takes SIGTERM.

**Turned off**, the registry is neither on `atexit` nor on SIGTERM, and you stop it yourself:

```python
jobs = (
    job_registry()
    .data_source(jobs_data_source)
    .with_jobs([...])
    .register_shutdown_hook(False)   # you call jobs.stop() yourself
    .build()
)
```

A job still running when the process ends is then cut off. Its row waits out the dead-execution
window (60 seconds on these defaults) before another instance runs it again. Nothing is lost,
since every job body already has to cope with running twice, but the stop is abrupt.

**If you write a SIGTERM handler yourself, keep what you find:**

```python
previous = signal.getsignal(signal.SIGTERM)

def wind_up(number, frame):
    jobs.stop()
    if callable(previous):
        previous(number, frame)

signal.signal(signal.SIGTERM, wind_up)
```

Dropping `previous` is the easy mistake, and harmless only in a `__main__` where nothing had
installed anything. Inside a gunicorn or uvicorn worker it silently discards the server's own.

## Build it in the process that runs the jobs

A `JobRegistry` owns threads and a database pool, and neither survives `fork()`. Under gunicorn
`--preload`, uwsgi, Celery's prefork pool or RQ, a registry built at import time is built in one
process and started in a copy of it. `start()` then raises `ForkedProcessError`, and
db-scheduler's own worker pool refuses work in a forked copy too.

Build and start it in the FastAPI lifespan, or at the top of the worker's `main()`.
[Startup and shutdown](../startup-and-shutdown.md) has the recipe for each server.

From `async def` code, such as a lifespan, start and stop it with `await asyncio.to_thread(...)`.
Starting registers the jobs in the database and stopping waits for the running jobs, and either
would hold up the event loop meanwhile.

## The `scheduled_tasks` table

One row per registered job. The row *is* the coordination, not a log of it: it carries the next
execution time, which instance currently holds the job, and its heartbeat. Whichever instance
claims the row runs the job; the rest do not.

The table is db-scheduler's, unchanged. [`scheduled_tasks`](../database/tables/scheduled-tasks.md)
lists every column and the DDL for each database. `scheduled_tasks_table(metadata)` builds it as
a SQLAlchemy table:

```python
from ekbatan.schema import scheduled_tasks_table
```

Its times are `TIMESTAMP` with no time zone, as in every framework table. If yours are
`timestamptz`, see
[the table's page](../database/tables/scheduled-tasks.md#if-the-time-columns-are-timestamptz).

## See also

- [`scheduled_tasks`](../database/tables/scheduled-tasks.md) - the table, and the heartbeat column
- [In-process event handlers](../events/local-event-handler.md) - two jobs on this registry
- [Concurrency](../runtime/concurrency.md) - what `workers(n)` is actually sizing
- [`job-worker-uv-pg`](../../ekbatan-py-examples/job-worker-uv-pg) - no HTTP at all; its async twin
  [`job-worker-uv-async-pg`](../../ekbatan-py-examples/job-worker-uv-async-pg) runs the jobs' work on a loop
