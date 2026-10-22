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

These are the **4 that somebody decided**. Each could have gone the other way. Five more are
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
| ~~[The connection they hand back has a `rows()` on it](../DIVERGENCE.md)~~ | **Relabelled parity in `EKB-0227`.** It is what keeps Java's shape - a query that ends with its mapping - where SQLAlchemy has no verb for that step. Dropping it would be the divergence. |
| ~~[Timestamps are stamped when the change is staged](../DIVERGENCE.md)~~ | **Closed in `EKB-0192` and `EKB-0194`.** `created_date` is required at construction, as Java's `Validate.notNull(builder.createdDate)` requires it, and nothing writes `updated_date` but the application. |
| ~~[Retries match an exception the way `except` does](../DIVERGENCE.md)~~ | **Closed in `EKB-0198`.** The case it rested on - "`on=OperationalError` matches every driver subclass" - was checked and is not real: SQLAlchemy raises that exact class for every dialect and it has no subclasses. Matching is now exact and the walk is `__cause__` only, as Java's are. |
| ~~[Equality compares the whole row](../DIVERGENCE.md)~~ | **Closed in `EKB-0228`.** Java's rule: the same row at the same version - class, id, state, version, and the timestamps on a model - with the hash by id. It is also Python's practice for saved records (Django, SQLAlchemy). Comparing every field had also made any model with a list field unhashable. |
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

## The migrator against Flyway - judgement calls

`docs/MIGRATOR-LEDGER.md` settles every behaviour of a Flyway migrate against this migrator, each
one run on Flyway 12 as well as read. Most rows copy Flyway. These are the ones where I chose - to
differ, or to copy something of Flyway's that bites.

| what | commit | the case for it | the case against |
| --- | --- | --- | --- |
| The lock is held for the whole run, and the files are checked against the history under it | `EKB-0235` | One lock rather than one per migration, and the checks never read a history another process is changing. The outcome was measured the same as Flyway's: four processes, each migration applied once, in order (parity C03) | Flyway's processes share out the work, one migration each; here one process does it all while the rest wait. They wait either way |
| `post-apply` hooks run at the end of every migrate, not only when something was applied | `EKB-0235` | It is Flyway's `afterMigrate` timing, run and seen (parity F18). yoyo's timing means a hook that failed never runs again, since the next run has nothing to apply | A yoyo user expects yoyo's timing, and an expensive hook now runs, under the lock, on every start of every replica |
| `baseline` refuses an empty database | `EKB-0235` | Flyway baselines it and then never runs its first migration; the next one failed on the missing table (parity X05c) | A deviation from Flyway, with no way round it for a schema whose objects the emptiness check does not see |
| A description longer than 200 characters is refused | `EKB-0235` | Flyway cuts it to fit the history, and then cannot match a repeatable migration to its row: its next migrate failed (parity H07) | Flyway's versioned migrations survive the cut; this refuses those too |
| A history table of the shape `EKB-0233` made is refused, not upgraded | `EKB-0235` | No release ever made one - the version is still `0.1.0.dev0` - so an upgrade would be code that runs once, on nobody's database | A developer database migrated in the day between the two commits has to have its table dropped by hand |
| `installed_on` stays text, in UTC | `EKB-0233`, `EKB-0235` | It reads the same on every dialect, with no driver's idea of a date in between | Flyway's is a timestamp column; date functions over this one need a cast |
| What yoyo cannot read as Flyway does is refused rather than misread: `DELIMITER`, a `.conf` file, a Flyway callback file, `R1__x.sql` | `EKB-0235` | yoyo glues `DELIMITER` onto the next statement, which fails after the statements before it have run; the rest would be skipped without a word | Flyway runs a file with `DELIMITER` in it; a Java project's procedures have to lose it to move over |
| Subdirectories are read | `EKB-0235` | Flyway reads them (parity F04): a file Flyway would run must not be one this skips. `EKB-0233` read one level only | A directory holding one subdirectory per dialect now merges them - and is refused, loudly, as duplicate versions |
| A schema is "not empty" with a materialized view, a partitioned or foreign table (PostgreSQL), or a view or trigger (SQLite) in it | `EKB-0235` | Each is something a migration made; Flyway's queries miss them | Not Flyway's exact rule |
| `info`, `repair` and `baseline` are part of the migrator | `EKB-0235` | Java reaches them through Flyway itself. Without them a recorded failure, or a database that predates its migrations, means editing the history table by hand | Surface Java's `FlywayMigrator` does not have |
| Copied although it bites: an older release re-applies its older repeatable migrations, and refuses to start when a newer release added one | `EKB-0235` | Flyway does both (parity R16, R23). A rule that tolerated the second cannot tell a file a newer release added from one a person deleted | During a rolling deploy a changed view flips back each time an old instance restarts; a rollback past a new repeatable migration needs `repair()` first |
| `target_schema_version` is the highest version applied, whatever this run did | `EKB-0233` | It answers "what version is this database at" | Flyway's is the last version this run applied, and empty when it applied none |

## Login fields and driver settings (`EKB-0239`)

Decided with the user on 2026-10-11, after `docs/DRIVER-SURVEY.md`; the calls made while building
it are below the first table.

| decision | why | who |
| --- | --- | --- |
| The login moves out of the url into `username` / `password`, and a url carrying one is refused | Java's rule: a url is logged and repeated | the user |
| `data_source_properties` replaces `connect_args`, no old name kept | unreleased (`0.1.0.dev0`); one name for one thing | the user |
| `user`, `password`, `passwd` refused in it, in any case; login-file keys (`passfile`, `service`, `conninfo`, `read_default_file`) are not | exactly Java's rule, with PyMySQL's alias; a wider refusal was offered and declined. May be relaxed one day in both ports together | the user |
| `autocommit` and `client_flag` are not refused | although they change transactions and row counts | the user |
| groups and lists allowed (option B) | some driver settings take one; Java's reason (Spring splitting dotted names) does not apply | the user |
| values handed over as given, not as text | Python drivers take typed arguments | the user |
| a url SQLAlchemy cannot parse is read from its text, not refused | two hosts written `h1:3306,h2:3306` were accepted before; Java reads every url's text | built |
| a login on a SQLite config is refused | SQLite has none, so it would be silently ignored | built |
| every `data_source_properties` value prints as `***`, the names shown | Java treats every value as a secret | built |
| `split_login(url)` is public | a url from a container or a command line has to become a config somewhere; schemagen and the tests need it, and so will an application | built |
| `schemagen --url` still accepts a url with a login | a development tool against a throwaway database; it splits the login out before migrating | built |

## Config files (`EKB-0240`)

Decided with the user on 2026-10-11 (see `docs/CONFIG-FILES-LEDGER.md`); the calls made while
building it are below the first rows.

| decision | why | who |
| --- | --- | --- |
| No config library: `tomllib`, `json`, `configparser`, and PyYAML for YAML | only option covering every format with placeholders; libraries compared by running them | the user |
| PyYAML is a normal dependency | YAML just works | the user |
| `${NAME}` / `${NAME:default}` in YAML, TOML, JSON; `%(ENV_NAME)s` in INI | each format's own style; Spring's and Supervisor's | the user |
| a missing variable with no default fails; one set but empty is used | Spring's behaviour; an empty password is valid | the user |
| INI typing: declared types for the datasource's keys, the quote rule in `data-source-properties` | no driver-specific knowledge needed | the user |
| a value filled from an environment variable is text in `data-source-properties` | the main use is secrets; a password like `1234` must not become a number | built |
| `$${` is a literal `${` in YAML, TOML, JSON | a way to write one at all | built |
| the other format's placeholder style is refused with a hint | `${X}` in INI would otherwise be a literal password | built |
| unknown keys under `ekbatan.sharding` are refused, also in INI - so a helper key for `%(name)s` must be an existing key | a typo should not pass silently | built |
| INI's `[DEFAULT]` section is not copied into other sections | its keys would otherwise appear as settings everywhere | built |
| a text field refuses a number from YAML (`username: 1234`) instead of converting it | YAML reads `0123` as 83; converting would hide that | built |
| everything outside `ekbatan.sharding` is ignored | the file may hold the application's own settings | built |

## The whole `ekbatan` section, and the examples on it

Asked by the user on 2026-10-11: every example on a config file, as close to Java's
`application.yml` as possible (see `docs/EXAMPLES-CONFIG-FILES-LEDGER.md`).

| decision | why | who |
| --- | --- | --- |
| `load_ekbatan_config` returns `EkbatanConfig(namespace, sharding, jobs, local_event_handler)`; `load_sharding_config` kept | one call for what Java binds from `ekbatan.*`; nothing that used the old one breaks | built |
| unknown keys under `ekbatan` refused (Spring ignores them) | the same rule as under `ekbatan.sharding`; `handling.enable` must not silently leave handling off | built |
| durations: ISO-8601 only, Java's `Duration.parse` grammar; a bare number refused | what Java's examples write and its binder accepts; compared with `Duration.parse` on 20 inputs | built |
| namespace default `default` | Java's `@DefaultValue("default")` | built |
| every example's file is YAML, keys spelled as the Java examples spell them (`defaultShard`, `primaryConfig`, `dataSourceProperties`, `poolSize`) | parity; the other formats are covered by the unit tests | built |
| url and login in the examples are `${NAME:default}` placeholders; Java's examples write the values | one file runs locally as it is, and the test fills it from its container, as Spring tests override properties | built |
| MySQL/MariaDB examples set `init_command: SET SESSION innodb_lock_wait_timeout=10` | PyMySQL's counterpart of the Java examples' `sessionVariables`; the test reads it back | built |
| the saga example runs on the fan-out and handling jobs, `handling.enabled: true` in its file; manual `deliver_pending_events` and `POST /transfers/deliver` removed | as Java's saga example; driving the pipeline by hand showed a path no service uses | the user |
| the saga test shares one running service across the module, and waits up to 15 s for the saga to settle | as Java's one application context and `await().atMost(15s)`; a service per test waited out db-scheduler's 10 s poll each time | built |
| the sharded example gets Java's deposit handler (as `alerts`, the name the other Python examples use for Java's notifications), the event jobs on one scheduler for every shard, and `handling.enabled: true` | as Java's sharded example | the user |
| the SQLite example runs on a real job registry; `deliver_pending_events` removed; its manual stipend run uses a scheduler whose clock is a day ahead | the standard way, as asked; the stipend already ran at startup | the user |
| SQLite `start()` waits for the stipend's run at startup | a `FixedDelay` job is due when registered; without the wait a wallet opened just after start could be paid | built |
| job worker threads and run-migrations stay environment variables | Java has no key for either | built |

## QUARTZ cron patterns read as Java reads them (`EKB-0246`)

Asked by the user on 2026-10-16: a converter that turns a QUARTZ pattern into croniter patterns
that mean exactly what Java db-scheduler computes, with many tests (see
`db-scheduler/docs/QUARTZ-CONVERTER-LEDGER.md`). The reference is cron-utils 9.2.1, which Java
db-scheduler runs - not the Quartz Scheduler.

| decision | why | who |
| --- | --- | --- |
| croniter kept; QUARTZ patterns ported from cron-utils' parser, validation and value generators, check for check, then handed to croniter as explicit values | no maintained Python library reads Quartz correctly (ten tried, by running); croniter is right for the other styles | the user |
| cron-utils' surprising readings reproduced: a wrapping range (`22-2`) is its first value and nothing inside a list; `L`, `L-n`, `LW` dropped from a day-of-month list and `nW` read as `n` there; `nW` on a month-end Sunday stays on it; day-of-week `L` and `L-n` mean Saturday; `1-5/2/3` is `1-5/2`, `6#3#2` is `6#3`, `LWLW` is `LW` | a Java and a Python scheduler can share one table and must compute the same next time; refusing them would also have been safe, but would break patterns that run in Java | built |
| a pattern cron-utils accepts but throws on while computing (`31W` with a shorter month in range, `6-2/2`) is refused when the schedule is built | the same failure, sooner, instead of an exception from inside the scheduler loop on reaching the bad month | built |
| text outside printable ASCII refused | Java reads non-ASCII digits as digits; nobody writes them on purpose | built |
| cron-utils' search reproduced: a start's fraction kept when the next second matches, nothing after 2099, nothing more than 100 years out, each `\|\|` part computed on its own | each is what Java returns; each is pinned by a test | built |
| daylight-saving behaviour left as croniter's, for every style (Dracula bug 10) | not a Quartz difference; changing it changes every style | the user |
| tests compare against answers recorded from cron-utils itself (56,477 cases, plus for 24,107 patterns whether Java throws from the first of some month); the recorder and a checksum-pinned Java runner are committed | the tests need no Java; anyone can regenerate or check the answers | built |

## Log and trace names, as Java's (`EKB-0248`)

Asked by the user on 2026-10-16: fix Dracula 6, matching Java.

