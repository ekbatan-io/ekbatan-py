# Dracula sweep: distributed jobs, keyed locks, db-scheduler port

Area: `src/ekbatan/distributedjobs/`, `src/ekbatan/locks/`, `db-scheduler/`.
Compared with Java `ekbatan-distributed-jobs`, `ekbatan-core/.../concurrent/`,
`ekbatan-keyed-lock-redis`, and `com.github.kagkarlsson:db-scheduler:16.7.1` (sources jar).

Three findings were checked by running code (2026-10-12): the MySQL lock name, asyncio
re-entry, and the Quartz day-of-week. The rest were checked by reading both sides. One is
marked unverified.

---

### MySQL/MariaDB lock names differ, so a Python service and a Java service do not block each other
- **What:** Python adds `ekbatan:` in front of the `GET_LOCK` name and Java does not. So the same key gives two different lock names, and a Python process and a Java process can both hold "the same" lock at the same time. The Python docstring says the names match exactly. They do not, and no test checks the name.
- **Python:** `src/ekbatan/locks/sql.py:225` `_name_prefix = "ekbatan:"`, `sql.py:248-251` `_name()`. Run for key `wallet:abc`: Python gives `ekbatan:813e4046e5b48a46`.
- **Java:** `ekbatan-core/src/main/java/io/ekbatan/core/concurrent/MySQLKeyedLockProvider.java:150-152` (`Long.toHexString(hash)`, no prefix), same in `MariaDBKeyedLockProvider.java:145-146`. Run for key `wallet:abc`: Java gives `813e4046e5b48a46`.
- **Why:** a choice (looks like a slip). Nothing in Python needs the prefix.
- **Could match Java?** yes. Drop the prefix (or add it on both sides) and add a test that pins the name, as `lock_key_hash` already has one.
- **Surprise:** high

### A keyed lock is "held" by the OS thread, so under asyncio two requests share one lock
- **What:** re-entry is tracked by `threading.get_ident()`. Every coroutine on one event loop runs on the same thread, so a second request on that loop "re-enters" the first request's lock and gets in straight away, with no waiting. The acquire call also blocks, which freezes the whole loop while it waits. Under gevent each greenlet has its own id, so it works there.
- **Python:** `src/ekbatan/locks/_holder.py:134` and `:151` (`(threading.get_ident(), key)`). Run: task A takes `wallet:1`, then task B on the same loop calls `reenter("wallet:1")` and gets a lease (`True`).
- **Java:** `KeyedReentrantHolder.java:62,85` keys on `Thread.currentThread().threadId()`. Each virtual thread has its own id, so each request is its own holder.
- **Why:** Python forces it. Python has no virtual threads, and the port is sync-only (DIVERGENCE.md "The framework is synchronous..."). That section covers the blocking problem but not the lost exclusion.
- **Could match Java?** partly. Keying on a `ContextVar` token would give one holder per asyncio task. The blocking call would still need `asyncio.to_thread`.
- **Surprise:** high
- **Fixed** (`EKB-0250`), by refusing rather than adapting: a holder per task would turn "both get in" into "the loop freezes" unless the lock also grew an async API, and ekbatan is synchronous throughout. Taking a lock on an event loop's thread now raises `ValidationError`, naming the way out (a `def` endpoint, `asyncio.to_thread`).

### Quartz cron: a numeric day-of-week runs one day late
- **What:** in a `CronStyle.QUARTZ` pattern, `2` means Monday in Java (1 = Sunday). croniter reads it as Tuesday. Day names (`MON`) and `?` work correctly.
- **Python:** `db-scheduler/src/db_scheduler/task/schedule/__init__.py:275-330` (croniter, `second_at_beginning` is the only setting that changes). Run: `CronSchedule('0 0 12 ? * 2', utc, QUARTZ)` from Monday 2026-10-12 13:00 gives Tuesday 2026-10-13 12:00. Recorded in `db-scheduler/docs/PORTING.md` (Deliberate differences, first row).
- **Java:** `db-scheduler .../task/schedule/CronSchedule.java:16-20,120-121` (cron-utils with `CronType.QUARTZ`).
- **Why:** a choice. croniter was picked instead of cron-utils, and it has no Quartz mode.
- **Could match Java?** yes. Turn numeric day-of-week values in QUARTZ patterns into names (or subtract 1) before handing the pattern to croniter.
- **Surprise:** high
- **Fixed** (`EKB-0246`): every QUARTZ pattern is now read the way cron-utils reads it and converted for croniter, not just the day numbers. See `db-scheduler/docs/QUARTZ-CONVERTER-LEDGER.md`.

