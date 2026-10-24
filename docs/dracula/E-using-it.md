# Draculas, area E: using it

Packaging, installing, setting up a project, wiring a service, running it, testing it, and the
tooling around it. Checked against both repositories on 2026-10-12. Findings marked
**(run)** were checked by running something, not only by reading.

Some overlap with area C (`C-config-migrations.md`) is expected - schema generation, builders,
migration locations, hand wiring. Here they are seen from the user's side: what happens when you
follow the guide and run the project. Area F merges the two.

---

### `ekbatan` is not on PyPI, so every install command in the docs fails - planned: published on PyPI later (the user, 2026-10-24)
- **Planned (the user, 2026-10-24)**: ekbatan will be published on PyPI later.
- **What:** The docs say `uv add ekbatan[postgres]`, but neither `ekbatan` nor
  `ekbatan-py-db-scheduler` has been published. A Java developer adds a Maven Central coordinate
  and it resolves. **(run)** `https://pypi.org/pypi/ekbatan/json` and
  `.../ekbatan-py-db-scheduler/json` both answer 404.
- **Python:** `docs/getting-started.md:21`, `website/src/pages/learn/complete-project-setup.mdx:76`,
  `README.md:59`; `RELEASE.md:35` ("Neither name has been published yet"); `pyproject.toml:3`
  (`0.1.0.dev0`).
- **Java:** `ekbatan-examples/spring-boot-wallet-rest-gradle-pg/gradle.properties:2`
  (`ekbatanVersion=1.1.2`, "published on Maven Central"); `build.gradle.kts:41`.
- **Why:** a choice - the release pipeline exists, but nothing has been released yet.
- **Could match Java?** yes - publish. Until then the docs should say "install from a git checkout".
- **Surprise:** high

### The setup guide's commands do not run as written
- **What:** Several steps of the "complete project setup" fail when you follow them in order:
  1. `uv add ekbatan[postgres,migrations]` is unquoted. In zsh (the macOS default) this stops
     with `no matches found`. The page warns about quotes in the pip tab only. **(run)**
  2. `uv init wallet` (uv 0.13) makes a `src/wallet/` package with the `uv_build` backend. The
     page's tree puts the package at `wallet/wallet/`. With that layout **every** `uv run` fails:
     `Expected a Python module at: src/wallet/__init__.py`. **(run)**
  3. The Makefile runs `uvicorn wallet.api:app`, but the page's `api.py` only defines
     `create_app()`; there is no `app`.
  4. Step 2 starts the database with `docker run --name wallet-pg`; step 15 checks it with
     `docker compose exec wallet-db ...`. There is no compose file and no `wallet-db`.
  5. `migrate(layout, MIGRATIONS)` is used but `MIGRATIONS` is never defined on the page.
  6. `docs/getting-started.md`'s Makefile watches `migrations/*.py`; migrations are `.sql`, so it
     would never regenerate.
- **Python:** `website/src/pages/learn/complete-project-setup.mdx:23-43, 55, 70, 76, 790, 898,
  913, 1353`; `docs/getting-started.md:21, 82`.
- **Fixed** (`EKB-0253`-`EKB-0255`): the page now renders the example projects' real files, and `scripts/check-setup-guide.py` follows it end to end for PostgreSQL, MySQL and MariaDB with uv, pip and poetry. See `docs/SETUP-GUIDE-LEDGER.md`.
- **Java:** none - `docs/gradle/getting-started.md` and each example's README match a project
  that builds (`./gradlew bootRun`).
- **Why:** a choice - doc drift; the examples are tested, the guide is not.
- **Could match Java?** yes - quote the extras, use the `src/` layout or `uv init --app`/no build
  system, use the factory form (`uvicorn --factory wallet.api:create_app`), and test the page.
- **Surprise:** high

### No DI container, no annotations, nothing found for you: you wire every piece by hand - kept by the user's decision (2026-10-24)
- **Kept by the user's decision (2026-10-24)**: left as it is.
- **What:** There is no `@EkbatanAction`, `@EkbatanRepository`, `@EkbatanEventHandler` or
  `@EkbatanDistributedJob`, and no Spring/Quarkus/Micronaut module. Your `build()` function
  constructs every repository, action, handler and job, registers each one, builds the jobs and
  lock pools itself, starts the job registry, and closes it all in the right order. Forget to
  register an action and you find out when it is executed.
