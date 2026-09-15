# Where this port is behind the Java one

Every way `ekbatan-py` is *worse* than `ekbatan`, in one place.

`DIVERGENCE.md` is the complete list of differences and is mostly neutral - the two ports do the
same thing in different idioms, and saying which is better would be meaningless. This file is
the biased subset: things a user would miss, or trip over, coming from Java. Nothing here is a
difference of taste.

Each row links to where the detail lives. Nothing is recorded only here.

---

## Missing outright

| | What Java has | Status |
| --- | --- | --- |
| **DI integration** | `ekbatan-di`: Spring, Quarkus and Micronaut modules with `@EkbatanAction` / `@EkbatanRepository` / `@EkbatanDistributedJob` auto-discovery, classpath scanning, and compile-time registration | **Not ported, no plan.** A Python application wires its actions and repositories by hand in one file. That is idiomatic here and the examples show it reading well - but it is real work Java does for you, and it is the largest single gap |
| **Native image** | GraalVM configuration, reachability metadata, native tests across the whole example grid | **No counterpart.** CPython has nothing equivalent |
| **Kafka Connect transform** | `debezium-smt`, which routes outbox rows to topics | **Deliberate.** It runs inside Kafka Connect, in a JVM, whichever language wrote the row. A Python service points the same connector at its database. What this port owes is the schema, which is `ekbatan.schema` and is checked by the conformance scenarios |
| **Graceful shutdown on a signal** | `JobRegistry.Builder.registerShutdownHook`, **on by default** | **Open, undecided.** A SIGTERM here kills the process mid-execution; the rows wait out the dead-execution window, 60s on framework defaults. Not a straight port: `atexit` does not fire on SIGTERM, so matching Java means a process-wide signal handler, which is a much bigger imposition in Python. See `DECISIONS-TO-REVIEW.md` |
| **Connection-pool leak detection** | `leakDetectionThreshold` - Hikari logs the borrower's stack when a connection is held too long | **Open, undecided, investigated in depth.** A naive port is worse than nothing: a handler raising on `checkin` *strands* the connection, so the detector would cause the leak it looks for. Ten findings and a narrower design are written up in `DECISIONS-TO-REVIEW.md` |
| **Partial indexes** | `WHERE delivered = FALSE` on the outbox and `WHERE state IN ('PENDING','FAILED')` on the notifications, on PostgreSQL | **Not emitted.** The table factory builds one index with no dialect variation, so PostgreSQL gets the portable composite form. Measured on 500,000 rows with a 1% backlog: **3,528 kB against Java's 56 kB**, and every insert pays for it. Workaround is a hand-written index in your own migration; the fix is dialect-aware index construction in `ekbatan.schema`. See [`database/tables/events.md`](database/tables/events.md) |
| **Pool idle tuning** | `idleTimeout`, `minimumIdle` | **No direct counterpart.** `pool_use_lifo=True` plus `pool_recycle` reaches the same outcome - measured, LIFO touches 1 connection where FIFO touches 4 - but there is no floor like `minimumIdle`, and retirement is by age rather than by idleness |

---

## Slower or costlier, but correct

| | Java | Here |
| --- | --- | --- |
| **Fan-out across shards** | one virtual thread per shard, in parallel | sequential. An eight-shard round takes eight batches' time. The fix is a pool around `_drain_shard`, not a redesign |
| **Event dispatch** | handlers invoked in parallel, outcomes committed in ~4 batched `UPDATE`s | one notification at a time; a hundred rows is a hundred `UPDATE`s |
| **Keyed-lock watchdog** | `Thread.ofVirtual()` per hold - effectively free | `threading.Timer`, a real OS thread. A thousand held locks is a thousand threads |
| **Job workers** | unbounded virtual threads | a fixed `DaemonThreadPool`. `JobRegistryBuilder.workers(n)` sizes it - a knob Java needs no equivalent of |
| **Blocking calls** | free, on virtual threads | **not free under asyncio.** Measured: a 0.5s call from an `async def` handler let a heartbeat tick 5 times where a free loop manages ~55. Safe under threaded servers and under FastAPI's plain `def` endpoints; an `async def` endpoint needs `asyncio.to_thread` |