| decision | why | who |
| --- | --- | --- |
| each logger named after the Java class that writes the same lines (`io.ekbatan.core.action.ActionExecutor`, `...Retry`, `...shard.DatabaseRegistry`, one per SQL lock backend, `io.ekbatan.flyway.FlywayMigrator` for the migrator, ...); every name checked against a Java class file | a logging config written for Java applies unchanged | the user |
| a Python-only line goes under the Java class with the same job (fan-out's "still had work" under `EventFanoutJob`, the lock watchdog under `KeyedReentrantHolder`) | there is no Java line to copy, only a class doing that work | built |
| tracer scope `io.ekbatan.core`, at this package's version | Java's scope; Java reports its artifact's version, so this reports its own | built |
| `opentelemetry-api` is a dependency; the SDK stays the application's choice | Java's `ekbatan-core` depends on the API; without an SDK both are no-ops. Reverses the old "not a dependency" choice | the user |
| the migrator's MDC keys `ekbatanShard` and `ekbatanTarget` as record attributes, from a context variable through a filter on its logger | Python has no MDC; this is its standard counterpart. Unlike Java's thread-wide MDC, the keys reach only the migrator's own lines | built |
| *superseded (`EKB-0317`): the messages are Java's too* log messages themselves left as they are | the row was about names; messages differ in places (`executing` vs `Executing`) | reverted |

## Keyed locks on an event loop (`EKB-0250`)

Decided with the user on 2026-10-17 (Dracula bug 3).

| decision | why | who |
| --- | --- | --- |
| a keyed lock taken on an asyncio event loop's thread raises `ValidationError`, instead of becoming async-aware | ekbatan is synchronous throughout; a holder per task alone would freeze the loop, and an async lock API is beyond Java. Worker threads (`def` endpoints, `asyncio.to_thread`) make each request its own holder, as in Java | the user |
| the check runs only if asyncio has been imported | without it no loop can be running; one dictionary lookup otherwise | built |

## The event handler registry, as Java's (`EKB-0251`)

Asked by the user on 2026-10-17: Dracula bug 4, exactly as Java.

| decision | why | who |
| --- | --- | --- |
| `EventHandlerRegistry.add` ported from Java's `withHandler`, check for check and in its order: blank name, missing event type, a name or alias already taken (one table for both, which also refuses one handler twice), two classes behind one short name, a blank alias, an alias equal to the handler's name | the same mistakes stopped at startup as in Java | the user |
| Java's messages, word for word; `ValidationError` for Java's `IllegalArgumentException` / NPE | a search for the Java message finds the Python one; the exception type follows the port's convention (Dracula 57) | built |

## The setup guide, from the examples' own files (`EKB-0253`-`EKB-0255`)

Asked by the user on 2026-10-17 (Dracula bug 6); the scope - full files, kept in sync - was the
user's choice. See `docs/SETUP-GUIDE-LEDGER.md`.

| decision | why | who |
| --- | --- | --- |
| every file on *Complete project setup* is the example project's own file, imported when the site is built | it cannot drift from code tested on every commit; the excerpts it had had drifted badly | the user |
| the REST examples' one migration split into Java's four files (`V0001__eventlog` ... `V0004__alerts`) | Java's examples split them so, and the page already recommended it | built |
| the page's layout is the examples': migrations and `ekbatan.yaml` inside the package under `src/` | what `uv init` and `poetry new` create, and a wheel then carries them | built |
| SQLite points to `wallet_service_sqlite` rather than having a FastAPI version of the page | no FastAPI SQLite example exists, and SQLite is not a production dialect | built |
| `codegen` works in a new project: the package comes from the module read, and a stand-in answers the model's import of the builders it is writing | without it no new project could generate its first builders | built |
| `scripts/check-setup-guide.py` follows the page for 3 databases x 3 installers, installing `ekbatan` from the checkout - its only substitution, until it is on PyPI | the proof that the page runs as written; Docker-bound, so not in `make lint` | built |

## A wheel that holds what the service reads (`EKB-0256`)

Asked by the user on 2026-10-17 (Dracula bug 7).

| decision | why | who |
| --- | --- | --- |
| `schema.py` stays gitignored, and the guide says so as a command, with the line each build tool needs to pack it anyway | it is generated, as Java's jOOQ output is; measured: poetry-core and hatchling drop it, `uv_build` and setuptools keep it | built |
| the guide's pip setup declares `[tool.setuptools.package-data]` for `ekbatan.yaml` and the migrations | measured: setuptools packs only `.py` files, so the wheel had neither, whatever git ignored - worse than the bug reported | built |
| the setup-guide check builds the wheel, checks it lists the files, and imports it installed in a fresh venv; the packaging runs in a git repository, made if the page did not make one | the failure only shows on a machine without the source tree; some tools honour `.gitignore` only inside a repository | built |

## Every example per build tool (`EKB-0257`)

Asked by the user on 2026-10-17: examples per build system, as Java has Gradle and Maven ones, and
the docs and website following the choice. The full matrix - every example in all three - was
the user's choice. See `docs/EXAMPLES-MATRIX-LEDGER.md`.

| decision | why | who |
| --- | --- | --- |
| 7 examples x uv, pip, Poetry = 21 standalone projects | Java carries its examples per build tool; the build tool decides what a wheel holds | the user |
| names are Java's: `[stack]-[domain]-[style]-[build][-variant]-[db]` (`fastapi-wallet-rest-uv-pg`); the package inside keeps its Python name in all three | a reader moving between the ports recognises them; identical code across builds | built |
| the uv project is the source; `scripts/sync-examples.py` writes the twins' `src/` and every project's build files, and `make lint` runs it with `--check` | 21 hand-kept copies drift; one source and a check do not | built |
| each project's tests run against its built wheel, installed in its own environment | the check that would have caught bug 7 on the examples themselves | built |
| `ekbatan` comes from this repository through each tool's own mechanism, marked for deletion once it is on PyPI | it is not published yet | built |
| lock files gitignored | they would pin a path into this checkout | built |
| root mypy checks the uv projects only | the twins are the same files; checking all three would type-check one file three times under one module name | built |
| the website's picker names the example for the database **and** the installer | the choice the user asked the docs to follow | built |

## Parity fixes, group L: locks and jobs (`EKB-0263`-`EKB-0269`)

Items 2 and 30-35 of the parity scan's importance table, fixed unattended on 2026-10-17 under the
rule "match Java". Ledger: [`parity-fix/L-locks-jobs.md`](parity-fix/L-locks-jobs.md).

| decision | why | what Java does |
| --- | --- | --- |
| an unreachable database is a `LockAcquisitionError` ("could not obtain a connection") on every SQL provider | the documented error for a backend failure; it used to escape as SQLAlchemy's `OperationalError` | wraps the failed connection the same way |
| MySQL / MariaDB: `GET_LOCK`'s seconds sent as a `Decimal`, 0 for no wait, else whole milliseconds (at least 1) over 1000 | Connector/J writes Java's double as `0.5`, which MySQL rounds half up; PyMySQL wrote a float as `0.5e0`, rounded half to even, so a 500 ms wait was none. Measured on MySQL 26.7 | `toGetLockTimeout`, `setDouble` |
| `close()` on a lock provider does nothing; open leases stay held until released or `max_hold` | Dracula 37: it released every thread's leases while their holders were inside. The examples still call it, harmlessly, as Java code calls `close()` | `default void close() {}`, not overridden |
| the Redis lock is Redisson's own layout and protocol: hash, field `<client id>:<thread id>`, Redisson 3.39.0's lock and unlock scripts verbatim, the unlock latch, `0` published on `redisson_lock__channel:{key}` | a Java and a Python service must share a lock; with a string key Java's `tryAcquire` threw `WRONGTYPE`. This ties the port to Redisson's internal format, which changes rarely; the version is named in the code | a Redisson `RLock` |
| a Redis waiter subscribes to that channel, one pub/sub connection per waiting call, and also wakes when the holder's TTL ends | what Redisson does; it replaces the 50 ms poll (Dracula 41) and the overshoot (scan row 18). Redisson shares one subscription connection per client; redis-py has no such pool, so a waiter takes its own for the length of its wait | `LockPubSub`, one shared subscription |
| not copied: Redisson's `WAIT` for replicas and its sharded `SPUBLISH` | the provider takes one `redis.Redis`, the single-server case, where Redisson skips both | used only on replicated or cluster configs |
| a waiting `acquire` cannot be interrupted: kept as a limit, written on the keyed-locks page | Python has no `Thread.interrupt()`. A cancel argument would be API Java does not have; raising an exception into the waiting thread (`PyThreadState_SetAsyncExc`) is unsafe mid-socket-read. `try_acquire` with a bounded wait, in a loop that checks a stop flag, is the honest way to stay stoppable | `acquire` throws `InterruptedException` within one 5 s segment (SQL) or at once (Redis) |
| `stop()` with a job past `shutdown_max_wait`: kept as a limit; one wait, then return; an INFO line "Cannot interrupt executor service threads in Python" where Java logs its interrupt | a second wait without an interrupt only delays the return; Java returns after the first wait for a job its interrupt ended, and the scan measured both at 1 s | waits, interrupts, waits again (up to 2x) |
| the Redis provider keeps wrapping a Redis failure as `LockAcquisitionError` | Java's own contract says a backend failure is `LockAcquisitionException`; its Redis provider lets Redisson's `RedisException` through, which is a Java bug (below) | lets `RedisException` escape |

## Events as Java's (parity fix group E, `EKB-0270`-`EKB-0276`)

Asked by the user on 2026-10-17: fix items 11, 12, 17, 18, 19 and 20 of the parity scan, matching
Java. See `docs/parity-fix/E-events.md`. No exceptions were needed: Python can do all of it, and
none of Java's behaviour here is a bug.

| decision | why | who |
| --- | --- | --- |
| `find_undelivered` and `find_due` take a connection of their own, never the open transaction (`EKB-0270`); the outbox repository's reads (`count`, `find_all`, `find_by_action_id`) still join it | Java's two event reads use `readonlyDb()` / `db()`; Java's outbox repository uses `txDbElseDb` for every read, so Python already matched there | the user |
| fan-out reads the replica, handling reads the primary (`EKB-0271`) | Java's choice: a lagging replica makes fan-out late, never wrong (the unique key drops a second insert), while a stale due row would call a handler twice. Overrides the earlier Python reasoning that fan-out should read and mark in one transaction | the user |
| a new notification is due at its event's `event_date` (`EKB-0272`) | Java's; an older event fanned out late is handled first, a future-dated one waits | the user |
| a failure expires at once when its next retry would land past `event_date + retention`, judged against one clock reading taken after the whole batch (`EKB-0273`) | Java's `postInvokeNow` rule; the per-row log lines moved with the decision: the failure is logged when it happens, retry-or-discard once the batch is done | the user |
| outcome buckets are written one auto-committed statement each, in Java's order - pre-flight expiries, successes, post-failure expiries, retries - and this port's own "no handler" bucket last (`EKB-0274`) | Java's; a failed write keeps the outcomes before it, so a succeeded handler is not called again. Overrides the earlier all-or-nothing choice | the user |
| a hand-built `EventHandlingJob` runs on `FixedDelay(poll delay)`, 1 s by default, and no longer reads `handling.enabled`; it keeps an `enabled=` argument (default `True`), and the examples pass `enabled=config.handling.enabled` (`EKB-0275`) | Java's job always runs and its DI wiring reads the switch by not creating the job. With no DI here, `build()` is the wiring, and `enabled=False` (a `DisabledSchedule`) is the nearest thing to "not created" that still lets one list of jobs be built either way | built |
| scan rows 14 (a missing handler is not a failed attempt) and 16 (exactly at the retention edge) left as they are | not in items 1-44; the probe still shows them (`e.round*.ghost`, `x.boundary.*`) | built |

## Transactions, actions, config and the pool, as Java's (`EKB-0300`-`EKB-0305`)

Parity-fix group T, 2026-10-17: items 3, 4, 5, 22, 23, 27, 28, 29 and 36 of the importance table,
each matched to Java. Ledger: [parity-fix/T-transactions-config.md](parity-fix/T-transactions-config.md).
Judgement calls taken on the way, none asked:

| decision | why | what Java does |
| --- | --- | --- |
| a nested `transaction()` on SQLite waits for the file lock and fails with "database is locked" once the outer block has written | SQLite has one writer per file; a second connection cannot write past the first one's lock. It is Java's "the inner block can block on the outer's locks", in SQLite's form | Java has no SQLite |
| items 22 and 23 are one commit (`EKB-0302`) | both are the executor's construction, in the same lines | - |
| the unknown shard policy keeps its Python spelling once read (`"use_default_shard"`), and its refusal names Java's (`'useDefaultShard' or 'fail'`) | any spelling now reads, so the message can ask for the one a Java config uses | `UnknownShardPolicy.from`, the same rule and message |
| the null checks raise `ValidationError`, not a `TypeError` | the port's other argument checks (`registry is required`, `database_registry cannot be None`) raise `ValidationError`; the message is Java's word for word | `NullPointerException` |
| an entity repository says "Entities collection cannot be null" | Java builds the word as `getDomainTypeName() + "s"`, which gives "Entitys"; this says what Java means (listed below) | "Entitys collection cannot be null" |
| a batch element that is `None` is refused with "... in collection cannot be null" in every batch call | Java checks the element by name only in `addAll`; elsewhere a null element fails inside jOOQ with no message | - |
| *undone (`EKB-0318`): the standard pool is back* the pool fills to `pool_size`, not to `pool_size + max_overflow` | `max_overflow` connections are, by SQLAlchemy's definition, opened on demand and closed on return. With the default `max_overflow=0` the two are the same, as Hikari's one maximum | `minimumIdle` defaults to `maximumPoolSize` |
| *undone (`EKB-0318`): the standard pool is back* after `pool_timeout` the caller gets the driver's own error (`OperationalError`, "password authentication failed ...") | SQLAlchemy wraps a pool error into its own types only if it is the driver's; a new error type here would break every `except OperationalError` | Hikari's `SQLTransientConnectionException` "Connection is not available, request timed out after 30000ms", the driver's error as its cause, wrapped by Ekbatan in `RuntimeException("Failed to acquire connection")` |
| *undone (`EKB-0318`): the standard pool is back* the filler is one daemon thread per pool, started by `engine_for`; a pool `engine.dispose()` recreates starts filling on its first checkout | Hikari starts at construction; SQLAlchemy recreates the pool on every `dispose()`, and filling that one at once would reopen what was just closed | Hikari fills from construction until `close()` |
| *undone (`EKB-0318`): the standard pool is back* each failed attempt is logged at DEBUG by `io.ekbatan.core.persistence.ConnectionProvider`, the driver's message only, no traceback | Hikari logs each attempt at DEBUG; the drivers' messages name the user and server, never the password - checked with a marker password in the tests and the probe | `com.zaxxer.hikari.pool.HikariPool` DEBUG "Cannot acquire connection from data source" |

Items 28 and 29 (the pool filling ahead of time, and retrying a connection it cannot open for
`pool_timeout`) were undone by the user's decision on 2026-10-18 (`EKB-0318`): they replaced part
of SQLAlchemy's pool by overriding its private methods, and the pre-opened connections skipped
`pool_pre_ping`, so after a database restart about one request in three failed (round 2, N6).
SQLAlchemy's standard `QueuePool` is back; both items stay as accepted differences from Hikari.

## The migrator stays on yoyo (`EKB-0310`) - the user's decision

A Flyway-compatible rewrite of the migrator (`EKB-0277`-`EKB-0285`, parity-fix group M) was made
unattended so a Java and a Python service could share one migration history. The user reverted it
on 2026-10-17: a service is written in one language, so nothing ever continues another port's
migrations, and yoyo had been investigated and judged reliable. Replacing a vetted dependency is
the user's call, never an unattended one. The Flyway observations that group recorded are in
`EKB-0283` if wanted.

A schema named in the url that does not exist is **not** created (scan item 43, Flyway's
`createSchemas`): the user, 2026-10-18, "if an schema does not exist, people should manually create
it, automatic is not good". The migrator stops with the database's own error.

Left as they are, by the user's decision (2026-10-18), of the parity scan's migration items:
13-16 (one migration history shared with Java), 39 (files Flyway skips or accepts), 40 (Flyway's
callbacks), 42 (`classpath:` / `filesystem:` locations), 43 (creating a missing schema) and 44
(non-transactional migrations stay marked `-- transactional: false`). Item 37 (`EKB-0312`) was
matched to Java on top of yoyo.

Item 38, the lock wait, was matched (`EKB-0313`) and then undone (`EKB-0314`) by the user's
decision: Flyway gives up after 50 s on PostgreSQL only because its PostgreSQL lock goes through
`RetryStrategy` (`lockRetryCount`) while its MySQL lock is a plain endless loop - an accident, not
a design. Giving up crashes every instance waiting behind a migration longer than 50 s, and a
waiter cannot be stuck behind a dead holder, because the lock is the database session's. So the
migrator waits as long as it takes on every database, logging every thirty seconds.

Items 77-84 (script migrations, how `V1_5` is stored, the history table's shape, the checksum
ignoring line breaks, the `installed_on` zone, the baseline text, the result of a run with nothing
to apply, and the success log lines) are left as they are, by the user's decision (2026-10-18).

## Log messages worded as Java's (`EKB-0317`)

Asked by the user on 2026-10-18 (parity round 2, N3 item 7), so a Java team's log searches and
alerts match Python's lines. Every Ekbatan log line now has Java's text, level and attached
exception: action execution, retries, transactions, the registry, persisting, locks, jobs and
event handling. Round 2's log probes: 75 of the 107 recorded Java lines are now identical (2
before); what still differs is each language's exception class names, the error messages
themselves (scan items 50-51), the order concurrent handlers finish in, db-scheduler's own lines,
and the migrator's (items 37 and 84, decided).

| decision | why | what Java does |
| --- | --- | --- |
| shards print as Java prints them inside messages - `ShardIdentifier(1, 0)`, and `(1,0)` in the transaction lines - through `_logtext`; elsewhere a shard is still `1:0` | the text a search matches has to be Java's; Dracula 66 is about `str()`, not log lines | its `toString()` and the transaction line's own format |
| discarded notifications: one `DISCARDED N event notification(s) ...` line per handler and reason for the batch, not one per row | Java's line, and an alert on `DISCARDED` now fires; after an outage it is one line, not thousands | the same |
| a handler failure: `Handler 'x' threw E while processing event <id> (attempts=n): m`, with the traceback; a non-`Exception` (a cancellation) as `threw Error` at ERROR | Java's two lines; Java's `Error` has no Python twin, a `BaseException` that is not an `Exception` is the nearest | WARN for an exception, ERROR for an `Error` |
| the partial cross-shard commit: Java's `CRITICAL: ... PARTIALLY COMMITTED!` text at ERROR with the exception, not at CRITICAL | Java's level and text | the same |
| the per-row `will be retried in` line is gone | Java has no such line; a retry is visible in the `threw` line and the row | none |
| a failed lock release names the caller's key, and MySQL/MariaDB warn when `RELEASE_LOCK` returns NULL or 0 | Java's lines; the key was not in the release payload before | the same |
| a failed rollback: Java's `Evicting connection from pool - ...` line, without a traceback | Java's line has none | the same |
| Java's DataSources "No username is configured" warning is not added | Java writes it for its unpooled data source only, which this port has no counterpart for | its driver data source |

## schemagen with forced types (`EKB-0321`-`EKB-0323`)

2026-10-18, unattended. The user: "in the java code we had the option of forced types (inside the
gradle script), could we have similar thing here? ... similar to what we have in java with sane
choices, and good defaults and types and decisions". Ledger: [SCHEMAGEN-LEDGER.md](SCHEMAGEN-LEDGER.md).
Every type below is proven on a real database by `tests/integration/test_schemagen_types.py`.

**The ones worth a glance first:**

| decision | why | what Java does |
| --- | --- | --- |
| a `TIMESTAMPTZ` column is no longer forced to `timestamp_column()`: it is `TIMESTAMP(timezone=True)`, with a note pointing at the UTC recommendation | it falls out of copying Java's forced type exactly (`TIMESTAMP` does not match the zoned type), and the value it reads is now right on any session zone. Round 2 had this as "leave" (N2 #1); nothing was done for it beyond following Java | `OffsetDateTime`, not forced |
| a MariaDB `JSON` column (`LONGTEXT` + `json_valid` check, which only `information_schema` reports) becomes `json_column()` | the docs already promised it; text there means payloads stop being objects. Beyond Java | `String`: its `(?i:JSON)` forced type never matches on MariaDB |
| ~~the `CHAR(36)` uuid guess runs on MariaDB as well as MySQL~~ **reversed by the user (EKB-0352): no built-in guess on any database; a project adds the rule, as Java's MySQL builds do; the MySQL/MariaDB examples and the setup guide carry it** - only `CHAR(36)`, only named `id`/`*_id`, never `VARCHAR` | asked for in these words; this port's MariaDB examples store uuids as `CHAR(36)` | MySQL only; Java's MariaDB uses native `UUID` |
| a PostgreSQL plain `JSON` column is `JSON(none_as_null=True)`, no longer `json_column()` | `json_column()` builds `JSONB`, so `create_all` would build a different column; it still reads a dict | `org.jooq.JSON`, not forced |
| settings in `[tool.ekbatan.schemagen]` of `pyproject.toml`, kebab-case keys as `[tool.ruff]` has; a flag wins; paths relative to the file; an unknown key is an error | where a Python project keeps build-time settings; a typo should not silently do nothing | the Gradle or Maven build script |
| keys, foreign keys, indexes (partial, on expressions), checks, server defaults, identity and generated columns, comments are all generated | so `create_all` in a test builds what the migrations build; measured equal on all four databases | `Keys`, `Indexes`, `defaultValue(...)` |

**The rest:**

| decision | why | what Java does |
| --- | --- | --- |
| a forced type says `name` (a framework helper, or a SQLAlchemy type written as Python with literal arguments) or `user-type` (a dotted path, `a.b.C` or `a.b:C`, with optional arguments) - exactly one | jOOQ's `name` and `userType`; a `TypeDecorator` is a user type and its converter in one, so there is no `converter` key | `withName`, `withUserType` + `withConverter` |
| matching is whole-text, as jOOQ's: `include-expression` against `column`, `table.column`, and `schema.table.column` for a listed schema, case-sensitive; `include-types` against every spelling of the type (the database's, SQLAlchemy's, with and without arguments, `TIMESTAMP`/`TIMESTAMPTZ`), case-insensitive | Java's own patterns (`.*\.id\|.*_id`, `CHAR\(36\)`, `TIMESTAMP`) then work unchanged; a type has no case | jOOQ's regexes; its default `COMMENTS` flag (whitespace ignored) is not copied |
| `exclude-expression`, `exclude-types`, `nullability` as jOOQ's; at least one include required | a forced type with no include would force every column | the same keys; jOOQ accepts none |
| the user's forced types are tried first, then Ekbatan's; the first match wins; `default-forced-types = false` drops Ekbatan's | jOOQ's order; the defaults are what every Java example copies | the list in each build script |
| the built-in five: zone-less `TIMESTAMP`/`DATETIME` to `timestamp_column()` everywhere; `JSONB` (PG) and `JSON` (MySQL, MariaDB, SQLite) to `json_column()`; PG `UUID` to `uuid_column()`; the `CHAR(36)` guess | Java's forced types, one for one; the PG uuid one keeps the schema `CHAR(36)` rather than `CHAR(32)` if it is ever used on MySQL | the same, as converters |
| a column forced to `timestamp_column()` that is not `TIMESTAMP`/`DATETIME(6)` - MySQL's `TIMESTAMP`, a bare `DATETIME`, `TIMESTAMP(3)` - is still forced, with a note saying what it does differently | Java forces them too; the note is the UTC recommendation where it applies | forced, silently |
| MariaDB's native `UUID` is `UUID()`, not `uuid_column()` | `uuid_column()` builds `CHAR(36)` there; `UUID()` builds the column the migration built and reads a `uuid.UUID` (measured) | `UUID`, no forced type |
| MySQL's `DOUBLE`/`REAL` is the generic `DOUBLE()` | MySQL's own `DOUBLE` type reads a `Decimal`; the test checks the type read, not only equality | `Double` |
| MySQL's `TINYINT(1)` (its `BOOLEAN`) is `BOOLEAN()` | it is how MySQL spells boolean; a real small integer is `TINYINT` without the width | `Byte` |
| an enum is `Enum("a", "b"[, name=...])` with string values | buildable and checkable; a generated enum class would be a second thing to keep in step | a generated enum class |
| a type the driver cannot read (`xml`, `point`, MariaDB `INET6`) is `NullType()` with a note naming the forced type to add - not refused | one exotic column should not stop the whole schema; tested: forcing such a column to a user type makes it readable and buildable | `Object`, with a warning |
| MySQL display widths dropped, character sets and collations kept, MariaDB's literal `NULL` default dropped | what reflection decorates against what the column holds | - |
| a serial key is left to SQLAlchemy (it builds `SERIAL` again); a serial elsewhere gets its `Sequence` and `nextval` default; an integer key the database does not auto-increment gets `autoincrement=False`; MySQL `AUTO_INCREMENT` outside the key gets a note | otherwise `create_all` adds or drops auto-increment the migration did not have | - |
| views are generated, into a second `view_metadata`; `views = false` drops them | queryable like tables, and `create_all` on `metadata` must not create a table by a view's name | generated beside the tables |
| the connection's own schema is always read and written unqualified; `schemas` lists the others, qualified, exported as `SCHEMA_TABLE`; listing the own one (`public`) means the same; an unlisted schema with tables is noted | jOOQ's `outputSchemaToDefault`; the throwaway database's own name means nothing | `schemas` + `outputSchemaToDefault` |
| `includes`/`excludes` over `table` and `schema.table`; the migration history always out; the framework's tables follow them | jOOQ's; twelve Maven builds exclude `scheduled_tasks` | the same |
| a foreign key to a table not generated is left out, with a note | it could not be built | generated |
| the framework's tables are still their helpers, now with their indexes from the migrations; a note when the migrations differ from the helper; an expression index on one is left out with a note | the contract stays one definition; `create_all` builds the indexes too | reflected (PG), excluded (MySQL) |
| extensions (`CREATE EXTENSION citext`) are not generated | the database's, not the schema's, and need rights the application may not have | not generated |
| the fingerprint still covers only the migrations; the example Makefiles depend on `pyproject.toml` too | reading settings on import would couple the module to a file a wheel does not ship | Gradle reruns on a build-script change |
| a user type is imported at generation, before any database starts, and an import failure names the forced type | a typo fails at once, not as a module that does not import; the cost: the type's module must not import the generated schema | jOOQ writes the name without loading it |
| a user type whose name SQLAlchemy has (`JSON`) is imported under an alias | the module would otherwise shadow one with the other | packages keep them apart |
| notes go to the logger `io.ekbatan.schemagen` as warnings; the command prints them as `note: ...` | the framework's loggers are all under `io.ekbatan` | Gradle task output |
| `image = "sqlite"`: a temporary file, no container | SQLite projects can generate too | - |
| `render()` takes the source `dialect`; without it the per-database forced types (the uuid guess) do not apply | the guess is MySQL's and MariaDB's only | - |
| every old flag still works; the examples moved to the table and a bare `python -m ekbatan.schemagen`; the setup guide keeps the flags, with a note | `scripts/check-setup-guide.py` runs the guide as written, flags included | - |
| `schemagen.py` became the package `schemagen/`; `generate`, `render`, `fingerprint`, `check_fingerprint` keep their names, `ForcedType`, `SchemagenConfig`, `load_config`, `DEFAULT_FORCED_TYPES` are new | a type table, configuration and rendering in one module was 1,000 lines | - |
| tests: `CASES_BY_DIALECT` + `case_id` in `conftest.py`, dropping pairs a database lacks and keeping each database's tests together | a joint parameter restarted the session container per case (measured); interleaving did the same | - |