- **Python:** `ekbatan-py-examples/fastapi_wallet_rest_pg/app.py:139-221` (the whole wiring),
  `:98-110` (`start()` / `close()` you call), `:179-181` (pools built from `jobsConfig` and
  `lockConfig` by hand); `api.py:123-131` (lifespan calls them).
- **Java:** `ekbatan-di/annotations/src/main/java/io/ekbatan/di/EkbatanAction.java` (and three
  siblings); `ekbatan-di/spring/autoconfigure/src/main/java/io/ekbatan/spring/EkbatanCoreConfiguration.java:207-281`
  (registry, clock, repository/action registries, executor as beans);
  `EkbatanDistributedJobsConfiguration.java:48-61` (jobs pool from `jobsConfig`), `:82`
  (`initMethod = "start", destroyMethod = "stop"`); `EkbatanLocalEventHandlerConfiguration.java:45-98`
  (handler registry and both event jobs). The Spring example's `Application.java:6-11` is the
  whole bootstrap. Java also documents plain wiring: `docs/wiring/without-di.md:3`.
- **Why:** a choice - Python has no dominant DI container, and FastAPI's `Depends` is not one.
- **Could match Java?** partly - a helper that builds the standard pieces from `EkbatanConfig`
  (registry, executor, jobs and lock pools, event jobs) would remove most of `build()`;
  discovery by decorator is possible but not usual Python.
- **Surprise:** high

### The generated `schema.py` is not built when you run, and a stale one refuses to import - kept by the user's decision (2026-10-24)
- **Kept by the user's decision (2026-10-24)**: no automatic regeneration - pip runs nothing before `python -m app`, so no one way fits uv, pip and Poetry. schemagen keeps starting a Docker container (testcontainers) and applying the migrations on it, the Makefile regenerates when the migrations change, and the import check refuses a stale file.
- **What:** Java regenerates jOOQ classes as part of compiling, so `./gradlew bootRun` on a fresh
  clone just works. Here `python -m yourapp` never regenerates. On a fresh clone the import fails
  until you run `make`; after **any** change to a migration file - even adding a comment - the
  module refuses to import until you regenerate. **(run)** Fresh clone:
  `ModuleNotFoundError: No module named 'fastapi_wallet_rest_pg.schema'`. After appending
  `-- a comment` to the migration: `ValidationError: schema.py was generated from migrations
  fingerprinted 322e18cdc8572117, but ... now fingerprints 23e00494f1cca0f9`.
  The example README's own "run it" block (`docker run`, `uv sync`, `python -m ...`) skips
  `make schema`, so it hits the first error on a fresh clone.
- **Python:** `src/ekbatan/schemagen.py:258-281` (`check_fingerprint`, run on import);
  `ekbatan-py-examples/fastapi_wallet_rest_pg/Makefile:26-27`; `README.md:50-52` of that example;
  `.gitignore` (`ekbatan-py-examples/*/schema.py`).
- **Java:** `ekbatan-examples/spring-boot-wallet-rest-gradle-pg/build.gradle.kts:94-104, 130-136`
  (codegen output added as a source dir, so compile depends on it).
- **Why:** Python forces it - nothing runs between editing a file and `python -m app`, so there is
  no build step to hook; Make is the stand-in.
- **Could match Java?** partly - Make gives the up-to-date check, but only if you run through
  `make`. The fingerprint could warn instead of refusing, or ignore whitespace and comments.
- **Surprise:** high

### `schema.py` can be silently left out of your wheel
- **What:** The guide says to gitignore `schema.py`. Hatchling - the backend `ekbatan` itself uses
  - leaves gitignored files out of the wheel, so the built package imports a module that is not in
  it. `uv_build` (what `uv init --package` picks) keeps it. In Java the generated classes are
  compiled into the jar whatever you ignore in git. **(run)** A hatchling project with
  `wallet/schema.py` in `.gitignore`: the wheel holds `__init__.py` and `app.py`, no `schema.py`.
  The same with `uv_build`: `schema.py` is there.
