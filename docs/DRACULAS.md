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
| 1 | MySQL/MariaDB lock names have an `ekbatan:` prefix Java's lack, so a Python and a Java service never block each other; the docstring says they match | run, re-checked | D |
| 2 | Quartz cron: a numeric day-of-week runs a day late (`2` is Monday in Java, Tuesday here) | run, re-checked (`PORTING.md` records it) | D |
| 3 | Keyed locks are held per OS thread, so two asyncio tasks on one loop both get "the same" lock | run | D |
| 4 | The handler registry accepts three clashes Java refuses: one handler twice, two event classes with one name, an alias equal to another handler's name | run | B |
| 5 | The "not registered" error says "pass the instance to execute()", which `execute` then refuses | run | B |
| 6 | The setup guide's commands do not run as written: unquoted extras in zsh, wrong package layout for `uv init`, `uvicorn wallet.api:app` with no `app`, a compose service that does not exist, `MIGRATIONS` never defined, a Makefile watching `*.py` migrations | run (two), read | E |
| 7 | The guide says to gitignore `schema.py`; a hatchling-built wheel then leaves it out silently | run | E |
| 8 | `DIVERGENCE.md` says the built-in reads match Java; they read inside the open transaction and Java's do not | run | A |
| 9 | A repository takes a `clock=` it never uses; the `Transaction` docstring names `registry.transaction()`, which does not exist | read | A |

## Part 2: the Draculas

### Sharing a database or a broker with Java services

| # | Dracula | Why | Match Java? | Surprise | Area |
|---|---|---|---|---|---|
| 1 | Event payloads and action params use Python field names (`new_balance`, not `newBalance`), decimals as text, and no pluggable JSON writer | convention + choice | partly | H | A2, B3 |
| 2 | The events tables default to the default schema, not `eventlog` | choice (SQLite has no schemas) | yes | H | A5, B4, C |
| 3 | Migration history is `ekbatan_schema_history` with SHA-256, not Flyway's table and CRC32: the ports cannot migrate one database | forced + choice | partly | H | C |
| 4 | db-scheduler task data is JSON; Java's default is Java serialization | forced | no | M | D |
| 5 | Redis lock is a plain key, Redisson uses a hash: mixing ports on one Redis is likely unsafe | forced | partly | M (unverified) | D |
| 6 | Tracer scope `ekbatan` and loggers `ekbatan.*`, not `io.ekbatan.*`; OpenTelemetry optional | choice | yes | M | C, B |
| 7 | The scheduler port always writes `priority`; Java leaves it NULL | choice | yes | L | D |

### Wiring and configuration

