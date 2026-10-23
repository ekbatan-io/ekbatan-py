# Draculas

Everything in ekbatan-py that would surprise a developer who knows the Java port, found by the
sweep of 2026-10-12 (see [DRACULA-LEDGER.md](DRACULA-LEDGER.md)). Each row links to the area
file that holds the detail: the Python and Java file:line, the reason, and how it could match.

- **Why:** *forced* = Python leaves no other way; *choice* = the port decided it; *convention* =
  ordinary Python practice.
- **Match Java?** yes / partly / no.
- **Surprise:** H / M / L.
- **Checked:** *run* = shown by running code; *read* = read on both sides. The six rows marked
  *re-checked* were confirmed again while merging.

Areas: [A](dracula/A-domain-persistence.md) domain and persistence,
[B](dracula/B-actions-events.md) actions and events, [C](dracula/C-config-migrations.md) config
and migrations, [D](dracula/D-jobs-locks.md) jobs and locks, [E](dracula/E-using-it.md) using it.

## Part 1: bugs the sweep found

Not design differences - each is wrong by the port's own standard, so each is worth fixing
whatever is decided about the rest.

| # | Bug | Checked | Area |
|---|---|---|---|
| 1 | ~~MySQL/MariaDB lock names have an `ekbatan:` prefix Java's lack, so a Python and a Java service never block each other~~ **fixed** (`EKB-0244`): bare hex as Java, pinned against Java's output and on real MySQL/MariaDB | run, re-checked | D |
| 2 | ~~Quartz cron: a numeric day-of-week runs a day late (`2` is Monday in Java, Tuesday here)~~ **fixed** (`EKB-0246`): QUARTZ patterns are read as cron-utils reads them and converted for croniter; checked against cron-utils' own answers for 56,477 recorded cases | run, re-checked (`PORTING.md` records it) | D |
| 3 | ~~Keyed locks are held per OS thread, so two asyncio tasks on one loop both get "the same" lock~~ **fixed** (`EKB-0250`): taking a lock on an event loop's thread is refused; ekbatan is synchronous, so call it from a worker thread | run | D |
| 4 | ~~The handler registry accepts three clashes Java refuses: one handler twice, two event classes with one name, an alias equal to another handler's name~~ **fixed** (`EKB-0251`): Java's `withHandler` ported check for check, with its messages; Java's registry tests ported case for case | run | B |
| 5 | ~~The "not registered" error says "pass the instance to execute()", which `execute` then refuses~~ **fixed** (`EKB-0252`): the clause is gone; the message only says to register the action | run | B |
| 6 | ~~The setup guide's commands do not run as written: unquoted extras in zsh, wrong package layout for `uv init`, `uvicorn wallet.api:app` with no `app`, a compose service that does not exist, `MIGRATIONS` never defined, a Makefile watching `*.py` migrations~~ **fixed** (`EKB-0253`-`EKB-0255`): the page shows the examples' real files, read at build time; `scripts/check-setup-guide.py` follows it in an empty folder for every database and installer, and all nine pass. Found on the way: `codegen` could not run in a new project (`EKB-0254`) | run | E |
| 7 | ~~The guide says to gitignore `schema.py`; a hatchling-built wheel then leaves it out silently~~ **fixed** (`EKB-0256`): measured per build tool - poetry-core and hatchling drop it, and setuptools drops the migrations and `ekbatan.yaml` whatever git says; the guide carries each tool's line and a packaging step, and the setup-guide check builds the wheel and runs it installed | run | E |
| 8 | ~~`DIVERGENCE.md` says the built-in reads match Java; they read inside the open transaction and Java's do not~~ **fixed** (`EKB-0259`): run side by side on PostgreSQL, Java's reads did not see a row added in the open transaction; the built-in reads now use `db()` as Java's do, and `DIVERGENCE.md` says so | run, both ports | A |
| 9 | A repository takes a `clock=` it never uses; ~~the `Transaction` docstring names `registry.transaction()`, which does not exist~~ | read | A |
|   | → docstring **fixed** (`EKB-0258`): it names `registry.manager(shard).transaction()` and `registry.default.transaction()`, both run to check. The unused `clock=` stays, by the user's decision (2026-10-17) | | |
| 11 | ~~The framework converted time by itself - "a very big bad dracula": `timestamp_column()`, the by-name repository mapping, models, outbox and notification rows, CDC rows, event JSON and the dispatcher all moved times to UTC and stamped `+00:00` (`EKB-0294`)~~ **fixed** (`EKB-0384`-): every time is written and read exactly as given; `SystemClock().now()` (`.instant()` since `EKB-0419`) is UTC with no zone attached; UTC is the user's job, strongly recommended. Guarded by `tests/unit/test_time_as_is.py`. See `docs/TIME-AS-IS-LEDGER.md` | run (all four databases, both modes) | A |
| 12 | ~~Row mapping was an untyped dict - "a big dracula": `to_row` returned and `from_row` took `dict[str, Any]` / `Row[Any] \| Mapping[str, Any]`, so a column left out, misspelt or mistyped in a repository passed mypy, where Java's `toRecord` / `fromRecord` are typed by the generated `WalletsRecord`~~ **fixed** (`EKB-0408`-): schemagen writes a `TypedDict` `<Table>Record` beside each table (a converter's column typed as its user type); `to_record(model) -> WalletsRecord` / `from_record(record: WalletsRecord)`; repositories are `ModelRepository[Wallet, WalletsRecord, ShardedId[Wallet]]`. Proven by `tests/unit/test_typed_records_mypy.py` and against the drivers in `test_schemagen_types`. See `docs/TYPED-RECORDS-LEDGER.md` | run (mypy; all four databases, both modes) | A |
| 13 | ~~Event JSON times sent to Java lacked the `Z` - problem (c): once the default clock gave naive UTC (`EKB-0384`), an event field filled from the clock was written as `LocalDateTime` text (`"2026-01-01T10:00:00"`), which a Java event declaring an `Instant` refuses; the docs told users to add `.replace(tzinfo=UTC)` themselves~~ **fixed** (`EKB-0419`-): `ekbatan.time` labels `Instant`, `LocalDateTime`, `OffsetDateTime`; the clock's one reading is `clock.instant()`; a JSON time field is written as its declared kind - an `Instant` with `Z`, byte for byte as Jackson (3000/3000 of Java's own) - and a plain `datetime` field is refused, failing the action with nothing saved. See `docs/TIME-KINDS-LEDGER.md` | run (mypy; Java's Jackson output; all four databases, both modes) | B |
| 14 | ~~An update did not write a column mapped from a field of another name: the columns an UPDATE set were the model's field names that are also columns, so a field `nick` that `to_record` puts under `nickname` was written by `add` and never by `update` - the call succeeded, the version moved on, the old value stayed. One row and every batch shape, every database, both modes~~ **fixed** (`EKB-0480`): an update writes every column the record `to_record` returns, as an insert does and as Java's update is built from `toRecord`; a column the record leaves out is not written; a batch (insert or update) whose records carry different keys is refused before anything is sent. Proven red then green by `tests/integration/test_update_writes_the_record.py` | run (all four databases, both modes) | A |
| 10 | Cron on clock-change days, every style: a time inside the hour skipped in spring runs at 03:00 here and not at all in Java; a daily time inside the hour repeated in autumn runs twice here and once in Java. Found while building the bug 2 fix. **Left as is** - the user's decision, 2026-10-16 | run (Amsterdam, New York) | D |

## Part 2: the Draculas

### Sharing a database or a broker with Java services

| # | Dracula | Why | Match Java? | Surprise | Area |
|---|---|---|---|---|---|
| 1 | ~~Event payloads and action params use Python field names (`new_balance`, not `newBalance`), decimals as text~~ **fixed** (`EKB-0290`): the text Java's default Jackson 3 mapper writes, byte for byte - camelCase keys, declaration order, decimals as numbers - checked over 50,000 values; still no pluggable JSON writer | convention + choice | partly | H | A2, B3 |
| 2 | The events tables default to the default schema, not `eventlog` **Not applicable (user, 2026-10-20): Java and Python services never share one database**, so nothing ever reads this table from the other port | choice (SQLite has no schemas) | yes | H | A5, B4, C |
| 3 | Migration history is `ekbatan_schema_history` with SHA-256, not Flyway's table and CRC32: the ports cannot migrate one database **Kept by the user's decision (2026-10-17)**: a service is written in Java or in Python, never half of each, so the migrator stays on yoyo; a Flyway-compatible rewrite (`EKB-0277`-`EKB-0285`) was reverted in `EKB-0310`. | forced + choice | partly | H | C |
| 4 | db-scheduler task data is JSON; Java's default is Java serialization **Not applicable (user, 2026-10-20): Java and Python services never share one database**, so nothing ever reads this table from the other port | forced | no | M | D |
| 5 | ~~Redis lock is a plain key, Redisson uses a hash: mixing ports on one Redis is likely unsafe~~ **fixed** (`EKB-0267`): Redisson's hash, scripts and unlock message; a Java and a Python service share one lock both ways, run | forced | partly | M (unverified) | D |
| 6 | ~~Tracer scope `ekbatan` and loggers `ekbatan.*`, not `io.ekbatan.*`; OpenTelemetry optional~~ **fixed** (`EKB-0248`): loggers named after the Java class writing the same lines, scope `io.ekbatan.core`, the OpenTelemetry API a dependency, the migrator's MDC keys | choice | yes | M | C, B |
| 7 | The scheduler port always writes `priority`; Java leaves it NULL **Not applicable (user, 2026-10-20): Java and Python services never share one database**, so nothing ever reads this table from the other port | choice | yes | L | D |

### Wiring and configuration

| # | Dracula | Why | Match Java? | Surprise | Area |
|---|---|---|---|---|---|
| 8 | No DI container, no annotations, no discovery: every repository, action, handler, job and pool is built and registered by hand in `build()` | choice | partly (a helper from `EkbatanConfig`) | H | B, C, D, E |
| 9 | A Java datasource block does not load: `jdbcUrl`, `maximumPoolSize`, `driverClassName` are unknown keys | choice | partly | H | C |
| 10 | Pool settings are SQLAlchemy's (`pool_size` + `max_overflow`, `pool_recycle`, `pool_pre_ping`); no `minimumIdle`, `idleTimeout`, leak detection | forced | partly | H | C |
| 11 | ~~The handling job built by hand never runs unless switched on; Java's always runs~~ **fixed** (`EKB-0275`): a hand-built job runs on a fixed delay, as Java's; `handling.enabled` is read by the wiring, as Java's DI reads it | choice | yes | H | B |
| 12 | ~~Two `SystemClock()` objects count as different clocks, and the executor refuses to start~~ **fixed** (`EKB-0302`): the clock check is gone, as Java has none; the action reads its own clock, the rows are stamped from the executor's | choice | partly | H | B |
| 13 | The app finds its config file by a path it passes; no profiles, no per-key env overrides, no classpath | forced + choice | partly | M | C, E |
| 14 | You declare the events tables and pass them to the executor and both jobs; ~~the executor builder needs `event_persister(...)`~~ **fixed** (`EKB-0302`): with no persister the executor builds the default one, as Java's builder does; the jobs still take their tables | choice | partly | M | B |
| 15 | Migration locations are plain folders: no default, no `classpath:` / `filesystem:` **Kept by the user's decision (2026-10-17)**: a service is written in Java or in Python, never half of each, so the migrator stays on yoyo; a Flyway-compatible rewrite (`EKB-0277`-`EKB-0285`) was reverted in `EKB-0310`. | forced + choice | partly | M | C, E |
| 16 | Config files in four formats, INI with `%(ENV_X)s`; no `.properties` | choice | partly | L | C |
| 17 | Unknown keys under `ekbatan` are refused; Spring ignores them | choice | yes | L | C |
| 18 | SQLite is accepted | choice | yes | L | C |
| 19 | Extra layout checks: a shard twice, an unconfigured default shard | choice | yes | L | C |

### Build, install and tooling

| # | Dracula | Why | Match Java? | Surprise | Area |
|---|---|---|---|---|---|
| 20 | `ekbatan` is not on PyPI, so every install command in the docs fails | choice (not released yet) | yes | H | E |
| 21 | `schema.py` is generated by a command you run against Docker, not by the build; a stale one refuses to import, even after a comment-only migration edit | convention | partly | H | C, E |
| 22 | `builders.py` is generated by hand and committed; users get no check that it is current | convention | partly (`--check`) | H | C, E |
| 23 | ~~The examples are not standalone projects: no `pyproject.toml`, run from the framework's checkout~~ **fixed** (`EKB-0257`): each is a project of its own - built with uv, pip and Poetry, 21 in all - tested as its installed wheel; only `ekbatan` itself still comes from this repository, until it is on PyPI | choice | yes (once published) | H | E |
| 24 | No compose file: you start the database yourself | choice | partly | M | E |
| 25 | Type checking is a separate step the app runs without | forced | partly | L | E |
| 26 | One package holds everything, test helpers included; `db_scheduler` always installed | choice | partly | L | E |
| 27 | Tracing needs a package you add; there is no extra | choice | yes | L | E |
| 28 | The framework's INFO logs are invisible unless the app configures logging | convention | partly | L | E |
| 29 | The example HTTP APIs differ from Java's: snake_case JSON, `/closure`, 503 on a busy lock, mapped errors | choice | yes | L | E |
| 30 | SQLite example and one web stack here; Quarkus/Micronaut, Maven and native images there | choice + forced | partly | L | E |

### Concurrency and process life

| # | Dracula | Why | Match Java? | Surprise | Area |
|---|---|---|---|---|---|
| 31 | The framework is blocking; an `async def` FastAPI endpoint that calls it stalls the server. **Now refused, not silent** (`EKB-0500`): the sync API on an event loop's thread raises `BlockingCallError`, naming `ekbatan.aio`, a plain `def` endpoint and `asyncio.to_thread` | forced | partly (gevent) | H | B, E |
| 32 | SIGTERM does not let running jobs finish unless you turn it on (main thread only). **Addressed: warns at startup** (`EKB-0498`): a registry started where nothing in the process handles SIGTERM logs one warning naming the two fixes, `register_shutdown_hook()` or `stop()` in your own shutdown; under uvicorn it stays quiet | forced | partly | H | D, E |
| 33 | Drain-at-exit is a second switch, `stop_on_exit`, on `atexit`. It does not cover SIGTERM, so turning it on or off does not change #32's startup warning; only a SIGTERM handler does | forced | partly | M | D |
| 34 | `workers(n)`: job threads have a ceiling, one per job by default | forced | no | M | D |
| 35 | `stop()` never interrupts a running job; it waits once, then leaves it. **Kept as a limit** (`EKB-0269`): Python cannot interrupt a thread; `stop()` returns when Java's does for an interrupted job, and logs that it cannot interrupt where Java logs its interrupt | forced | no | M | D |
| 36 | A waiting `acquire` cannot be interrupted. **Kept as a limit** (`EKB-0268`): Python has no thread interrupt; the docs say to use `try_acquire` with a bounded wait where stopping matters | forced | partly | M | D |
| 37 | ~~A lock provider's `close()` releases every thread's held locks; Java's does nothing~~ **fixed** (`EKB-0266`): `close()` does nothing, as Java's; open leases stay held | choice | yes | M | D |
| 38 | Build the scheduler after `fork()` (gunicorn `--preload`). **Addressed: refused with a clear message** (`EKB-0500`): every object that owns connections or threads - registries, lock providers, the job registry, the handler loop - raises `ForkedProcessError` when used in a process `fork()` copied it into, saying to build it in the lifespan or at the top of `main()`; nothing is reset. Per-server recipes: `docs/deploying.md` | forced | no | M | D, E |
| 39 | Each server process has its own pools and scheduler | forced | no | M | E |
| 40 | Handlers run on real OS threads, one batch wide per shard | forced | partly | L | B |
| 41 | ~~Redis `acquire` polls every 50 ms instead of being woken~~ **fixed** (`EKB-0267`): waiters subscribe to Redisson's unlock channel, and a bounded wait ends on time | forced | partly | L | D |
| 42 | CPU-heavy jobs share one core (GIL) | forced | no | L | D |

### Models, repositories, transactions

| # | Dracula | Why | Match Java? | Surprise | Area |
|---|---|---|---|---|---|
| 43 | ~~Timestamps are stored without a time zone, `clock.now()` has one: comparing them raises `TypeError`~~ **fixed differently** (`EKB-0384`, after `EKB-0294`): the default clock now gives naive UTC, the same as what the columns hand back, and nothing is converted - see Part 1, bug 11 | choice | partly | H (re-checked by run) | A1 |
| 44 | ~~A transaction inside a transaction joins the outer one; Java's opens a second~~ **fixed** (`EKB-0301`): a nested block opens a second transaction on a second connection, as Java's; an action run inside the caller's transaction commits on its own | choice | yes | H | A3 |
| 45 | A model names its builder as a type argument, in a different order from Java; leaving it out fails only on `copy()` | forced | partly | H | A4 |
| 46 | Built-in reads see the open transaction's uncommitted rows | choice | yes | M | A7 |
| 47 | A repository's key type is the typed id, not the raw UUID | choice | yes | M | A6 |
| 48 | Read methods are named differently (`find`, `find_where`, `all`), return `None` not `Optional` | choice + convention | partly | M | A8 |
| 49 | Tables are SQLAlchemy written by hand; ~~`to_row` returns a dict~~ the mapping is typed by a generated record since `EKB-0408` (bug 12) - a `dict` class with `column_` getters since `EKB-0485` | choice | partly | M | A9 |
| 50 | Connections come from `with` blocks, are public, and "all shards" gives pairs | forced + convention | partly | M | A10 |
| 51 | `fetch_one` means "exactly one", not jOOQ's "at most one" | choice | yes | M | A11 |
| 52 | ~~`version` defaults to 1~~ **fixed** (`EKB-0292`): required, as Java's; a builder never given one refuses with "version cannot be null" | choice | yes | M | A12 |
| 53 | Every non-optional field is checked for `None` automatically | choice | yes | M | A13 |
| 54 | ~~A model's own `__post_init__` must call `super().__post_init__()`~~ **fixed** (`EKB-0497`): a model's own `__post_init__` is wrapped when the class is defined, so the built-in checks run, then its code, then the model's required `validate()` - with or without `super()`, and nothing twice | forced | yes | M | A14 |
| 55 | An event names its model with a string; forgetting it fails at the first event | forced | partly | M | A15 |
| 56 | ~~Two events with the same fields are equal, even about different models~~ **fixed** (`EKB-0496`): `model_id` and `model_name` are part of an event's equality and hash, so events about different models are never equal | convention + choice | yes | M | A16 |
| 57 | Exceptions end in `Error`, share `EkbatanError`; `ValidationError` stands for NPE / IAE / ISE | convention | partly | M | A17, B |
| 58 | `unknown_shard_policy` is a string, not an enum | choice | yes | M | A18, C |
| 59 | The clock is the framework's own (`now()`); `VirtualClock` is in the main package and starts frozen | forced + choice | partly | M | A19, B |
| 60 | `ShardedId.shard` refuses a non-v7 UUID | choice | yes | L | A20 |
| 61 | Registries have public constructors; the repository and action registries refuse duplicates Java replaces | choice | yes | L | A21, B |
| 62 | Database wiring takes SQLAlchemy engines; `manager()`, `default`, `shards` | forced + choice | partly | L | A22 |
| 63 | The sharding strategy has a different shape; no `ShardedUUID` class | convention | partly | L | A24 |
| 64 | On MySQL, `add` / `update` return a rebuilt object | choice | yes | L | A28 |
| 65 | Typed values compare across subclasses and print bare | choice | yes | L | A26 |
| 66 | A shard prints as `1:2` | choice | yes | L | A27 |
| 67 | Methods only Python has: `evolve`, `without_events`, `minted_at`, `shard=` on reads... | choice | yes | L | A25 |

### Actions and events

| # | Dracula | Why | Match Java? | Surprise | Area |
|---|---|---|---|---|---|
| 68 | `execute(Action, params, principal="x")`: principal last, a plain string | convention + choice | partly | M | B |
| 69 | An action can rename itself in the outbox by overriding `name` | choice | yes | M | B |
| 70 | ~~`event_type` is a string on an event and a class on a handler; overriding the event's breaks fan-out silently~~ **fixed** (`EKB-0495`): an event's `event_type` is final, its class name, as in Java - mypy flags an override and the event class is refused when it is defined | choice | yes | M | B |
| 71 | A handler is any object with `name`, `event_type`, `handle`, as class attributes | convention | partly | L | B |
| 72 | Retry delay is float seconds; `RetryConfig()` with no arguments means "one retry after 100 ms" | choice | yes | L | B |
| 73 | A missing handler is not counted as a failed attempt | choice | yes | L | B |
| 74 | `ActionSpec` verifiers fail on a falsy return value | forced | no | L | B |
| 75 | Parameterless actions take `None`, stored as `{}` | choice | yes | L | B |
| 76 | `EventEnvelope` has an `attempt` field | choice | yes | L | B |
| 77 | Event jobs take the config object; `EventFanout` / `EventDispatch` are public | choice | partly | L | B |
| 78 | Streaming: one plain `ActionEvent`, no Avro / Protobuf | choice | partly | L | B |
| 79 | Python-only test and plan helpers (`run_action`, `current_plan`, `bool(plan)`) | choice | yes | L | B |

### Migrations and generators

| # | Dracula | Why | Match Java? | Surprise | Area |
|---|---|---|---|---|---|
| 80 | Flyway callbacks are refused; the one hook is `post-apply*.sql` **Kept by the user's decision (2026-10-17)**: a service is written in Java or in Python, never half of each, so the migrator stays on yoyo; a Flyway-compatible rewrite (`EKB-0277`-`EKB-0285`) was reverted in `EKB-0310`. | forced | partly | M | C |
| 81 | A no-transaction migration is marked `-- transactional: false` **Kept by the user's decision (2026-10-17)**: a service is written in Java or in Python, never half of each, so the migrator stays on yoyo; a Flyway-compatible rewrite (`EKB-0277`-`EKB-0285`) was reverted in `EKB-0310`. | forced | partly | M | C |
| 82 | The migrator refuses what Flyway skips (`R1__x.sql`, `DELIMITER`, long descriptions...) **Kept by the user's decision (2026-10-17)**: a service is written in Java or in Python, never half of each, so the migrator stays on yoyo; a Flyway-compatible rewrite (`EKB-0277`-`EKB-0285`) was reverted in `EKB-0310`. | choice | yes | M | C |
| 83 | A migration waiting for another's lock waits forever; Flyway gives up after 50 s **Kept by the user's decision (2026-10-18, `EKB-0314`)**: Flyway's limit is PostgreSQL-only by accident and crashes instances waiting behind a long migration; a session lock frees itself when its holder dies, so waiting is safe | choice | yes | M | C |
| 84 | No `customize`, no SQL placeholders, undo, target version or code migrations **Kept by the user's decision (2026-10-17)**: a service is written in Java or in Python, never half of each, so the migrator stays on yoyo; a Flyway-compatible rewrite (`EKB-0277`-`EKB-0285`) was reverted in `EKB-0310`. | forced | partly | M | C |
| 85 | The migrator's API is shaped differently (`YoyoMigrator`, `migrate_data_source`, `info`, `repair`) **Kept by the user's decision (2026-10-17)**: a service is written in Java or in Python, never half of each, so the migrator stays on yoyo; a Flyway-compatible rewrite (`EKB-0277`-`EKB-0285`) was reverted in `EKB-0310`. | forced + choice | partly | M | C |
| 86 | `data_source_properties` keep types, allow groups, use Python driver names; an env-filled number is text | forced | no | M | C |
| 87 | Generated builders: one `builders.py`, a module function per model, setters only | convention | yes | L | C |
| 88 | `schemagen` picks the dialect from the Docker image; lives in the `codegen` extra | choice | yes | L | C |
| 89 | Python-only helpers: `ShardingConfig.single`, `split_login`, `migrate_registry`, `emit_ddl` | choice | yes | L | C |

### Jobs and locks API

| # | Dracula | Why | Match Java? | Surprise | Area |
|---|---|---|---|---|---|
| 90 | The scheduler port has no `SchedulerClient` builder | choice | yes | M | D |
| 91 | `build_manual()` and `ManualJobRegistry` exist only here | choice | yes | L | D |
| 92 | Lock API: `None`, `release()`, `held`, a Protocol | convention | partly | L | D |
| 93 | Lock providers: `builder().engine(...)`, public constructors; Redis provider not exported | convention | partly | L | D |
| 94 | Scheduler port: no `register_shutdown_hook()`, some renamed builder methods, refuses zero intervals | choice | yes | L | D |
| 95 | `JobRegistry` extras: `.scheduler`, `with_config`, `with` block | convention | partly | L | D |

### Everywhere

| # | Dracula | Why | Match Java? | Surprise | Area |
|---|---|---|---|---|---|
| 96 | snake_case names, `X.builder()` instead of `X.Builder.x()`, properties instead of getters | convention | no | L | A29, B |

Known before the sweep and not repeated in the areas: urls that name the driver
(`postgresql+psycopg://`).