## An update writes the record (`EKB-0480`)

An update wrote only the columns named like a model field; a column mapped from a field of
another name kept its old value with no error. It now writes every key of the record
`to_record` returns, as an insert does and as Java's update does. The judgement calls:

| decision | why | the other way |
| --- | --- | --- |
| a batch whose records carry different keys is refused (`ValidationError`, nothing sent) - for `add_all` too, not only `update_all` | one statement writes one set of columns; an INSERT batch is compiled from the first row's keys, and a later row's extra key was dropped with no error (measured on the old code) | write each distinct key set as its own statement |
| an update record without the id or `version` key is refused | the update finds its row by id and moves the version on; without `version` in the record the lock would not move | let it through and leave the version where it was |
| a record key the table has no column for is refused with `ValidationError`, naming the key and the table's columns | before, an insert raised SQLAlchemy's `CompileError` and an update ignored the key | keep SQLAlchemy's error |
| the columns are written in the table's order, not the record's | the same key set always builds the same statement text, so the per-size statement cache keeps working | the record's order |
| statements are cached per column set as well as per size; the single-row and column-set caches are capped at 32, as the batch cache was | a repository whose records leave a column out sometimes would otherwise rebuild, or grow the cache without bound | no cap |

## Ported an invariant Java already had - lower risk

Each of these exists in the Java, was missing here, and is now present with a test that fails
without it.

| what | commit |
| --- | --- |
| MySQL/MariaDB `GET_LOCK` names are Java's - bare hex, no `ekbatan:` prefix - so a Python and a Java service contend for one lock. Found by the Dracula sweep; pinned against Java's own output for five keys, and on real MySQL and MariaDB by asking the server for Java's name while Python holds the lock | `EKB-0244` |
| A `DataSourceConfig` never prints its password - nor does anything holding one (a layout, a migration result), nor an error about a url. Java's prints no fields at all; here the password is inside the url and a dataclass printed every field, so `log.info(result)` wrote it out. Now `***`, as SQLAlchemy prints its own urls; secret-looking `connect_args` hidden too; and a test refuses any later dataclass whose url or password field would print by default. Proven with a real user and password on PostgreSQL, MySQL and MariaDB, every logger at DEBUG | `EKB-0237` |
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

## JSON, domain model and timestamps (`EKB-0290`-`EKB-0295`)

Parity-fix group J, 2026-10-17, unattended. The user: "i like the thing to be compatible with
java, so camel case". Ledger: [parity-fix/J-json-domain.md](parity-fix/J-json-domain.md).

| decision | why | what Java does |
| --- | --- | --- |
| params and payload JSON is the text Ekbatan's default Jackson 3 mapper (`JsonMapper.builder().build()`) writes: camelCase keys, declaration order, decimals and floats as numbers in Java's `toString` forms, `java.time` forms, base64 bytes | a Java and a Python service share the outbox; measured equal over 50,000 values | the reference |
| a field name becomes Java's by dropping each `_` before a letter or digit and raising that character (`new_balance` -> `newBalance`); leading and trailing `_` kept; two fields with one JSON name refused | the obvious mapping, and the only one that round-trips | Java's field names are already camelCase |
| keys in declaration order, never sorted; `modelId`, `modelName` first in a payload | what Jackson writes for a record or a class with a `@JsonCreator` - Ekbatan's events all have one | Jackson 3 sorts the keys of a class with no creator; a dataclass always has one |
| an aware `datetime` at UTC is written as an `Instant`, at another offset as an `OffsetDateTime`, a naive one as a `LocalDateTime` | Python has one type for the three; the zone it carries is the only signal. Limit: Java's `OffsetDateTime` at `Z` cuts the fraction (`.4Z`), the `Instant` form groups it (`.400Z`) | three types |
| `Id` / `ShardedId` written as `{"id": ..., "value": ...}`, another `TypedValue` as `{"value": ...}`; read back from that shape or from a bare value | that is what Jackson writes for Java's getters; Java cannot read either back (no creator), so Python reading both is what Java would mean | see "Noticed in the Java" |
| every JSON number read as a `Decimal` | a double turns `10.50` into `10.5` and cuts long decimals; reading Java's rows exactly | Java reads through a double; see "Noticed in the Java" |
| MySQL's JSON column keeps a fraction as a double (`50.00` -> `50.0`), measured | the database's, not the port's: Java sends the same text and gets the same | the same |
| a JSON key missing on read leaves the field `None`, or its declared default | Jackson leaves a Java field null; a dataclass default is the nearest thing to a Java field initialiser | null; a missing primitive is refused (Python has no primitives) |
| a value that cannot be read raises `ValidationError` naming the class, field and key | Jackson's `InvalidFormatException` names the type and value | |
| a number or boolean read into a `str` field becomes its text | Jackson coerces it the same way | |
| `version` has no default (`EKB-0292`) | Dracula 52: Java refuses a row built without one | `Validate.notNull(builder.version, "version cannot be null")` |
| a builder passes a field whose setter was never called as `None`, so the row's own check refuses it by name ("id cannot be null"); a field typed `X \| None` with no default becomes `None` | a Java builder field is null until set, and the constructor's `Validate.notNull` names it | the same messages, as `NullPointerException` |
| the framework's fields are checked in Java's order - `id`, `events`, `state`, `created_date`, `version`, the version's range - then the model's own | which error a row with two mistakes reports should not depend on the port | `Model`/`Entity` constructor, then the subclass's |
| `increase_version()` with no version set raises `ValidationError("version cannot be null")` | Java's `version + 1` on a null `Long` throws; the same call fails, with a clearer message | an NPE |
| calling the dataclass constructor directly without a field still raises `TypeError` | that is the call Java does not let you write (its constructor takes the builder); a factory method that calls `cls(...)` must pass `version=1`, as Java's calls `withInitialVersion()` | - |
| `Id` and `ShardedId` compare as Java's `UUID.compareTo` does: each 64-bit half as a signed number (`EKB-0293`) | a Java and a Python service ordering the same ids agree; version 7 ids are unaffected until the year ~6400. The database's `ORDER BY` stays unsigned, in both ports | `Id.compareTo` / `ShardedId.compareTo` |
| comparing an `Id` with a `ShardedId` raises `TypeError` | Java's `Comparable<Id>` / `Comparable<ShardedId>` refuse it at compile time; it used to compare the uuids | does not compile |
| every timestamp the framework hands back is an aware UTC `datetime`, Java's `Instant` (`EKB-0294`): a model's dates, rows read through a repository (any `datetime` field), through `timestamp_column()`, the outbox and notification rows, `ActionEvent` | Dracula 43: a value read back now equals the one written and compares with `clock.now()` | `Instant` everywhere |
| the stored values are unchanged: zone-less columns holding the UTC time | that is what Java writes (measured 2026-10-06 on PostgreSQL, MySQL, MariaDB); B probe `s32.*` still prints Java's exact stored text | `InstantConverter` |
| a `datetime` with no zone given to a model or bound to a column is taken as UTC, not refused | Java's `Instant` has no zone-less form, so there is no Java behaviour to copy; refusing would break every `datetime(2026, 1, 1)` in tests and scripts | - |
| `timestamp_column()` is now a type that converts on the way in and out; the framework also converts explicitly in its own row mapping | so a table declared with a plain `DateTime` (a hand-written schema, the test fixture's `wallets`) reads back aware too | - |

## schemagen converters (`EKB-0328`-)

Track A of [CONVERTER-AND-WEBSITE-LEDGER.md](CONVERTER-AND-WEBSITE-LEDGER.md), 2026-10-18,
unattended. The design was agreed; these are the calls made while building it.

| decision | why | what Java does |
| --- | --- | --- |
| SQLAlchemy's statement cache keys a `converted()` type on the converter object itself (its own `==`/hash; a plain class is equal only to itself), and on identity when it cannot be hashed (a plain `@dataclass`) | keyed on the class alone, two converters of one class with different state shared a statement: `Plus(10)` read 102 instead of 110 (measured). An unhashable converter would otherwise raise at query time | jOOQ has no statement cache of this kind |
| the cache key uses SQLAlchemy's public `cache_ok` rule (the `__init__` attributes), not its private `_static_cache_key` | the converter is stored for the key as itself, or by identity when it cannot be hashed (a plain `@dataclass`), so no private method is overridden; `Plus(2)` and `Plus(10)` still read 102 and 110 (`tests/unit/test_schema_converters.py`). A first version overrode the private method; replaced before merge (`EKB-0331`) | - |
| `Converter[DbT, UserT]` is a `Protocol`, usable as an optional base | any object with `from_db`/`to_db` works, as asked; subclassing it still lets mypy flag a missing method and a wrong `DbT` (checked with `mypy --strict`) | `Converter<T, U>` is an interface you must implement |
| a converter wraps what the column would be without it: the next matching forced type that is not a converter, else the type table | so a converter on a `TIMESTAMP` receives `timestamp_column()`'s aware UTC `datetime` on every database, the value the docs' mapping table lists; a second matching converter is not stacked | the converter receives the JDBC value of the column's own type |
| a converter path may name a class (instantiated, with arguments if given), a factory function, or a converter object made once in a module | the same three forms `user-type` took; a TypeEngine given as a converter, or a converter given as `sqlalchemy-type`, is refused naming the right key | `withConverter` names a class |
| `sql_type(...)` uses the database's own spelling, lower case as reported (`xml`, `inet6`, `vector(3)`) | `create_all` then builds exactly that, and the reflected type round-trips (tested on PostgreSQL, MariaDB, pgvector) | jOOQ maps it to `Object` |
| `NullType()` and its note survive only for `render()` on tables built by hand with no known spelling | a real run always has the database's spelling | - |
| the mapping tables on the website are three short columns with notes underneath, and wrap on phones (a page-scoped style) | the site's tables are `max-content` wide; the first draft put the Python column off screen at desktop width. The shared layout was not touched, to stay out of Track B's way | - |
| the Java build-setting table moved from the schemagen page to `DIVERGENCE.md` | the user wants the website free of Java; the mapping is still useful to a maintainer | - |
| `docs/getting-started.md` and `learn/install.mdx` keep their one Java sentence about schemagen | they mention neither `user-type` nor `NullType`; removing Java from them is Track B's, and editing them here would collide | - |

## examples with forced types (`EKB-0350`-)

| decision | why |
| --- | --- |
| each generating example has two converters - one picked by type, one by name - and no `name` target | no column in these schemas needs a `name` rule: the built-in ones already cover uuids, JSON and timestamps, so it would change nothing a reader can see. Adding a column just to show one would be a column nobody would have |
| the currency rule picks by type: every `CHAR(3)` column becomes a `Currency` | every `CHAR(3)` here is a currency code, a common convention in money schemas, and it shows `include-types`. A later `CHAR(3)` that is not a currency (a country code) would be caught too; it would need an `exclude-expression` then |
| the second rule is by name: `alerts.recipient` -> `EmailAddress` (REST x4, job worker), `transfer_steps.step` -> `StepName` (saga) | the saga has no alerts table; its step marker is the natural enum. The job worker has the same two tables as the REST service, so it uses the same two rules |
| the same rules on PostgreSQL, MySQL and MariaDB | the column types are the same on all three (`CHAR(3)`, `VARCHAR(255)`), so nothing per database was needed |
| action parameters and events keep plain text (`currency: str`, `recipient: str`); only the model holds `Currency` and `EmailAddress` | parameters and events are written to the outbox as JSON. A dataclass there would be written `{"code": "EUR"}` and change what other services read; the payload stays `"currency": "EUR"` (tested) |
| a lowercase or malformed currency is now refused with a 400 | `Currency` takes only `[A-Z]{3}`; before, `"eur"` was stored as is. A small behaviour change in the examples |
| `DepositMoneyAction` checks the recipient before it deposits; a bad address is a 400 | the alert is written later by a handler; a bad address there would fail on every retry. Before, any text was accepted |
| value classes live in `model.py`; `converters.py` imports them | the saga's `StepName` was already in `model.py`, and imports then run one way. schemagen imports `converters.py` before `schema.py` exists, so it must not import `schema.py` |
| `StepNameConverter` stores the member's name | the repository wrote `step.name` before, so the stored text is unchanged |
| the `state` columns are not converted | the framework reads them as text (`state != 'DELETED'`), so they stay written by hand |
| no new migration | every converted column already existed |
| the forced types are written by `scripts/sync-examples.py` (`Forced`, `WALLET_FORCED`, `SAGA_FORCED`) | it writes every `pyproject.toml` from a template, so the three builds of an example cannot differ; an edit to a project's own file is overwritten |
| the SQLite examples are left as they are | they have no migrations: the hand-written `schema.py` builds the tables with `create_all`, so there is nothing for schemagen to read. Generating would mean adding migrations to the smallest example. Their README's old reason, "generating needs Docker", was out of date (`image = "sqlite"` needs none) and is corrected |
| the saga's new currency test waits for the saga to finish | without it, its steps landed in the next test and broke that test's count of three units of work (seen in a run) |

## Noticed in the Java, not acted on

- **`RedisKeyedLockProvider` does not wrap a Redis failure** (found by parity group L,
  2026-10-17). `KeyedLockProvider`'s javadoc says "a backend failure raises
  `LockAcquisitionException`"; the Redis provider calls `rlock.tryLock` / `lockInterruptibly`
  with no `catch`, so an unreachable Redis reaches the caller as Redisson's `RedisException` (run:
  `tryAcquire` against a closed port threw `RedisException`).
  The SQL providers wrap. ekbatan-py wraps in all four.

See the table at the top of `JAVA-PARITY-LEDGER.md`: `EmbeddedBitsShardingStrategy` decodes shard
bits from a `ShardedId` with no version guard, while the `Id` branch beside it has one.

## website without java (`EKB-0336`-)

| decision | why |
| --- | --- |
| one page, `reference/beside-java` (docs: `beside-java.md`), holds every working-together fact; other pages keep at most one sentence and a link | the reader who runs only Python is not sent through Java on every page; the one who shares a database finds it all in one place |
| traces, logger names and config keys are on that page too, not only tables, the lock, JSON, ids and streaming | each one is a real setting a mixed team shares (one dashboard, one logging config, one shard layout) |
| the logger name `io.ekbatan.flyway.FlywayMigrator` stays on the migrations and observability pages | it is the real name a reader types into a logging config; each page says once why it looks like that |
| the payload sentence "`modelId` and `modelName` come first" still names the Java consumer | that consumer is the only real reason for the order; inventing another would be wrong |
| config-files states the duration and missing-variable rules with no reason | the only reasons given were "as Java's binder / Spring does"; no other reason exists in the code |
| the footer's "The Java port" link now points to the new page; "a port of ekbatan" became "ekbatan for python" | the site-wide footer should not send every reader to Java |
| `docs/README.md` keeps its "About the port" list | it is the repository's index of maintainer files, not a copy of a website page |
| `.astro` code comments that mention Java (footer, nav, index pages) are left | not shown on any page, as the task said |
| example comments shown by the setup guide were rewritten; the mysql and mariadb `api.py` too, though not shown | the three projects stay alike, and `sync-examples.py` copies them to the pip and Poetry builds |
| links to maintainer files (`DIVERGENCE.md`, `SHORTCOMINGS.md`, the ledgers, `parity-probes/`) were dropped from reader pages | they are written against Java; a reader page should stand without them |
| wrong statements found while rewriting were fixed, each checked in `src/`: retry matching is by exact class; `Model` has three type parameters; a repository must write `to_row`/`from_row`; handlers in a dispatch batch run at the same time; event fan-out across shards is concurrent; there is no in-process lock provider; the framework declares no indexes; a failed cross-shard commit logs at ERROR with a `CRITICAL:` prefix | each contradicted the code or another page |
| the claim that a backward index scan serves db-scheduler's `priority DESC, execution_time ASC` was dropped | read backwards, `(priority, execution_time)` gives DESC, DESC - it does not match the mixed order |
| site tables still scroll sideways on many pages; only the new page's tables were made to fit | the scrolling table is the site's own design, used on pages this track did not change |
| the setup-guide check fails on `main` too and was not fixed here | `EKB-0350` gave the examples forced types (`Currency`), but the guide builds a project without `converters.py` or the forced-types settings, so its tests fail; that is converter work (track C), not this track Fixed in `EKB-0347`: step 8 of the guide adds `converters.py`, the two forced-type rules and an explicit regenerate (`make` alone could miss a change made in the same second); the Makefile rule also depends on `pyproject.toml`. All 9 database x installer runs pass |

## schemagen tests (EKB-0355-)

Hardening the schemagen test suite (ledger: `SCHEMAGEN-TEST-LEDGER.md`) found seven real bugs.
Each was proved on the unchanged code first - the test failed, or a probe on a real database
showed it - then fixed minimally in `src/ekbatan/schemagen/`. Old-code failure output is in the
ledger's probe table.

### Bugs fixed