All five are in `DIVERGENCE.md` under *"There are no virtual threads"* and *"The framework is
synchronous"*.

---

## Smaller surface

| | Java | Here |
| --- | --- | --- |
| **Migration targeting** | `FlywayMigrator.migrate(Target...)` | `migrate(config, path, shards=[...])` - added in `EKB-0137`, so this one is closed |
| **db-scheduler builder** | `registerShutdownHook`, `dueExecutor`, `housekeeperExecutor`, `commitWhenAutocommitDisabled` | none of the four. Only the first matters, and it is the shutdown row above. `db-scheduler/docs/FILE-LEDGER.md` marks `SchedulerBuilder` **partial** for it |
| **Assertion DSL** | `ActionSpec` and `ActionAssert`, 24 fluent methods with 38 tests of their own | `run_action` returns data and you use `assert`. Deliberate - pytest already prints both sides - but it is less to reach for |
| **Examples** | 41 projects, including a full native-image grid and one per DI framework | 7. The database axis is mirrored; the DI and native axes have nothing to mirror |

---

## Everything outside the code

**The documentation is written.** Thirty-four pages, indexed at [docs/README.md](README.md):
every Java page that has a counterpart here now has one, plus five this port needed and Java does
not - concurrency, SQLite, migrations, FastAPI wiring, and schemagen.

What is left is **the website** - Java has 76 files of Astro at `ekbatan-io.github.io/ekbatan`
and this port has nothing - and two process directories, `openspec/` and `agent-skills/`. That
surface is enumerated and counted in [`BEYOND-CODE-LEDGER.md`](BEYOND-CODE-LEDGER.md), which
stands at 86%.

The repository's own essentials are no longer part of it. LICENSE, AGENTS.md, CLAUDE.md,
RELEASE.md and the publishing workflow were written in the first pass over that ledger, and two
real defects fell out of building the artifacts to check them:

- **Neither distribution shipped its licence text** while both declared `license = "Apache-2.0"`.
  Fixed in both; verified by reading `dist-info/licenses/` out of the built wheels.
- **`ekbatan` depended on `ekbatan-py-db-scheduler` with no version bound at all**, so
  `pip install ekbatan` would have taken any future release of a distribution this repository
  cuts itself. Now pinned. The pin has a pre-release trap of its own, which `RELEASE.md`
  records: `~=0.1.0` does not admit `0.1.0.dev0`, and `[tool.uv.sources]` hides that from every
  test in this repository.

## Not a gap in the code, but worth knowing

**The port has never run in production.** The ledger says 246/247 settled and the suite passes on
SQLite, PostgreSQL, MySQL and MariaDB - but "settled" means *read whole and reconciled against
the Java*, not *proven under load*. Every session that properly exercised a subsystem found
roughly one real defect in it.

**CI has never executed.** The workflow is written and correct, but this repository has no git
remote, so nothing has ever run it. That is how an example test stayed red for several commits
without anyone noticing.

**35 files in `db-scheduler/` fail `ruff format --check`.** Pre-existing, cosmetic, and would
fail the lint job the first time CI runs.

---

## What is *not* on this list, and why

Differences that are neutral or in this port's favour live in `DIVERGENCE.md`: mapping by field
name instead of generated records, one `Repository` instead of an abstract base, `with` blocks
instead of four callback overloads, SQLite as a supported dialect, a write without `RETURNING`
rebuilding its rows, `ContextVar` instead of `ScopedValue`. Several are places where this port
does more than Java, and those are in `DECISIONS-TO-REVIEW.md` under *"Went further than Java"*
precisely so they get argued about rather than assumed.
