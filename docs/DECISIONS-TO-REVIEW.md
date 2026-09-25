# Decisions taken during the parity port

`DIVERGENCE.md` says where this port differs from the Java one and why. It does not say **who
decided**, or **when**, or **how sure I am** - so a judgement call I made last week reads exactly
like a design decision taken deliberately months ago. The `ordered_uuid` episode is the proof: it
was written up in `DIVERGENCE.md`, argued convincingly, and wrong. It was caught by being asked
about, not by being written down.

This file is the other list: every change to Python **behaviour** made during the port, sorted by
how much scrutiny it deserves rather than by subject. Renames and test-only changes are at the
bottom.

## Every divergence somebody chose

`DIVERGENCE.md` now labels all 37 of its sections
**forced**, **chosen** or **parity**, and `scripts/divergence.py` refuses to pass if one is
unlabelled or if a **chosen** one does not link here. That check exists because it was needed:
`readonly_db` meaning "a read" rather than "the replica" sat in `DIVERGENCE.md` for weeks
reading like a fact about the language, and was found by being asked about.

These are the **6 that somebody decided**. Each could have gone the other way. Five more are
struck out and kept struck rather than deleted - four reversed after being asked about, and one
(`EKB-0199`) that described behaviour this port never had.

| divergence | why it is a decision, not a consequence |
| --- | --- |
| ~~[An action runs by type or by instance](../DIVERGENCE.md)~~ | **Closed in `EKB-0224`.** The executor takes a type only, as Java's takes a class. The instance form was measured running an unregistered action, and one built with a different clock, past the clock check; both are refused now, and tests use `ActionSpec.of(action)` as Java's do. |
| ~~[Events carry no identity](../DIVERGENCE.md)~~ | **Closed in `EKB-0218`.** The class line names the model (`ModelEvent["Wallet"]`, Java's `Wallet.class`) and the constructor takes its id, as Java's does. The persister writes both as the event carries them instead of filling them in from the model it was attached to. |
| [A typed builder is required in both ports, for different reasons](../DIVERGENCE.md) | Both require a builder a checker can read - Java at compile time, here at the `copy()`/`builder()` call (`EKB-0196`). Accepting a **hand-written** one is the addition; Java has only the generated one. |
| ~~[Identifiers get a named type, not a generic parameter](../DIVERGENCE.md)~~ | **Closed in `EKB-0223`.** Ids are spelled `ShardedId[Wallet]`, as Java's `ShardedId<Wallet>`, with no class per model; a checker keeps two models' ids apart and at runtime they compare by uuid, as Java's do. `ShardedId` no longer subclasses `Id`, matching Java, where an `Id` and a `ShardedId` of one uuid are never equal. |
| ~~[`AbstractRepository` is just `Repository`, and is usable as it stands](../DIVERGENCE.md)~~ | **Closed in `EKB-0226`.** Java's four names and layering: `Repository` is the narrow promise (a `Protocol`) that the registry, executor and persister depend on, and `AbstractRepository` is the abstract implementation. Its stated reason - that the class worked without a mapping - had stopped being true in `EKB-0217`. |
| ~~[Row mapping is by field name unless you say otherwise](../DIVERGENCE.md)~~ | **Closed in `EKB-0217`.** Java declares `fromRecord`/`toRecord` abstract and every repository writes both; `to_row`/`from_row` now refuse until written, here too. The default was the one piece of magic left in the read and write paths - rename a column without renaming the field and it silently stopped matching. Where names line up it is two lines; the bridge is still written once in the framework rather than once per repository. |
| ~~[An unregistered shard is refused, not defaulted](../DIVERGENCE.md)~~ | **Struck in `EKB-0199`: it was never true.** Both ports fall back to the default shard, line for line. The refusal quoted was `DatabaseRegistry.manager()`, which routing never reaches - every path resolves through `effective_shard` first. No decision was ever taken here, because there was nothing to decide. |
| [The connection they hand back has a `rows()` on it](../DIVERGENCE.md) | jOOQ ends a query with `fetch(this::fromRecord)` and SQLAlchemy has no such verb. Adding `Db.rows(...).fetch(mapper)` is new surface; a comprehension after `execute` would have done. |
| ~~[Timestamps are stamped when the change is staged](../DIVERGENCE.md)~~ | **Closed in `EKB-0192` and `EKB-0194`.** `created_date` is required at construction, as Java's `Validate.notNull(builder.createdDate)` requires it, and nothing writes `updated_date` but the application. |
| ~~[Retries match an exception the way `except` does](../DIVERGENCE.md)~~ | **Closed in `EKB-0198`.** The case it rested on - "`on=OperationalError` matches every driver subclass" - was checked and is not real: SQLAlchemy raises that exact class for every dialect and it has no subclasses. Matching is now exact and the walk is `__cause__` only, as Java's are. |
| [Equality compares the whole row](../DIVERGENCE.md) | **Behavioural and easy to trip over**: two wallets with one id and version but different balances are equal in Java and not here. |
| ~~[A shard member carries one datasource pair, not a named map](../DIVERGENCE.md)~~ | **Closed in `EKB-0222`.** The layout is Java's three levels - groups, members, and a `configs` map on each member keyed `"primaryConfig"` / `"secondaryConfig"` / anything else - with `config_for(name)`. The examples declare their jobs and lock pools as `"jobsConfig"` and `"lockConfig"`, as Java's `application.yml` does. |
| [SQLite is a dialect here and is not one in Java](../DIVERGENCE.md) | An addition. Java refuses everything but the three. |
| ~~[`plan.add` hands back the stamped row, not the argument](../DIVERGENCE.md)~~ | **Closed in `EKB-0192`.** `created_date` is required at construction now, so there is nothing to stamp and `add` hands back the object it was given - `isSameAs`, as Java does. |
| ~~[The test-support API is data, not an assertion DSL](../DIVERGENCE.md)~~ | **Closed in `EKB-0219`.** `ActionSpec`/`ActionAssert` are ported method for method with Java's messages, and Java's 38 `ActionSpecTest` cases with them. A verifier's falsy return fails, because a Python lambda returns its verdict where Java's `Consumer` asserts. |
| [The shutdown hook drains on exit by default, and catches SIGTERM only when asked](../DIVERGENCE.md) | **Narrowed in `EKB-0221`.** The additive half - `atexit`, Python's version of Java's hook list - is now **on by default**, as OpenTelemetry's `shutdown_on_exit` is: a started registry drains on a normal exit and on Ctrl-C. What remains a decision is the SIGTERM half, `register_shutdown_hook()`, still opt-in because CPython has one handler slot per signal and does not run `atexit` on SIGTERM. Every library read (OpenTelemetry, Sentry, APScheduler; gunicorn, uvicorn, Celery, RQ, Dramatiq, Huey) leaves signals to whatever owns the process. The event-loop half was closed in `EKB-0214`. |
| [Additions with no Java counterpart](../DIVERGENCE.md) | Additions by definition. Each one is something this port has and Java does not. |

The other 12 are **forced** - CPython has no `ScopedValue`, no virtual threads, no Flyway, no
Redisson - and 12 are **parity**, recorded because the mechanism differs while the behaviour
does not.

## Open - not decided, needs a call

These also appear in [`SHORTCOMINGS.md`](SHORTCOMINGS.md),
which lists every way this port is behind the Java one in one place.

Nothing has been done about these. They are here so they are not lost between "noticed" and
"fixed".

### No leak detection, and no idle timeout, on the connection pools

**Investigated in depth before deciding. Every claim below was verified by running.**

Java's `DataSourceConfig` carries `leakDetectionThreshold`, `idleTimeout` and `minimumIdle`;
SQLAlchemy's pool has no equivalent of any of the three.

#### The easy half: `idleTimeout` and `minimumIdle`

SQLAlchemy's idiom is `pool_use_lifo=True` alongside `pool_recycle`. LIFO reuses the hot
connection so the cold tail sits untouched and ages out. Measured over eight sequential borrows
against a pool of four: FIFO touches **all four** connections, LIFO touches **one**. Not
Hikari's semantics - there is no floor like `minimumIdle`, and retirement is by age rather than
by idleness - but it is the outcome people want the knob for, and it is one config line.

#### The hard half: `leakDetectionThreshold`

A naive port is *worse than nothing*, and the investigation found why. Ten findings:

| # | finding | consequence |
| --- | --- | --- |
| 1 | A handler raising in `checkin` **strands the connection** - verified, pool left with 1 checked out | a bug in the leak detector *causes* the exact leak it exists to find. The worst possible failure |
| 2 | A handler raising in `checkout` propagates | any bug breaks every checkout in the process |
| 3 | `id(ConnectionRecord)` is **reused** after `dispose()` - observed directly | keying the map by `id()` matches the wrong entry. Use `record.info` or a weakref |
| 4 | `detach` fires **no** `checkin` | the map grows forever and warns about a connection that legitimately left the pool |
| 5 | The keyed lock holds `engine.connect()` for the whole lease, up to `max_hold` (typically 30s) | Hikari's 2s minimum threshold would flood the lock pool with false positives. Detection has to be per-engine |
| 6 | In CPython a *dropped* connection returns on garbage collection - immediately by refcount, or at the next cyclic collection | the "forgot to close" leak Hikari catches is largely self-healing here. Only a **retained** reference is a true leak |
| 7 | Handlers are reentrant, and a handler touching its own pool recurses | a detector must never touch the pool from inside a handler |
| 8 | The pool holds no lock across the event - another thread made progress during a handler | our own lock cannot deadlock against the pool's. The one piece of good news |
| 9 | The map must be guarded; 3200 concurrent borrows across 8 threads | a sweep iterating an unguarded dict races with checkout |
| 10 | A forked child inherits the map but not the watchdog thread | stale entries, reported by nobody |

#### Cost, measured

| | per checkout |
| --- | --- |
| sweep at `pool_size=10` / `50` / `200` | 0.44 / 1.39 / 4.89 us |
| stack capture (the dominant cost) | 5.9 - 7.7 us |
| for scale: `select 1` on in-memory SQLite | 9.3 us |
| for scale: Postgres over a socket | 150 - 500 us |

#### What I would build, and what I would not

**Not** Hikari's design. A periodic sweeper on the hot path of every checkout, whose failure
mode is stranding connections, is a bad trade for a language whose garbage collector already
returns dropped connections.

**Instead**, the narrow version that keeps all the value:

- Record the borrower's stack on checkout **only when enabled** - zero cost otherwise.
- Report at the one moment it is certainly not a false alarm: **pool exhaustion**. Wrap
  SQLAlchemy's `TimeoutError` where this port opens connections and attach "these N connections
  are checked out, and here is where each was borrowed".
- Every handler body wrapped in `try/except BaseException`, never re-raising - finding 1 makes
  that non-negotiable.
- Also report anything still outstanding at `close()`, which costs nothing.

That gives an operator staring at a hung service the thing they actually need - the application
line that borrowed and never returned - with no background thread, no periodic work, no
false positives, and no way to strand a connection. The idle-pool and long-hold problems simply
do not arise, because the detector only ever speaks when the pool is genuinely exhausted.


Java's `DataSourceConfig` carries `leakDetectionThreshold` and `idleTimeout` (and `minimumIdle`);
SQLAlchemy's pool has no equivalent of any of the three, so this port simply does not offer them.
Written up in `DIVERGENCE.md`; the decision needed is whether `leakDetectionThreshold` is worth
building. A connection borrowed and never returned inside a unit of work is the failure this
framework's transaction handling exists to prevent, and Java gets a log line for it that we do
not. It is implementable - a checkout timestamp plus a periodic sweep over `pool.checkedout()` -
but it is new code with a background thread, not a setting to pass through, so it is a call
rather than a port.

### The scheduler cannot be stopped by a signal

Found in `EKB-0132`, by re-reading the three files that had been marked `done` at ledger-creation
time rather than by a porting pass. Written up in full in `DIVERGENCE.md`; the decision needed is
below.

**What Java does.** db-scheduler's own `SchedulerBuilder.registerShutdownHook` defaults to
`false`. `JobRegistry.Builder` deliberately flips it to `true`, and calls it in `build()`. So a
SIGTERM runs `Scheduler.stop()` and in-flight executions drain. Opting out is a documented
choice - `registerShutdownHook(false)` - for tests and for hosts that own shutdown.

**What this port does.** Nothing, at either level. Verified by running: building a registry adds
no `atexit` callback and leaves `SIGTERM` at `SIG_DFL`. A container stop kills the process
mid-execution; those rows stay `picked` with a frozen heartbeat and are revived only by the
dead-execution path, 60s later on framework defaults. `JobRegistry.stop()` and the `with` block
work - the gap is only when nobody calls them.

**Why it is two changes, not one.** The vendored `SchedulerBuilder` has no `register_shutdown_hook`
either, so `JobRegistryBuilder` currently *cannot* offer one. db-scheduler's `FILE-LEDGER.md` row
is now `partial` for the same reason.

**What needs deciding, and why I did not just port it.** A JVM shutdown hook and a Python exit
hook are not the same object:

- `atexit` does not run on SIGTERM at all - the default disposition terminates the process. Java's
  hook does run on SIGTERM. Matching the behaviour therefore means installing a **signal handler**,
  not an `atexit` callback.
- A library installing a process-wide `signal.signal` handler is a bigger imposition in Python
  than `addShutdownHook` is in Java: there is one handler per signal per process, it can only be
  set from the main thread, and overwriting an application's own handler is a real hazard.
  Gunicorn, Celery and uvicorn all install their own.
- So the faithful option and the polite option differ, and which one is right depends on how this
  framework expects to be hosted. Candidates: (a) signal handler, default on, as Java; (b) signal
  handler, default **off**, opt-in via `register_shutdown_hook(True)`; (c) `atexit` only, which
  covers a clean interpreter exit but not the container stop that actually matters; (d) leave it
  to the caller and document `with job_registry(...) as reg` as the supported shape.

My inclination is (b) - chain to the previous handler rather than replace it - but it is a
framework-shape question, not a porting question, so it is yours.

**Related, same area, also open:** Java's `EventFanoutJob.execute` loops until
`ctx.getSchedulerState().isShuttingDown()`; this port's returns when idle and ignores `ctx`
entirely, so there is no `shouldStop` counterpart to hook a cooperative stop into. Whatever is
decided above, these two want deciding together.

## Already reverted

| what | commit | how it went |
| --- | --- | --- |
| `ordered_uuid()` as the outbox primary key, so `(event_date, id)` would be a total order | added in `EKB-0108`, reverted in `EKB-0111` | I treated "rows of one action tie on `event_date`" as a defect and reached for a framework change. It is not a defect - `event_date` is the ordering key a consumer uses, and rows of one action are genuinely simultaneous. Four of the six "failures" that motivated it were the *test clock* being frozen. Java uses `UUID.randomUUID()` here and its v7 is only ever the sharding-bit-carrying `ShardedUUID` |

## Reversed after review

The list below is the point of this file: it exists so a decision I took can be checked rather
than assumed. One has been reversed so far - see the struck row under "Went further than Java".

## Went further than Java - the ones most worth arguing about

These change behaviour Java does not have. Each is defensible and each is mine.

| what | commit | the case for it | the case against |
| --- | --- | --- | --- |
| A write with no `RETURNING` rebuilds its rows through the read mappers instead of handing back the caller's objects | `EKB-0115`, `EKB-0116` | On MySQL, `update()` returned a model still carrying the event it had just persisted; feeding that into another update writes the deposit to the outbox twice. Proven by running | Java takes the plain branch (`return List.copyOf(domainObjects)`) and is safe only because its types make the raw-id half impossible and `nextVersion()` drops events. So this is a real fix for a real Python-only bug - but it is behaviour Java does not have, and it costs a `to_row`/`from_row` round trip per row |
| ~~`ModelEvent.payload()` excludes `model_id` and `model_name`~~ **Reversed**: the payload carries both under Java's names, `modelId` and `modelName`, and `EKB-0218` added a test asserting it. | `EKB-0109` | They have columns of their own, which is where a consumer reads them. `ActionEvent` - the wire contract - calls the payload "raw JSON" | Java's payload *does* contain them, because they are public fields and Jackson serialises everything public. A consumer written against a Java producer and deserialising the payload with `@JsonCreator` will find them null. Verified that `TestModelEvent` accepts them "for protocol compatibility" |
| A JDBC url gets its own error message | `EKB-0113` | It is the likeliest mistake anyone moving between the ports makes, and unqualified it reads as a missing driver | Invented here; Java has no equivalent. Harmless, but it is a message nobody asked for |
| Every shard must speak the same dialect | pre-existing, confirmed `EKB-0113` | The framework picks its SQL, lock provider and upsert form from one dialect | Java does not check this. If a mixed layout is ever legitimate, this refuses it |

## Ported an invariant Java already had - lower risk

Each of these exists in the Java, was missing here, and is now present with a test that fails
without it.

| what | commit |
| --- | --- |
| `Model` / `Entity` / `TypedValue` reject null id, null state, null/absent version, `version < 1`, null events | `EKB-0108` |
| A model's events tuple is copied, so a "frozen" model cannot change underneath it | `EKB-0108` |
| A connection whose rollback failed is invalidated rather than returned to the pool | `EKB-0113` |
| A failed *commit* takes the rollback path, and is evicted if that fails too | `EKB-0113` |
| The partial-commit alarm fires whatever killed the shard, not only an `Exception` | `EKB-0118` |
| `NoShardingStrategy.uses_shard_aware_id` is `False`, with the repository special-casing it | `EKB-0112` |
| `CrossShardError` carries Java's two named shards rather than a list | `EKB-0112` |
| A single-database registry needs no explicit default shard | `EKB-0112` |
| `update_no_result` distinguishes a non-unique key from a lost race, so it is not retried | `EKB-0115` |
| The executor names `registry`, `repositories` and the action when they are missing | `EKB-0119` |
| The cross-shard warning is written before the commits, not after | `EKB-0118` |

## Renames, for parity

`copy_from` → `copy_base`, `emits_events` → `is_model`, `RetryConfig.retries` → `max_retries`,
`ExecutionConfiguration.retries` → `retry_configs` (`EKB-0108`, `EKB-0118`). The last one also
removed a collision: two fields called `retries` meaning different things.

`ReleaseReason.CLOSE` / `WATCHDOG` → `CLOSED` / `EXPIRED` (pre-existing, found by diffing enum members in `EKB-0137`). Internal to a private module and never stored, so it is a name only - unlike `EventNotificationState`, which is a column value and matches Java member for member.

## Test harness - changed what is being tested

| what | commit | why it matters |
| --- | --- | --- |
| MySQL and MariaDB containers start at all | `EKB-0108` | testcontainers 4.15 rejects the `mysql+` prefix the code passed, so **no MySQL or MariaDB test had been running** |
| MariaDB is addressed as `mariadb+pymysql`, not `mysql+pymysql` | `EKB-0108` | SQLAlchemy picks DDL, `SKIP LOCKED` support and version reporting off the scheme, and the framework picks its lock provider off `engine.dialect.name`. "The MariaDB suite" was exercising MySQL paths |
| MariaDB waits for the second "ready for connections" | `EKB-0108` | The first is the temporary server the entrypoint runs to initialise the data directory. Confirmed by reading the container logs: 11.4 logs it twice |
| Tests select outbox rows by `action_name` / `action_id` / row id rather than by position | `EKB-0111` | They were asserting an order neither port promises, and passing only because Postgres and SQLite happen to return insertion order |
| The wallet fixture gained a JSON list column and a nullable integer | `EKB-0134` | Java's `Dummy` fixture carries both deliberately - `aliases` is "a value that can only reach the database through a converter", `rewardPoints` is "nullable on purpose ... a batch write in which every row leaves this null is what exposes an untyped NULL bind". This port's fixture had neither, so neither path was tested. Adding them **immediately failed on Postgres**: SQLAlchemy renders a bare `None` inside `VALUES` as the literal `NULL` rather than a bound parameter, dropping the declared type, and Postgres then infers `text` - so `reward_points=v.reward_points` failed with "column is of type integer but expression is of type text". The suite's one existing all-null case used a nullable *text* column, which is the single type an uncast NULL happens to infer as. Fixed by casting; verified against all four dialects |

## Noticed in the Java, not acted on

See the table at the top of `JAVA-PARITY-LEDGER.md`: `EmbeddedBitsShardingStrategy` decodes shard
bits from a `ShardedId` with no version guard, while the `Id` branch beside it has one.
