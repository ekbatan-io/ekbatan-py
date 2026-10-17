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
| **Graceful shutdown on a signal** | `JobRegistry.Builder.registerShutdownHook`, **on by default** | **Half on by default, as of `EKB-0221`.** The additive half is `atexit`, and a started registry is on it by default - it drains on any normal exit and on Ctrl-C. SIGTERM does not run `atexit` in CPython, so the SIGTERM half is `register_shutdown_hook()`, still opt-in because CPython has one handler slot per signal. Under a server that is covered by the lifespan; a standalone worker without the hook still loses its in-flight executions to the dead-execution window, 60s on framework defaults |
| **Connection-pool leak detection** | `leakDetectionThreshold` - Hikari logs the borrower's stack when a connection is held too long | **Open, undecided, investigated in depth.** A naive port is worse than nothing: a handler raising on `checkin` *strands* the connection, so the detector would cause the leak it looks for. Ten findings and a narrower design are written up in `DECISIONS-TO-REVIEW.md` |
| ~~**Partial indexes**~~ | `WHERE delivered = FALSE` on the outbox and `WHERE state IN ('PENDING','FAILED')` on the notifications, on PostgreSQL | **Closed in `EKB-0215`, by removing something rather than adding it.** `ekbatan.schema` declared three indexes, and one declaration cannot be partial on PostgreSQL and composite on MySQL - so it was composite everywhere, 3,528 kB against 56 kB on 500,000 rows with a 1% backlog. Java's framework declares no DDL at all: its repositories reach for tables through `DSL.table(DSL.name(...))` and the indexes live in `V0001__eventlog.sql`, which the application owns. Ours now match - no indexes in the framework, and every PostgreSQL migration here emits the partial form |
| **Pool idle tuning** | `idleTimeout`, `minimumIdle` | **No direct counterpart.** `pool_use_lifo=True` plus `pool_recycle` reaches the same outcome - measured, LIFO touches 1 connection where FIFO touches 4 - and the pool fills to `pool_size` as Hikari's default `minimumIdle` does (`EKB-0305`), but there is no setting for a lower floor, and retirement is by age rather than by idleness |

---

## Slower or costlier, but correct

| | Java | Here |
| --- | --- | --- |
| ~~**Fan-out across shards**~~ | one virtual thread per shard, in parallel | **Closed in `EKB-0203`.** Both event jobs drain their shards through a thread pool, one thread per shard, with no setting - Java has none either. Measured at one 40 ms round trip per shard: eight shards 354 ms -> 48 ms, thirty-two 1405 ms -> 47 ms. A single shard still runs inline |
| ~~**Event dispatch**~~ | handlers invoked in parallel, outcomes committed in ~4 batched `UPDATE`s | **Closed in `EKB-0204` and `EKB-0205`.** Handlers run in parallel, one thread per notification per shard, bounded by the batch size as Java's is - measured at 20 ms per handler, a batch of 100 goes from 2376 ms to 28 ms. Outcomes are one `UPDATE` per bucket: a batch of 100 that all succeed is 1 statement, a realistic mix is 4, which is Java's number |
| ~~**Keyed-lock watchdog**~~ | `Thread.ofVirtual()` per hold - effectively free | **Closed in `EKB-0206`.** One watchdog thread holding a heap of deadlines, waiting until the earliest is due. A thousand held locks: 1 thread instead of 1000, 6 ms late instead of 48, registered in 2 ms instead of 70 |
| **Job workers** | unbounded virtual threads | a fixed `DaemonThreadPool`. `JobRegistryBuilder.workers(n)` sizes it - a knob Java needs no equivalent of |
| **Blocking calls** | free, on virtual threads | **free under gevent, not free under asyncio.** Under `gevent.monkey.patch_all()` every blocking call yields - the suite passes unchanged, 631 against 631 - which is what virtual threads are, and the framework needs no code for it. Under **asyncio** it is not free: measured, a 0.5s call from an `async def` handler let a heartbeat tick 5 times where a free loop manages ~55. Safe under threaded servers and under FastAPI's plain `def` endpoints; an `async def` endpoint needs `asyncio.to_thread`. gevent serves WSGI, so FastAPI cannot use it |

All five are in `DIVERGENCE.md` under *"There are no virtual threads"* and *"The framework is
synchronous"*.

---

## Smaller surface

| | Java | Here |
| --- | --- | --- |
| **Migration targeting** | `FlywayMigrator.migrate(Target...)` | `migrate(config, path, shards=[...])` - added in `EKB-0137`, so this one is closed |
| **db-scheduler builder** | `registerShutdownHook`, `dueExecutor`, `housekeeperExecutor`, `commitWhenAutocommitDisabled` | none of the four on the vendored builder. The one that mattered is answered a level up, on `JobRegistryBuilder` - see the shutdown row above. `db-scheduler/docs/FILE-LEDGER.md` marks `SchedulerBuilder` **partial** for it |
| **Examples** | 41 projects, including a full native-image grid and one per DI framework | 7. The database axis is mirrored; the DI and native axes have nothing to mirror |

---

## Everything outside the code

**Nothing outstanding.** [`BEYOND-CODE-LEDGER.md`](BEYOND-CODE-LEDGER.md) stands at **68/68**:
the licence, the agents guide, the release procedure and its workflow, thirty-four documentation
pages, the website, the openspec workflow and the agent skills.

Two things about that number are worth saying plainly. It counts **written**, not **exercised** -
the release workflow has never cut a release and the website has never been deployed. And five of
those pages have no Java counterpart at all, because this port needed them and Java did not:
concurrency, SQLite, migrations, FastAPI wiring, and schemagen.

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
