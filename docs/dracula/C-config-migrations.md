# Draculas - area C: configuration, migrations, code generators

Files covered: `config.py`, `config_files.py`, `_credentials.py`, `_redact.py`, `migrations.py`,
`_yoyo.py`, `schemagen.py`, `codegen.py`, `_tracing.py`, and what `ekbatan/__init__.py` exports from
them. Compared with Java's `DataSourceConfig`, `ShardingConfig`, `ekbatan-di/*`, `FlywayMigrator`,
the examples' jOOQ codegen setup, `AutoBuilder`, and the OpenTelemetry use in `ekbatan-core`.

Not repeated: urls that name the driver (`postgresql+psycopg://` vs `jdbc:postgresql:`).

"Verified by run" means the behaviour was run on 2026-10-12, not only read.

---

### A Java `application.yml` datasource block does not load - kept by the user's decision (2026-10-24)
- **Kept by the user's decision (2026-10-24)**: left as it is.
- **What:** The config file uses the Python field names for a datasource. Java's keys - `jdbcUrl`,
  `maximumPoolSize`, `driverClassName`, `minimumIdle`, `idleTimeout`, `leakDetectionThreshold` - are
  refused as "unknown key". The layout around them (`defaultShard`, `groups`, `primaryConfig`...)
  is Java's, so the file *looks* shared but is not. Verified by run: `jdbc-url` and
  `maximum-pool-size` are both refused.
- **Python:** `config_files.py:457-473` (allowed keys are the `DataSourceConfig` fields, `config.py:65-74`); example `ekbatan-py-examples/fastapi_wallet_rest_pg/ekbatan.yaml` uses `url` and `poolSize`
- **Java:** `ekbatan-core/.../config/DataSourceConfig.java:175-316` (builder setters Jackson binds to); `ekbatan-examples/spring-boot-wallet-rest-gradle-pg/src/main/resources/application.yml`
- **Why:** a choice - the keys follow the SQLAlchemy pool, which has different knobs.
- **Could match Java?** partly - `jdbcUrl` could be read as an alias of `url` and `maximumPoolSize` mapped to `pool_size`; `driverClassName`, `minimumIdle`, `idleTimeout` and `leakDetectionThreshold` have nothing to map to.
- **Surprise:** high

### The pool settings are SQLAlchemy's, not Hikari's - kept by the user's decision (2026-10-24)
- **Kept by the user's decision (2026-10-24)**: left as it is.
- **What:** Java has one ceiling, `maximumPoolSize`. Python has `pool_size` plus `max_overflow`,
  and adds `pool_timeout`, `pool_recycle` (30 min), `pool_pre_ping` (on) and `echo` (print all SQL).
  Java's `minimumIdle`, `idleTimeout` and `leakDetectionThreshold` do not exist - so no warning
  for a connection held too long.
- **Python:** `config.py:65-74`, validation `config.py:88-93`
- **Java:** `DataSourceConfig.java:52-62`, checks `DataSourceConfig.java:79-101`
- **Why:** Python forces it - a different pool library with different knobs.
- **Could match Java?** partly - `maximumPoolSize` could be a front for `pool_size`; leak detection and idle timeout would have to be written from scratch.
- **Surprise:** high

### The app reads the config file itself, and wires everything by hand
- **What:** In Java, Spring/Quarkus/Micronaut read `ekbatan.*` from `application.yml` and create
  the registry, executor and so on as beans. In Python you call `load_ekbatan_config(path)` on a
  separate file (`ekbatan.yaml`) and then build each piece yourself. There are no profiles
  (`application-prod.yml`), no layering of several sources, and no overriding one key from the
  environment or the command line - only `${VAR}` placeholders written into the file.
  `load_ekbatan_config` is not exported from `ekbatan`; it lives in `ekbatan.config_files`.
- **Python:** `config_files.py:88-134`; example wiring `ekbatan-py-examples/fastapi_wallet_rest_pg/app.py:117-152`
- **Java:** `ekbatan-di/spring/autoconfigure/.../EkbatanCoreConfiguration.java:83-106` (binding), `:177-199` (reads every Spring property source), `:207-211` (registry bean)
- **Why:** Python forces it - there is no DI container in the stack to do the binding.
- **Could match Java?** partly - a helper could build the registry and executor straight from `EkbatanConfig`, and environment overrides per key could be added; profiles would be new invention.
- **Surprise:** high