### SIGTERM does not drain running jobs unless you turn it on
- **What:** in Java, an ordinary container stop (SIGTERM) runs `Scheduler.stop()` and lets running jobs finish. That is on by default. In Python, SIGTERM kills the process mid-job unless you call `register_shutdown_hook()`. That call only works on the main thread, and it throws an error from `start()` anywhere else.
- **Python:** `src/ekbatan/distributedjobs/__init__.py:311` (`_shutdown_hook = False`), `:437-465`; `_shutdown.py:52-71` (main-thread check at `:61`).
- **Java:** `JobRegistry.java:187` (`registerShutdownHook = true`), `:436-438`.
- **Why:** Python forces it. CPython has one handler slot per signal, so taking it silently would steal the app's own SIGTERM. Also `atexit` does not run on SIGTERM.
- **Could match Java?** partly. It could be on by default and chain the old handler, which it already does when asked. But it would still fail off the main thread, and it would still take a slot the app may want.
- **Surprise:** high

### `workers(n)`: job threads have a ceiling, one per job by default
- **What:** Python has a `workers(n)` setting that Java does not. Job bodies run on real OS threads, at most one per registered job unless you raise it. Java runs each job on its own virtual thread, with no ceiling. In Python, if you schedule overlapping runs or use immediate execution, jobs wait for a free thread.
- **Python:** `src/ekbatan/distributedjobs/__init__.py:381-413`, used at `:597` (`threads(self._workers or len(self._jobs))`).
- **Java:** `JobRegistry.java:420-422` (`threads(jobs.size())` plus `Executors.newVirtualThreadPerTaskExecutor()`).
- **Why:** Python forces it. There are no virtual threads.
- **Could match Java?** no. An unbounded pool of OS threads is the wrong default.
- **Surprise:** medium

### `stop()` never interrupts a running job; it waits once, then leaves it running
- **What:** Java waits `shutdownMaxWait`, then interrupts the job threads and waits again, so up to 2x. Python waits `shutdown_max_wait` once, logs the jobs still running, and returns. Those jobs keep running on daemon threads until the interpreter exits and kills them mid-line. A job blocked in `sleep()` or I/O gets no `InterruptedException`-style signal.
- **Python:** `db-scheduler/src/db_scheduler/executor.py:342-360`, `scheduler.py:336-360`, `pool.py:1-10` (daemon workers); `distributedjobs/__init__.py:219-225` (docstring says bounded by `shutdown_max_wait`).
- **Java:** db-scheduler `Executor.java:66-70` ("Will wait up to 2x"), `ExecutorUtils.java:28-37` (`shutdownNow()`); Ekbatan `JobRegistry.java:121-125`.
- **Why:** Python forces it. Python cannot interrupt a thread.
- **Could match Java?** no for the interrupt. Jobs have to check `ctx.scheduler_state.is_shutting_down` themselves.
- **Surprise:** medium

### Drain-at-exit is a separate `stop_on_exit` switch, built on `atexit`
- **What:** Java has one switch, `registerShutdownHook`. Python splits it in two: `stop_on_exit` (on by default; runs on normal exit, an unhandled exception and Ctrl-C) and `register_shutdown_hook` (off by default; runs on SIGTERM). A Java developer who turns off the one switch they know leaves the other one on. With a job in flight, exit can wait for up to `shutdown_max_wait` (30 min by default).
- **Python:** `src/ekbatan/distributedjobs/__init__.py:176-179` (`atexit.register` in `start()`), `:415-435`; `build_manual` never registers it (`:515-518`).
- **Java:** `JobRegistry.java:302-305` (one knob).
- **Why:** Python forces it. The two halves of a JVM shutdown hook map to two different Python mechanisms.
- **Could match Java?** partly. One builder switch could set both, but SIGTERM would still need the opt-in described above.
- **Surprise:** medium

