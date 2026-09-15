# Jobs

Periodic background work that should run on **exactly one** instance across a cluster - a daily
report, an hourly cleanup, a periodic reconciliation. A thin facade over
[db-scheduler](https://github.com/kagkarlsson/db-scheduler).

- **[Distributed jobs](distributed-jobs.md)** - `DistributedJob`, the registry, the
  `scheduled_tasks` table, the dedicated pool, `workers(n)`, and how the event pipeline's two
  jobs plug into the same registry

← [docs index](../README.md)
