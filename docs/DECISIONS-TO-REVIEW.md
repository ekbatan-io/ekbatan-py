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

> **Superseded in part by [parity fix M](#parity-fix-m-the-migrator-flyway-compatible-ekb-0277-ekb-0282)**,
> which made the migrator write Flyway's own history and take Flyway's lock. The rows marked
> *superseded* below no longer hold; the others still do.

| what | commit | the case for it | the case against |
| --- | --- | --- | --- |
| The lock is held for the whole run, and the files are checked against the history under it | `EKB-0235` | One lock rather than one per migration, and the checks never read a history another process is changing. The outcome was measured the same as Flyway's: four processes, each migration applied once, in order (parity C03) | Flyway's processes share out the work, one migration each; here one process does it all while the rest wait. They wait either way |
| *superseded (`EKB-0279`): Flyway's callbacks instead* `post-apply` hooks run at the end of every migrate, not only when something was applied | `EKB-0235` | It is Flyway's `afterMigrate` timing, run and seen (parity F18). yoyo's timing means a hook that failed never runs again, since the next run has nothing to apply | A yoyo user expects yoyo's timing, and an expensive hook now runs, under the lock, on every start of every replica |
| `baseline` refuses an empty database | `EKB-0235` | Flyway baselines it and then never runs its first migration; the next one failed on the missing table (parity X05c) | A deviation from Flyway, with no way round it for a schema whose objects the emptiness check does not see |
| *superseded (`EKB-0277`): cut to fit, as Flyway, and matched by what the history holds* A description longer than 200 characters is refused | `EKB-0235` | Flyway cuts it to fit the history, and then cannot match a repeatable migration to its row: its next migrate failed (parity H07) | Flyway's versioned migrations survive the cut; this refuses those too |
| *superseded (`EKB-0277`): the table is Flyway's now* A history table of the shape `EKB-0233` made is refused, not upgraded | `EKB-0235` | No release ever made one - the version is still `0.1.0.dev0` - so an upgrade would be code that runs once, on nobody's database | A developer database migrated in the day between the two commits has to have its table dropped by hand |
| *superseded (`EKB-0277`): Flyway's timestamp column and default* `installed_on` stays text, in UTC | `EKB-0233`, `EKB-0235` | It reads the same on every dialect, with no driver's idea of a date in between | Flyway's is a timestamp column; date functions over this one need a cast |
| *superseded (`EKB-0277`, `EKB-0279`): all read as Flyway reads them* What yoyo cannot read as Flyway does is refused rather than misread: `DELIMITER`, a `.conf` file, a Flyway callback file, `R1__x.sql` | `EKB-0235` | yoyo glues `DELIMITER` onto the next statement, which fails after the statements before it have run; the rest would be skipped without a word | Flyway runs a file with `DELIMITER` in it; a Java project's procedures have to lose it to move over |
| Subdirectories are read | `EKB-0235` | Flyway reads them (parity F04): a file Flyway would run must not be one this skips. `EKB-0233` read one level only | A directory holding one subdirectory per dialect now merges them - and is refused, loudly, as duplicate versions |
| A schema is "not empty" with a materialized view, a partitioned or foreign table (PostgreSQL), or a view or trigger (SQLite) in it | `EKB-0235` | Each is something a migration made; Flyway's queries miss them | Not Flyway's exact rule |
| `info`, `repair` and `baseline` are part of the migrator | `EKB-0235` | Java reaches them through Flyway itself. Without them a recorded failure, or a database that predates its migrations, means editing the history table by hand | Surface Java's `FlywayMigrator` does not have |
| Copied although it bites: an older release re-applies its older repeatable migrations, and refuses to start when a newer release added one | `EKB-0235` | Flyway does both (parity R16, R23). A rule that tolerated the second cannot tell a file a newer release added from one a person deleted | During a rolling deploy a changed view flips back each time an old instance restarts; a rollback past a new repeatable migration needs `repair()` first |
| *superseded (`EKB-0278`): Flyway's, empty when nothing ran* `target_schema_version` is the highest version applied, whatever this run did | `EKB-0233` | It answers "what version is this database at" | Flyway's is the last version this run applied, and empty when it applied none |

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
| log messages themselves left as they are | the row was about names; messages differ in places (`executing` vs `Executing`) | built |

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

## Parity fix M: the migrator, Flyway-compatible (`EKB-0277`-`EKB-0282`)

The goal the user set: a Java service (Ekbatan's Flyway wrapper) and a Python service migrate the
same database, in either order, taking turns, at the same time. So the Python migrator now reads
and writes what Flyway 12 reads and writes. yoyo is gone - it could not split statements as
Flyway does, replace placeholders, or tell which statements refuse a transaction. These replace
the "migrator against Flyway" calls above wherever the two disagree.

| what | why | what Java does |
| --- | --- | --- |
| The migrator is written in Python, porting Flyway's classes: `ResourceNameParser`, `ChecksumCalculator`, `PlaceholderReplacingReader`, the dialect parsers, the history DDL, the lock templates | Sharing a database needs the same checksum, the same statements, the same rows and the same lock, byte for byte; a port of the source is the only way to get there that can be checked line by line | Runs Flyway 12 |
| Renamed `YoyoMigrator` to `FlywayMigrator` (and the builder) | Java's name; yoyo is no longer under it. The docstring says it is not Flyway and needs no Java | `FlywayMigrator` |
| The `migrations` extra is gone | Nothing to install: the drivers come with `postgres`/`mysql`/`mariadb` | - |
| Every pending file is parsed, placeholders replaced, before the first one runs | An unknown placeholder or an unfinished statement fails with nothing applied. Flyway parses each migration just before it runs it, so it applies V1 and then fails on V2 | Applies the files before the bad one |
| The lock is held for the whole run (kept from `EKB-0235`), on Flyway's key | Flyway takes it per migration; a Python and a Java migrator still wait for each other, since the key is the same, and the history is always read under it | Per migration |
| PostgreSQL: the session form of Flyway's advisory lock, on the migrating connection | Flyway takes the transaction form on a second connection and holds a transaction open while a `CREATE INDEX CONCURRENTLY` runs on the first - which is why it waits on itself forever. The two forms conflict, so the ports still exclude each other | `pg_try_advisory_xact_lock` on its own connection |
| A failing `afterEachMigrateError` callback runs after the failed migration is rolled back, in its own transaction | Flyway runs it inside the failed migration's transaction, which PostgreSQL has aborted, so the callback fails and its error replaces the migration's | Inside the aborted transaction |
| A long repeatable description is matched by what the history holds (197 characters and `...`) | Flyway stores the cut description and matches the uncut one, so it loses the row: the file reads as never applied and the row as missing | Loses the row; the next migrate fails |
| `classpath:a/b` is the resource folder `b` of the importable package `a` (`importlib.resources`); a plain path is a folder | Python's nearest thing to a classpath root is an importable top-level package, and it works from a wheel. A plain path read as a classpath location finds nothing in Java (see below) | Classpath roots |
| Script migrations (`V2__x.py`) and baseline migrations (`B5__x.sql`) are refused | This migrator runs no programs, and skipping a file Java applies would leave two databases different. Refusing is the loud version | Runs them |
| `baseline` still refuses an empty database (kept from `EKB-0235`) | Baselining an empty database skips the migrations that build it | Baselines it |
| `validate()` is part of the migrator | So both ports can be asked whether a database matches the files; Java reaches Flyway's `validate` through Flyway | Through Flyway |
| Flyway's own log lines are written under Flyway's class names (`org.flywaydb.core.internal.command.DbMigrate`, ...) | One logging config - `org.flywaydb: WARN` - quietens both ports, and a log search for Flyway's wording finds both | The same names |
| No config-file keys for the migrator; settings go through `customize` | Java's ekbatan config has none either - Java's wrapper takes Flyway settings through `customize(FluentConfiguration)` - so this mirrors it with `customize(FlywayConfiguration)` | `customize` |
| The result's `warnings` stay empty | Flyway's `MigrateResult.warnings` holds only `outOfOrder` warnings, a mode not here; warnings go to the log, as Flyway's do | Empty |
| `installed_on` is the column's default, as Flyway writes it; on PostgreSQL the session zone is the server's | Python has no JVM zone to set; the value is the database's clock either way (scan row 29, left as is) | The JVM's zone on PostgreSQL |
| MySQL's warnings are logged with SQL state `HY000` | The driver gives no state for a warning; the message and code are MySQL's | Connector/J's state |
| A `.sql.conf` file's own placeholders are not replaced | Flyway replaces them; no use was found for it, and it would read placeholders before the database is reached | Replaces them |
| Galera / Percona strict mode: the `GET_LOCK` lock is taken anyway | Flyway falls back to a table lock there; not built, as no Ekbatan dialect is Galera | Table lock |
| The `migrations` and `post-apply` names are not kept as aliases | ekbatan-py is unreleased; no shims | - |


## JSON, domain model and timestamps (`EKB-0290`-`EKB-0296`)

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

## Noticed in the Java, not acted on

- **`RedisKeyedLockProvider` does not wrap a Redis failure** (found by parity group L,
  2026-10-17). `KeyedLockProvider`'s javadoc says "a backend failure raises
  `LockAcquisitionException`"; the Redis provider calls `rlock.tryLock` / `lockInterruptibly`
  with no `catch`, so an unreachable Redis reaches the caller as Redisson's `RedisException` (run:
  `tryAcquire` against a closed port threw `RedisException`).
  The SQL providers wrap. ekbatan-py wraps in all four.

See the table at the top of `JAVA-PARITY-LEDGER.md`: `EmbeddedBitsShardingStrategy` decodes shard
bits from a `ShardedId` with no version guard, while the `Id` branch beside it has one.

### Flyway, as Ekbatan's `FlywayMigrator` runs it (found by parity fix M)

| what | how it was seen | what Python does instead |
| --- | --- | --- |
| **A migration with `CREATE INDEX CONCURRENTLY` hangs for ever.** Flyway runs it outside a transaction on one connection while its lock's transaction stays open on the other, and the index build waits for that transaction (`pg_stat_activity`: `virtualxid`) | run (scan f5, `concurrent_index_no_marker`: still waiting after 30 s) | one connection, a session lock, no open transaction: it finishes |
| **A waiting Flyway migrator holds a transaction open**, so any `CREATE INDEX CONCURRENTLY` - another port's included - waits until Flyway gives up (50 retries, about a minute). Setting `flyway.postgresql.transactional.lock=false` makes Flyway take the session lock and the wait goes away | run (f6 `e1`: Python's index done only after Java's give-up; `e1b`: with the setting, done at once and Java applied nothing) | - (nothing a Python process can change); documented on the migrations page |
| **A plain absolute path as a location is read as a classpath location**, finds nothing, and the run "succeeds" with 0 applied | run (scan f5, `plain_absolute_path`) | a plain path is a folder |
| **`afterEachMigrateError` runs inside the failed migration's transaction**, which PostgreSQL has aborted, so the callback fails and its error replaces the migration's | read (`DbMigrate.doMigrateGroup`, `DefaultCallbackExecutor`) | runs it after the rollback |
| **A repeatable migration with a description over 200 characters loses its row**: stored cut to 197 + `...`, matched uncut, so the next migrate finds a missing row and a file never applied | run earlier (MIGRATOR-LEDGER H07), read again (`MigrationInfoServiceImpl`) | matches by what the history holds |
| **An unknown placeholder in V2 fails after V1 is applied**: each migration is parsed just before it runs | read (`ParserSqlScript`, `DbMigrate.applyMigrations`) | every pending file is parsed first; nothing is applied |

Found by parity-fix group J (`EKB-0290`), run in a throwaway copy of the Java repo:

- **Decimals lose digits on the way back in.** `JSONBObjectNodeConverter` (and the three other
  converters in `persistence/jooq/converter/`) read the stored text with a plain `ObjectMapper`,
  so every JSON number with a fraction becomes a `double`: `10.50` comes back `10.5`, and
  `123456789012345678901234567890.123456789` comes back `123456789012345680000000000000`. That
  is what `EventHandlingJob` hands a handler. The fix is one setting on those mappers:
  `JsonMapper.builder().enable(DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS).build()`.
  Measured: with it, Java's own round trip keeps every digit and scale (`json.ownRoundTrip.withBigDecimalForFloats`
  in `parity-probes/G-domain-model/json.java.out`). `JsonNodeFeature.STRIP_TRAILING_BIGDECIMAL_ZEROES`
  is already off by default in Jackson 3.
- **Typed ids reach JSON as `{"id": ..., "value": ...}`.** `TypedValue`, `Id` and `ShardedId`
  have no `@JsonValue`, so Jackson writes their getters, and no `@JsonCreator`, so Jackson cannot
  read them back at all. `@JsonValue` on `getValue()` (and a creator) would make them the bare
  uuid a consumer can join on. This port writes Java's current shape.