- **Python:** `website/src/pages/learn/complete-project-setup.mdx:33, 45-47`;
  `docs/getting-started.md:57-58`; `pyproject.toml:92-94` (ekbatan's own hatchling backend).
- **Java:** `build.gradle.kts:130-136` of the Spring example (generated sources are part of
  `main`, so part of the jar).
- **Why:** a Python convention - build backends decide file inclusion, and several honour
  `.gitignore`.
- **Could match Java?** partly - document it, and show the hatchling `artifacts = ["*/schema.py"]`
  line, or recommend `uv_build`.
- **Surprise:** high
- **Fixed** (`EKB-0256`): built a wheel with each tool the guide uses. `uv_build` keeps everything; poetry-core drops a gitignored `schema.py` (fixed with `[[tool.poetry.include]]`); setuptools keeps `schema.py` but drops `ekbatan.yaml` and the migrations even with nothing ignored (fixed with `[tool.setuptools.package-data]`); hatchling drops `schema.py` (`artifacts`). The guide carries the lines and a packaging step; `scripts/check-setup-guide.py` builds the wheel and imports it installed in a fresh venv.

### The framework is blocking, the web framework is async: an `async def` endpoint stalls the server - settled by the user (2026-10-24)
- **Settled by the user (2026-10-24)**: the refusal, with `ekbatan.aio` for async code, is the answer; nothing more to do.
- **What:** Every example is FastAPI, which is built on an event loop. The framework only has
  blocking calls. A plain `def` endpoint is fine (FastAPI runs it in a thread pool), but an
  `async def` endpoint that calls the executor stops every other request in that process for the
  length of the call. ~~Nothing in the API warns you.~~ Since `EKB-0500` the sync API refuses
  it with `BlockingCallError` (see `docs/startup-and-shutdown.md`). In Spring MVC every request has its own
  (virtual) thread, so blocking costs nothing.
- **Python:** `ekbatan-py-examples/fastapi_wallet_rest_pg/api.py:158-240` (all endpoints `def`);
  `docs/wiring-fastapi.md:72-84`; `DIVERGENCE.md:1317-1350` (measured: 5 heartbeat ticks instead
  of about 55 during a 0.5 s call).
- **Java:** `ekbatan-examples/spring-boot-wallet-rest-gradle-pg/src/main/java/io/example/wallet/web/WalletController.java:67-105`
  (plain blocking methods); `README.md:13` of the Java repo ("designed around virtual threads").
- **Why:** Python forces it - Python has no virtual threads; the usual web stack is async.
- **Could match Java?** partly - under gevent and a WSGI server, blocking is as cheap as in Java
  (see below), but not under FastAPI.
- **Surprise:** high

### The examples are not standalone projects
- **What:** Each Java example is its own Gradle or Maven project with its own wrapper, pulling
  Ekbatan from Maven Central; you can copy the folder out and build it. The Python examples have no
  `pyproject.toml`. They run inside the framework's own uv project, use the local source through a
  path dependency, need `uv sync --extra <db> --group examples` first (FastAPI is in a dependency
  group that is not installed by default), and are run as modules from the parent folder
  (`cd ekbatan-py-examples && uv run python -m fastapi_wallet_rest_pg`).
- **Python:** `pyproject.toml:44-48` (path source), `:79-86` (`examples` group);
  `ekbatan-py-examples/fastapi_wallet_rest_pg/Makefile:31-35` (`cd ..`, `cd ../..`);
  `ekbatan-py-examples/README.md:126-129`; no `ekbatan-py-examples/*/pyproject.toml`.
- **Java:** `ekbatan-examples/spring-boot-wallet-rest-gradle-pg/settings.gradle.kts:1`,
  `gradlew`, `build.gradle.kts:30-41`; its `README.md:1` ("A standalone Spring Boot example that
  uses Ekbatan from Maven Central").
- **Why:** a choice - with nothing on PyPI, the examples have to use the checkout.
- **Could match Java?** yes - once published, give each example its own `pyproject.toml` that
  depends on `ekbatan[...]`.
- **Surprise:** high

### A worker process must catch SIGTERM itself, or its jobs are not drained on a container stop
- **What:** A Java service stops cleanly on SIGTERM: Spring closes its context and the job
  registry's `stop` runs. In Python, `atexit` does not run on SIGTERM, so a `docker stop` kills the
  job registry mid-work unless you install a signal handler or call
  `register_shutdown_hook()` (off by default).
- **Python:** `ekbatan-py-examples/job_worker_pg/__main__.py:41-78`; `DIVERGENCE.md:1417`;
  `docs/DECISIONS-TO-REVIEW.md:42`.
- **Java:** `EkbatanDistributedJobsConfiguration.java:82` (`destroyMethod = "stop"`, run by
  Spring's JVM shutdown hook).
- **Why:** Python forces it - one signal handler per process, and servers like uvicorn and
  gunicorn take it; a library that grabbed it would break them.
- **Could match Java?** partly - under uvicorn the lifespan already stops it; a bare worker has
  to opt in. Since `EKB-0498` a bare worker that has not is told so by one warning at startup.
- **Surprise:** medium

### Typed builders are committed by hand, and a user project has no check that they are current - to be decided later (the user, 2026-10-24)
- **To be decided later (the user, 2026-10-24)**, as #21 was.
- **What:** Java's annotation processor rewrites `WalletBuilder` on every compile. Here you run
  `python -m ekbatan.codegen` yourself and commit the file. The repository checks its own
  examples with `scripts/check-builders.py`, but that script is not shipped and only looks at
  `ekbatan-py-examples/`; `ekbatan.codegen` has no `--check` option. A stale builder in your
  project is caught at run time, and only if the new field is required.
- **Python:** `src/ekbatan/codegen.py:258-265` (only `modules`, `--out`, `--package`);
  `scripts/check-builders.py:42` (`TREES = [ROOT / "ekbatan-py-examples"]`);
  `DIVERGENCE.md:194-232`; the guide's Makefile has no builders rule
  (`complete-project-setup.mdx:908-916`).
- **Java:** `build.gradle.kts:45-46` of the Spring example (`annotationProcessor`).
- **Why:** a choice - committing the output avoids a build step.
- **Could match Java?** partly - ship a `python -m ekbatan.codegen --check` and put it in the
  guide's Makefile.
- **Surprise:** medium

### Each server process has its own pools and its own scheduler
- **What:** Python services scale by running several processes (`uvicorn --workers 4`,
  gunicorn). Each one builds every pool and runs the job scheduler, so the database sees four
  times the connections. One JVM has one set.
- **Python:** `docs/runtime/concurrency.md:259-273`; `ekbatan-py-examples/fastapi_wallet_rest_pg/__main__.py:24-33`
  (factory form so a forked worker does not inherit the parent's pool).
- **Java:** none - one process per service.
- **Why:** Python forces it - the GIL makes processes the usual way to use several cores.
- **Could match Java?** no - but the docs cover the sizing.
- **Surprise:** medium

### No compose file: you bring your own database to run an example
- **What:** `./gradlew bootRun` in a Java example starts Postgres from its `compose.yaml` and
  stops it afterwards. No Python example has a compose file; `make run` expects a server already
  listening on `localhost` with login `ekbatan/ekbatan`. The run instructions say
  `docker compose up -d` anyway, and the MySQL and MariaDB ones say "or any Postgres".
- **Python:** `ekbatan-py-examples/fastapi_wallet_rest_pg/__main__.py:3`,
  `fastapi_wallet_rest_mysql/__main__.py:3`, `fastapi_wallet_rest_mariadb/__main__.py:3`;
  `fastapi_wallet_rest_pg/ekbatan.yaml` (defaults).
- **Java:** `ekbatan-examples/spring-boot-wallet-rest-gradle-pg/compose.yaml`;
  `build.gradle.kts:55-57` (`spring-boot-docker-compose`); `README.md` "Run locally".
- **Why:** a choice - nothing in Python starts compose on run, but a compose file costs nothing.
- **Could match Java?** partly - add a `compose.yaml` per example and a `make db`; it will not
  start by itself.
- **Surprise:** medium

### The config file is found by a path you pass; no profiles, no environment overrides
- **What:** Spring finds `application.yml` on the classpath, lets any key be overridden by an
  environment variable or a profile, and binds it for you. Here you pass the file's path to
  `load_ekbatan_config` (the examples use `Path(__file__).parent / "ekbatan.yaml"`; the guide uses
  a path relative to the current folder). An environment variable only changes a value that the
  file already reads through `${...}`. There are no profiles.
- **Python:** `ekbatan-py-examples/fastapi_wallet_rest_pg/app.py:65, 139`;
  `src/ekbatan/config_files.py:88-99, 137-150`; `complete-project-setup.mdx:231`.
- **Java:** `ekbatan-examples/spring-boot-wallet-rest-gradle-pg/src/main/resources/application.yml`;
  `EkbatanCoreConfiguration.java:85` (bound from Spring's `Environment`);
  `WalletControllerIntegrationTest.java` (`@SpringBootTest(properties = ...)` overrides keys).
- **Why:** a choice - no DI framework, so no binder; Python has no classpath.
- **Could match Java?** partly - env-var overrides by key name could be added; the classpath could
  be imitated with `importlib.resources`.
- **Surprise:** medium

### FastAPI wiring has traps of its own
- **What:** Three things a Spring developer never meets: (1) `TestClient` runs startup only inside
  `with TestClient(app)`; without the `with`, nothing is built and every request fails;
  (2) the `Annotated[..., Depends(...)]` alias must sit at module level, or with
  `from __future__ import annotations` FastAPI reads the parameter as a query field and answers
  422; (3) uvicorn must use the factory form, or a reloader fork shares the parent's database
  sockets.
- **Python:** `ekbatan-py-examples/fastapi_wallet_rest_pg/test_fastapi_wallet_rest_pg.py:56-60`;
  `api.py:100-113`; `__main__.py:24-33`.
- **Java:** none - `@SpringBootTest` starts the context; constructor injection
  (`WalletController.java:33-38`).
- **Why:** a Python convention - these come from FastAPI and uvicorn, not the framework.
- **Could match Java?** no - they belong to the web stack; the examples comment each one.
- **Surprise:** medium

### Migrations are a folder you point at and ship yourself; there is no default location
- **What:** Java defaults to `classpath:db/migration`, inside the jar. `migrate()` here needs a
  path, and a relative one is read from the current folder. The examples keep `migrations/`
  inside the package so it ships with it; the setup guide puts it at the project root, outside the
  package, where a wheel will not include it.
- **Python:** `src/ekbatan/migrations.py:1087-1092, 295-304` (no locations is an error);
  `ekbatan-py-examples/fastapi_wallet_rest_pg/app.py:61`; `complete-project-setup.mdx:27-30`.
- **Java:** `ekbatan-flyway/src/main/java/io/ekbatan/flyway/FlywayMigrator.java:67`
  (`DEFAULT_LOCATION = "classpath:db/migration"`).
- **Why:** Python forces it - no classpath.
- **Could match Java?** partly - a default of "`migrations/` next to the calling package", or
  `importlib.resources`; and the guide should keep the folder inside the package.
- **Surprise:** medium

### The framework's log lines are invisible unless you set up logging
- **What:** Spring Boot prints INFO logs out of the box, so a Java developer sees each Flyway
  migration at startup. Python libraries only log; the app has to configure it. The FastAPI
  examples do not, so the migrator's INFO lines ("migrating to version ...") never show; only
  warnings get through.
- **Python:** `src/ekbatan/migrations.py:1203, 1248, 1269` (`LOG.info`); only
  `ekbatan-py-examples/job_worker_pg/__main__.py:27` calls `logging.basicConfig`.
- **Java:** `ekbatan-examples/spring-boot-wallet-rest-gradle-pg/src/main/resources/application.yml`
  (`logging.level.io.ekbatan: INFO`); `EkbatanShardFlywayMigrator.java:23`.
- **Why:** a Python convention - a library must not configure logging.
- **Could match Java?** partly - the examples could configure logging in `__main__.py`.
- **Surprise:** low

### The example services answer differently from Java's
- **What:** Same domain, different HTTP API: snake_case JSON (`owner_id`, `opening_balance`) vs
  camelCase (`ownerId`, `initialBalance`); `POST /wallets/{id}/closure` vs `/close`; no `version`
  or shard in the response; a busy wallet is a 503 with `Retry-After` (lock `try_acquire`) where
  Java waits; not-found, invalid and stale are mapped to 404/400/409, where Java's examples map
  nothing, so those reach the client as 500.
- **Python:** `ekbatan-py-examples/fastapi_wallet_rest_pg/api.py:48-56, 145-156, 193-199, 212`.
- **Java:** `WalletController.java:40-52, 82, 91`; no `@ControllerAdvice` or `@ExceptionHandler`
  anywhere under `ekbatan-examples/`.
- **Why:** a choice - Python naming habits, and a decision to map errors in the examples.
- **Could match Java?** yes - either side could change; only matters when comparing the two.
- **Surprise:** low

### One package holds everything, including the test helpers
- **What:** Java splits the framework into artifacts you pick (core, flyway, distributed-jobs,
  local-event-handler, keyed-lock-redis, test-support as `testImplementation`). Python is one
  `ekbatan` package: jobs, events, locks and `ActionSpec` ship in the runtime package, and
  `import ekbatan` loads all of them. The db-scheduler port is always installed, as a top-level
  `db_scheduler` package. The `codegen` extra installs psycopg even for a MySQL project.
- **Python:** `pyproject.toml:27, 40`; `src/ekbatan/__init__.py:47, 93, 136`;
  `src/ekbatan/distributedjobs/__init__.py:23`; `db-scheduler/pyproject.toml:44`.
- **Java:** `settings.gradle.kts` (separate modules); `ekbatan-distributed-jobs/build.gradle.kts:25`;
  `ekbatan-test-support/`.
- **Why:** a choice - extras cover the optional drivers; the rest is small enough to ship together.
- **Could match Java?** partly - split into more extras; not worth much in Python.
- **Surprise:** low

### Tracing needs a package you add yourself, and there is no extra for it
- **What:** Java core always depends on `opentelemetry-api`, so spans appear as soon as an agent
  or SDK is present. Here spans are made only if OpenTelemetry is importable, and `tracing` is a
  dependency group of this repository, not an extra a user can ask for.
- **Python:** `src/ekbatan/_tracing.py:19-20`; `pyproject.toml:74` (group, not extra).
- **Java:** `ekbatan-core/build.gradle.kts:74`.
- **Why:** a choice - not forcing a tracing stack on every user.
- **Could match Java?** yes - add a `tracing` extra.
- **Surprise:** low

### Type checking is a separate step you may skip
- **What:** In Java a type error stops the build. Here mypy runs only through `make lint`; the
  app runs whether it passes or not. The guide's `make run` and `make test` do not run mypy.
- **Python:** `pyproject.toml:111-118`; `Makefile` (`lint:` target);
  `complete-project-setup.mdx:913-915`.
- **Java:** none - `javac` is the build.
- **Why:** Python forces it - no compile step.
- **Could match Java?** partly - make `test` depend on `lint` in the guide's Makefile.
- **Surprise:** low

### gevent is the closest thing to virtual threads, and no example uses it
- **What:** Monkey-patching with gevent at the entry point makes every blocking call cheap, like a
  virtual thread, with no framework code. It works only with WSGI servers (gunicorn, etc.), and
  every example is FastAPI, so none shows it.
- **Python:** `DIVERGENCE.md:1352-1387`; `Makefile` (`test-gevent`); `docs/runtime/concurrency.md:216`.
- **Java:** none - virtual threads are built in.
- **Why:** Python forces it - no virtual threads in the language.
- **Could match Java?** partly - a WSGI + gevent example would show the Java-like setup.
- **Surprise:** low

### Different example axes: SQLite and one stack here; native images and three DI stacks there
- **What:** Python has a zero-setup SQLite example (no Docker) that Java does not, and only one
  web stack (FastAPI) and one tool (uv + Make). Java's grid has Spring Boot, Quarkus and
  Micronaut, Gradle and Maven, and GraalVM native variants; Python has nothing like native images.
- **Python:** `ekbatan-py-examples/README.md` ("Start here instead", "Why three REST projects and
  not thirty-six").
- **Java:** `ekbatan-examples/` (40 projects, `*-native-*`, `*-maven-*`).
- **Why:** a choice for the stacks; Python forces the lack of native images.
- **Could match Java?** partly - a Flask/Django example could be added; native images have no
  Python counterpart.
- **Surprise:** low
