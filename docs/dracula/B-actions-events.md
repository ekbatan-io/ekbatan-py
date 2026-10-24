# Dracula sweep - area B: actions, events, streaming, test helpers

Files: `src/ekbatan/action/`, `src/ekbatan/events/`, `src/ekbatan/streaming.py`,
`src/ekbatan/testing.py`, and what `src/ekbatan/__init__.py` exports from them. Java side:
`ekbatan-core/.../core/action/`, `ekbatan-events/local-event-handler/`,
`ekbatan-events/streaming/`, `ekbatan-test-support/`, `ekbatan-di/`.

Swept on 2026-10-12. "Run" means the claim was checked by running code on 2026-10-12 (scratch
scripts against `ekbatan-py-examples/wallet_service_sqlite`); everything else was read on both
sides. Ordered by surprise, highest first.

---

### ~~The handling job you build yourself never runs unless you switch it on~~ fixed (`EKB-0275`)
- **Fixed:** a hand-built `EventHandlingJob` runs on `FixedDelay(handling poll delay)`, 1 s by default, as Java's does, whatever the config says. The job no longer reads `handling.enabled`; the wiring does, as Java's DI does - the examples pass `.enabled(config.handling.enabled)`. `enabled=False` still registers it with a `DisabledSchedule`.
- **What:** `EventHandlingJob(...)`, or its builder, with no config gives a job whose schedule is
  `DisabledSchedule`, so it registers and never delivers anything, and nothing says so. In Java a
  hand-built `EventHandlingJob` always runs. Only the Spring/Quarkus/Micronaut wiring checks
  `handling.enabled`, and it does that by not creating the bean at all. Run: both forms report
  `enabled=False` and a `DisabledSchedule`.
- **Python:** `src/ekbatan/events/jobs.py:181,186,204-206`. The default is `HandlingConfig.enabled = False` (`events/config.py:35`)
- **Java:** `ekbatan-events/local-event-handler/.../job/EventHandlingJob.java:158-160` (always `FixedDelay.of(pollDelay)`, no enabled flag); the switch lives in DI, `ekbatan-di/spring/autoconfigure/.../EkbatanLocalEventHandlerConfiguration.java:97` (`@ConditionalOnProperty`)
- **Why:** a choice. With no container to decide whether the bean exists, the switch was moved onto the job, and it kept the DI default of "off".
- **Could match Java?** yes. A hand-built job could default to on, leaving "off" as the config-file default only. That is what the Java split amounts to.
- **Surprise:** high

### ~~Two `SystemClock()` objects count as "different clocks", and the executor refuses to start~~ fixed (`EKB-0302`)
- **Fixed:** the check is gone, as Java has none. An action built with another clock is accepted;
  it reads its own clock, and the rows are stamped from the executor's. The text below is what it was.
- **What:** the executor checks that every registered action holds the *same clock object* it
  holds (`is`, not "same kind"). Build actions with `SystemClock()` and leave the executor's clock
  unset, and the executor makes its own `SystemClock()` and refuses at build time:
  `OpenWalletAction was built with a different clock than this ActionExecutor`. Run: confirmed.
  Java has no such check, and `Clock.systemUTC()` twice is fine there.
- **Python:** `src/ekbatan/action/executor.py:226` (`self.clock = clock or SystemClock()`), `:245-261` (identity check)
- **Java:** none. `ActionExecutor.java:99,411` takes `Clock.systemUTC()` by default and never compares it with the actions' clocks
- **Why:** a choice. Without DI the two clocks are wired by hand and can differ. The check catches a frozen-clock test that passes by accident.
- **Could match Java?** partly. The check could treat two `SystemClock`s as equal (or `SystemClock` could be a single shared object), which keeps the protection and removes the trap.
- **Surprise:** high

### ~~Event payloads and action params are written with Python field names (`new_balance`), not Java's (`newBalance`)~~ fixed (`EKB-0290`)
- **Fixed** (`EKB-0290`): written as Java's Jackson writes them - `newBalance`, declaration order, decimals as numbers - byte for byte.
- **What:** the outbox JSON uses the dataclass field names as they are, so snake_case, with only
  `modelId`/`modelName` in camelCase. Java's Jackson writes `newBalance`. A Java consumer or Java
  handler reading a Python-written row (the ports claim they share one table) will not find its
  fields. Decimals are written as strings (`"15.0000"`). Run: a deposit wrote
  `{"amount": "5.00", "modelId": ..., "modelName": "Wallet", "new_balance": "15.0000"}` and
  `action_params` `{"amount": ..., "recipient": ..., "wallet_id": ...}`. Java's examples
  read `@JsonProperty("newBalance")` (`spring-boot-wallet-rest-maven-native-pg/.../WalletMoneyDepositedEvent.java:26`).
  Whether Java writes BigDecimal as a number or a string is unverified here.
