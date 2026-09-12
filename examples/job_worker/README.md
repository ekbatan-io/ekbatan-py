# job_worker

A worker process: jobs, no HTTP. The Python counterpart to Java's
`spring-boot-job-worker-gradle-pg`.

```
uv run python -m examples.job_worker     # SQLite, ctrl-c to stop
uv run pytest examples/job_worker
```

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

**Shutdown is yours to arrange.** This port installs no signal handler and no `atexit` hook, so
a SIGTERM ends the process with executions still picked and their rows waiting out the
dead-execution window — 60 seconds on framework defaults. `__main__.py` catches the signal and
calls `stop()`, which is what makes a rolling deploy clean rather than merely survivable.

That is six lines the application owns, and deliberately not something the framework does for
you: there is one signal handler per signal per process, and a library that installed one would
overwrite whatever gunicorn, uvicorn or the application had already put there. Java's
`JobRegistry` registers a JVM shutdown hook by default and has no such problem — that divergence
is written up in `DIVERGENCE.md`.

## The pools

The scheduler gets **its own engine**, sized above the worker count. db-scheduler writes
heartbeats from a housekeeping thread; a job body holding the last connection starves it, and an
execution whose heartbeat stops is declared dead and run again somewhere else. The framework
warns at startup when the two are mismatched — listen to it.

`workers(n)` sets how many threads run job bodies. Java needs no such setting: it hands
db-scheduler an unbounded virtual-thread executor. Here they are real OS threads, so the number
is chosen rather than assumed.

## Seeing the cluster behaviour

SQLite will not show you the interesting part. Point two workers at one Postgres and watch each
slot run once:

```
WORKER_DATABASE_URL=postgresql+psycopg://ekbatan:ekbatan@localhost:5432/wallet \
  uv run python -m examples.job_worker
```