### `KeyedLockProvider.close()` releases held locks, including other threads' locks
- **What:** Java's `close()` does nothing by default, and no provider overrides it. Python's `close()` releases every lock this provider is currently holding, for every thread, while those threads are still using them. A re-entered hold only loses one level.
- **Python:** `src/ekbatan/locks/sql.py:95-96`, `redis.py:132-133`, `_holder.py:181-185`.
- **Java:** `KeyedLockProvider.java:125` (`default void close() {}`), not overridden in `PostgresKeyedLockProvider`, `MySQLKeyedLockProvider`, `MariaDBKeyedLockProvider` or `RedisKeyedLockProvider`.
- **Why:** a choice.
- **Could match Java?** yes. Make `close()` a no-op, or just stop the watchdog.
- **Surprise:** medium

### The db-scheduler port stores task data as JSON; Java's default is Java serialization
- **What:** with no serializer set, Java db-scheduler writes `task_data` with `JavaSerializer`, and the port writes JSON. A Java and a Python instance sharing one `scheduled_tasks` table cannot read each other's one-time task data unless both are set to the same format. Ekbatan's own recurring jobs carry no data, so they are not affected.
- **Python:** `db-scheduler/src/db_scheduler/jdbc/repository.py:93-95` (`serializer or JsonSerializer()`); `builder.py:131` (`_serializer = None`).
- **Java:** db-scheduler `SchedulerBuilder.java:65`, `serializer/Serializer.java:22` (`DEFAULT_JAVA_SERIALIZER`).
- **Why:** Python forces it. Java serialization has no Python counterpart (PORTING.md, last row).
- **Could match Java?** no for the default. Java users would have to set a JSON serializer on their side.
- **Surprise:** medium

### The db-scheduler port has no `SchedulerClient` builder
- **What:** Java has `SchedulerClient.Builder.create(dataSource, tasks).serializer(..).tableName(..).build()` for a process that only schedules work. The port has no builder. You construct `StandardSchedulerClient(task_repository)` yourself and wire `JdbcTaskRepository(engine, scheduler_name=..., customization=..., task_resolver=...)` by hand. That repository's defaults (for example `persist_timestamp_in_utc=True`) are not the ones the scheduler builder would pick.
- **Python:** `db-scheduler/src/db_scheduler/client.py:176-189`; `jdbc/repository.py:55-67`. The README ("build a `StandardSchedulerClient` over the same table") shows no code for it.
- **Java:** db-scheduler `SchedulerClient.java:342-385`.
- **Why:** a choice (not ported).
- **Could match Java?** yes. A `SchedulerClient.builder(engine, *tasks)` that reuses the scheduler builder's repository setup.
- **Surprise:** medium

### A waiting `acquire` cannot be interrupted
- **What:** Java's `acquire` throws `InterruptedException`. SQL providers check for an interrupt between 5-second segments, and Redis responds at once, so a shutdown can stop a thread that is waiting on a lock. Python's `acquire` loops until it gets the lock, and another thread has no way to stop it.
- **Python:** `src/ekbatan/locks/sql.py:69-77`, `redis.py:99-109`.
- **Java:** `KeyedLockProvider.java:94`, `PostgresKeyedLockProvider.java:87-95`, `RedisKeyedLockProvider.java:74` (`lockInterruptibly`).
- **Why:** Python forces it. Python has no `Thread.interrupt()`.
- **Could match Java?** partly. A cancel flag or event checked between segments is possible, but it would be new API.
- **Surprise:** medium

### Build the scheduler after `fork()`
- **What:** under gunicorn `--preload`, uwsgi or a manual fork, a `JobRegistry`/`Scheduler` built before the fork has no worker threads in the child, and its pool refuses work. Java developers never meet this.
- **Python:** `db-scheduler/src/db_scheduler/pool.py:35-54` (`os.register_at_fork` marks pools as forked); README "Threads, and forking".
- **Java:** none.
- **Why:** Python forces it. Threads do not survive `fork()`, and Python servers fork worker processes.
- **Could match Java?** no.
- **Surprise:** medium