### The migration history table is not Flyway's, so the two ports cannot share a database
- **What:** History goes into `ekbatan_schema_history` with a SHA-256 checksum and `installed_on`
  stored as text. Java's Flyway uses `flyway_schema_history` with a CRC32 checksum. A database
  migrated by one port cannot be migrated by the other.
- **Python:** `migrations.py:121`, `migrations.py:453-476` (`history_table`)
- **Java:** `ekbatan-flyway/.../FlywayMigrator.java:202-209` (Flyway with its defaults, so `flyway_schema_history`)
- **Why:** Python forces it (there is no Flyway for Python), plus a choice for the name and the stronger checksum.
- **Could match Java?** partly - the table name, the CRC32 checksum and a timestamp column could all be copied; it would cost the stronger edit detection.
- **Surprise:** high

### `schema.py` comes from a command you run by hand against Docker - kept by the user's decision (2026-10-24)
- **Kept by the user's decision (2026-10-24)**: no automatic regeneration - pip runs nothing before `python -m app`, so no one way fits uv, pip and Poetry. schemagen keeps starting a Docker container (testcontainers) and applying the migrations on it, the Makefile regenerates when the migrations change, and the import check refuses a stale file.
- **What:** Java's jOOQ classes are made by the Gradle build every time (start container, run
  Flyway, generate into `build/`). In Python you run `python -m ekbatan.schemagen --migrations ...
  --out app/schema.py`, it starts a Docker container (Postgres by default). The examples
  gitignore the result (corrected while merging: it is not committed). If a migration changes and you forget, `schema.py` raises when imported. The result is
  SQLAlchemy `Table` objects (`WALLETS.c.balance`), not jOOQ table and record classes.
- **Python:** `schemagen.py:1-50`, fingerprint check `schemagen.py:187-205`, CLI `schemagen.py:323-345`, default image `schemagen.py:213`
- **Java:** `ekbatan-examples/spring-boot-wallet-rest-gradle-pg/build.gradle.kts:7` (`jooq-docker` plugin), `:95-110` (output in `build/generated-jooq`, forced types)
- **Why:** a Python convention - Python has no compile step to hook the generation into.
- **Could match Java?** partly - a build hook or a pre-commit step could run it; nothing runs automatically before `python -m app`.
- **Surprise:** high

### Typed builders come from a command you run by hand, and nothing checks they are current - to be decided later (the user, 2026-10-24)
- **To be decided later (the user, 2026-10-24)**, as #21 was.
- **What:** Java's `@AutoBuilder` annotation processor writes `WalletBuilder` on every compile, so
  it can never be stale. In Python you run `python -m ekbatan.codegen myapp.model --out
  myapp/builders.py` and commit the file. No annotation: every model in the module gets one. A
  stale builder is not caught by the type checker, and the staleness check
  (`scripts/check-builders.py`) is in this repository only - it is not shipped to users.
- **Python:** `codegen.py:1-23`, model discovery `codegen.py:45-63`, CLI `codegen.py:253-273` (no `--check` option); `scripts/check-builders.py`
- **Java:** `ekbatan-annotation-processor/.../AutoBuilder.java:8-18`; `build.gradle.kts:46` (`annotationProcessor`)
- **Why:** a Python convention - no compile step, so generation is a separate command.
- **Could match Java?** partly - a `--check` flag on `ekbatan.codegen` would give users the guard; always-fresh like Java is not possible.
- **Surprise:** high

### Migration locations are plain folder paths; no `classpath:`, no `filesystem:`, no default
- **What:** Flyway defaults to `classpath:db/migration` and takes `classpath:`/`filesystem:`
  prefixes. Python needs at least one location and takes it as a folder path, relative to the
  working directory. Verified by run: `filesystem:mig` and `classpath:db/migration` both fail with
  "no migrations directory", and no location at all fails with "at least one migrations location
  is required".
- **Python:** `migrations.py:295-305`
- **Java:** `FlywayMigrator.java:67` (`DEFAULT_LOCATION`), `:300-309`
- **Why:** Python forces part of it (there is no classpath); refusing the `filesystem:` prefix is a choice.
- **Could match Java?** partly - `filesystem:` could be stripped and accepted, and a package's folder could be the default (`importlib.resources`).
- **Surprise:** medium

