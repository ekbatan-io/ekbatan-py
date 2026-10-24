# job-worker-poetry-async-pg

A worker process - distributed jobs, no HTTP - on **PostgreSQL**, with the jobs' work on `ekbatan.aio`. Two shards, `global`
and `mexico`, each on a server of its own.

It is the Python counterpart of the Java job-worker example: the same model, actions, event
handler, jobs and test cases, under the same names, built three ways - uv, pip and Poetry.

<!-- build: written by scripts/sync-examples.py -->
```
make            # generate what is out of date, then test the built wheel, installed
make run        # start the databases, run it, stop the databases
```

`make run` needs only Docker: it starts the two databases `compose.yaml` describes, one server per shard - the image the tests use, with the login and database `ekbatan.yaml` defaults to - waits until it is ready, runs the app, and stops the databases when the app stops. `docker compose down` throws the databases away.

Built with **Poetry**. The same project with [uv](../job-worker-uv-async-pg) and [pip and setuptools](../job-worker-pip-async-pg) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.

Needs `poetry` 2 on the path.
<!-- /build -->

## What it shows

- **`WalletStipendJob`** - every two seconds, each open wallet below 100.00 gets 10.00, through
  `WalletDepositMoneyAction` under the wallet's keyed lock. A failed deposit is logged and
  skipped, so one bad row does not halt the pass.
- **`WalletReportJob`** - every five seconds, counts the wallets and sums their balances, and logs
  it. No action, no writes.
- **The listen-to-yourself chain, job-driven.** Each stipend deposit's event reaches
  `WalletMoneyDepositedEventHandler`, which starts `CreateNotificationAction`: a row in
  `notifications`, the same chain a request drives in the REST examples.
- **Once across the cluster.** Every worker process running this shares one `scheduled_tasks`
  row per job, so each job runs once per slot however many workers there are. The fan-out and
  handling jobs are claimed the same way.

## Jobs on threads, their work on a loop

Distributed jobs are sync: db-scheduler claims a job's row and calls `execute` on one of its
threads. Each job here hands its work to the worker loop - a `HandlerLoop`, one event loop on a
thread of its own - with `loop.run(...)`, so the work awaits an `AsyncActionExecutor`, async
repositories and an async keyed lock. The event handler runs on the same loop. Everything that
awaits was built for that loop and is closed on it, last.

## SIGTERM

A started registry drains on any normal exit and on Ctrl-C - it registers with `atexit`. CPython
kills the process on SIGTERM without running `atexit`, so `__main__.run()` catches the signal and
calls `close()`: a container stop then lets the jobs in flight finish.

## How the schema gets there

`schema.py` is generated, not written: `make` runs `python -m ekbatan.schemagen`, which starts a
PostgreSQL container, applies the migrations in `migrations/` to it, and reflects the result - so the
Python and the database cannot drift. The module records a fingerprint of the migrations it came
from and checks it on import, so a stale one refuses to load rather than quietly disagreeing with
the database.

One forced type in `pyproject.toml`: every `CHAR(3)` column is a currency code, whatever it is
called, and reads as the app's own `Currency` - the wallet's `java.util.Currency`, which Python
does not have. `CurrencyConverter`, in `converters.py`, turns the text into a `Currency` and back,
so the repository never sees the text. Action parameters and events keep the plain code,
`"currency": "EUR"`, because they are written to the outbox as JSON for other services to read.

## Its test

```
make
```

Against two PostgreSQL servers in Testcontainers, one per shard, it asks what the Java example's
test asks: the stipend job tops up the under-funded wallet on each shard and its deposits leave
notifications, and the driver settings in `ekbatan.yaml` reach the database. Two checks are its
own: no object of the worker shows the database password, and the migration files pass the checks
a deploy runs.