- **Python:** `src/ekbatan/domain.py:138-146` (`payload()`), `src/ekbatan/_json.py:58-61` (field names as is), written by `src/ekbatan/action/outbox.py:364`
- **Java:** `SingleTableJsonEventPersister.java:131-139` (`objectMapper.valueToTree(event)` at :132, Jackson's camelCase field names)
- **Why:** a Python convention. Field names are snake_case and the serialiser copies them.
- **Could match Java?** yes. The serialiser could camelCase keys on write and reverse it in `decode`, or a per-field name could be allowed. Either changes every stored row, so it needs a decision.
- **Surprise:** high

### ~~You declare the outbox and notification tables yourself, pass them in three places, and by default they are not in `eventlog`~~ fixed (`EKB-0520`)
- **Fixed** (`EKB-0520`, the user's decision 2026-10-24): the tables are fixed, as Java's, and nothing passes them - `eventlog.events` / `eventlog.event_notifications` on PostgreSQL; on MySQL and MariaDB the connected database (a separate `eventlog` database would be shared by every database on the server); SQLite has no schemas. See `docs/EVENTLOG-LEDGER.md`.
- **What:** Java writes to a fixed `eventlog.events` / `eventlog.event_notifications`. Here the
  application builds `outbox_table(metadata)` / `notifications_table(metadata)` and hands them to
  the executor (`events=` or a hand-built `SingleTableJsonEventPersister(registry, EVENTS)`), the
  fan-out job (`events=`, `notifications=`) and the handling job (`notifications=`). The default
  is a table named `events` in the *default* schema. `eventlog` needs `schema="eventlog"`. A
  Debezium connector set up from the Java docs for `eventlog.events` finds nothing. ~~The executor
  builder also *requires* `event_persister(...)`, where Java's builder makes the default persister
  itself.~~ **Fixed** (`EKB-0302`): with no persister, the builder and the constructor build the
  default one over `outbox_table(MetaData())`, as Java's builder does.
- **Python:** `src/ekbatan/schema.py:112` (`schema: str | None = None`), `src/ekbatan/action/executor.py:230-235,564-565`, `src/ekbatan/events/jobs.py:111-112,177`
- **Java:** `ekbatan-core/.../single_table_json/EventEntityRepository.java:35-38` (`DSL.name("eventlog", ...)`), `ActionExecutor.java:103-105` (default persister)
- **Why:** a choice. SQLite has no schemas, so the table is passed in rather than named in code. The `schema=None` default follows from that.
- **Could match Java?** partly. `schema` could default to `"eventlog"` on the dialects that have schemas, and the builder could take `events(table)` the way the constructor does. Passing the table in has to stay while SQLite is supported.
- **Surprise:** high

### No annotations, no discovery: every action, handler and job is listed by hand - kept by the user's decision (2026-10-24)
- **Kept by the user's decision (2026-10-24)**: left as it is.
- **What:** Java marks classes `@EkbatanAction` / `@EkbatanEventHandler` and a DI module (Spring,
  Quarkus, Micronaut) finds them, builds them with their dependencies, and builds the registries,
  executor and jobs. Here you write a `build()` function that constructs every action with its
  repositories and clock, puts each into `ActionRegistry.builder().with_action(...)`, puts each
  handler into `EventHandlerRegistry`, and builds both event jobs.
- **Python:** `src/ekbatan/action/registry.py:98-124`, `src/ekbatan/events/handler.py:210-240`, `docs/wiring.md:1-8`, `ekbatan-py-examples/wallet_service_sqlite/app.py:82-160`
- **Java:** `ekbatan-di/annotations/.../EkbatanAction.java`, `EkbatanEventHandler.java`, `ekbatan-di/spring/autoconfigure/.../internal/EkbatanActionsHolder.java`
- **Why:** a choice. Python has no standard container, and the port chose an explicit list over a decorator registry.
- **Could match Java?** partly. A `@ekbatan_action` decorator that collects classes is possible, but it still could not build them with their dependencies without a container.
- **Surprise:** high

### The handler registry accepts clashes Java refuses
- **What:** Java refuses (1) the same handler registered twice, (2) two event classes with the
  same simple name, and (3) an alias equal to another handler's name or alias. Python accepts all
  three without a word. Run: the same instance twice gives two subscribers; two `Same` event
  classes are both subscribed under `"Same"`; an alias `"h"` on a second handler is accepted and
  `by_name("h")` returns the first handler. Read, not run: in case 3 the second handler's queued
  rows under its old name go to the wrong handler. In case 2 one handler is given the other
  class's payload.
- **Python:** `src/ekbatan/events/handler.py:109-124` (only blank name and a different instance under the same name are refused)
- **Java:** `ekbatan-events/local-event-handler/.../EventHandlerRegistry.java:99-139`
- **Why:** a choice (an omission). The Python checks were written for names only.
- **Fixed** (`EKB-0251`): `EventHandlerRegistry.add` is Java's `withHandler` check for check - one lookup table for names and aliases, the short-name collision, the alias checks - with Java's messages; `EventHandlerRegistryBuilderTest` is ported case for case in `tests/unit/test_event_handler_registry.py`.
- **Could match Java?** yes. Port Java's three checks into `add`.
- **Surprise:** medium

### The framework is synchronous, so under asyncio you must push every call to a thread - settled by the user (2026-10-24)
- **Settled by the user (2026-10-24)**: the refusal, with `ekbatan.aio` for async code, is the answer; nothing more to do.
- **What:** `perform`, `execute`, handlers and repositories are plain blocking functions. Java
  can block for free on virtual threads. In an `async def` FastAPI endpoint, a direct
  `executor.execute(...)` stops the whole event loop. You need `await asyncio.to_thread(...)`
  or a plain `def` endpoint. **Since `EKB-0500` it is refused**, not silent: the sync API on an
  event loop's thread raises `BlockingCallError` before it touches the database.
- **Python:** `src/ekbatan/action/executor.py:274-355` (no `async` anywhere in `src/`), `DIVERGENCE.md` "The framework is synchronous..."
- **Java:** `ActionExecutor.java:160` (blocking, run on virtual threads)
- **Why:** Python forces it. Blocking is only free under gevent. asyncio needs a second, async API.
- **Could match Java?** partly. Under gevent it already behaves like Java. Under asyncio only an async surface would.
- **Surprise:** medium

### `execute` takes the principal last, as a keyword, and the principal is a plain string
- **What:** Java: `execute(principal, DepositMoneyAction.class, params)`. Python:
  `execute(DepositMoneyAction, params, principal="alice")`. The principal is optional and is
  `str | None`, not a `java.security.Principal`. `perform(self, principal, params)` and
  `ActionSpec.with_principal` take the same string.
- **Python:** `src/ekbatan/action/executor.py:274-281`, `src/ekbatan/action/action.py:91`
- **Java:** `ActionExecutor.java:160,179-180`, `Action.java:103`
- **Why:** a Python convention for the order and the keyword (optional arguments go last). The type is a choice: Python has no standard `Principal`.
- **Could match Java?** partly. The order could match, but a required first positional principal is unusual in Python. A `Principal` protocol with `.name` could be added.
- **Surprise:** medium

### An action can rename itself in the outbox by overriding `name`
- **What:** `Action.name` is a property that defaults to the class name, and the executor writes
  whatever it returns into `action_name` and the logs. In Java the name is always
  `actionClass.getSimpleName()` and cannot be overridden.
- **Python:** `src/ekbatan/action/action.py:77-81`, used at `src/ekbatan/action/executor.py:299`
- **Java:** `ActionExecutor.java:186`
- **Why:** a choice. It keeps `action_name` stable across a class rename.
- **Could match Java?** yes. Drop the property and always use `type(action).__name__`.
- **Surprise:** medium

### `event_type` means a string on an event and a class on a handler
- **What:** `ModelEvent.event_type` is a property returning the class name (a `str`). A handler's
  `event_type` is the event *class*. Java has `handler.eventType()` (a Class) and no
  `eventType` on the event. Read, not run: the event's property can be overridden. The persister
  then writes the new name, but the handler registry still subscribes under
  `handler.event_type.__name__`. Fan-out would never match, and nothing would complain.
- **Python:** `src/ekbatan/domain.py:149-152`, `src/ekbatan/action/outbox.py:362,374-388`, `src/ekbatan/events/handler.py:82-86,121`
- **Java:** `EventHandler.java` (`Class<E> eventType()`), `SingleTableJsonEventPersister.java:151-165` (always `getSimpleName()`)
- **Why:** a choice.
- **Could match Java?** yes. Make the event's name not overridable (or not public), or give it a different name.
- **Surprise:** medium

### A handler is any object with `name`, `event_type` and `handle`, usually declared as class attributes
- **What:** `EventHandler` is a `Protocol`. A handler is written as `name = "wallet-deposit-alert"`
  and `event_type = MoneyDepositedEvent` on the class, inherits nothing, and is checked only when
  registered (a missing `event_type` is an `AttributeError` there). `aliases` is optional and
  read with `getattr`. Java implements an interface with `name()`, `eventType()` and `handle()`,
  and the compiler checks them.
- **Python:** `src/ekbatan/events/handler.py:68-90,122`, example `ekbatan-py-examples/wallet_service_sqlite/handlers.py:21-25`
- **Java:** `ekbatan-events/local-event-handler/.../EventHandler.java`
- **Why:** a Python convention. A `Protocol` is how Python writes an interface.
- **Could match Java?** partly. An abstract base class would give a clearer error, but class attributes are still how it reads in Python.
- **Surprise:** low

### Retry delays are float seconds, and `RetryConfig()` with no arguments means something
- **What:** `RetryConfig(on=StaleRecordError, max_retries=1, delay=0.1)`. The delay is a float
  number of seconds, while the event config next to it uses `timedelta`. Every argument has a
  default, so `RetryConfig()` is "one retry on a stale record after 100 ms". The exception class
  is a field (`on`, which can be a tuple) rather than the map key Java's `withRetry(Class, ...)`
  takes. Java: `new RetryConfig(1, Duration.ofMillis(100))`, both arguments required.
- **Python:** `src/ekbatan/action/executor.py:47-62,153-166`
- **Java:** `RetryConfig.java:29`, `ExecutionConfiguration.java:63`
- **Why:** a choice.
- **Could match Java?** yes. `delay: timedelta` and no defaults would match. `on` on the config is the recorded divergence.
- **Surprise:** low

### A missing handler is not counted as a failed attempt
- **What:** when a queued row names a handler that is no longer registered, Python leaves it
  pending, does not count an attempt, and looks again after a backoff. Java treats it as a
  delivery failure (attempts go up, it retries, then it expires). Both expire after the retention
  window, but `attempts` and the logs and metrics differ.
- **Python:** `src/ekbatan/events/dispatch.py:218-226`, `src/ekbatan/events/repository.py:178-197`
- **Java:** `EventHandlingJob.java:439-443`
- **Why:** a choice (`EKB-0231`), recorded in the parity ledger.
- **Could match Java?** yes. Raise inside the delivery as Java does.
- **Surprise:** low

### Misconfiguration raises `ValidationError` / `EkbatanError`, not Java's exception types
- **What:** what Java reports as `NullPointerException`, `IllegalArgumentException` or
  `IllegalStateException` (no action registered, a null argument, `plan()` outside an action, a
  handler name clash) is `ValidationError` here (a `ValueError`). `current_plan()` outside a unit
  of work raises `EkbatanError`. Retry config names `StaleRecordError`, not `StaleRecordException`.
- **Python:** `src/ekbatan/action/registry.py:73`, `src/ekbatan/action/action.py:46`, `src/ekbatan/errors.py:24-28`
- **Java:** `ActionRegistry.java:38` (`Validate.notNull` gives NPE), `Action.java:71` (`IllegalStateException`)
- **Why:** a Python convention. Python names exceptions `...Error` and has no NPE.
- **Could match Java?** partly. The types could be `TypeError`/`ValueError`/`RuntimeError`, but there is no exact match.
- **Surprise:** low

### `VirtualClock` ships in the main package, and `VirtualClock(instant)` starts frozen - settled by the user (2026-10-24)
- **Settled by the user (2026-10-24)**: kept as it is.
- **What:** Java's `VirtualClock` is in the separate `ekbatan-test-support` artifact, extends
  `java.time.Clock` (so it fits anywhere a `Clock` does), and `new VirtualClock(realClock)`
  starts running. Python's is exported from `ekbatan` itself and implements ekbatan's own
  one-method `Clock` protocol (`instant()` since `EKB-0419`, `now()` before it). `VirtualClock(instant)` starts
  *frozen* at that instant.
- **Python:** `src/ekbatan/clock.py:21-24,39-67`, exported in `src/ekbatan/__init__.py`
- **Java:** `ekbatan-test-support/.../time/VirtualClock.java:25,31-58`
- **Why:** a choice for the frozen start and for where it lives. Python has no standard `Clock` type to extend.
- **Could match Java?** partly. It could move to `ekbatan.testing`. The constructor meaning was chosen on purpose.
- **Surprise:** low

### `ActionSpec` verifiers fail on a falsy return value
- **What:** Java's verifier is a `Consumer` that asserts inside itself. Here a verifier that
  *returns* something fails when the value is falsy (`False`, `0`, `""`, `[]`), so
  `lambda w: w.balance == x` works. A verifier that happens to return `0` or an empty list fails
  too.
- **Python:** `src/ekbatan/testing.py:128-147`
- **Java:** `ekbatan-test-support/.../action/ActionAssert.java` (Consumer-based)
- **Why:** Python forces it. A lambda cannot hold `assert`, and dropping its result would pass every test.
- **Could match Java?** no. A literal port would pass tests by accident.
- **Surprise:** low

### Parameterless actions take `None`, and `None` is stored as `{}`
- **What:** `execute(SomeAction, None)` is how you run an action with no parameters, and the
  outbox stores `action_params = {}`. Java refuses a null params object.
- **Python:** `src/ekbatan/action/outbox.py:394-412`
- **Java:** `SingleTableJsonEventPersister.java:141-147` (`:142-146`, `isInstanceOf(ObjectNode)` refuses null)
- **Why:** a choice. Recorded in DIVERGENCE.md.
- **Could match Java?** yes. Refuse `None`, at the cost of a dummy params class for every parameterless action.
- **Surprise:** low

### The "not registered" error tells you to do the thing `execute` then refuses
- **What:** running an unregistered type says "...or pass the instance to execute()". Passing the
  instance is then refused: "execute takes the action's type...". Run: both messages seen
  one after the other. This is a stale message, not a design choice.
- **Python:** `src/ekbatan/action/registry.py:73-78` vs `src/ekbatan/action/executor.py:289-296`
- **Java:** `ActionRegistry.java:38` ("No action registered for class")
- **Why:** a choice left behind. `EKB-0224` removed the instance form, and this message was not updated.
- **Could match Java?** yes. Drop the clause.
- **Surprise:** low
- **Fixed** (`EKB-0252`): the clause is dropped.

### The action registry refuses a second instance of the same type
- **What:** registering two instances of one action class raises. Java's builder does `put`, so
  the second replaces the first without a word. The Python registry can also be built straight
  from a list (`ActionRegistry([a, b])`), and `with_action` takes no class token.
- **Python:** `src/ekbatan/action/registry.py:41-66,107-115`
- **Java:** `ActionRegistry.java:63-66`
- **Why:** a choice (stricter).
- **Could match Java?** yes, but Java's silent replace is the weaker behaviour.
- **Surprise:** low

### `EventEnvelope` has an `attempt` field Java's does not
- **What:** the envelope carries `attempt` (1, 2, ...). Java's envelope has no attempt count. A
  handler written against one port's envelope would not compile or run against the other's if
  it used this.
- **Python:** `src/ekbatan/events/handler.py:59-61`
- **Java:** `ekbatan-events/local-event-handler/.../EventEnvelope.java` (11 fields, no attempt)
- **Why:** a choice (an addition).
- **Could match Java?** yes. Remove it, or add it to Java.
- **Surprise:** low

### The event jobs take the config object, and the drainers are public classes of their own
- **What:** `EventFanoutJob(registry, handlers, events=..., notifications=..., config=LocalEventHandlerConfig(...), enabled=...)`
  reads its tuning from the config object. Java's job builders take plain values (`pollDelay`,
  `batchSize`, ...) and the DI module copies them in from config. Python also exports
  `EventFanout` and `EventDispatch` (with `drain_once()` and `drain_until_idle()`) for running a
  round by hand. Java exposes `drainOneRound()` on the job itself.
- **Python:** `src/ekbatan/events/jobs.py:106-130,148-155`, `src/ekbatan/events/fanout.py:78-99`, `src/ekbatan/events/dispatch.py` (`drain_once`, `drain_until_idle`)
- **Java:** `EventFanoutJob.java:119,199-280`, `EventHandlingJob.java:188,472-592`
- **Why:** a choice. With no DI layer to translate config into job settings, the job reads the config itself.
- **Could match Java?** partly. Plain-value builder methods could be added next to `config(...)`.
- **Surprise:** low

### Handlers run on real OS threads, up to one batch wide per shard
- **What:** dispatch runs a batch's handlers in a thread pool as wide as the batch (default 100)
  per shard. These are OS threads, not virtual threads. Handlers must be thread-safe, as in Java,
  but a large `handling_batch_size` now means that many real threads.
- **Python:** `src/ekbatan/events/dispatch.py:127`, `src/ekbatan/events/_across.py:28-71`
- **Java:** `EventHandlingJob.java` (a virtual thread per notification, `Executors.newVirtualThreadPerTaskExecutor()`)
- **Why:** Python forces it. CPython has no virtual threads.
- **Could match Java?** partly. gevent gives the same effect.
- **Surprise:** low

### Streaming: one plain `ActionEvent`, not exported at top level, with extra helpers
- **What:** Java ships consumer-side `ActionEvent` classes in JSON, Avro and Protobuf flavours,
  plus Debezium transforms. Python has one dataclass in `ekbatan.streaming` (not exported from
  `ekbatan`), with Python-only `from_row`, `from_message(json_columns_are_strings=...,
  timestamps_are_epoch_micros=...)` and `is_sentinel`. There are no Avro/Protobuf classes.
- **Python:** `src/ekbatan/streaming.py:38-136`
- **Java:** `ekbatan-events/streaming/action-event/{json,avro,protobuf}`, `ekbatan-events/streaming/debezium-smt/`
- **Why:** a choice. The transforms run in Kafka Connect (a JVM) whatever wrote the row.
- **Could match Java?** partly. Avro/Protobuf consumer types could be generated for Python.
- **Surprise:** low

### Python-only test and plan helpers, and a plan you read differently
- **What:** `run_action(action, params)` returns a `StagedWork` (Python only). `current_plan()`
  is a public function that any code can call (Java's `plan()` is `protected` on the action).
  `ActionPlan(clock)` needs a clock. `plan.additions(Wallet)` returns a list where Java returns a
  map keyed by id. "Has changes" is `bool(plan)`, not `hasChanges()`.
- **Python:** `src/ekbatan/testing.py:45-122`, `src/ekbatan/action/action.py:42-53`, `src/ekbatan/action/plan.py:51,125-134`
- **Java:** `Action.java:69`, `ActionPlan.java:33,120-147`
- **Why:** a choice for the helpers and the return types. `bool(plan)` is a Python convention.
- **Could match Java?** yes for the return types. The helpers are additions.
- **Surprise:** low

### Names and logger names (the one snake_case item)
- **What:** methods are snake_case (`execute`, `with_principal`, `assert_emitted`), the plan is a
  property (`self.plan`, not `plan()`), and builders start with `X.builder()`, not a static
  `X.Builder.x()` import. Loggers are `ekbatan.action` and `ekbatan.events`, not Java's class names
  (`io.ekbatan.core.action.ActionExecutor`), so logging config written for Java does not apply.
- **Python:** `src/ekbatan/action/action.py:83-85`, `src/ekbatan/action/executor.py:41,263-265`, `src/ekbatan/events/dispatch.py:61`
- **Java:** `Action.java:69`, `ActionExecutor.java:80,429`
- **Why:** a Python convention.
- **Could match Java?** partly. Logger names could follow module paths (`ekbatan.action.executor`). The rest is idiom.
- **Logger names fixed** (`EKB-0248`): they are now Java's class names, `io.ekbatan.core.action.ActionExecutor` and the rest. The snake_case names stay.
- **Surprise:** low