### Flyway callbacks are refused; the only hook is `post-apply*.sql`
- **What:** A file named `afterMigrate.sql` (or any `beforeX`/`afterX` callback) stops the
  migration with an error. The one hook is yoyo's: any file whose name starts with `post-apply`,
  run at the end of every migrate. Verified by run.
- **Python:** `migrations.py:132`, `:138`, `:384-389`
- **Java:** none in `FlywayMigrator`; Flyway runs its callback files by default
- **Why:** Python forces it - yoyo has one hook and no Flyway callbacks.
- **Could match Java?** partly - `afterMigrate.sql` could be read as the post-apply hook; the other callbacks would have to be written.
- **Surprise:** medium

### A migration that cannot run in a transaction says so with a comment line
- **What:** For `CREATE INDEX CONCURRENTLY` and the like, the file's first line must be
  `-- transactional: false`. Flyway has a `.sql.conf` file for this (refused here) or works it out
  from the statement.
- **Python:** `migrations.py:26-28`, `.conf` refused `migrations.py:345-350`
- **Java:** none - Flyway's own behaviour
- **Why:** Python forces it - this is how yoyo marks it.
- **Could match Java?** partly - Flyway's `executeInTransaction=false` in a `.conf` file could be read; detecting it from the statement would be new work.
- **Surprise:** medium

### The migrator refuses things Flyway lets through
- **What:** A `.sql` file with a name Flyway would quietly skip (`R1__x.sql`, `create.sql`) stops
  the migration. So does `DELIMITER`, yoyo's `-- depends:`, a description over 200 characters,
  baselining an empty database, and a url that reaches a read-only replica. Flyway skips, accepts,
  or migrates in those cases.
- **Python:** `migrations.py:390-399`, `:428-450`, `:1492-1505`; replica check `_yoyo.py:181`
- **Java:** none - Flyway's own defaults
- **Why:** a choice - each is something that went wrong silently in Flyway when measured.
- **Could match Java?** yes - each refusal could be turned off; the port chose not to.
- **Surprise:** medium

### A migration waiting for another one's lock waits forever
- **What:** A second process waits for the lock with no time limit, logging "still waiting".
  Flyway gives up after 50 one-second retries and fails. The lock is also held for the whole run,
  not per migration.
- **Python:** `_yoyo.py:100-116`
- **Java:** none in `FlywayMigrator`; Flyway's default `lockRetryCount` applies
- **Why:** a choice - giving up crashes every replica waiting behind a long migration.
- **Could match Java?** yes - a retry limit is a few lines.
- **Surprise:** medium

### No `customize`, and several Flyway features are missing
- **What:** Java's builder hands you Flyway's own configuration (`customize`). Python's builder has
  only `output_query_results`. Also missing: placeholders in SQL, undo migrations, a target
  version, migrations written in code, and every callback except the one hook.
- **Python:** `migrations.py:1610-1620` (`YoyoMigratorBuilder`)
- **Java:** `FlywayMigrator.java:439` (`customize`)
- **Why:** Python forces it - yoyo is not Flyway and has no such settings.
- **Could match Java?** partly - placeholders and a target version could be built; a Flyway configuration object cannot be handed over.
- **Surprise:** medium

### The migrator's API is shaped differently
- **What:** `migrate(config, *locations)` takes only a `ShardingConfig`. One plain database is
  `YoyoMigrator.migrate_data_source(ds, ...)`; there is no `migrate(url, user, password, ...)`.
  `migrate(Target...)` becomes `migrate_targets([...])`. A `shards=` argument picks some shards.
  `info`, `repair` and `baseline` are added. The class is `YoyoMigrator`, not `FlywayMigrator`.
  The result is a smaller `MigrateResult`, not Flyway's.
- **Python:** `migrations.py:1087-1116`, `:1364-1515`, `:1576-1607`, `MigrateResult` `:975-989`
- **Java:** `FlywayMigrator.java:78-137`
- **Why:** Python forces the renames (no overloading); the rest is a choice.
- **Could match Java?** partly - `migrate` could check its first argument's type to accept a `DataSourceConfig` or a url, as Java's overloads do.
- **Surprise:** medium