| bug | fix | how the old code was shown to fail |
| --- | --- | --- |
| a `\r` (or NUL, or another control character) in a comment, default or name made the generated module a syntax error | `lit()` escapes every control character but tab (`\r` -> `\\r`, others `\\xNN`); tab and `\n` are written as before | unit `test_any_text_in_a_comment_or_default_survives_into_the_module[carriage return, crlf, nul]`: `SyntaxError: unterminated string literal` / `cannot contain null bytes`; probe on PostgreSQL 17 with `COMMENT ON COLUMN ... IS E'line1\r\nline2'`: the generated module failed to import |
| `url` written as a list or table in the file was echoed in the error, password and all | for `url` only, the error says the kind (`got a list`), never the value | unit `test_a_url_of_the_wrong_kind_is_refused_without_printing_it`: the password was in the message |
| `--config` naming a directory, or a config file that is not UTF-8, gave a traceback | `load_config` turns `OSError` and `UnicodeDecodeError` into `ValidationError` (`cannot read ...`, `... is not UTF-8`) | unit tests raised `IsADirectoryError` and `UnicodeDecodeError` |
| `[tool.ekbatan] schemagen = "x"` was refused as "unknown key(s) ['x']" (the string's letters); `[tool] ekbatan = 1` and `tool = 1` were `AttributeError` tracebacks | each of `tool`, `tool.ekbatan`, `tool.ekbatan.schemagen` must be a table, said by name and kind - the value is not shown, as a misplaced url would carry its password | unit `test_a_section_that_is_not_a_table_is_refused_as_such`: the wrong message, and `AttributeError` twice |
| an image from a registry with a port (`localhost:5000/mysql:8.4`) or pinned by digest (`mysql@sha256:...`) started a **PostgreSQL** container | the database kind is the last path part before its tag or digest | unit `test_the_image_names_the_database_whatever_registry_or_digest_it_comes_from`, with testcontainers' classes replaced by recorders: `_FakePostgres` was started for all three |
| PostgreSQL: a single-column integer key with its own name (`CONSTRAINT pk_t PRIMARY KEY (id)`) came back from `create_all` as `SERIAL` (a `nextval` default the migration never had) | `autoincrement=False` is written for a lone integer key whether the key is on the column or written apart by name | integration `test_a_single_column_key_with_its_own_name_is_not_made_auto_increment[postgres]`: rebuilt default `nextval('keyed_id_seq'::regclass)` against `None` |
| PostgreSQL: a non-key `SERIAL` in a schema listed in `schemas` was declared `Sequence("t_seq_seq")` with no schema, so `create_all` failed: `relation "audit.tallies_seq_seq" does not exist` | the sequence is declared in the schema its `nextval(...)` names: `Sequence("tallies_seq_seq", schema="audit")` | integration `test_a_serial_that_is_not_the_key_brings_its_sequence[postgres]` failed with that `UndefinedTable` |

Output changes for anyone regenerating: only the cases above. A comment or default holding a
control character other than tab and `\n` is now written escaped (it did not parse before, or
held the raw byte); a named single integer key gains `autoincrement=False`; a listed schema's
sequence gains `schema=...`.

### Open questions - left as they are

| question | what happens today | options |
| --- | --- | --- |
| **a table named like something the module imports** - `text`, `date`, `varchar`, `json`, `uuid`, ... or `migrations_fingerprint` - **fixed in `EKB-0416`**, see "schemagen: qualified sqlalchemy names" below | `TEXT = Table("text", ...)` replaces `from sqlalchemy import TEXT`; a later table (or view) that writes `TEXT()` raises `TypeError: 'Table' object is not callable` on import. A table `migrations_fingerprint` overwrites `MIGRATIONS_FINGERPRINT`, so the stale check always fires. Not fixed: every fix changes an exported name, or refuses a schema that imports today (a `text` table with no later `TEXT()` works) | (a) export such a table as `TEXT_`, as Python keywords already are (`class` -> `CLASS_`) - changes a name some module may import; (b) import SQLAlchemy's names under an alias when a table takes them - no exported name changes, the module reads less plainly; (c) refuse, "exclude it or rename it". Pinned by two strict xfail tests in `tests/unit/test_schemagen_hardening.py` that flip when it is fixed |
| **MySQL/MariaDB: a foreign key from a listed schema into the connection's own database** - **option (a) taken in `EKB-0417`**: still left out, and the note now says why (the key would have to name the own database) instead of claiming the table is not generated | left out, with the note "refers to `<db>.parents`, which is not generated" - the table *is* generated, unqualified. PostgreSQL keeps the key (it reports the own schema unqualified). Tried and reverted: writing the target unqualified - MySQL then reads `REFERENCES parents` inside `CREATE TABLE other.entries` as `other.parents`, and `create_all` fails (`Failed to open the referenced table`) | (a) keep leaving it out, but word the note truthfully ("cross-database key into the own database cannot be written without its name"); (b) write the database's real name into the key - `create_all` works only on a database of that name, against the docs' "works whatever the database is called"; (c) leave as is. Pinned per database by `test_a_foreign_key_from_a_listed_schema_into_the_own_schema` |
| `image = ""` and `includes = ""` in the file - **refused since `EKB-0417`**, with every other text setting: `ValidationError` "<key> is empty; remove the line to use the default". (Corrected by the review, EKB-0460: this said `excludes = ""` used to exclude every table. It did not - the old code skipped an empty `excludes`, so it excluded none, and an empty `includes` fell back to every table; read as no setting at all, either way silently.) Taken by the main session under the user's "nothing silent" rule; **worth a look** | read as no setting: the default image, every table | an empty string could be refused instead. Pinned by `test_an_empty_image_or_includes_falls_back_to_the_default` |

### Docs gaps found (docs not edited, per the task)

- `docs/database/schemagen.md` says `include-expression` is tried as `schema.table.column`
  "for a schema in `schemas`". The code also tries it for the connection's own schema
  (`public.widgets.id`, MySQL's database name, SQLite's `main`), on purpose (`_reflect.Relation`
  says so). Tested as the code does it (`test_each_forced_type_key_picks_exactly_the_columns_it_says`).
- The same page lists "indexes (... on expressions)" as generated. On SQLite, SQLAlchemy's
  reflection skips an expression index with its own `SAWarning`, so it is not in the module;
  MariaDB has no expression indexes. True on PostgreSQL (tested).
- Index options holding a list or dict are written with single quotes
  (`postgresql_include=['note']`, `mysql_length={'note': 10}`), unlike the rest of the module.
  Cosmetic; not changed.

## async trial

Judgement calls taken while building the `ekbatan.aio` trial (branch `async-trial`, uncommitted).
The plan and the results are in `docs/ASYNC-LEDGER.md`.

| decision | why |
| --- | --- |
| the shared logic lives in private base classes (`_RepositoryCore`, `_ShardRegistry`, `_ExecutorCore`, `_ChangePersisterCore`, `_SingleTableJson`, `_ActionCore`); the public sync classes extend them and keep their names, signatures and behaviour | one copy of every rule without a second public hierarchy; `isinstance(x, DatabaseRegistry)` and every sync import still work. The async classes extend the same bases |
| the async classes are not subclasses of the sync ones (`AsyncDatabaseRegistry` is not a `DatabaseRegistry`, `AsyncAction` is not an `Action`) | a coroutine `perform` or `get` overriding a plain one breaks every caller typed on the sync class; siblings on a shared base cannot be passed where the other is expected |
| one `ContextVar` (`ekbatan_tx`) holds both sync and async open transactions, keyed by manager | the brief said reuse it; a manager is only ever one kind, so the two never meet. Its type widened from `Mapping[TransactionManager, Db]` to `Mapping[object, Any]` - private, nothing outside `persistence` read it |
| `ActionRegistry` holds both kinds; `get` is now typed `type[A] -> A` | it is a map from type to instance and calls nothing. The new signature also gives sync callers the concrete action class back rather than `Action[P, R]` |
| an executor refuses the other kind of action when it is built (`ValidationError`, "all-sync or all-async") | a sync executor running an `AsyncAction` would hand back an un-awaited coroutine and commit an empty plan. Found while writing the test, not reported by a user |
| the sync `RepositoryRegistry` is reused for async repositories (one `type: ignore` in the async executor) | it is a map by model type and never calls a repository. A typed `AsyncRepositoryRegistry` is a small todo if the ignore is unwelcome |
| the extra is `ekbatan[asyncpg] = ["asyncpg>=0.30", "SQLAlchemy[asyncio]>=2.0.30"]`; asyncpg and `SQLAlchemy[asyncio]` were also added to the `dev` group so the suite runs | SQLAlchemy's asyncio API needs greenlet; its own `asyncio` extra says which one, so no greenlet pin of ours. greenlet was already in the dev environment (gevent pulls it). No other dependency was added |
| `async_engine_for` shares the sync engine's settings (`_engine_kwargs`): pool, pre-ping, JSON serialiser, `connect_args` | two pools configured from one `DataSourceConfig` cannot drift. Consequence: `data_source_properties` go to the async driver as given, so they are asyncpg's names (`server_settings`), not libpq's (`options`) - the UTC recipe in the docs is psycopg-only today |
| async built-in reads go to the primary outside the transaction (`db()`), as the sync ones and Java's do | same semantics in both modes; a read that must see the open transaction uses `tx_db_else_db`, as in sync |
| async scatter reads (`find_where` with no shard) visit shards one after another, as sync does | one meaning first; running them concurrently with `asyncio.gather` is a real async win and a todo (R9), with a decision on what a failing shard means |
| `close()` is `aclose()` on the async registry and manager, plus `async with` | `dispose()` is a coroutine on an async engine; a sync `close()` cannot await it |
| the async actions in the test fixtures override `name` to their sync twin's name | both modes then write the same `action_name`, so one assertion covers both - and it is what `name` exists for |
| the parity scenarios are written once as coroutines against a `Service` that awaits only what is awaitable | "same scenarios for sync and async" with one body each; the two concurrency tests (threads vs tasks) and cancellation are written per mode because they test the concurrency model itself |
| the benchmark emulates anyio's 40-thread default with `ThreadPoolExecutor(40)` as the loop's default executor and `asyncio.to_thread` | anyio is only in the `examples` group; this is the same limit without adding it. Same pool size (40) for every engine |
| the benchmark also runs psycopg's async mode (`postgresql+psycopg` through `create_async_engine`) | it needs no new dependency, and it was nearly as fast as asyncpg - which bears on whether asyncpg should be the recommended extra (open, see ledger) |
| `AsyncDb` subclasses SQLAlchemy's `AsyncConnection` and is built through its constructor, as `AsyncEngine.connect()` builds it | mirrors `Db(Connection)`; uses only public API (`start`, `execution_options`, `execute`) |

## async (professional)

The trial above, finished: branch `async-trial`, `EKB-0356` to `EKB-0363`. The plan and the
measurements are in `docs/ASYNC-LEDGER.md`; the reader's page is `docs/runtime/async.md`.

### Decided by the user

| decision | what was built |
| --- | --- |
| **the approved design** (before the trial): package `ekbatan.aio`, `Async` prefix, shared private cores, a service all-sync or all-async, `to_row`/`from_row` plain `def`, one transaction `ContextVar`, event handlers and jobs sync on threads | as described; the cores are listed in `AGENTS.md` |
| **drivers - the proper default for each mode.** Sync stays psycopg, PyMySQL, sqlite3; async defaults asyncpg (PostgreSQL), aiomysql (MySQL, MariaDB), aiosqlite (SQLite); with `SQLAlchemy[asyncio]` the only new dependencies, as extras named beside the sync ones, and in the dev group | extras `postgres-async`, `mysql-async`, `mariadb-async`, `sqlite-async`; `ASYNC_DEFAULT_DRIVERS`; `uv.lock` gains aiomysql 0.3.2 and aiosqlite 0.22.1 and nothing else |
| **capping requests in flight - best practice, no semaphore.** The pool is the cap: the same `pool_size` / `max_overflow` / `pool_timeout` as sync, fail fast past `pool_timeout` with a clear error and no secrets; document limiting at the server (`uvicorn --limit-concurrency`); rerun the benchmark with the timeout in place | `test_a_request_waiting_past_pool_timeout_fails_fast` and `test_requests_beyond_the_pool_wait_their_turn_and_all_commit` on PostgreSQL, MySQL, MariaDB; the example maps the timeout to a 503 and sets `limit_concurrency`; benchmark tables at 30 s and 0.5 s in the ledger and on the page |
| **multi-shard reads at the same time** with `asyncio.TaskGroup`; one failing shard fails the read, cancels the others, and the error names the shard (no secrets); no partial results; the sync order and meaning; decide the streaming iterators | `_on_each` in `aio/repository.py`; `test_a_read_across_shards_asks_them_at_once_and_keeps_the_sync_order`, `test_one_failing_shard_fails_the_read_names_the_shard_and_cancels_the_rest` (proven able to fail: sequential reads turn it red), on all four databases. The iterators: see below |
| **UTC on asyncpg**: the documented setup must work on asyncpg, proven by a test, documented, without overriding SQLAlchemy internals | libpq settings translated into asyncpg's `connect_args` (below); `test_the_documented_utc_setup_puts_each_async_session_on_utc` against a database whose default zone is Tehran, with the control |
| **deferred**: keyed locks and event handlers inside async actions | not built; documented on the async page, the guide, `SHORTCOMINGS.md` and the example ("no keyed lock - the optimistic lock decides") |

### Judgement calls

| decision | why |
| --- | --- |
| the trial's `asyncpg` extra is dropped, not aliased | it was never released (branch only), and one naming scheme - `<database>-async` - reads better than two spellings of the same thing |
| **which driver a url means.** A url naming a sync driver (`psycopg`, `psycopg2`, `pg8000`, `pymysql`, `mysqldb`, `mysqlconnector`, `pysqlite`) or none connects through the async default; any other driver is kept as written. So `postgresql+psycopg://` is asyncpg in async mode, and psycopg's own async mode is written `postgresql+psycopg_async://` | an async service always has sync parts (handlers, jobs, migrations), so one layout file must serve both registries; "the default driver for each mode" read literally. The sync `engine_for` is unchanged: a url naming an async driver still fails there, and the docs say to write the sync url |
| **libpq settings translated for asyncpg, and only these**: `options` (`-c name=value` and `--name=value` only, anything else refused) and `application_name` into `server_settings`; `connect_timeout` to `timeout`; `sslmode` to `ssl`. A setting given both ways is refused; no refusal repeats a value | needed for the UTC rule and for one file serving psycopg and asyncpg; the four are the ones with a one-to-one asyncpg meaning. Other libpq names reach asyncpg as given and asyncpg refuses them itself |
| the pool timeout is SQLAlchemy's own `TimeoutError`, not wrapped in an ekbatan error | the sync pool raises the same error today; wrapping only the async one would make the two differ. Its message holds no url or password (tested) |
| a failing shard's error is **its own exception, re-raised**, with `add_note` naming the shard (group:member and member name) and listing any other failed shards by kind only - not an `ExceptionGroup`, not a new error type | the sync read raises that exception, so a caller's `except` works the same in both modes (tested: same type as the sync read over the same databases). Notes show in a traceback but not in `str(error)` - flag if `str` must name the shard |
| `find_one_where` and `exists_where` also ask every shard at once | the user's rule covers multi-shard reads; the answer is the sync one (first shard in order that has the row). Cost: every shard is queried where sync stops early, and a later shard failing fails a read the sync call might have answered |
| `dbs()` / `readonly_dbs()` stay one shard at a time and lazy | they hand a connection to the caller's own code for as long as the caller holds it; running them at once would hold every shard's connection and change what "stop at the first" means |
| a read on one shard gets no note | as the sync read; the note is only for the scatter, where "which shard?" is a real question |
| `RepositoryRegistry` holds either kind: a type alias `AnyRepository = Repository[Any] \| AsyncRepository[Any]`; `repository()` returns the union | the trial's `type: ignore` hid that an async service fails `mypy --strict` at `RepositoryRegistry.builder().with_model_repository(...)` - found by type-checking the new example. One shared registry, as with `ActionRegistry`. A sync caller of `repository()` (none in the code or docs) now gets the union |
| the registry and executor builders became private generic bases (`_ShardRegistryBuilder`, `_ExecutorBuilder`) that the sync and async builders extend | one copy of each builder's checks; the sync builders' names, setters and behaviour are unchanged |
| `AsyncEventEntityRepository` added beside the async persister, not exported from `ekbatan.aio` | mirrors the sync `EventEntityRepository`, which `ekbatan` does not export either |
| the testing helpers are `AsyncActionSpec` and `async_run_action` in `ekbatan.aio.testing`, returning the sync `ActionAssert` / `StagedWork` | after `perform` nothing is awaited, so every check is the sync one; the function takes the `async_` prefix as `async_engine_for` does |
| `count_statements` takes an async registry (its listeners go on each engine's `sync_engine`) | SQLAlchemy fires an async engine's events there and refuses listeners on the async engine itself |
| **gevent**: async runs are skipped under `make test-gevent`, and the docs say the two do not mix | measured: under monkey-patching aiosqlite's worker threads became greenlets blocking the loop's thread and units of work stalled at the pool. gevent and asyncio answer the same question |
| **the test harness**: the existing integration modules run a second time against `ekbatan.aio` through a blocking portal (`tests/modes.py`) that rebinds each module's names to async twins, rather than rewriting every test as a coroutine | "parametrise the existing scenarios, do not copy them": the test bodies are the same lines in both runs. The async objects are the real ones on a real loop and driver; only the test body is not a coroutine. Each calling thread keeps one `contextvars.Context`, so an ambient transaction behaves as within one task. What the harness cannot express is tested natively in `test_async.py` |
| test edits the async runs needed, each kept honest for both modes: local `create_engine` / `event` / framework imports hoisted to module level (so they can be rebound); `KeyboardInterrupt` raised inside a task becomes `asyncio.CancelledError` in the async run (a `KeyboardInterrupt` in a task stops the loop itself); `registry.close()` is `aclose()` in the async run; a hand-written insert now passes `to_storage(...)` (asyncpg refuses an aware value for a zone-less column - psycopg accepted it, so the sync run is unchanged in effect); the scatter test expects every shard queried in the async run; error messages naming an action's class name the class actually built; a recording outbox has an async twin | each difference is the API's real behaviour, not a weakened assertion |
| in async runs, `ActionRegistry._add` and `RepositoryRegistry._add` are patched (test-only, by `monkeypatch`) to unwrap the harness's proxies | a proxy stored inside the framework would be called from the event loop and block it on itself |
| **the examples**: `fastapi-wallet-rest-*-async-pg` and `-async-mariadb`, built uv/pip/Poetry by `sync-examples.py`. The deposit takes no keyed lock (the optimistic lock decides; a twice-lost race is a 409); the stipend job is left out (the event jobs show sync work beside async requests); a `handlersConfig` pool for the handler's sync registry; `poolTimeout: 5`, `limit_concurrency` 50 | locks inside async are deferred, so the example shows the honest alternative; one job is enough to show "jobs stay sync"; pool sizes chosen per user, not traffic. MariaDB rather than MySQL for the second database: aiomysql drives both, and the suite runs MySQL - **changed by the user**: `-async-mysql` added beside it |
| the examples' `ekbatan.yaml` names the sync drivers (`postgresql+psycopg://`, `mariadb+pymysql://`) and sets UTC with `options: -c TimeZone=UTC` / `init_command` | one file for both sides is the point being shown. The six older examples set no session time zone (they rely on a UTC container) - not changed here; worth a look against the UTC rule |
| the setup guide's async step uses `sh` fences, which `scripts/check-setup-guide.py` does not run | the walkthrough proves the sync project as written; the async step is optional and its files are the async examples', proven by their own tests. Running it through the walkthrough would need a second project per database x installer - flag if wanted |
| `scripts/bench-async.py` gained `--pool-timeout` (default 30, the config's), counts timed-out deposits instead of timing them, and spells psycopg's async mode `psycopg_async` | the user asked for numbers with the pool timeout in place; `postgresql+psycopg` now means asyncpg in async mode |
| pre-ping stays on for async pools | parity with sync; it costs 1.5-2.5 ms per unit of work on both drivers (measured), and is the setting that keeps a dropped connection from failing a request |
| asyncpg's refusal of an aware `datetime` in a zone-less column is documented, not worked around | every framework write already goes through `to_storage`; only hand-written SQL meets it |
| the surface check (`test_the_async_class_has_the_sync_ones_public_surface`) allows exactly one difference: `close` / `aclose` on the registry and the manager | `dispose()` is a coroutine on an async engine |
| psycopg's async mode is accepted when the url names it, but the suite does not run on it | not a default; covered by a unit test and by the benchmark |
| X3 (MDC-like log context) and X5 (metrics) not built for async | the sync request path has neither |
| `DIVERGENCE.md` records `ekbatan.aio` as **chosen**, linking here | Java has no async API; the user decided this port needs one |

## async follow-ups

Three follow-ups to the work above, asked on 2026-10-19: UTC in the older examples, async keyed
locks and async event handlers, and the suite on psycopg's own async mode. Ledger:
`docs/ASYNC-LEDGER.md#follow-ups-2026-10-19-ekb-0364-on`.

### Judgement calls

| decision | why |
| --- | --- |
| **UTC in the examples: every pool, not only the request pool** - `primaryConfig`, `jobsConfig`, `lockConfig` (and `handlersConfig`) each get the setting; the async examples' `jobsConfig` gained it too | the rule is "each session is put on UTC"; db-scheduler's `scheduled_tasks` and a lock's session are sessions too, and a pool left out is the one whose `NOW()` is off |
| on MySQL and MariaDB the request pool's existing `init_command` became one `SET SESSION innodb_lock_wait_timeout=10, time_zone = '+00:00'` | `init_command` takes one statement in PyMySQL; one `SET` with two assignments keeps both settings |
| "change the template in `sync-examples.py`": the layouts are not templated - `ekbatan.yaml` is copied byte for byte from the uv project - so the uv projects were edited and the script wrote the pip and Poetry copies | the script's own rule ("edit only the uv ones"); nothing in the template mentions the layout |
| the off-UTC proof is one test per database, in `fastapi-wallet-rest-{pg,mysql,mariadb}`: it moves the server's default to Tehran (`ALTER DATABASE ... SET TimeZone`, `SET GLOBAL time_zone = '+03:30'`), checks the control, builds the service with `build()` (not started) and asks each of its pools; the other five examples only check their pools report UTC on a UTC container | the user asked for at least one per database; building the real service, not a hand-made engine, is what shows the example's file does it |
| the setup guide's step 4 gained a paragraph on the UTC setting | it shows these very files (imported from the examples), so it now shows the setting and should say why |
| **no async in-memory lock provider** | the request named one, but the sync side has none: it was removed in EKB-0225 (Java has none, and a lock in one process's memory excludes nothing in the others). "A counterpart of every provider the sync side has" is PG, MySQL, MariaDB and Redis |
| **async locks live in `ekbatan.aio.locks`** (SQL providers, protocol, lease) and `ekbatan.aio.locks.redis`, mirroring `ekbatan.locks` / `ekbatan.locks.redis`; not re-exported from `ekbatan.aio` | the sync side keeps locks out of `ekbatan`'s top level too; one place per backend |
| **the holder is the task** (`asyncio.current_task()`); a task started inside a held region - `gather` included - is another holder and waits | the sync rule is "per thread, not per call stack": work handed elsewhere is a different holder. A `ContextVar` holder would be inherited by child tasks and let them walk in |
| **`acquire()` returns an object that is both awaitable and an async context manager** (`async with locks.acquire(k, hold):` or `lease = await locks.acquire(...)`); `try_acquire` is a plain coroutine returning `AsyncLease | None`; `release()` is awaited; `close()` stays a no-op `def` | the sync names and shapes, with `async` where something waits; `try_acquire` can be `None`, so it cannot be a context manager itself, as on the sync side |
| **the hold limit runs on the loop** (`loop.call_later`), and its release is a task on that loop | releasing a lock held on an async connection is itself awaited, on the loop the connection belongs to; the sync watchdog thread cannot await it |
| **a wait cancelled part way closes its connection** (invalidate) rather than returning it; on Redis the release script is sent for that owner before the cancellation goes on | the server may have granted the lock in the instant the task was cancelled; a pooled connection holding a lock hands it to the next borrower. New rule, no sync counterpart (a sync wait cannot be cancelled) |
| **Redis owner field**: `<client id>:<counter>` - a number drawn per outermost acquisition, not a thread id | the holder is a task; re-entry is counted in-process; a number never reused cannot match a later task. The MONITOR comparison with Java's recording still matches (the owner is normalised there) |
| the sync providers' statements and result readers moved into module functions and constants (`PG_LOCK`, `mysql_got_lock`, `segments`, ...) that both sides call; the sync PG single try still sends psycopg's `%(k)s` spelling | one copy of each rule; the sync statements are unchanged on the wire |
| the sync loop refusal now names `ekbatan.aio.locks` as the answer on an event loop, beside `asyncio.to_thread` | it is now the right answer there |
| **the test harness runs every call a thread makes in one long-lived task** (`tests/modes.py`), where it used to start a task per call sharing one context | a lock held by a task is re-entered only by that task: with a task per call, the sync suite's re-entry tests could not run on the async providers. Closer to "a thread is one task" than before; the whole suite still passes through it |
| **async handlers: one long-lived loop on a thread of its own (`HandlerLoop`), the delivery thread waits on it** - not `asyncio.run` per event, not the application's own loop | `asyncio.run` per event opens the handler's pools again for every event. The app's loop would let a handler share the request executor, but `jobs.stop()` called on that loop (as every example's shutdown does) would wait for a handler that needs the same loop: a deadlock until `shutdown_max_wait`. A loop of its own has neither problem; fan-out and dispatch stay on their threads, so claiming, retry, backoff and expiry are untouched |
| **what an async handler awaits is built for the handler loop and closed on it** - `handler_loop.run(handler_registry.aclose())` before `handler_loop.close()` | measured: an asyncpg engine used on one loop and disposed from another after the first closed logs `Event loop is closed` and leaves unclosed sockets; disposed on its own loop it is clean. So the loop is a public object the application can hand to the job and use at shutdown |
| `HandlerLoop` is public in `ekbatan.events`, with `run()` and `close()`; `EventDispatch(handler_loop=)` and `EventHandlingJob.builder().handler_loop()`; without one a dispatch makes its own lazily (no thread for a sync-only service) and closes it in `close()` | the smallest surface that lets an application close what its handlers use; a loop handed in is never closed by the dispatch |
| `AsyncEventHandler` is a protocol in `ekbatan.events` (beside `EventHandler`), not in `ekbatan.aio`; the registry is typed for either (`AnyEventHandler`) | a handler is registered where sync handlers are; the protocol needs nothing from `ekbatan.aio`. Detection at dispatch is by what `handle` returns (an awaitable), so a callable returning one works too |
| a handler raising `KeyboardInterrupt` / `SystemExit` on the loop is carried back to the delivery thread and recorded as a failure (ERROR line), as for a sync handler; a cancellation (e.g. the loop closing) arrives as `asyncio.CancelledError` | asyncio would otherwise stop the loop itself on such an exception and every later async handler would hang |
| the async examples: fan-out and dispatch now read the queue through a sync registry on the **`jobsConfig`** pool (they are jobs); `handlersConfig` became the async handler's pool; a new async `lockConfig` pool for leases; the lock provider is picked by `registry.dialect` so `app.py` stays the same file on PostgreSQL and MariaDB; `jobs.stop()` now runs in `asyncio.to_thread` at shutdown | a pool per use, as the sync examples have; the handler's pool must be async and its own (loop-bound). Sizes unchanged (jobs 8 with 3 workers) - flag if the jobs pool should grow |
| test harness: `HANDLER_KINDS = ("sync", "async")` re-runs `test_events.py` with each handler wrapped in an `AsyncTwin` whose `async def handle` awaits the sync body on a worker thread (`asyncio.to_thread`) | parametrises the existing scenarios rather than copying them: dispatch takes the async path while the body, its counters and its exceptions stay the test's. One assertion differs by kind: the "delivered inline" test checks the delivery pool is never built in both runs, and the thread name only in the sync run |
| **psycopg async in the suite: a session option, `--async-driver=psycopg_async`**, which swaps the PostgreSQL entry of `ASYNC_DEFAULT_DRIVERS` for the session - not a third `MODES` value | every async run then goes through `async_url`/`async_engine_for` exactly as a url naming `postgresql+psycopg_async://` does, without tripling every id; a run is "the PostgreSQL suite on psycopg async", as the user asked. A test pins which driver the run really used |
| the one asyncpg-only check (`server_settings` spelled asyncpg's way) runs only when the run's driver is asyncpg | psycopg refuses it, correctly - it is not a libpq setting. Nothing else differed: no library change was needed |
| `make test-psycopg-async` runs `tests/integration` only | the unit tests pin asyncpg as the default (`ASYNC_DEFAULT_DRIVERS`), which this run changes on purpose |
| async handler runs (`HANDLER_KINDS`) skip under `make test-gevent`, as the async modes do | asyncio and gevent do not mix (measured earlier) |

## sync/async docs

Sync and async made a first-class choice across the setup guide, the docs, the website and
AGENTS.md, asked on 2026-10-19. Ledger: `docs/SYNC-ASYNC-DOCS-LEDGER.md`.

| decision | why |
| --- | --- |
| **the async SQLite twin leaves out the stipend job**, as the async FastAPI examples do | jobs always run on threads through the sync API; an async service writes one exactly as the sync twin does, over a sync executor - repeating it shows nothing async. Its README says so. Fan-out and dispatch are still there, as jobs |
| the async SQLite twin uses three pools over the one file (requests async, handler async, jobs sync) built from `primaryConfig` alone; no `handlersConfig` in its `ekbatan.yaml` | SQLite has one url and no login; the server examples' four named pools would be four copies of one line. Its README and `app.py` say a real deployment gives the jobs their own pool |
| **SQLite got real steps in the setup guide** (the `wallet-service-*-sqlite` files, a script instead of HTTP, a hand-written schema, no migrations), where it used to say "no step here" | the checker has to build every database x installer x mode, and the twins exist; the page now builds what the picker's Example row names |
| the guide's async Makefile runs uvicorn with `--limit-concurrency 50` | the guide replaces each example's `__main__.py` with the Makefile; the async `__main__.py` sets that limit, so the Makefile carries it |
| migrations, `model.py`, `converters.py` and SQLite's `schema.py` / `model.py` are shown once for both modes | they are byte-identical between each example and its twin; `tests/unit/test_setup_guide.py` fails if they stop being so |
| picker attributes take space-separated values (`data-db="pg mysql mariadb"`) | without it every server-database step would be written three times more; the checker reads the same rule |
| step 12 (the job) in async mode is prose only, pointing at the sync tab | the async examples have no job; see the first row |
| `ModeTabs` remembers the mode under the setup picker's key and field (`ekbatan-py.picker`, `mode`), in localStorage only; no URL parameter | the database and installer choices are not in the URL either |
| **Going async (`docs/going-async.md`, `learn/async`) is kept**, reframed as moving an existing sync service over | a file-by-file migration still helps someone with a sync service; starting fresh is the setup guide's Async view. Not merged |
| the async reference (`runtime/async`) lost its "keep the sync API when..." paragraph, its async/shared/sync table and most of "one kind per service" - each now a short pointer to Sync or async? | those moved to the new page; drivers, the pool, tasks, locks, the handler loop and gevent stay |
| Sync or async? sits in the learn path after Installing it, and in the reference index | it is a choice made at setup time |
| `docs/sync-or-async.md` links to the website's setup guide by its `.mdx` path | there is no docs copy of the setup guide; the link resolves in the repository |
| Java mentions were removed from the sync SQLite example's files (`app.py`, `ekbatan.yaml`, `schema.py`, `repository.py`, the test) and its README's stale "no row mapping" line was fixed | the setup guide now shows those files on the website, which explains features without Java; the README contradicted `EKB-0217` |
| the checker unsets `VIRTUAL_ENV` and drops this repository's `.venv` from `PATH` | it runs under `uv run`, and Poetry installed the walkthrough's packages into this repository's own environment - which broke the pg/poetry/sync run (and churned the root venv) when `uv run` re-synced it mid-run. A reader's shell has no venv active; this is the checker matching that, not a change to what it checks |
| **found, fixed in the docs: a task inherits the open transaction.** The transaction-manager page said the binding "does not propagate to other threads or tasks". Measured (`AsyncTransactionManager`, aiosqlite): a task from `asyncio.create_task` and a call through `asyncio.to_thread` both see the open transaction - both copy the context. A `ThreadPoolExecutor.submit` does not. The page now says so | **worth a look**: `asyncio.to_thread` handing a *sync* transaction's connection to another thread is the corruption the old sentence warned about; the framework does nothing to stop it. Not changed in `src/` |
| the page agents' smaller fixes, each checked against `src/`: `self.plan` *is* visible in a task gathered inside `perform` (it was said not to be); async scatter reads ask every shard at once (several pages said one at a time); SQLite has no keyed lock at all (install page said it "degrades"); an empty code block in learn/sharding step 1 filled; a duplicated table in `docs/database/repositories.md` removed | wrong for one mode or both |
| where a long config sample is the same in both modes but is loaded differently, the sample stays single with a one-line `# async mode: AsyncDatabaseRegistry.from_config(layout)` comment | a switch would repeat twenty identical lines |
| the checker now stops the service until nothing listens on port 8000 (TERM, then KILL after 15 s) before removing the database container | a uvicorn reloader ignored SIGTERM, outlived its run, and answered the next runs' health checks from a database that was gone - six MariaDB runs "failed" with no tables. **Worth a look**: the reloader (`--reload`, async MySQL example run through Poetry) did not stop on SIGTERM; its child did when signalled directly. Not reproduced outside the polluted-venv run |
| not done: the one-of-a-kind examples (sharded, saga, job worker) have no async twins | not asked; AGENTS.md says so |

## async examples: sharded, saga, job worker

Async twins of the last three one-of-a-kind examples, asked on 2026-10-19. Ledger:
`docs/SYNC-ASYNC-DOCS-LEDGER.md#async-twins-of-the-last-three-examples-and-three-docs-leftovers-2026-10-19`.

| decision | why |
| --- | --- |
| **No library change for the job worker.** A job's work is async by handing a coroutine to a `HandlerLoop` with `loop.run(...)` from `execute`; the job itself stays sync on db-scheduler's threads | `HandlerLoop` is public, its `run()` is documented as callable from any thread but the loop's, and it is already used that way by applications (`loop.run(registry.aclose())`) and tested (`test_async_handlers.py`). The job thread waits, so claim, heartbeat, failure and retry are db-scheduler's unchanged. **Worth a look:** the class is named for handlers. If a job-facing name is wanted (an alias such as `WorkLoop`, or a sentence in its docstring), that is a `src/` change I did not make |
| the job worker's jobs and its async handler share **one** loop (`HandlerLoop(name="worker-loop")`, handed to dispatch with `.handler_loop(loop)`) and one async registry on `primaryConfig` | everything that awaits is then built for one loop and closed on it once, at the end of `close()`; two loops would need two registries for no gain in a process that does nothing else |
| the job worker twin keeps the sync twin's tests on a manual scheduler, plus two: the stipend's work runs on the `worker-loop` thread (recorded from inside the coroutine), and what the coroutine raises reaches `execute` | the "really runs async" check the other twins have as a driver check; the driver check is in the settings test too |
| the job worker README says when async job work is worth it (work that waits on many things at once) and that the sync worker is the simpler choice otherwise | honest about the cost: a job of a few statements in a row gains nothing from a loop |
| **sharded twin: the keyed lock lives on the global member only** (`lockConfig` there, one `AsyncPostgresKeyedLockProvider`), and the deposit takes it whichever shard the wallet is on | a lock is about the wallet's key, not its rows; one database holding every key is how the sync REST examples do it. Added because the task asked for the lock "as in the other async REST examples" - the sync sharded example has none, so this is the one behaviour the twin has that its sync twin lacks; a test pins it (eight deposits at once on a Mexican wallet) |
| sharded twin: fan-out and dispatch read every shard through a sync registry built by hand from **each member's `jobsConfig`** (a new `jobsConfig` on the Mexico member, pool 4) | events commit on their wallet's shard, so the event jobs need every shard; a pool per use, as the other async examples have. The scheduler stays on the global member's pool |
| sharded twin: the handler's registry covers the default shard only (`handlersConfig` on the global member) | alerts have plain ids and all live on the default shard; the handler writes nowhere else |
| sharded twin: `total_per_shard` asks each shard's replica in a `TaskGroup` rather than through `readonly_dbs()` | the task asked that shard reads run at once; `readonly_dbs()` is sequential by design, so the example writes the gather itself, all or nothing like the inherited reads. `find_all_by_owner` gets this for free from `find_where` |
| saga twin: no keyed lock | the sync saga has none and the task did not ask for one; a transfer's idempotency is the unique step marker, unchanged |
| saga twin: the request executor also registers `CompleteTransferAction` and `RefundTransferAction` (on the request pool), as the sync one does | the replay test runs step two by hand through it; the handlers use their own executor on `handlersConfig` |
| saga twin's "runs async" check asks `pg_stat_activity` for a connection with `application_name = 'wallet-handlers'` after a transfer settles | from the outside, and only the handlers' pool sets that name. It must run before the settings test, which opens that pool directly; it is placed so |
| tests that drive a service directly (not through HTTP) await on a `HandlerLoop` of their own (`Run` in the sharded and saga tests) | the examples may import only the framework, the standard library and what they declare; anyio is not declared. `client.portal` is still used for HTTP tests, as in the other async examples |
| async REST twins also map SQLAlchemy's pool `TimeoutError` to 503 and set uvicorn's `limit_concurrency`, as the other async REST examples do | the same caps as their siblings; the sync sharded and saga examples have neither |
| generated `builders.py` may exceed the line length: `[tool.ruff.lint.per-file-ignores]` for `ekbatan-py-examples/*/src/*/builders.py` (E501) | codegen's first line names the module, and `fastapi_wallet_rest_async_sharded_pg` makes it 103 characters. The file is generated and checked by `scripts/check-builders.py`; changing codegen would be a `src/` change |
| `DIVERGENCE.md`'s "per-notification fork did not port" paragraph rewritten | measured by the existing barrier test: a batch's handlers run together, as SHORTCOMINGS already said (`EKB-0204`). **Left for you:** `EventDispatch.drain_once`'s docstring says the same stale thing; a `src/` change, so not made |

## time as is (EKB-0384-)

The framework stops converting time, asked on 2026-10-19 ("a very big bad dracula"). Ledger:
`docs/TIME-AS-IS-LEDGER.md`.

| decision | why |
| --- | --- |
| **`to_instant` and `to_storage` are removed from `ekbatan.clock`** (and `_as_utc` with them), not kept as user helpers | nothing in the library should invite conversion; they were only ever in `ekbatan.clock.__all__`, not the top-level package. Examples, tests and docs no longer use them. An application that wants a conversion writes the one line itself |
| **`timestamp_column()` is kept, as a column type only**: zone-less `DateTime`, `DATETIME(6)` on MySQL/MariaDB; no bind or result processing (its class is now `_Timestamp`) | it does something real besides converting - MySQL's bare `DATETIME` drops the fraction silently. Measured: naive UTC with microseconds round-trips exactly through it on all four databases, both modes |
| **schemagen still forces zone-less `TIMESTAMP`/`DATETIME` to `timestamp_column()` by default** | it now converts nothing, so the forced type only carries the precision and the notes about columns that differ from `DATETIME(6)`; dropping it would also drop those notes. Generated `schema.py` files are unchanged |
| **`uuid_instant(id)` / `minted_at` return naive UTC**, built by adding the id's milliseconds to a naive epoch | it is not a stored value but a time made by the framework; naive UTC matches the default clock, so `clock.now() - id.minted_at` works. Before: aware UTC through `fromtimestamp(..., tz=UTC)` |
| **CDC epoch microseconds (`ActionEvent.from_message(timestamps_are_epoch_micros=True)`) decode to a naive value** (`epoch + timedelta(microseconds=n)`), and zone-less CDC values are no longer stamped UTC | Debezium's number is the zone-less column's own value; naive is exactly that value. Also exact now - the old `fromtimestamp(n / 1e6)` went through a float |
| **Resolved by the user's design (2026-10-22): the time kinds - see "time kinds (EKB-0419-)" below.** An event field declared `Instant` is written with `Z`; a plain `datetime` field is refused; the `.replace(tzinfo=UTC)` advice is gone from the docs. Was: **Open, user undecided (2026-10-20); nothing changed.** event JSON (`_json._datetime`) writes a value as it is: naive -> `LocalDateTime` text, zero offset -> `...Z`, other offsets kept | the `astimezone(UTC)` on a zero-offset value moved nothing, but it was a conversion call; removed. **Worth a look:** a time straight from the default clock is now naive, so a payload field filled from `clock.now()` is written as `LocalDateTime` (`"2026-01-01T10:00:00"`), not `Instant` (`"...Z"`). A Java consumer whose event declares `Instant` would refuse it. Documented on "Running beside a Java service": give such a field `clock.now().replace(tzinfo=UTC)` in your own code |
| a clock giving aware values with zone-less columns now fails in the dispatcher (`TypeError` comparing naive and aware) | the rule: the application keeps clock and columns consistent; documented in "What you give is what is stored" and AGENTS.md. No friendlier error was added (that would be the framework inspecting zones) |
| the migrator's own `installed_on` text stamp (`datetime.now(UTC)...Z`) is left as is, allow-listed in the guard test | it is a value the migrator writes about itself into a text column, not a time an application handed in; the migrator was reverted once by the user (EKB-0310), so it is left alone |
| parity probes under `parity-probes/` still import `to_instant`/`to_storage` and are not updated | they are recorded evidence from earlier rounds, not run by CI |
| the test fixtures' `wallets` table uses `timestamp_column()` (was plain `DateTime`), and the suite's MySQL migration was regenerated (`DATETIME(6)`) | so a model's microseconds round-trip in the suite on MySQL too, as they do in every example |
| **db-scheduler persistence converts nothing either** (`JdbcTaskRepository._bind` returns the value; the read side no longer stamps UTC) and **its `SystemClock` gives naive UTC**; `SettableClock` keeps what it is given (it used to refuse a naive value) | the same rule, asked for the port too. The default clock matches the recommended zone-less `TIMESTAMP`/`DATETIME(6)` table, so what is written reads back equal and compares with the clock |
| **schedules still do zone arithmetic**: `Daily` and cron read a naive time as UTC to find "08:00 in Rome", and answer in the kind they were asked in (naive UTC for naive, aware UTC for aware) | not persistence: a schedule in a named zone cannot be computed without knowing which instant a time is. Nothing a schedule returns is written back other than as returned. The guard test covers the persistence path only (`clock.py`, `jdbc/`, `serializer/`, `scheduled_execution.py`, `client.py`) |
| **Not applicable (user, 2026-10-20): "python and java would not share a single db", so a Python worker never runs on Java's `timestamptz` table.** The aware-clock note stays on the table's page for any zoned table; the pages that offered sharing the table with Java were trimmed (async dracula fixes, below). Was: **worth a look: a `timestamptz` `scheduled_tasks` table now needs an aware clock** (`customize_scheduler(lambda b: b.clock(AwareUtcClock()))`, documented on the table's page). The Java migration creates `timestamptz`, so a Python worker beside Java on Java's table must set it | with the naive default clock the scheduler compares naive with the aware times PostgreSQL hands back and raises `TypeError` (measured: a one-second job on a `timestamptz` table still ran, but every restart logged the `TypeError` while reconciling the recurring job, and the suite's zoned PostgreSQL scheduler tests failed until given an aware clock). Before, the read side stamped UTC and the write side normalised, so either column type "just worked" - that was exactly the automatic conversion now removed |
| task data serializes a `datetime` with `isoformat()` as given (was `astimezone(UTC).isoformat()`) | the same rule; a naive time serializes without an offset |
| the `ExecutionTimeAndId` token counts seconds from the epoch in the value's own terms (naive epoch for a naive time) and decodes to naive UTC | it was `astimezone(UTC)`, which reads a naive value as local time - wrong under the new default |
| the suite's clock and `NOW` follow the table: naive for the zone-less shape and for MySQL/MariaDB/SQLite (no zoned type), aware UTC only for PostgreSQL's zoned shape | the application's job under the rule, done the way an application would do it |

## async dracula fixes

Every row of `docs/ASYNC-DRACULAS.md`, approved by the user on 2026-10-20: sync and async differ
only where async forces it. Ledger: `docs/ASYNC-DRACULAS-FIX-LEDGER.md`.

| decision | why |
| --- | --- |
| **Row 5: async `find_one_where` / `exists_where` still ask every shard at once**, but take the answers in shard order (`_first_answer`): the first shard with the row wins, the reads after it are cancelled, and a shard fails the call only when every shard before it said "not here" | the user asked for sync's answer (stop at the first shard in order with the answer, so a broken shard sync never reaches cannot fail the read); this gives exactly that answer and keeps the speed-up. The one remaining difference: a shard after the answering one may already have run its query before it is cancelled, where sync never asks it. Sequential reads would remove even that, at one round trip per shard |
| Row 6: the shard note is made once, in `_RepositoryCore._shard_failure`; a sync read across more than one shard adds `ekbatan: reading shard 1:0 (mexico) failed, so the whole read failed`, async adds "and the reads still running on the other shards were cancelled" | one copy of the rule; the extra clause is only true in async. As in async, a single-shard read gets no note. A side effect in both modes: `find_one_where` meeting two matches on one shard of several now carries the note too (its `ValidationError` is raised inside the shard's read) |
| the sync repository's `_readers()` was removed | nothing calls it after rows 5 and 6; the sync scatter reads go through `_on_each` / `_first_answer`, the async ones' twins |
| Row 17: `ekbatan.aio` exports exactly the twins of what `ekbatan` exports: `AsyncKeyedLockProvider`, `AsyncLease`, the three SQL providers and `AsyncSqlKeyedLockProviderBuilder` - not `AsyncSqlKeyedLockProvider`, not Redis | the sync package exports `SqlKeyedLockProviderBuilder` but not `SqlKeyedLockProvider`, and Redis lives in its own module on both sides; the new surface test compares the two packages both ways (`async_url`, which has no sync twin, is the one allowed extra) |
| Row 1: each async repository's `to_row` / `from_row` is the sync twin's text, docstrings included, at the same place in the class; the async REST and job-worker module docstrings now carry the sync twin's "the mapping is written" paragraphs | "exactly as the sync twins write them". On the way the sync pg REST repository had one comment wrapped narrower than its MySQL/MariaDB twins; re-wrapped so the three async copies stay identical for the setup guide |
| Row 10: the REST pairs keep only `find_for_wallet` (sync lost six helpers, async lost `find_all_by_owner` and `total_balance`); the job-worker pair keeps `find_open_below` and `find_for_wallet` | "keep only what each example uses". **Worth a look:** the REST examples no longer show a query of their own over `readonly_dbs()`; the SQLite pair (which keeps every helper, all tested) and the sharded pair still do |
| Row 12: the setup guide now shows the async `app.py` per database (it was one file for three) | each names its own lock provider now, so the three files differ by one name each, as the sync ones do |
| `EKB-0395` (row 11) also carries row 12's `app.py` edits | both rows edited the same import lines of the same three files; `EKB-0396` holds the rest of row 12 (setup guide, its test). Noted rather than rewritten, as history is not rewritten here |
| Row 2: the stipend's work runs on the **handler loop**, over the handler's registry and executor (`handlersConfig`), which gain the wallets repository and `DepositMoneyAction`; `WalletService` gains `handler_wallets` | an async pool belongs to the loop it is used on, and the requests' loop is uvicorn's; a second loop and pool just for a daily job would be more to close for no gain - the job worker shares one loop between its jobs and its handler for the same reason. `handlersConfig` stays at five connections |
| Row 2: the sync REST twins had **no stipend test**; one was added to both twins of each REST pair (a scheduler whose clock says a day has passed runs the row once), the async one also recording that the work ran on the handler loop | so the twins' tests stay the same; the async check is the "really runs async" part |
| Row 2: the async SQLite `start()` is now `async def` and waits for the stipend's first run with `await asyncio.sleep`, as the sync `start()` waits with `time.sleep` | waiting inside the event loop must not block it - a difference async forces. Its callers (`drive`, `__main__`) await it |
| Row 3: the 503 test was added to **both** twins of every REST, saga and sharded pair (it holds every request connection, then a read waits out `poolTimeout`: 5 s), not only to sync | adding it to sync alone would have made a new difference; it goes red on sync without the handler (a raw `TimeoutError` reaches the test) |
| Row 3: the sync saga and sharded `primaryConfig`s get `poolTimeout: 5` but no `poolSize` line | they had none; the default is ten, the async twins' value |
| Row 3: in the docs the two caps moved to a both-modes section ("Cap the load, in both modes" on Wiring with FastAPI); "Going async" now says the error mapping and the door cap stay as they are | they are no longer async additions |
| Row 8: the async Tehran test checks four pools (requests, lock, handler, jobs), each on its own loop, and closes the service on the loop the request pool was used on | the async service has the handler's pool the sync one lacks; proven red with `options: -c statement_timeout=0` in place of the UTC setting, where the old settings test alone stayed green |
| Row 4: the sync sharded example's lock is placed exactly as the async twin's: `lockConfig` on the global member only, one provider, the key taken whichever shard the wallet is on; the settings test also checks the lock pool's size, its UTC session and the provider | the user chose to add the lock to sync rather than remove it from async; the earlier decision ("async examples: sharded, saga, job worker", the lock row) said the sync sharded example has none - no longer true |
| Row 19 kept by choice (the user) | one `ekbatan.yaml` names the sync driver; the async registry connects with its async default |
| time as is, (a): only the scheduler-table parts of "Running beside a Java service" were trimmed | as asked. **Worth a look:** the same page's "What to set: the schema (`eventlog`)" step and its "Migrations" note also speak of one shared database; left, since they concern the events tables and the request was about the scheduler table. **Done (`EKB-0441`, review fixes):** the page now says the two never share a database; the schema step, the shared-tables and Migrations sections, the SQL-lock sharing and the config-file section are gone, and every other page that offered one shared database was trimmed |
| pre-existing, not ours: `test_routing.py`'s async MySQL/MariaDB runs report one `PytestUnraisableExceptionWarning` (aiomysql's `Connection.__del__` after its loop closed) | present with the new tests deselected too; not investigated further |

## typed records (EKB-0408-)

The user's design (2026-10-20): schemagen writes a `TypedDict` `<Table>Record` beside each table,
the mapping methods become `to_record` / `from_record`, and repositories are generic in the
record. Ledger: `docs/TYPED-RECORDS-LEDGER.md`. The calls taken on the way:

| decision | why |
| --- | --- |
| **A module from an older schemagen refuses to import** (added after the run, by the main session): `SCHEMA_FORMAT = 2` is written into every generated module and passed to `check_fingerprint`; a module with none, or another number, raises `ValidationError` saying to regenerate - also in an installed wheel, where the migrations half stays quiet | the agent hit a pre-records `schema.py` that `make` did not rebuild, because the rule depends only on the migrations; it would have imported quietly without the records the repositories name. Raise the number whenever the generated shape changes |
| **Type parameters `[M, R, K]`**: `ModelRepository[Wallet, WalletsRecord, ShardedId[Wallet]]`, and the same for `EntityRepository`, `AbstractRepository` and the `ekbatan.aio` twins | Java's order is model, record, table, id (`ModelRepository<Wallet, WalletsRecord, Wallets, UUID>`); the table here is a `Table` value passed to `__init__`, not a type, so it is the one parameter left out |
| **Renamed outright, no deprecated aliases** for `to_row` / `from_row` | the project is unreleased; an alias would keep the untyped name alive in every search result |
| `EventEntity.to_row`/`from_row`, `EventNotification.to_row`/`from_row` and `ActionEvent.from_row` **keep their names** | they are the framework's own row classes and a consumer's factory for a CDC message, not a repository's mapping; Java names none of them `toRecord`. **Worth a look** if one name everywhere is wanted |
| The framework hands `from_record` **the row's own `RowMapping`**, not a `dict` copy, cast to the record type | no allocation per row (the performance tests read thousands); `RowMapping` answers `record["col"]`, `in`, `.get`, `.keys()`. Without `RETURNING` (MySQL) it is the dict `to_record` wrote |
| **`FetchResult`'s terminals now hand the mapper the row's mapping**, not the `Row` | so `fetch(self.from_record)` is typed and right. A behaviour change for a mapper that reads a row by position (`row[0]`), which a `RowMapping` refuses; by attribute (`row.currency`) still runs but is a mypy error, by name (`row["currency"]`) is the way. Unreleased, so no shim. `unique(strategy)` still gets SQLAlchemy's `Row`, as SQLAlchemy documents |
| A mapper is typed `Callable[[R], T]` with `R` bound to `Mapping[Any, Any]` | lets `fetch(self.from_record)` take the record type, and gives a lambda a `Mapping` row (so `row.x` is caught). The statement's columns are **not** checked against `R` - nothing can check SQL against a type before it runs; the docs say to select the table's columns when handing it `from_record` |
| `columns_by_field_name(model) -> R` casts | it is built from the dataclass's fields at run time, so mypy takes it on trust; the docs say the key-by-key mapping is the checked one |
| **JSON columns are `ekbatan.schema.JsonValue`** = `Mapping[str, JsonValue] \| Sequence[JsonValue] \| str \| int \| float \| Decimal \| bool \| None` | what a JSON column really reads back: the framework's engine reads fractions as `Decimal`, any other engine as `float`. `Mapping`/`Sequence` rather than `dict`/`list` so a `dict[str, Decimal]` can be written without a cast. **Worth a look:** the framework's serializer also *writes* dataclasses, uuids and datetimes inside JSON; a record typed by what reads back makes writing those a mypy error (convert first, or give the column a converter) |
| **An enum column is a `Literal` of its values** (`Literal["NEW", "DONE"]`) | the precise type, as jOOQ generates an enum class. **Worth a look:** writing `state.name` (typed `str`) into one needs a cast or a converter; no example has an enum column |
| **A column the database fills is `NotRequired`**: a server default (MariaDB's reported `NULL` default excluded), an identity, a computed column, an auto-increment | an insert may leave it out, and must leave out a computed one; requiring them would make a correct `to_record` a type error. The price: mypy does not catch such a column left out |
| **`Any`, with a note, only where nothing can say**: `sql_type(...)`, PostgreSQL `BIT` (psycopg reads `str`, asyncpg its own `BitString`), a `sqlalchemy-type` whose `process_result_value` converts but is not annotated | the user's rule was "never silently `Any`"; these are said in the run's notes. A converter that says nothing is an **error**, not `Any`, as asked |
| Dialect types with no `python_type` are typed by **measurement**, on psycopg, asyncpg, PyMySQL and aiomysql: `INET` -> `IPv4Address \| IPv6Address`, `CIDR` -> `IPv4Network \| IPv6Network`, `MONEY`/`MACADDR`/`TSVECTOR`/... -> `str`, `OID`/MySQL `YEAR`/`BIT` -> `int`, ranges -> `Range[...]`, `HSTORE` -> `dict[str, str \| None]` | `test_schemagen_types` now reads every case back through the framework's sync and async engines and checks it against the record; it first went red on `INET`/`CIDR` (read as `ipaddress` objects, where `str` had been the guess) and on `BIT` |
| A converter's type: **its `from_db` return annotation first, then its `Converter[DbT, UserT]` base** | the user's order. An explicit `-> Any` is accepted (it is said, not silent). A return annotation naming a type variable, or one imported only under `TYPE_CHECKING`, falls back to the base, and is an error naming the cause if there is none |
| The records' types are **imported at run time**, not under `TYPE_CHECKING` | so `typing.get_type_hints(WalletsRecord)` works (the integration test uses it). It adds no import cycle: a converter's user type lives in a module the converter's own module already imported, and schema.py imports the converter |
| Records for **the framework's tables and for views** too (`EventsRecord`, `EventNotificationsRecord`, `ScheduledTasksRecord`) | "beside each Table"; jOOQ generates them too. Unused by the framework itself |
| Names: jOOQ's PascalCase (`event_notifications` -> `EventNotificationsRecord`); another schema's table `audit.entries` -> `AuditEntriesRecord`; a leading digit gets `T` (`2024_sales` -> `T2024SalesRecord`). Two tables that would share a record **stop the run**, as two sharing a constant do | consistent with the constants' rules |
| A user type or converter **named like a record, or like another import** (`UUID` from two modules), is imported under an alias (`myapp_model_UUID`) | the existing alias rule for converters, extended to the records' types. **Not fixed:** a *table* named like an import (`text` -> `TEXT`) still shadows SQLAlchemy's - the known issue from the schemagen tests (P2), left as it was |
| A column named like a type its record uses (`date: date`), or not an identifier (`class`), makes that record the **call form** `TypedDict("X", {...})` | in a class body `date: date` names the field, not the type |
| `__all__` lists the tables, then the records, then `metadata` | tests compared exact lists; tables first keeps the old order readable |
| Existing test converters with no annotations were **annotated** (`-> Decimal`, `-> object`, `-> Tag`) | the new rule refuses them otherwise; none of them was about annotations |
| The historical ledgers, dracula area files, behaviour-parity notes and `parity-probes/` outputs **keep `to_row` / `from_row`** | they record what was true when they were written |
| **Found while running: a `schema.py` generated before `EKB-0408` is not regenerated by `make`** - its Makefile rule depends on the migrations and `pyproject.toml`, not on the ekbatan version, so a project that upgrades keeps a module without records until it is deleted (`make clean`, or `rm schema.py`). Hit on seven pip/Poetry example copies; deleted and rerun, all green. **Not fixed - worth a look:** folding the schemagen version into the fingerprint would make the stale module fail loudly on import, as a changed migration does |

## a sync transaction belongs to its thread (EKB-0415)

| decision | why |
| --- | --- |
| **Decided by the user (2026-10-21): another thread that finds a sync transaction gets `TransactionThreadError`** - not a shared connection, and not a connection of its own as Java's `ScopedValue` would give it | `asyncio.to_thread` copies the caller's context, so the thread found the transaction and used its connection while the opening thread did (reproduced on SQLite). Nobody wants concurrent writes in one transaction, so it is a mistake to catch, not a feature: sharing corrupts, and a connection of its own would leave that work behind on a rollback |
| The check lives in `TransactionManager.current()` and `connection()`, the two ways a repository reaches the ambient transaction; the `Transaction` object handed to the `with` block is not checked | passing that object to another thread is an explicit act, not an inherited context |
| Async transactions are unchanged: tasks started inside share the connection and take turns | they run on one thread, and SQLAlchemy's async connection serialises them; tested since EKB-0361 |

## schemagen: qualified sqlalchemy names (EKB-0416)

The bug: a table's constant is its name in capitals, and the generated module imported
SQLAlchemy's types by their own capital names. A table named `text` replaced `TEXT`, and the next
table writing `TEXT()` failed on import: `TypeError: 'Table' object is not callable`. The
user's fix (2026-10-21): `import sqlalchemy`, and every SQLAlchemy name written through it -
`sqlalchemy.Table`, `sqlalchemy.Column`, `sqlalchemy.TEXT()`, `sqlalchemy.text(...)`,
`sqlalchemy.types.NullType()`. `SCHEMA_FORMAT` is 3. The calls taken on the way:

| decision | why |
| --- | --- |
| **A table named `migrations_fingerprint` or `schema_format` is exported as `MIGRATIONS_FINGERPRINT_` / `SCHEMA_FORMAT_`**, as a keyword already is (`class` -> `CLASS_`) | the re-check of every unqualified name found it: the table replaced the module's own constant, and the stale check then always fired (the fingerprint) or always refused (the format). Option (a) of the open question under "schemagen tests". It changes an exported name only for a table that never imported cleanly |
| **A converter or `sqlalchemy-type` named like a table's constant is now imported under an alias** (`from myapp.clash import T as myapp_clash_T` beside a table `t`) | found the same way: the old alias check looked at SQLAlchemy's names and other user imports, not at the tables, so `T = Table(...)` replaced the converter before the column that calls it. The check now also covers record names. Pinned by `test_a_converter_named_like_a_table_s_constant_is_aliased`, red on the old code |
| A user type named like a SQLAlchemy name (`JSON`) **is still aliased** (`myapp_types_JSON`), though the module no longer imports SQLAlchemy's bare | the task kept the existing clash handling; and a bare `JSON` in a schema module would read as SQLAlchemy's. **Worth a look** if the alias now seems needless |
| **Dialect modules stay `from sqlalchemy.dialects import postgresql`**, written `postgresql.JSONB()` | checked for clashes: a constant is all capitals and a record ends in `Record`, so neither can be `postgresql`, `mysql` or `sqlite`; a user type of that name is aliased (they are reserved). Tables named `postgresql`, `mysql`, `sqlite` are in the new tests |
| **A record's types from SQLAlchemy stay imported bare**: `from sqlalchemy.dialects.postgresql.ranges import Range, MultiRange` | mixed case, so no table constant can be one; and the records' existing alias rule already covers a clash. The new test asserts that nothing SQLAlchemy imports bare is all capitals |
| Nested names in a type's `repr` are qualified too: `postgresql.DOMAIN('d', sqlalchemy.INTEGER())`, `postgresql.HSTORE(text_type=sqlalchemy.Text())` | through one helper, `qualify()` in `_types.py`, which also writes a forced `name = "Text"` as `sqlalchemy.Text()`. The settings file still takes `Text`, `String(36)`, `postgresql.INET` as before |
| **The test's names are read off schemagen's source**, plus every column type `sqlalchemy` and the dialect modules export, plus every name the generated module binds itself (108 SQLAlchemy names; ~120 table names after lower-casing and adding the module's own) | the type table writes any type it has no rule for as its `repr`, so "every name schemagen can write" includes every exported type; the module's own names catch the fingerprint and format clashes |
| The integration twin leaves out a table named **`citext` on PostgreSQL** | there a table is also a type, and the citext extension's type has the name in the same schema; PostgreSQL refuses it. The pure render test covers the name |
| **The type cases moved** from `test_schemagen_types.py` to `tests/schemagen_type_cases.py`, unchanged but for the qualified sources | the new integration test builds its table from the same cases |
| Docs and website show the generated snippets qualified, and say why in one paragraph on the schemagen page; hand-written schemas (`repositories.md`, the SQLite examples, `tests/fixtures/schema.py`) keep their plain style | they are not generated, and a table there cannot shadow anything without the author seeing it |

## time kinds (EKB-0419-)

The user's design (2026-10-22), closing problem (c) - event JSON times sent to Java lacked the
`Z`. Ledger: `docs/TIME-KINDS-LEDGER.md`. Not redesigned: three `NewType` labels in
`ekbatan.time`, `Clock.instant()` as the one reading, models and framework times `Instant`,
persistence as is, schemagen records by kind (no column comments read), JSON by declared label.
The calls taken on the way:

| decision | why |
| --- | --- |
| **The labels live only in `ekbatan.time`**, not re-exported from `ekbatan` | the design names the module and the refusal message points to it; a top-level export would also need an `ekbatan.aio` twin (`test_async_engine` compares the packages) for no gain |
| ~~**An action's shorthand `self.now()` is renamed `self.instant()`** (sync and async), no alias~~ **Superseded by `EKB-0426`: the shorthand is gone; an action reads `self.clock.instant()`, as Java does** | it was "shorthand for `self.clock.now()`"; a shorthand under the old name would keep `now()` alive. The guard test checks `Action` and `AsyncAction` have no `now` |
| `VirtualClock(instant=...)` (was `now=`); `pause_at` / `resume_from` take a plain `datetime`, kept as given, and `instant()` hands it back labelled `Instant` | "what it's given is kept as given"; a test writes `VirtualClock(datetime(2026, 1, 1, 10))` without wrapping. An aware value given stays aware - the label is not checked, as the design says |
| **db-scheduler's clock keeps Java db-scheduler's name, `now()`**, returning the naive-UTC moment it already did; no code changed in `db-scheduler/` | it is a port of db-scheduler, whose `Clock` has `Instant now()`; it is a separate distribution that does not depend on `ekbatan`, so it cannot use `ekbatan.time` - its `now()` stays typed `datetime`. Where an example hands it a time from ekbatan's clock (`SettableClock(SystemClock().instant() + ...)`) that is a plain `datetime` to it. The scheduled-tasks page says which clock has which name |
| The time-as-is guard's allow-listed clock line now reads `return Instant(datetime.now(UTC).replace(tzinfo=None))` | the same single allowance, re-worded because the value is now labelled; the list did not grow |
| **A `datetime` nothing declares a kind for is refused too** - a value inside `dict[str, Any]` params, a bare value given to `dumps`, a key an overridden `payload()` adds. ~~and a value inside a user's own `json_column()`~~ **Changed by the review fixes (`EKB-0436`): a user's own JSON column writes a `datetime` as it is given again** (persistence writes what it is given; the engine's writer is now `column_dumps`, `canonical(by_value=True)`) | the design refuses a plain `datetime` field because nothing says which kind it is; an undeclared one says even less. Message: `... is a datetime nothing declares a kind for, so it cannot be written: put it in a dataclass field and declare it Instant, LocalDateTime or OffsetDateTime (ekbatan.time)`. **Worth a look** for the user's JSON columns: a `dict` holding a `datetime` used to be written as text with no zone |
| Refusal wording: `event <Class> field <name>` for a `ModelEvent`, `dataclass <Class> field <name>` for a params or nested dataclass, `action params['key']` / `field reminders[1]` inside containers | the design gave the event wording; the others follow it and name the thing to fix |
| **Reading an `Instant` takes only text ending in `Z`**; text with an offset (even `+00:00`) or none is refused, not converted | Java always writes an `Instant` with `Z`. Turning `+02:00` text into UTC digits is a conversion (`astimezone`), which the time rule forbids. Java's own reader would accept an offset - **worth a look** if a non-Ekbatan producer writes offsets into an `Instant` field |
| Reading a plain `datetime` field is left as it was (the zone the text has) | writing is where the refusal belongs; a class with a plain field cannot have written the row in the first place |
| An event is written through `canonical_event(event)`: `payload()`'s keys are matched back to the class's fields by their Java names, and each value takes its field's declared kind | `payload()` returns a plain `dict`, which has lost the annotations. An override of `payload()` keeps working for everything but an undeclared `datetime` |
| Declared kinds reach through nested dataclasses, `list` / `tuple` (positional too) / `set` / `Sequence`, `dict` / `Mapping` (keys and values), `X \| None`, `Annotated`, `type` aliases, `TypedDict`, `TypedValue[X]` subclasses and a user's own `NewType` over a label | the design asked for nested, lists/tuples, dicts and Optional; the rest cost a line each. A union of two kinds picks the one whose class fits, else it is undeclared |
| **Decoding a `Sequence[X]` field now rebuilds its items** (it handed back the raw list) | found by the round-trip test: `Sequence[OffsetDateTime]` came back as strings |
| schemagen: a column forced to `OffsetDateTime` that is **already zoned** keeps the type table's own type (`postgresql.TIMESTAMP(timezone=True, precision=3)`), not `DateTime(timezone=True)`; a zone-less column forced to it becomes `sqlalchemy.DateTime(timezone=True)` | "keep precision behaviour": the built-in `TIMESTAMPTZ` rule must not drop a `TIMESTAMPTZ(3)`'s precision. `TIMESTAMPTZ` columns generate exactly the source they did before; only the record changed |
| The record's kind comes from the column type itself - a zone-less `DateTime` is `Instant`, a zoned one `OffsetDateTime` - so it holds with `default-forced-types = false` and for a column forced to `timestamp_column` or `DateTime`; only `LocalDateTime` needs the forced type | one rule wherever a timestamp comes from. `timestamp_column` stays a valid forced name (record `Instant`) |
| PostgreSQL range types (`TSRANGE`, `TSTZRANGE`) stay `Range[datetime]` | not in the design; a range's bounds are not a field the JSON or a model reads |
| The built-in forced types are five: `Instant` (zone-less `TIMESTAMP\|DATETIME`) and `OffsetDateTime` (`TIMESTAMPTZ`, PostgreSQL) replace the one `timestamp_column` rule | as the design says; the schemagen page's table and `test_the_built_in_forced_types_are_the_five_the_docs_list` follow |
| **Every example's `WalletOpenedEvent` gains `opened_at: Instant`**, set from the wallet's `created_date`, and each example's tests check the outbox writes it with `Z` | the task asked that examples' events declare `Instant`, and none had a time field. **Java's examples' `WalletCreatedEvent` has no time field** - a difference between the two ports' examples |
| `ekbatan.codegen` writes a labelled model field's setter with its label (`def opens_at(self, value: LocalDateTime)`) - it wrote `Any` for any `NewType` | found while checking a `LocalDateTime` model field; pinned in `test_codegen` |
| The repository's by-name mapping reads a label as the class under it (`Instant` -> `datetime`), so a time column is still handed over untouched without a per-row check | found by `test_performance`'s "a column that needs no conversion is not inspected per row" |
| Ruff's `RUF009` (a call in a dataclass default) is told the labels are immutable calls (`extend-immutable-calls` in `pyproject.toml`) | `opened_at: Instant = Instant(x)` is the plain value `x`; a project of the user's own that runs ruff needs the same three lines - **worth a look** whether the docs should say so |
| `ActionEvent` (the CDC view) types its dates `Instant` | framework-written times; the value is whatever the CDC message held, as before - a label, not a check |
| The parity probe `json_formats_probe.py` was updated to declare each value's kind and re-run: `odt.write` is now 3000/3000 (was 2984: an aware value at offset zero was taken for an `Instant`), and DIVERGENCE's "OffsetDateTime at offset zero" corner is gone | the labels fix it; Java's recorded output was not touched |
| ~~The "Time kinds" table on the Always-UTC page has a **Java** column~~ **Removed by the main session (EKB-0425)**: the Python site explains features without Java; the Python-to-Java mapping stays on the "Running beside a Java service" page, where it is a real working-together fact | the user's rule: the Python website is independent of Java except for Java+Python collaboration |

## schemagen completeness (EKB-0427-)

2026-10-22, unattended. The user: "it's very very very very important, it needs to be done
thoroughly, we don't want to limit the capabilities of the framework users". Ledger, with the
feature matrix measured by round trip on all four databases:
[SCHEMAGEN-COMPLETENESS-LEDGER.md](SCHEMAGEN-COMPLETENESS-LEDGER.md).

**The ones worth a glance first:**

| decision | why | the other way |
| --- | --- | --- |
| **MySQL: a `CURRENT_TIMESTAMP` / `NOW()` default (and `ON UPDATE`) of a column forced to `timestamp_column()` is written `CURRENT_TIMESTAMP(6)`**, with a note | the column becomes `DATETIME(6)` by the EKB-0321 decision, and MySQL refuses `DATETIME(6) DEFAULT CURRENT_TIMESTAMP`: the module's `create_all` failed on the most usual MySQL timestamp there is (`TIMESTAMP DEFAULT CURRENT_TIMESTAMP`). MariaDB makes the same change itself, silently | keep every `Instant` column's own type (`DATETIME`, `TIMESTAMP`) and only label the record - reverses EKB-0321 |
| **Python enum classes are not generated** - a PostgreSQL or MySQL enum stays `sqlalchemy.Enum("a", "b", name=...)` and its record `Literal["a", "b"]` | jOOQ generates a Java enum and sqlacodegen a Python one; binding one changes what a read returns (an enum member, not a `str`), and a record typed `Mood` refuses a plain `"happy"` under mypy - every model with an enum column would change. A `StrEnum` would keep reads comparable to strings. **Proposal below**; `Literal` already gives mypy the values | generate `class Mood(StrEnum)` per enum type, bind `Enum(Mood, values_callable=...)`, record `Mood` - opt-in by a setting |
| **A sequence of its own (PostgreSQL, MariaDB) is a module constant**, `STANDALONE_SEQ = sqlalchemy.Sequence("standalone_seq", ..., metadata=metadata)`, with every option PostgreSQL would not choose by itself; a column defaulting to `nextval` of it keeps the text default and no longer carries a `Sequence(...)` of its own | jOOQ generates `Sequences`; before, an unused sequence was not generated and a used one lost START/INCREMENT/MIN/MAX/CACHE/CYCLE. `select(STANDALONE_SEQ.next_value())` now works. A `SERIAL`'s sequence stays on its column | - |
| **PostgreSQL partitions are not written as tables**: the parent gets `postgresql_partition_by=...`, and each partition is created after it by `sqlalchemy.event.listen(PARENT, "after_create", sqlalchemy.DDL("CREATE TABLE ... PARTITION OF ... FOR VALUES ..."))`, with a note | before, the parent was built unpartitioned and each partition as an unrelated table. Queries go through the parent; jOOQ lists partitions as tables | export each partition as a `Table` too, in a metadata `create_all` does not build |
| **A materialized view goes in `view_metadata`**, like a view | it was in `metadata`, so `create_all` built a *table* by its name | - |
| **MySQL and MariaDB tables carry their options**: `mysql_engine`, `mysql_default_charset`, `mysql_collate`, `mysql_row_format`... (every one reflection reads but the comment and the AUTO_INCREMENT counter); this adds lines to every MySQL/MariaDB table, the examples' too | a `latin1` table's text columns were rebuilt `utf8mb4`, a MyISAM/Aria table InnoDB, a `utf8mb4_unicode_ci` table at the server's default collation | write only options that differ from the server's defaults - but the server's defaults are not the migration's, and a test server's may differ |
| **Index elements are written as the database writes them** (`sqlalchemy.text("a DESC NULLS LAST")`, `text("(lower(`name`))")`, `text("name COLLATE NOCASE")`) only where a plain column name would lose something; plain columns stay names | a sort order made the module's `create_all` fail on PostgreSQL, a MySQL functional index wrote `Index("t_func")` with no column (also a failure), and collations, DESC and SQLite expression indexes were lost | - |
| **A key column is never `X \| None` in its record** | SQLite alone reflects a key column as nullable; the module declares it a key, so NOT NULL | - |
| **SQLite: a type name SQLAlchemy reads only by affinity (`ANY`, `UNSIGNED BIG INT`, none) is written as SQLite spells it**, `sql_type("ANY")`; its record follows SQLite's affinity rules (INTEGER -> `int`, TEXT -> `str`, REAL -> `float`, NUMERIC/BLOB/none -> `Any`) | a STRICT table refuses the `NUMERIC` it was rebuilt with; an untyped column made `create_all` fail | - |
| **Records of spelled types the drivers agree on are typed**: PostgreSQL `xml`, `tsquery`, `jsonpath` -> `str` (arrays `list[str]`), MySQL/MariaDB spatial -> `bytes`, MariaDB `inet4`/`inet6` -> `str`; the rest (`point` on PostgreSQL: text on psycopg, `Point` on asyncpg) stay `Any` with the note | measured on psycopg, asyncpg, PyMySQL, aiomysql | - |
| **A PostgreSQL domain is written whole** (`postgresql.DOMAIN("d", <base with length>, collation=, default=, constraint_name=, check=, not_null=)`), and a domain column gets its own NOT NULL and default, not the domain's | reflection kept the domain's name and base only, so `create_all` on an empty database built a domain with no check, and moved the domain's NOT NULL/default onto every column | - |
| A domain with several checks is written with one, joined by AND (names lost); a NOT NULL domain's named check loses its name | SQLAlchemy's `DOMAIN` takes one check, and writes `CONSTRAINT name` before `NOT NULL`, naming the NOT NULL. A note says so each time | - |
| **What no module can declare is said, one note per kind**: functions, procedures, triggers, composite types, extensions (PostgreSQL); routines, triggers, events, invisible columns, unenforced checks, index comments, invisible/ignored indexes, system-versioned tables (MySQL/MariaDB); triggers (SQLite) | it was silent; the user's rule is that no omission is silent. The note for a composite type and an extension says what to create before `create_all` | build them through `DDL` events - possible for some (a composite type, `ALTER TABLE ... ALTER INDEX ... INVISIBLE`), not done unattended |
| `SCHEMA_FORMAT` is 5 | the module gained sequence constants, partition DDL, dependencies and table options | - |
| PostgreSQL inheritance: the child keeps every column and gets `postgresql_inherits=` plus `CHILD.add_is_dependent_on(PARENT)` | create_all orders by foreign keys only; `capitals` sorts before `cities` | write only the child's own columns - the `Table` would then not have the inherited ones to query |
| MySQL `SPATIAL` index's reported prefix of 32 is ignored; MySQL `SRID n` is kept in the spelled type, `sql_type("point SRID 4326")` | - | - |
| Synthetic keys (jOOQ `syntheticObjects`) are not supported | no reflection to read, and a hand-written `Table` beside the module covers it | a `synthetic-keys` setting |

**Proposals - big new subsystems, not built. Declined by the user (2026-10-22): none of the three, at least not for now** - no routines, no Python enum classes, no composite-type classes. Kept below only as the record of what was considered; do not build them without asking.

1. **Routines (jOOQ's `Routines`).** jOOQ generates a class per stored function and procedure,
   typed by its parameters, so `Routines.addOne(cfg, 1)` is checked at compile time. Here:
   read `pg_proc` / `information_schema.PARAMETERS`, map each parameter and result type through
   the type table, and write `def add_one(x: int) -> FunctionElement[int]: return
   sqlalchemy.func.add_one(sqlalchemy.literal(x, sqlalchemy.INTEGER()))` (a `type_=` keeps the
   result typed), and a `CALL` helper for procedures. A few hundred lines plus overloads,
   OUT/INOUT parameters, set-returning functions and defaults. **Recommendation: yes, later, as
   its own task** - it is the one jOOQ feature here users of stored functions will miss; today
   `sqlalchemy.func.add_one(...)` works untyped and the note says so.
2. **Python enum classes for enum types** (above). **Recommendation: an opt-in setting**
   (`enum-classes = true`) writing `StrEnum`s - reads stay `==` to strings, the records name the
   class; off by default so no model changes under anyone.
3. **Composite types / UDT classes.** jOOQ generates a record class per composite type. Here: a
   `TypedDict` or dataclass per type and a `TypeDecorator` parsing psycopg's text `(Main,123)` and
   asyncpg's `Record`. **Recommendation: no** until someone asks - rare in services, and the
   driver formats differ; the note says how to create the type for `create_all`.

## review fixes (EKB-0436-)

2026-10-22, unattended: the findings of independent reviews of the time work, the transaction
thread guard, the docs and the examples, each proven by a run. Ledger:
[REVIEW-FIXES-LEDGER.md](REVIEW-FIXES-LEDGER.md).

| decision | why |
| --- | --- |
| **Decided by the user (2026-10-22): no example holds the environment.** `Settings.environ` (and `build(environ)` / `create_app(environ)`) are gone from all 14 examples; `build()` calls `load_ekbatan_config(config_file)` and the placeholders are read from the process environment as the file is loaded. Tests set the variables with `monkeypatch.setenv` (module-scoped fixtures: `pytest.MonkeyPatch.context()`). Each example runs against a database whose real password is `Mk-7f3a-SECRET` and asserts it shows in no `repr`/`str` of what the service builds | replaces the first idea, `field(repr=False)`: a mapping of the whole environment kept in an object is a leak waiting for the next `vars()` or debugger |
| **Decided by the user: the loader never holds the environment.** With no `environ=`, each placeholder is looked up with `os.environ.get(name)` when it is filled (`_from_process`); a caller's own mapping is asked through its `.get`. `environ=` stays | proven by a failed load's traceback frames: on the old code `_read_file.variables`, `_fill.variables` and the INI interpolation held `os.environ` |
| **A JSON column of the application's own writes a `datetime` as given** (naive: no zone; aware: its offset, `Z` at zero) - the engine's writer is now `column_dumps` = `canonical(by_value=True)`. A field declared with a kind is still written as its kind there, and a value of the wrong sort for a declared kind is still refused | "persistence writes what it is given"; the refusal was meant for event payloads and action params, which the outbox turns into text by their declared kinds before the engine sees them. **Worth a look:** whether a declared-kind mismatch inside a user's JSON column should also be written by value instead of refused |
| **A union of two kinds (`Instant \| LocalDateTime`) is refused on write and on read** with "declared as more than one kind (...); pick one" | it was "nothing declares a kind" on write and a raw string handed back on read. `Instant \| None` is unchanged |
| An `Instant` given offset text (`+00:00`) now points to `OffsetDateTime` only; one given zone-less text points to `LocalDateTime` only | the old message offered both for either |
| **`TransactionContextError`** (new, exported from `ekbatan` and `ekbatan.errors`): a transaction block that ends in another context than it began in raises it, in place of Python's bare `ValueError: Token ... was created in a different Context`, sync and async (`_leave` in `persistence.py`) | the FastAPI sync `yield` dependency case: it can only be seen when the block ends - nothing can tell the endpoint that its work ran outside the transaction. The commit has already happened by then (the dependency's empty transaction); the endpoint's statements committed on their own. **Worth a look:** whether a new public error is wanted, or `TransactionThreadError` should carry it |
| The time guard's new patterns (`.timestamp()`, `fromtimestamp(`, `- x.utcoffset()`, `ZoneInfo(`, any `datetime.now(`) needed no allow-list change; `clock.py`'s module docstring was reworded (`datetime.now()` -> the wall clock, ``datetime.now``) so the docstring is not taken for a call | the allow-list stays exactly the two lines |
| The two stale parity probes **run again** (`now()` -> `instant()`, `to_storage` reported as removed); their recorded `.out` files are kept as history and the re-runs written beside them (`*.rerun-2026-10-22.out`) | a probe that cannot run proves nothing; overwriting the recorded run would lose what was compared then |
| `beside-java.md`: the "What to set" schema step, "The shared tables", "Migrations", the SQL keyed-lock sharing and "The config file" are gone; the `events` table comparison stays, reframed as what the Java Debezium transforms need from the Python service's own table; ids stay, reframed as read from events | the user's rule (2026-10-20): Java and Python never share a database; broker and Redis facts stay. **Worth a look:** the config-file key parity (kebab/camel, ISO durations) is now said only on the config-files page's own terms, not as a Java fact |
| T4 (schemagen forcing `OffsetDateTime` on a zone-less column, or `Instant`/`LocalDateTime` on a non-time column) was **not touched** - handed to the schemagen stream | that stream owns `src/ekbatan/schemagen/` |

## records built by keyword (EKB-0447)

2026-10-22. **Decided by the user:** every `to_record` builds its record by calling the record
class with keyword arguments - `return WalletsRecord(id=wallet.id.value, ...)` - instead of a dict
literal. All 14 examples (uv, and the pip and Poetry copies written from them), the suite's
fixtures and inline test repositories, the docs and website pages, `AGENTS.md`, the maintainer
skill and the `to_record` docstring in `src/ekbatan/repository.py`.

| decision | why |
| --- | --- |
| A record whose column name is not a valid Python identifier or is a keyword (`order date`, `class`, a name starting `__`) is built as a dict literal; the docs say so where `to_record` is taught | such a name cannot be a keyword argument. Both forms produce the same plain `dict`, and `mypy --strict` reports a missing key, an unknown or misspelt key and a wrong type for the call form as for the literal (proven by `tests/unit/test_typed_records_mypy.py`, now written in the call form, with the same error codes) |
| `from_record` still reads `record["key"]`; the event outbox's `to_row` / `from_row` are untouched | not repository mappings |
| The schemagen-owned files (its docs page, website page, ledger, tests and the generator's own text) were **not changed** - another stream owns them; the places still showing a dict-literal `to_record` are listed for after the merge | avoid conflicting edits |

## schemagen review fixes (EKB-0436-)

2026-10-22, unattended, in the worktree `ekbatan-py-sgfix`, branch `schemagen-review-fixes`
(commits EKB-0450 to EKB-0464; EKB-0436-0449 belong to the other stream). An independent review
of schemagen found 14 things, each proven by a run; all are fixed, each with a test that failed
before and passes after. Row by row: [SCHEMAGEN-COMPLETENESS-LEDGER.md](SCHEMAGEN-COMPLETENESS-LEDGER.md),
"Review fixes".

**The ones worth a glance first:**

| decision | why | the other way |
| --- | --- | --- |
| **MySQL/MariaDB table options reflection does not read (`STATS_PERSISTENT=0`, `STATS_AUTO_RECALC`, MariaDB's `PAGE_CHECKSUM`) are written, not only noted** (finding 14 asked for a note) | read from `SHOW CREATE TABLE`, written as `mysql_stats_persistent="0"`; the round trip proves `create_all` rebuilds them. Only what is not `NAME=value` (a `TABLESPACE` name) is noted | a note only, as the review said |
| **MySQL/MariaDB partitioning, every kind, is applied after the table**: `sqlalchemy.event.listen(T, "after_create", sqlalchemy.DDL("ALTER TABLE %(fullname)s PARTITION BY ..."))`, the clause as the server prints it; reflection's `mysql_partition_*` options are no longer written - HASH and KEY too, which worked before | one way for all kinds; reflection misreads a LIST partition's values (`IN (1,2)` -> `IN (12)`), and SQLAlchemy cannot write the definitions | keep `mysql_partition_by`/`mysql_partitions` for HASH and KEY, DDL for RANGE/LIST only |
| **PostgreSQL storage parameters, replica identity, a column's STORAGE/COMPRESSION/STATISTICS/options are written** as `after_create` `ALTER TABLE` statements (beyond the review - found by widening the round trip's description) | they were lost silently; replica identity matters to CDC, which the outbox feeds | notes only |
| **Row level security, policies, rules, extended statistics, PostgreSQL index comments, a table's tablespace, publications are noted, not written**; a `NOT VALID` check or key is built valid, with a note; a custom collation is noted with "create it before `create_all`" | the review asked for notes at least; each could also be `after_create` DDL (policies, rules, statistics, index comments) | write them as DDL after the table |
| **`SCHEMA_FORMAT` is 6** | MySQL modules gained `after_create` partitioning; PostgreSQL modules gained `after_create` statements and catalog-true sequence defaults | stay at 5 (the shape did not change, only content) |
| **A default drawing on a sequence another table's SERIAL owns** writes the default and `BORROWER.add_is_dependent_on(OWNER)`, not a `Sequence(...)` of its own | declaring it made create_all create the sequence first, and the owner's SERIAL then made `owner_t_id_seq1` | declare the sequence on both columns |
| **An array of a domain**: written whole, but its record is `Any`, with a note | measured: psycopg hands back the array's text, which SQLAlchemy splits into characters (`['{','5',',','6','}']`); asyncpg reads `[5, 6]`. The note names the way out (cast to the base type's array, measured to read a list on psycopg) | a type with a `column_expression` cast in the generated module, so both drivers read a list |
| **A MySQL/MariaDB ENUM or SET with a backslash in a value is written `sql_type("enum(...)")`**, the record still `Literal[...]` / `set[str]`, with a note | SQLAlchemy's MySQL ENUM/SET put the value into CREATE TABLE with the backslash unescaped (measured: `x\y` built as `xy`); unescaping the values alone fixed the reads and broke the DDL | keep `sqlalchemy.Enum` and accept the DDL loss; it loses SQLAlchemy's value check on write either way |
| PostgreSQL `"char"` and `name` are written `sql_type("\"char\"")`, `sql_type("name")` (beyond the review: both were rebuilt `VARCHAR`); `"char"`'s record is `Any` (psycopg `str`, asyncpg `bytes`), `name`'s `str` | measured on both drivers | - |
| A converter whose `from_db` says `-> Any` falls through to its `Converter[DbT, UserT]` base; with no base either, the record is `Any` with a note - not an error | the review asked for exactly this; an explicit `Any` is the user's own statement | refuse it, as a converter that says nothing is refused |
| An empty path flag (`--migrations=`, `--out=`, `--config=`) is refused by argparse (exit 2, usage); every other empty setting by `ValidationError` (exit 1) | `Path("")` is `.`, so the emptiness is gone by the time the config sees it | read the flags as text and refuse them in `main` |
| A module from a newer schemagen says "written by a newer schemagen" and to upgrade ekbatan | regenerating it with the older ekbatan would lose what the newer one wrote | say only "regenerate" |
| MySQL/MariaDB view columns take NOT NULL and a collation other than the database's from `information_schema.COLUMNS` (beyond the review: found by comparing views) | reflection reads a view by `DESCRIBE`; a view's record said `X \| None` of a NOT NULL column | - |
| SQLite generated columns and a column-level foreign key's name and actions are read from the table's text and `PRAGMA foreign_key_list` (the generated columns beyond the review: `create_all` failed on two in a row) | SQLAlchemy's SQLite reflection reads them with patterns that miss or overrun | - |
| The round trip's view comparison normalises MariaDB integer display widths (`bigint(12)` vs `bigint(20)`) | a display width is not data; MariaDB gives a computed view column a width of its own | compare widths and note them |
| A time kind on a column it cannot label is **refused** (an error, not a note): `OffsetDateTime` on a zone-less timestamp, any time kind on a non-timestamp column (handed over: time review T4) | a note would still write a module whose column or record is wrong; the user's rule is never silent, and the fix is one line in the rule | a note, and write the column as its own type |
| Ekbatan's built-in time-kind rules skip a column that is not a timestamp even when its type spells like one | SQLAlchemy's `Interval` compiles to `DATETIME` on SQLite, so the `Instant` default took it; refusing a built-in rule would break generation for nothing the user wrote | - |

Left as it is, noticed on the way:

- A MySQL view's key column reads as `NotRequired[int]` in its record (reflection marks it
  auto-increment through the view). Harmless for reads; not changed.
- `tests/schemagen_support.describe_database` reads SQLite's DEFERRABLE and foreign key names
  with schemagen's own text reader (`sqlite_table_parts`), so for those two the round trip is
  backed by `writes` checks on the module's text, which do not depend on it.

## records as typed dicts with column_ methods (EKB-0485-)

2026-10-22, unattended, from [RECORDS-DICT-LEDGER.md](RECORDS-DICT-LEDGER.md). **Decided by the
user:** every `<Table>Record` is a subclass of `dict[str, Any]` with `__slots__ = ()`, a
keyword-only `__init__`, `__getitem__` overloads under `TYPE_CHECKING`, and a
`column_<name>()` method per column - `record["wallet_id"]` and `record.column_wallet_id()` both
work, both checked by mypy. Attribute properties on SQLAlchemy rows were rejected first (too
slow). `SCHEMA_FORMAT` is 7.

**The ones worth a glance first:**

| decision | why | the other way |
| --- | --- | --- |
| **A read builds each record with `dict.__new__(Record)` + `dict.update(zip(keys, row))`**, skipping the keyword `__init__` (`ekbatan.fetching.records` / `record_of`) | measured on 10k rows x 10 columns, construction only: `row._mapping` 2.9 ms, a hidden subclass of the record with `dict.__init__` 5.4 ms, `__new__` + `update` 6.0 ms, plain `dict(zip)` 4.5 ms, per-row `row._fields` 11.9 ms (so the keys are read once per result). The hidden subclass was 10% faster but makes `type(record)` not the record class and breaks pickling by name; end to end (`find_where`, 10k rows) the difference is lost in the noise | the hidden subclass |
| **The record class is read off the repository's generic bases** (`ModelRepository[Wallet, WalletsRecord, ...]`, through any generic class of the user's own in between), once, at construction; where it names no record class (a `TypedDict`, a `Mapping`, a type variable) every read hands `from_record` a plain `dict` | no new constructor argument; the generics already say it, and the test harness' async twins keep `__orig_bases__` | a `record_type=` constructor argument |
| **What a `rows(...)` mapper gets**: a mapper whose first parameter is annotated with a record class gets that class; a repository's own method (`self.from_record`, `self.model_by_field_name`) gets the repository's record even when its annotation cannot be read; anything else - a lambda - still gets the row's mapping (`RowMapping`). The annotation is read once per function (weakly cached), and a decorated mapper is read through `__wrapped__` (`inspect.unwrap`) | a projection lambda over arbitrary columns has no record to be; a typed mapper and `from_record` must get the class, or `record.column_x()` fails at run time while mypy is happy | always a plain dict for every mapper; or always the mapping and document that `column_x()` does not work in `fetch` |
| **A column the database fills: the keyword is `X \| Unset = UNSET`; `record["x"]` and `column_x()` are typed `X`** (as the column reads, `\| None` if nullable) - not "maybe missing" | read from the database the key is always there, which is where records are read; a record built without it raises `KeyError` on read, as a `NotRequired` key did. mypy does not catch reading it from a just-built record | type the getter `X \| Unset`, forcing a check on every read of every defaulted column |
| **Keywords are sanitized ASCII**: every character outside `[A-Za-z0-9_]` becomes `_` (so `größe` is `gr__e=`), a leading digit gets `_` before it, a leading `__` becomes `_` (a class body would mangle it), a keyword or `self` / `super` / `__class__` (what `__init__` itself uses), or `UNSET` when the record uses the marker, gets `_` after it | Python NFKC-normalises non-ASCII identifiers, so two different column names could become one keyword silently | allow any `str.isidentifier()` name |
| **Collisions**: the column whose name needed no change keeps the keyword / method; the others get `_2`, `_3`... in column order, skipping names taken; the run prints a note | deterministic and independent of column order for the natural name | number in column order only |
| `{**record}`, `dict(record)` and `x.update(record)` are **mypy errors** (it cannot pick a `__getitem__` overload for a plain `str`); documented: `dict(record.items())`, or type it `Mapping[str, Any]` first. Fine at run time | the price of the overloads; adding a catch-all `str` overload would make `record["idd"]` pass | a last `__getitem__(str) -> Any` overload |
| No `__setitem__` / `get` overloads: `record["x"] = wrong` and `record.get("idd")` are not caught | records are built by keyword; kept the generated class small | overload them too |
| **An UPDATE cannot be handed the record object itself**: SQLAlchemy reserves a column's name for the SET clause's own bind, so the UPDATE binds `_v_<column>` names. The values are read from the very object `to_record` returned - no copy - and a test proves it with a record that notes each read | SQLAlchemy Core's rule | `text()` statements |
| On MySQL (no `RETURNING`) `from_record` gets the record `to_record` wrote; if `to_record` returned something that is not an instance of the record class (a plain dict, legacy code) it is copied into one first (`_as_record`) | "from_record always gets the record class" holds on every path | hand it on as it is |
| `columns_by_field_name` now builds the record class, not a plain dict | the by-name mapping's record should have the methods too | - |
| The examples keep `record["x"]` in `from_record`; the repositories docs show both forms; the new integration test's `from_record` mixes them | the user's ask: `record["x"]` is the main, documented style | `column_x()` in some example |
| The generated record's docstring line is longer than 100 characters | the generated module is gitignored and not formatted, as the column lines already are | wrap it |
| A one-column table's record has a single, non-overloaded `__getitem__` (`@overload` needs two); its `type: ignore[override]` sits on that line. A record with more columns has one ignore, on its first `@overload` (where mypy reports it) | `--strict` warns of an unused ignore, so the placement is proven by the clean mypy run | - |
| `tests/records.py` reads a record's keys, keywords, types and filled columns by **running its `__init__`** with a marker per keyword; `schemagen_support.hints` uses it | a record no longer has `__annotations__` per key; re-deriving the naming rule in the test would test the rule against itself | parse the generated source |
| The async test harness' blocking method wrapper (`tests/modes.py`, `_Blocking`) gained `__wrapped__` | `fetch(self.from_record)` in a test's own async repository passes that wrapper; real user code passes the bound method | - |
| `ekbatan.fetching.record_class` / `records` / `record_of` are module-level helpers, not in `__all__` | shared by the repository and `FetchResult`; not API | private names |

Measured, before (414c085) against after, 10k rows of the suite's wallets: inserts unchanged
(PostgreSQL ~260 ms, SQLite ~2.9 s); `find_where` about 3% slower on PostgreSQL (89-90 ms ->
92-96 ms), within noise on SQLite (91-93 -> 92-97 ms). Table in the ledger.

Left as it is, noticed on the way:

- The framework's own tables (`EventsRecord`, `EventNotificationsRecord`, `ScheduledTasksRecord`)
  are records like any table now; nothing in the framework reads them.
- `docs-parity.py` reports "4 owed" (Java-side, not fatal) before and after.
