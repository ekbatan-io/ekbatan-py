# job-worker-pip-pg

A worker process: jobs, no HTTP. The Python counterpart to Java's
`spring-boot-job-worker-gradle-pg`. Its async twin, [`job-worker-uv-async-pg`](../job-worker-uv-async-pg),
keeps the jobs on threads and runs their work on a loop, awaited.

<!-- build: written by scripts/sync-examples.py -->
```
make            # generate what is out of date, then test the built wheel, installed
make run        # run it; the database comes first, above
```

Built with **pip and setuptools**. The same project with [uv](../job-worker-uv-pg) and [Poetry](../job-worker-poetry-pg) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.

The venv is made with `python3`, which must be 3.13 or later - `make PYTHON=python3.13` if yours is older.
<!-- /build -->

The settings are in `ekbatan.yaml`, in the same keys as Java's `application.yml`: the layout
with the scheduler's own `jobsConfig` pool, the namespace, `jobs.pollingInterval: PT10S` and
`local-event-handler.handling.enabled`. Durations are ISO-8601, as Java's `Duration.parse` reads
them. Each pool puts its sessions on UTC with `options: -c TimeZone=UTC`, whatever the server's
default.

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
the signal and calls `stop()`, which is what makes a rolling deploy clean rather than merely
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
converter = "job_worker_pg.converters.CurrencyConverter"
include-types = "CHAR\\(3\\)"

# One column, picked by its name.
[[tool.ekbatan.schemagen.forced-types]]
converter = "job_worker_pg.converters.EmailAddressConverter"
include-expression = "alerts\\.recipient"
```

and schemagen writes these two lines into `schema.py`:

```python
Column("currency", converted(CHAR(length=3), CurrencyConverter()), nullable=False)
Column("recipient", converted(VARCHAR(length=255), EmailAddressConverter()), nullable=False)
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

`workers(n)` sets how many threads run job bodies. Java needs no such setting: it hands
db-scheduler an unbounded virtual-thread executor. Here they are real OS threads, so the number
is chosen rather than assumed.

## Seeing the cluster behaviour

Start two of these against the same database and watch each slot run once rather than twice:

```
WORKER_DATABASE_URL=postgresql+psycopg://localhost:5432/wallet \
WORKER_DATABASE_USERNAME=ekbatan WORKER_DATABASE_PASSWORD=ekbatan \
  make run
```