### `data_source_properties` keep their types, allow groups, and use Python driver names
- **What:** Java turns every value into text and refuses nested blocks and lists. Python passes
  values as written (`10` stays a number), allows groups and lists, and also refuses `passwd`. The
  names are the Python driver's, so Java's `ApplicationName` is `application_name` here. A trap
  only Python has: a value filled from an environment variable (`${T:10}`) is text, and PyMySQL
  fails on `connect_timeout="10"`.
- **Python:** `config.py:389-416`; `config_files.py:35-38`, `:242`, `:476-492`
- **Java:** `DataSourceConfig.java:218-251`
- **Why:** Python forces it - Python drivers take typed values, and some take groups.
- **Could match Java?** no - text values break PyMySQL; the env-variable case could be typed by the same quote rule INI uses.
- **Surprise:** medium

### Tracing scope and logger names differ from Java's
- **What:** Python's spans come from tracer `"ekbatan"` version `"0.1.0"`; Java's from
  `"io.ekbatan.core"` version `"1.1.2"`. Filtering traces by scope catches only one port. Loggers
  are `ekbatan.*` here and `io.ekbatan.*` in Java, so `logging.level.io.ekbatan` has no Python
  match. Also OpenTelemetry is optional here (spans appear only if it is installed); in Java it is
  a required dependency. The migrator has no MDC keys (`ekbatanShard`, `ekbatanTarget`); the shard
  label is in the message text.
- **Python:** `_tracing.py:19-24`; `migrations.py:117`
- **Java:** `ekbatan-core/.../ActionExecutor.java:81`; `ekbatan-core/build.gradle.kts:74`; `FlywayMigrator.java:60-64`
- **Why:** a choice for the names (the span names and attributes do match); optional OpenTelemetry is a Python convention.
- **Could match Java?** yes - the tracer name and version are one line; logger names could be `io.ekbatan.*` too.
- **Surprise:** medium
- **Fixed** (`EKB-0248`): each logger is named after the Java class that writes the same lines (`io.ekbatan.core.action.ActionExecutor`, ...), the tracer scope is `io.ekbatan.core` at this package's version, `opentelemetry-api` is a dependency, and the migrator puts `ekbatanShard` and `ekbatanTarget` on its records. See `docs/runtime/observability.md#loggers`.