| # | Dracula | Why | Match Java? | Surprise | Area |
|---|---|---|---|---|---|
| 8 | No DI container, no annotations, no discovery: every repository, action, handler, job and pool is built and registered by hand in `build()` | choice | partly (a helper from `EkbatanConfig`) | H | B, C, D, E |
| 9 | A Java datasource block does not load: `jdbcUrl`, `maximumPoolSize`, `driverClassName` are unknown keys | choice | partly | H | C |
| 10 | Pool settings are SQLAlchemy's (`pool_size` + `max_overflow`, `pool_recycle`, `pool_pre_ping`); no `minimumIdle`, `idleTimeout`, leak detection | forced | partly | H | C |
| 11 | The handling job built by hand never runs unless switched on; Java's always runs | choice | yes | H | B |
| 12 | Two `SystemClock()` objects count as different clocks, and the executor refuses to start | choice | partly | H | B |
| 13 | The app finds its config file by a path it passes; no profiles, no per-key env overrides, no classpath | forced + choice | partly | M | C, E |
| 14 | You declare the events tables and pass them to the executor and both jobs; the executor builder needs `event_persister(...)` | choice | partly | M | B |
| 15 | Migration locations are plain folders: no default, no `classpath:` / `filesystem:` | forced + choice | partly | M | C, E |
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
| 23 | The examples are not standalone projects: no `pyproject.toml`, run from the framework's checkout | choice | yes (once published) | H | E |
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
| 31 | The framework is blocking; an `async def` FastAPI endpoint that calls it stalls the server | forced | partly (gevent) | H | B, E |
| 32 | SIGTERM does not let running jobs finish unless you turn it on (main thread only) | forced | partly | H | D, E |
| 33 | Drain-at-exit is a second switch, `stop_on_exit`, on `atexit` | forced | partly | M | D |
| 34 | `workers(n)`: job threads have a ceiling, one per job by default | forced | no | M | D |
| 35 | `stop()` never interrupts a running job; it waits once, then leaves it | forced | no | M | D |
| 36 | A waiting `acquire` cannot be interrupted | forced | partly | M | D |
| 37 | A lock provider's `close()` releases every thread's held locks; Java's does nothing | choice | yes | M | D |
| 38 | Build the scheduler after `fork()` (gunicorn `--preload`) | forced | no | M | D |
| 39 | Each server process has its own pools and scheduler | forced | no | M | E |
| 40 | Handlers run on real OS threads, one batch wide per shard | forced | partly | L | B |
| 41 | Redis `acquire` polls every 50 ms instead of being woken | forced | partly | L | D |
| 42 | CPU-heavy jobs share one core (GIL) | forced | no | L | D |

### Models, repositories, transactions

| # | Dracula | Why | Match Java? | Surprise | Area |
|---|---|---|---|---|---|
| 43 | Timestamps are stored without a time zone, `clock.now()` has one: comparing them raises `TypeError` | choice | partly | H (re-checked by run) | A1 |
| 44 | A transaction inside a transaction joins the outer one; Java's opens a second | choice | yes | H | A3 |
| 45 | A model names its builder as a type argument, in a different order from Java; leaving it out fails only on `copy()` | forced | partly | H | A4 |
| 46 | Built-in reads see the open transaction's uncommitted rows | choice | yes | M | A7 |
| 47 | A repository's key type is the typed id, not the raw UUID | choice | yes | M | A6 |
| 48 | Read methods are named differently (`find`, `find_where`, `all`), return `None` not `Optional` | choice + convention | partly | M | A8 |
| 49 | Tables are SQLAlchemy written by hand; `to_row` returns a dict | choice | partly | M | A9 |
| 50 | Connections come from `with` blocks, are public, and "all shards" gives pairs | forced + convention | partly | M | A10 |
| 51 | `fetch_one` means "exactly one", not jOOQ's "at most one" | choice | yes | M | A11 |
| 52 | `version` defaults to 1 | choice | yes | M | A12 |
| 53 | Every non-optional field is checked for `None` automatically | choice | yes | M | A13 |
| 54 | A model's own `__post_init__` must call `super().__post_init__()` | forced | no | M | A14 |
| 55 | An event names its model with a string; forgetting it fails at the first event | forced | partly | M | A15 |
| 56 | Two events with the same fields are equal, even about different models | convention + choice | yes | M | A16 |
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
| 70 | `event_type` is a string on an event and a class on a handler; overriding the event's breaks fan-out silently | choice | yes | M | B |
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
| 80 | Flyway callbacks are refused; the one hook is `post-apply*.sql` | forced | partly | M | C |
| 81 | A no-transaction migration is marked `-- transactional: false` | forced | partly | M | C |
| 82 | The migrator refuses what Flyway skips (`R1__x.sql`, `DELIMITER`, long descriptions...) | choice | yes | M | C |
| 83 | A migration waiting for another's lock waits forever; Flyway gives up after 50 s | choice | yes | M | C |
| 84 | No `customize`, no SQL placeholders, undo, target version or code migrations | forced | partly | M | C |
| 85 | The migrator's API is shaped differently (`YoyoMigrator`, `migrate_data_source`, `info`, `repair`) | forced + choice | partly | M | C |
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