### Redis lock: other key layout than Redisson, so mixing Java and Python on one Redis is unsafe (unverified)
- **What:** Python stores the lock as a plain string (`SET NX PX`). Redisson stores it as a hash. With the same namespace and key, Redisson's lock script would likely fail with a `WRONGTYPE` error on a key Python holds, instead of waiting. Python treats a Redisson-held key as busy, which is correct.
- **Python:** `src/ekbatan/locks/redis.py:140` (`client.set(prefix + key, token, nx=True, px=...)`).
- **Java:** `ekbatan-keyed-lock-redis/.../RedisKeyedLockProvider.java:72-74` (`redisson.getLock(...).lockInterruptibly(...)`).
- **Why:** Python forces it. There is no Redisson for Python (DIVERGENCE.md "The Redis lock is `SET NX PX`...").
- **Could match Java?** partly. Python could write Redisson's hash format with the same Lua scripts. Not tried.
- **Surprise:** medium (unverified: the Redisson side was not run)

### Redis `acquire` checks every 50 ms instead of being woken up
- **What:** Redisson wakes a waiter through pub/sub when the lock is freed. Python waiters retry `SET NX` every 50 ms, so hand-off can lag by up to 50 ms, and each waiter sends about 20 Redis commands a second.
- **Python:** `src/ekbatan/locks/redis.py:45` (`POLL = 50ms`), `:105-109`, `:122-130`.
- **Java:** `RedisKeyedLockProvider.java:74,92` (Redisson `lockInterruptibly` / `tryLock`).
- **Why:** Python forces it. redis-py has no blocking `SET NX` and no Redisson.
- **Could match Java?** partly. Release could publish on a channel and waiters could subscribe.
- **Surprise:** low

### `build_manual()` and `ManualJobRegistry` exist only in Python
- **What:** Python's builder can produce a registry with no threads and a settable clock, for tests (`registry.scheduler.run_any_due_executions()`, `.tick(...)`). Java tests use db-scheduler's `TestHelper` directly.
- **Python:** `src/ekbatan/distributedjobs/__init__.py:264-280`, `:488-518`.
- **Java:** none.
- **Why:** a choice.
- **Could match Java?** yes. Java could add the same builder method over `TestHelper.createManualScheduler`.
- **Surprise:** low

### Lock API shape: `None`, `release()`, `held`, a Protocol
- **What:** `try_acquire` returns `Lease | None` where Java returns `Optional<Lease>`. A lease has `release()` and `held` where Java has `close()` and `isHeld()`. `KeyedLockProvider` is a structural `Protocol`, so any object with matching methods counts as one. The error is `LockAcquisitionError`, not `...Exception`.
- **Python:** `src/ekbatan/locks/base.py:66-72,89-102`; `errors.py:84`.
- **Java:** `KeyedLockProvider.java:77,121,137-151`; `LockAcquisitionException.java:16`.
- **Why:** a Python convention.
- **Could match Java?** partly. A `close()` alias on `Lease` is cheap. `Optional` is not Python.
- **Surprise:** low

### Lock providers: one `builder()` per class, an `engine`, and public constructors
- **What:** Java uses `PostgresKeyedLockProvider.Builder.postgresKeyedLockProvider().connectionProvider(cp).build()`. Python uses `PostgresKeyedLockProvider.builder().engine(engine).build()`, or just `PostgresKeyedLockProvider(engine)`. The Redis provider uses `.client(redis.Redis)` and is not exported from `ekbatan.locks` (import it from `ekbatan.locks.redis`). Its constructor raises `LockAcquisitionError` for a blank namespace, while its builder raises `ValidationError` for the same mistake.
- **Python:** `src/ekbatan/locks/sql.py:51-55,263-283`; `redis.py:87-97,175-202`; `locks/__init__.py:17-26`.
- **Java:** `PostgresKeyedLockProvider.java:240-266`; `RedisKeyedLockProvider.java:161-201`.
- **Why:** a Python convention (an `Engine` instead of a `ConnectionProvider`; constructors are public in Python).
- **Could match Java?** partly. The two blank-namespace errors could at least be the same type.
- **Surprise:** low