### ~~The framework's tables are not in an `eventlog` schema~~ matched on PostgreSQL (`EKB-0520`)
- **Fixed** (`EKB-0520`, the user's decision 2026-10-24): the tables are fixed, as Java's, and nothing passes them - `eventlog.events` / `eventlog.event_notifications` on PostgreSQL; on MySQL and MariaDB the connected database (a separate `eventlog` database would be shared by every database on the server); SQLite has no schemas. See `docs/EVENTLOG-LEDGER.md`.
- **What:** Java keeps `events` and `event_notifications` in the PostgreSQL schema `eventlog`. The
  Python table helpers default to no schema, so `emit_ddl` and the examples create them in the
  default schema (`public`). Overlaps area A/B.
- **Python:** `schema.py:112-136` (`schema=None`); `migrations.py:1697-1713` (`emit_ddl`); `ekbatan-py-examples/fastapi_wallet_rest_pg/migrations/V0001__eventlog.sql:18,45`
- **Java:** `ekbatan-core/.../EventEntityRepository.java:35` (`SCHEMA_NAME = "eventlog"`); example `V0001__eventlog.sql:1-3`
- **Why:** a choice - SQLite has no schemas, so the application picks one.
- **Could match Java?** yes - default to `eventlog` on PostgreSQL and MySQL/MariaDB (a database there), none on SQLite.
- **Surprise:** medium

### Config files come in four formats, each with its own placeholder style
- **What:** YAML, TOML, JSON and INI, chosen by file extension. No `.properties`. In INI an
  environment variable is `%(ENV_DB_PASSWORD)s` and has no default; `${...}` there is refused.
- **Python:** `config_files.py:25-30`, `:71`, `:137-153`, `:245-297`
- **Java:** none - YAML and `.properties`, through the DI framework
- **Why:** a choice - formats Python projects use.
- **Could match Java?** partly - `.properties` could be added; INI's style is INI's.
- **Surprise:** low

### Unknown keys under `ekbatan` are refused
- **What:** A key under `ekbatan` that means nothing stops the load, with its path. Spring
  ignores unknown `ekbatan.*` keys (only the `sharding`, `jobs` and `local-event-handler` blocks are
  strict there). Datasource keys also accept snake_case (`pool_size`), which Java does not.
- **Python:** `config_files.py:101`, `:355-379`
- **Java:** `ekbatan-di/spring/autoconfigure/.../EkbatanProperties.java:15-16`; strict blocks `EkbatanCoreConfiguration.java:94-96`
- **Why:** a choice - a typo would otherwise switch a feature off silently.
- **Could match Java?** yes.
- **Surprise:** low

### SQLite is accepted
- **What:** `sqlite://` is a valid url here; Java refuses anything but PostgreSQL, MySQL and
  MariaDB. A login on a SQLite config is refused.
- **Python:** `config.py:37`, `:98-101`
- **Java:** `DataSourceConfig.java:135-145`
- **Why:** a choice - fast tests with no Docker.
- **Could match Java?** yes - drop it, losing the quick test run.
- **Surprise:** low

### Configs are built with keyword arguments; builders and names are snake_case
- **What:** The normal way is `DataSourceConfig(url=..., pool_size=20)`. The builder exists but is
  reached as `DataSourceConfig.builder()`, not `DataSourceConfig.Builder.dataSourceConfig()`, with
  setters like `.pool_size(...)`. `dialect` is a plain string (`"postgres"`), not jOOQ's
  `SQLDialect`. `unknown_shard_policy` is the string `"use_default_shard"` or `"fail"`, not the
  `UnknownShardPolicy` enum.
- **Python:** `config.py:40-74`, `:132-134`, `:136-173`, `:282`, `:419-480`
- **Java:** `DataSourceConfig.java:148-167`; `ShardingConfig.java:38`, `:105`
- **Why:** a Python convention.
- **Could match Java?** partly - an `UnknownShardPolicy` enum is easy; the naming style stays Python's.
- **Surprise:** low

### Python-only helpers
- **What:** `ShardingConfig.single(url, ...)` for one database; `split_login(url)` to pull a login
  out of a url; `migrate_registry` (creates missing tables with `create_all`); `emit_ddl` (prints
  CREATE statements to start a migration from). None exist in Java.
- **Python:** `config.py:345-386`; `migrations.py:1687-1713`
- **Java:** none
- **Why:** a choice.
- **Could match Java?** yes - they are additions; Java could gain them or Python drop them.
- **Surprise:** low

### Extra checks on the layout
- **What:** A `(group, member)` given twice, a default shard that is not configured, and shards
  with different databases are all refused when the config is built. Java's `ShardingConfig` checks
  none of these (the duplicate silently replaces the first pool and leaves it open).
- **Python:** `config.py:297-318`
- **Java:** `ShardingConfig.java:40-46`
- **Why:** a choice.
- **Could match Java?** yes - Java could add them.
- **Surprise:** low

### Generated builders are shaped differently
- **What:** All builders go into one `builders.py`. The factory is a module function
  `wallet(source=None)`, where Java has `WalletBuilder.wallet()`. The builder has setters only;
  Java's also has a getter per field.
- **Python:** `codegen.py:170-184`
- **Java:** `AutoBuilderProcessor.java:108`, `:210-244`
- **Why:** a Python convention.
- **Could match Java?** yes - a static method and getters could be generated.
- **Surprise:** low

### `schemagen` picks the database from the container image, and lives under the `codegen` extra
- **What:** The dialect follows `--image` (default `postgres:17-alpine`); a MySQL app needs
  `--image mysql:...`. `--url` migrates and reflects an existing database instead. Its dependencies
  come with the `codegen` extra, although `ekbatan.codegen` (builders) needs none.
- **Python:** `schemagen.py:213`, `:270-305`, `:323-345`; `pyproject.toml:40`
- **Java:** `build.gradle.kts:7`, `:68` (`jooqCodegen("org.postgresql:postgresql")`)
- **Why:** a choice.
- **Could match Java?** yes - names and defaults only.
- **Surprise:** low