### db-scheduler port: no `register_shutdown_hook()`, and a few renamed builder methods
- **What:** the port's `SchedulerBuilder` has no `register_shutdown_hook()`. Some names change by more than snake_case: `startTasks` becomes `add_start_tasks`, `pollUsingFetchAndLockOnExecute` becomes `poll_using_fetch`, and `Scheduler.create(ds, ...)` becomes the function `create(engine, ...)`. `dueExecutor`, `housekeeperExecutor` and `commitWhenAutocommitDisabled` are gone.
- **Python:** `db-scheduler/src/db_scheduler/builder.py:147,156,248,379`.
- **Java:** db-scheduler `SchedulerBuilder.java:91,129,134,196,201,230`.
- **Why:** a choice. The signal problem above applies, and the JDBC-only knobs have nothing to act on.
- **Could match Java?** yes for the names. Partly for the hook.
- **Surprise:** low

### db-scheduler port refuses zero or negative intervals
- **What:** `polling_interval(0)` and `heartbeat_interval(0)` raise an error. Java db-scheduler accepts them and then polls in a tight loop. (Ekbatan's `JobRegistry` refuses them on both sides.)
- **Python:** `db-scheduler/src/db_scheduler/builder.py:58-70,165-171`.
- **Java:** db-scheduler `SchedulerBuilder.java:101-108` (no check).
- **Why:** a choice.
- **Could match Java?** yes, but refusing is safer.
- **Surprise:** low

### `JobRegistry` extras: `.scheduler`, `with_config`, `with` block, public constructor; other error type
- **What:** Python's `JobRegistry` gives access to the underlying scheduler, works as a `with` block, and has a public constructor. The builder takes a `JobsConfig` with `with_config()`; in Java the DI layer copies each value. `JobsConfig.builder()` stands in for Java's `JobsConfig.jobsConfig()`. A failed start raises `ValidationError` (a `ValueError`) where Java raises `IllegalStateException`.
- **Python:** `src/ekbatan/distributedjobs/__init__.py:141-163,206-217,253-258,478-486`; `config.py:71-73`.
- **Java:** `JobRegistry.java:67,104,110` (private constructor, `IllegalStateException`); Spring `EkbatanDistributedJobsConfiguration.java:89-93`.
- **Why:** a Python convention, plus no DI container to do the copying.
- **Could match Java?** partly. The error type could be a Python `RuntimeError`-style state error.
- **Surprise:** low

### No container wiring: you make the jobs engine and call start/stop yourself
- **What:** in Java, Spring/Quarkus/Micronaut find `@EkbatanDistributedJob` beans, create a dedicated jobs pool, and call `start`/`stop` for you with the hook off. In Python you build a separate `Engine`, register each job by hand, and call `start()`/`stop()` from your app's startup and shutdown (for example a FastAPI lifespan).
- **Python:** `src/ekbatan/distributedjobs/__init__.py:314-322` (engine "should typically be a dedicated one").
- **Java:** `ekbatan-di/spring/autoconfigure/.../EkbatanDistributedJobsConfiguration.java:46-48,66-93`; `ekbatan-di/annotations/.../EkbatanDistributedJob.java`.
- **Why:** a Python convention (no common DI container).
- **Could match Java?** partly. Small helpers for FastAPI and Django are possible.
- **Surprise:** low

### CPU-heavy jobs do not run in parallel (GIL)
- **What:** job bodies, the poll loop and the heartbeat thread all share one interpreter lock. Two CPU-heavy jobs take turns on one core, where Java would use two cores. Unverified: a C extension that holds the lock for a long time could delay heartbeats past the 60-second dead window and cause a second run elsewhere.
- **Python:** `db-scheduler/src/db_scheduler/pool.py` (threads); `distributedjobs/__init__.py:370-371` (heartbeat on a housekeeping thread).
- **Java:** `JobRegistry.java:422` (virtual threads on all cores).
- **Why:** Python forces it.
- **Could match Java?** no. A process pool would be a different design.
- **Surprise:** low (heartbeat part unverified)

### The port always writes `priority`
- **What:** Java leaves the `priority` column NULL when priority ordering is off. The port always writes a value. That is harmless, but a shared table will have Python rows with a priority and Java rows without one.
- **Python:** `db-scheduler/src/db_scheduler/jdbc/repository.py:197`; PORTING.md "priority written only when...".
- **Java:** db-scheduler `JdbcTaskRepository` (writes priority only when `enablePriority()` is set), per PORTING.md. Not re-read here.
- **Why:** a choice. NULL sorts differently on Postgres and MySQL.
- **Could match Java?** yes, but the port's way is safer.
- **Surprise:** low
